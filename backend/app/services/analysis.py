"""AI incident/alert analysis service.

Two engines:
  1. LLM engine — used when a provider is configured (structured JSON, prompt-injection safe).
  2. Local rule-based engine — honest fallback; labeled `local_rules`, produces
     evidence-derived output only, and states exactly what an LLM would add.
"""
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AIAnalysis, Alert, Incident, Resolution, Service
from app.services import llm
from app.services.llm import ANALYSIS_SYSTEM_PROMPT

log = logging.getLogger("alertiq.analysis")

_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def collect_alert_context(db: Session, alert: Alert, extra_alerts: int = 15) -> dict:
    recent = db.scalars(
        select(Alert).where(Alert.service_name == alert.service_name)
        .order_by(Alert.timestamp.desc()).limit(extra_alerts)
    ).all()
    services = db.scalars(select(Service)).all()
    svc = next((s for s in services if s.name == alert.service_name), None)
    verified = db.scalars(
        select(Resolution).where(Resolution.verified == True, Resolution.service_name == alert.service_name)  # noqa: E712
    ).all()
    return {
        "alert": {
            "id": alert.id, "service": alert.service_name, "title": alert.title,
            "description": alert.description[:2000], "severity": alert.severity,
            "labels": alert.labels, "metric_values": alert.metric_values,
            "timestamp": alert.timestamp.isoformat(),
        },
        "alerts": [
            {"id": a.id, "service": a.service_name, "title": a.title, "severity": a.severity,
             "timestamp": a.timestamp.isoformat()} for a in recent
        ],
        "service": {"name": svc.name, "dependencies": svc.dependencies,
                    "last_deploy_at": svc.last_deploy_at.isoformat() if svc and svc.last_deploy_at else None}
        if svc else None,
        "verified_resolutions": [
            {"signature": r.problem_signature, "solution": r.solution} for r in verified[:5]
        ],
    }


def collect_incident_context(db: Session, incident: Incident) -> dict:
    linked = _incident_alerts(db, incident)
    verified = []
    for s in incident.affected_services or []:
        verified += db.scalars(
            select(Resolution).where(Resolution.verified == True, Resolution.service_name == s)  # noqa: E712
        ).all()
    return {
        "incident": {
            "id": incident.id, "title": incident.title, "severity": incident.severity,
            "status": incident.status, "affected_services": incident.affected_services,
            "created_at": incident.created_at.isoformat(),
            "correlation_evidence": incident.correlation_evidence,
        },
        "alerts": [
            {"id": a.id, "service": a.service_name, "title": a.title, "severity": a.severity,
             "description": (a.description or "")[:500], "timestamp": a.timestamp.isoformat(),
             "metric_values": a.metric_values} for a in linked
        ],
        "verified_resolutions": [{"signature": r.problem_signature, "solution": r.solution} for r in verified[:8]],
    }


def _incident_alerts(db: Session, incident: Incident) -> list[Alert]:
    from app.models import AlertIncidentLink
    links = db.scalars(select(AlertIncidentLink).where(AlertIncidentLink.incident_id == incident.id)).all()
    out = []
    for link in links:
        a = db.get(Alert, link.alert_id)
        if a:
            out.append(a)
    return sorted(out, key=lambda x: x.timestamp)


# ------------------------------------------------------------------ local rules engine
_KNOWN_CAUSE_TABLE = [
    (re.compile(r"connection.*(pool|limit)|too many connections", re.I),
     "Database connection pool exhaustion — connections are not being released or the pool is undersized for current traffic."),
    (re.compile(r"latency|slow|timeout", re.I),
     "Elevated latency on a dependency path — commonly DB query latency, saturation of a connection/worker pool, or an upstream slowdown."),
    (re.compile(r"cpu|memory|oom|disk", re.I),
     "Resource saturation (CPU/memory/disk) — check for leaks, traffic growth, or a recent change in resource limits."),
    (re.compile(r"5[0-9][0-9]|error rate|errors", re.I),
     "Elevated server-side error rate — inspect application logs at the timestamps of the correlated alerts."),
    (re.compile(r"deploy|release|rollout", re.I),
     "Change-related failure — symptoms began after a deployment; evaluate rollback or a targeted fix."),
    (re.compile(r"queue|backlog|kafka|rabbitmq", re.I),
     "Message-queue backlog — consumers may be slower than producers; check consumer lag and downstream capacity."),
]


def local_analysis(db: Session, context: dict) -> dict:
    """Rule-based analysis from actual platform data only. Honest limitations stated."""
    alert = context.get("alert") or {}
    alerts = context.get("alerts") or []
    incident = context.get("incident") or {}
    subject_title = alert.get("title") or incident.get("title") or ""
    services = ([alert.get("service")] if alert.get("service") else []) or (incident.get("affected_services") or [])

    causes, steps = [], []
    for pattern, cause in _KNOWN_CAUSE_TABLE:
        if pattern.search(subject_title):
            causes.append(cause)
    for a in alerts:
        for pattern, cause in _KNOWN_CAUSE_TABLE:
            if pattern.search(a.get("title", "")) and cause not in causes:
                causes.append(cause)

    # deployment evidence
    deploy_related = any(
        (a.get("labels") or {}).get("deployment") for a in ([alert] if alert else []) + alerts
    ) or (context.get("service") or {}).get("last_deploy_at")
    if deploy_related and "Change-related failure — symptoms began after a deployment; evaluate rollback or a targeted fix." not in causes:
        causes.append("Change-related failure — a deployment association is present in the data.")

    metric_values = alert.get("metric_values") or {}
    evidence = [
        f"Alert #{alert.get('id')}: '{alert.get('title')}' on '{alert.get('service')}' "
        f"(severity={alert.get('severity')}, at {alert.get('timestamp')})" if alert else None,
        *[f"Correlated alert #{a.get('id')}: '{a.get('title')}' on '{a.get('service')}' at {a.get('timestamp')}"
          for a in alerts[:8]],
        *[f"Metric {k}={v} recorded on the alert" for k, v in metric_values.items()],
    ]
    evidence = [e for e in evidence if e]

    if not causes:
        causes = ["Insufficient data to determine a likely cause — no known signature matched the alert text."]
        steps = ["Add recent application logs around the alert timestamp to the platform.",
                 "Record dependency information for the affected services (Integrations/Services page).",
                 "Enable an LLM provider for deeper analysis of the available context."]
    else:
        steps = [
            "Confirm the alert timeline: check exactly when symptoms began and what changed just before.",
            "Inspect application/traces logs for the affected services at the alert timestamps.",
            f"Check resource and dependency health for: {', '.join(services) or 'affected services'}.",
            "Review the correlated alerts on the incident page for a causal ordering.",
            "If a recent deployment is implicated, consider a rollback while investigating.",
        ]

    gaps = []
    if not metric_values:
        gaps.append("No metric values attached to the alert — attach cpu/memory/latency/error_rate for stronger evidence.")
    if not context.get("verified_resolutions"):
        gaps.append("No verified historical resolutions for this service yet — verify resolutions to improve future analysis.")
    if not llm.available():
        gaps.append("No LLM provider configured — this analysis used the local rule-based engine only.")

    top_sev = incident.get("severity") or alert.get("severity") or "medium"
    sev = ("critical" if _SEVERITY_ORDER.get(top_sev, 2) >= 3 else top_sev)
    return {
        "summary": (f"Analysis of '{subject_title}' on '{', '.join(services) or 'unknown service'}' "
                    f"({len(alerts)} related alert(s) in context)."),
        "severity_assessment": sev,
        "possible_root_cause": causes[0] if causes else "unknown - insufficient data",
        "evidence": evidence,
        "observed_patterns": [f"{len(alerts)} related alerts share this window" if alerts else "single alert, no related alerts in context"],
        "possible_causes": causes,
        "related_alerts": [a.get("id") for a in alerts if a.get("id") is not None],
        "affected_services": services,
        "investigation_steps": steps,
        "recommended_resolution": ("Start with the least-risky investigation steps above; apply a fix only after "
                                   "confirming the cause. No destructive action is recommended automatically."),
        "risk_level": "high" if sev == "critical" else ("medium" if sev == "high" else "low"),
        "confidence": 0.35 if not causes or len(causes) == 1 and "Insufficient" in causes[0] else 0.5,
        "consequences": "If unresolved, elevated error rates/latency may propagate to dependent services "
                        "(see service dependencies recorded in the platform).",
        "references": [],
        "information_gaps": gaps,
        "engine": "local_rules",
    }


# ------------------------------------------------------------------ LLM path
def llm_analysis(db: Session, context: dict) -> dict:
    prompt = llm.build_context_prompt(context)
    result = llm.complete_json(ANALYSIS_SYSTEM_PROMPT, prompt)
    # hard guard: clamp confidence, normalize enums
    try:
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0))))
    except (TypeError, ValueError):
        result["confidence"] = 0.0
    result["severity_assessment"] = result.get("severity_assessment", "medium").lower()
    if result["severity_assessment"] not in _SEVERITY_ORDER:
        result["severity_assessment"] = "medium"
    result["engine"] = f"llm:{settings.llm_provider}"
    return result


def analyze(db: Session, context: dict) -> dict:
    if llm.available():
        try:
            return llm_analysis(db, context)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM analysis failed, falling back to local rules: %s", exc)
            result = local_analysis(db, context)
            result["information_gaps"].append(f"LLM provider error ({type(exc).__name__}); local rules used instead.")
            return result
    return local_analysis(db, context)


def persist_analysis(db: Session, context: dict, result: dict, alert_id: int | None, incident_id: int | None) -> AIAnalysis:
    row = AIAnalysis(
        alert_id=alert_id, incident_id=incident_id,
        summary=result["summary"], severity_assessment=result["severity_assessment"],
        possible_root_cause=result["possible_root_cause"], evidence=result.get("evidence", []),
        observed_patterns=result.get("observed_patterns", []), possible_causes=result.get("possible_causes", []),
        related_alerts=result.get("related_alerts", []), affected_services=result.get("affected_services", []),
        investigation_steps=result.get("investigation_steps", []),
        recommended_resolution=result.get("recommended_resolution", ""),
        risk_level=result.get("risk_level", "unknown"), confidence=result.get("confidence", 0.0),
        consequences=result.get("consequences", ""), references=result.get("references", []),
        information_gaps=result.get("information_gaps", []), engine=result["engine"],
    )
    db.add(row)
    if incident_id:
        inc = db.get(Incident, incident_id)
        if inc:
            inc.root_cause = result["possible_root_cause"]
            inc.root_cause_confidence = result["confidence"]
            inc.recommended_resolution = result["recommended_resolution"]
    db.commit()
    db.refresh(row)
    return row


def analyze_json(db: Session, context: dict, alert_id: int | None = None, incident_id: int | None = None) -> AIAnalysis:
    result = analyze(db, context)
    return persist_analysis(db, context, result, alert_id, incident_id)