"""AI assistant: data-grounded Q&A over the user's monitoring data.

Every answer is computed from actual platform data. Intent detection routes the
question to a data builder; the LLM (if configured) narrates over that data —
it is never allowed to invent data because the data block is authoritative.
"""
import json
import logging
import re
from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Alert, AnomalyResult, Incident, Resolution, Service, utcnow
from app.services import anomaly as anomaly_svc
from app.services import llm

log = logging.getLogger("alertiq.assistant")


def _counts(db: Session) -> dict:
    now = utcnow()
    return {
        "total_alerts": db.scalar(select(func.count(Alert.id))) or 0,
        "open_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.status == "open")) or 0,
        "critical_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.severity == "critical")) or 0,
        "resolved_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.status == "resolved")) or 0,
        "active_incidents": db.scalar(select(func.count(Incident.id)).where(Incident.status != "resolved")) or 0,
        "resolved_incidents": db.scalar(select(func.count(Incident.id)).where(Incident.status == "resolved")) or 0,
        "total_incidents": db.scalar(select(func.count(Incident.id))) or 0,
        "now": now.isoformat(),
    }


def _alert_breakdown(db: Session, hours: int) -> dict:
    since = utcnow() - timedelta(hours=hours)
    alerts = db.scalars(select(Alert).where(Alert.timestamp >= since)).all()
    return {
        "window_hours": hours,
        "count": len(alerts),
        "by_severity": dict(Counter(a.severity for a in alerts)),
        "by_service": dict(Counter(a.service_name for a in alerts).most_common(10)),
        "by_hour": dict(Counter(a.timestamp.strftime("%Y-%m-%dT%H:00Z") for a in alerts)),
        "top_alerts": [{"id": a.id, "title": a.title, "service": a.service_name,
                        "severity": a.severity, "timestamp": a.timestamp.isoformat()}
                       for a in sorted(alerts, key=lambda x: -x.timestamp.timestamp())][:15],
    }


def _service_profile(db: Session, name: str) -> dict:
    svc = db.scalar(select(Service).where(Service.name == name))
    alerts = db.scalars(select(Alert).where(Alert.service_name == name)
                        .order_by(Alert.timestamp.desc()).limit(50)).all()
    return {
        "service": name,
        "recorded_dependencies": svc.dependencies if svc else [],
        "last_deploy_at": svc.last_deploy_at.isoformat() if svc and svc.last_deploy_at else None,
        "recent_alerts": [{"id": a.id, "title": a.title, "severity": a.severity,
                           "timestamp": a.timestamp.isoformat(), "metric_values": a.metric_values} for a in alerts],
        "open_count": sum(1 for a in alerts if a.status == "open"),
    }


# ------------------------------------------------------------------ intent routers
def _intent(question: str) -> str:
    q = question.lower()
    if re.search(r"root cause|caused this|cause of", q):
        return "root_cause"
    if re.search(r"unusual|anomal|pattern.*(last|24|hours|days)|new pattern", q):
        return "anomalies"
    if re.search(r"related|correlat", q):
        return "correlation"
    if re.search(r"resolve|fix|how (can|do) i|remediat", q):
        return "resolution"
    if re.search(r"investigate first|first step|priorit", q):
        return "prioritize"
    if re.search(r"similar incidents|past incidents|happened before", q):
        return "history"
    if re.search(r"fail next|most likely to fail|risk", q):
        return "risk_forecast"
    if re.search(r"why.*(service|generating)|so many alerts", q) or re.search(r"\b(why|what)\b", q):
        return "service_deep_dive"
    return "overview"


def _build_overview(db: Session) -> dict:
    c = _counts(db)
    return {"summary_counts": c, "last_24h_alerts": _alert_breakdown(db, 24),
            "anomalies_recent": _recent_anomalies(db)}


def _recent_anomalies(db: Session, limit: int = 8) -> list[dict]:
    rows = db.scalars(select(AnomalyResult).order_by(AnomalyResult.created_at.desc()).limit(limit)).all()
    return [{"id": a.id, "service": a.service_name, "kind": a.kind, "score": a.score,
             "confidence": a.confidence, "method": a.method, "explanation": a.explanation} for a in rows]


def answer(db: Session, question: str) -> dict:
    """Returns {answer, engine, context_used, sources, caveats}."""
    intent = _intent(question)
    builders = {
        "overview": _build_overview,
        "anomalies": lambda d: {"anomalies": _recent_anomalies(d, 12),
                                "fresh_scan": anomaly_svc.run_full_scan(d),
                                "window": f"last {settings.anomaly_scan_history_days} days"},
        "root_cause": lambda d: {"summary_counts": _counts(d),
                                 "active_incidents": [_inc(d, i) for i in d.scalars(
                                     select(Incident).where(Incident.status != "resolved")
                                     .order_by(Incident.created_at.desc()).limit(5)).all()],
                                 "last_24h_alerts": _alert_breakdown(d, 24)},
        "correlation": lambda d: {"active_incidents": [_inc(d, i) for i in d.scalars(
            select(Incident).where(Incident.status != "resolved")
            .order_by(Incident.created_at.desc()).limit(8)).all()]},
        "resolution": lambda d: {"active_incidents": [_inc(d, i) for i in d.scalars(
            select(Incident).where(Incident.status != "resolved")
            .order_by(Incident.created_at.desc()).limit(5)).all()],
            "verified_resolutions": [{"service": r.service_name, "signature": r.problem_signature,
                                      "solution": r.solution} for r in d.scalars(
            select(Resolution).where(Resolution.verified == True).limit(10)).all()]},  # noqa: E712
        "prioritize": lambda d: {"active_incidents": [_inc(d, i) for i in d.scalars(
            select(Incident).where(Incident.status != "resolved")
            .order_by(Incident.severity.desc() if False else Incident.created_at.desc()).limit(10)).all()],
            "summary_counts": _counts(d)},
        "history": lambda d: {"resolved_incidents": [_inc(d, i) for i in d.scalars(
            select(Incident).where(Incident.status == "resolved")
            .order_by(Incident.resolved_at.desc()).limit(10)).all()],
            "verified_resolutions": [{"service": r.service_name, "signature": r.problem_signature,
                                      "solution": r.solution} for r in d.scalars(
            select(Resolution).where(Resolution.verified == True).limit(10)).all()]},  # noqa: E712
        "risk_forecast": lambda d: {"by_service_24h": _alert_breakdown(d, 24).get("by_service"),
                                    "by_service_7d": _alert_breakdown(d, 168).get("by_service"),
                                    "anomalies": _recent_anomalies(d, 6),
                                    "services": [{"name": s.name, "dependencies": s.dependencies,
                                                  "criticality": s.criticality}
                                                 for s in d.scalars(select(Service)).all()]},
        "service_deep_dive": lambda d: _service_question_context(d, question),
    }
    context = builders[intent](db)

    engine = "local_data_summary"
    answer_text = _local_answer(intent, question, context)
    caveats = ["Answer generated from platform data by the local summary engine."]

    if llm.available():
        try:
            result = llm.complete_json(
                "You are AlertIQ's assistant. You are given the user's question and an authoritative JSON data block "
                "computed from their monitoring platform. Answer ONLY from that data block. If the data block lacks "
                "information needed, say exactly what is missing. Distinguish confirmed facts from hypotheses. "
                "Never invent data. Respond ONLY with JSON: {\"answer\": \"...\", \"caveats\": [\"...\"]}",
                "AUTHORITATIVE DATA BLOCK (computed from the platform, use only this):\n"
                + json.dumps(context, default=str)[:14000]
                + f"\n\nUser question: {question}",
            )
            answer_text, caveats = result["answer"], result.get("caveats", caveats)
            engine = f"llm:{settings.llm_provider}"
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM assistant failed, using local summary: %s", exc)
            caveats.append(f"LLM error ({type(exc).__name__}); local summary used.")

    return {"answer": answer_text, "engine": engine, "context_used": _slim(context),
            "sources": [], "caveats": caveats}


def _inc(db: Session, i: Incident) -> dict:
    from app.services.analysis import _incident_alerts
    return {"id": i.id, "title": i.title, "severity": i.severity, "status": i.status,
            "affected_services": i.affected_services, "created_at": i.created_at.isoformat(),
            "root_cause": i.root_cause, "correlation": i.correlation_evidence,
            "alert_count": len(_incident_alerts(db, i))}


def _service_question_context(db: Session, question: str) -> dict:
    m = re.search(r"(?:service[s]?\s+)?[\"']?([a-z0-9_\-\.]{2,})[\"']?", question.lower())
    services = [s.name for s in db.scalars(select(Service)).all()]
    target = next((s for s in services if s and s in question.lower()), None)
    if target is None and m:
        candidate = m.group(1)
        target = next((s for s in services if candidate in s.lower()), None)
    if target:
        return {"service_profile": _service_profile(db, target),
                "anomalies_for_service": _recent_anomalies(db, 12)}
    return {"summary_counts": _counts(db), "last_24h_alerts": _alert_breakdown(db, 24),
            "note": "no specific service matched; provide an overview"}


def _local_answer(intent: str, question: str, context: dict) -> str:
    if intent == "overview":
        c = context["summary_counts"]
        a = context["last_24h_alerts"]
        top = ", ".join(f"{s} ({n})" for s, n in list(a["by_service"].items())[:5]) or "none"
        return (f"Platform status: {c['total_alerts']} total alerts ({c['open_alerts']} open, "
                f"{c['critical_alerts']} critical), {c['active_incidents']} active incidents, "
                f"{c['resolved_incidents']} resolved. Last 24h: {a['count']} alerts; noisest services: {top}. "
                f"Recent anomalies on record: {len(context.get('anomalies_recent', []))}.")
    if intent == "anomalies":
        items = context.get("anomalies", [])
        if not items:
            return "No anomalies on record. A fresh scan was triggered — check the Anomalies page in a moment."
        lines = [f"- [{i['kind']}] {i['service']}: score={i['score']}, confidence={i['confidence']} — {i['explanation']}"
                 for i in items[:8]]
        return "Detected anomalies (method-labeled, no model training claimed):\n" + "\n".join(lines)
    if intent == "root_cause":
        incs = context.get("active_incidents", [])
        if not incs:
            return "There are no active incidents right now, so there is no current root cause to analyze."
        parts = []
        for i in incs[:5]:
            parts.append(f"- Incident #{i['id']} '{i['title']}' ({i['severity']}, {i['alert_count']} alerts): "
                         f"{i['root_cause'] or 'root cause not yet analyzed — run Analyze on the incident page'}.")
        return "Active incidents and their recorded root causes:\n" + "\n".join(parts)
    if intent == "correlation":
        incs = context.get("active_incidents", [])
        if not incs:
            return "No active incidents — nothing is currently correlated into an incident."
        parts = []
        for i in incs[:8]:
            hyp = (i.get("correlation") or {}).get("causal_hypotheses") or []
            parts.append(f"- Incident #{i['id']} '{i['title']}' links {i['alert_count']} alerts across "
                         f"{', '.join(i['affected_services'])}. " + ("; ".join(hyp) if hyp else "No causal hypothesis yet."))
        return "Note: correlation does not prove causation.\n" + "\n".join(parts)
    if intent == "resolution":
        incs = context.get("active_incidents", [])
        verified = context.get("verified_resolutions", [])
        parts = [f"- Incident #{i['id']} '{i['title']}': {i['root_cause'] or 'analyze the incident for recommendations'}."
                 for i in incs[:5]]
        if verified:
            parts.append("Verified historical fixes:\n" +
                         "\n".join(f"  - [{v['service']}] {v['signature']} → {v['solution']}" for v in verified[:5]))
        return ("\n".join(parts) if parts
                else "No active incidents. Verified historical fixes will be suggested automatically for matching signatures.")
    if intent == "prioritize":
        incs = sorted(context.get("active_incidents", []),
                      key=lambda i: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(i["severity"], 2))
        parts = [f"{n}. [{i['severity'].upper()}] Incident #{i['id']} '{i['title']}' "
                 f"({i['alert_count']} alerts, services: {', '.join(i['affected_services']) or 'n/a'})"
                 for n, i in enumerate(incs, 1)]
        return ("Recommended investigation order (severity first):\n" + "\n".join(parts)) if parts \
            else "No active incidents — nothing to prioritize."
    if intent == "history":
        incs = context.get("resolved_incidents", [])
        if not incs:
            return "No resolved incidents recorded yet — history will build as incidents are resolved and verified."
        parts = [f"- #{i['id']} '{i['title']}' ({i['severity']}) — root cause on record: {i['root_cause'] or 'not analyzed'}"
                 for i in incs]
        return "Similar past incidents (most recent first):\n" + "\n".join(parts)
    if intent == "risk_forecast":
        d24, d7 = context.get("by_service_24h") or {}, context.get("by_service_7d") or {}
        if not d7:
            return "Not enough alert history to forecast risk. Ingest alerts (CSV or integrations) for a few days first."
        rising = [(s, d7.get(s, 0), d24.get(s, 0)) for s in d7 if d24.get(s, 0) > max(1, d7.get(s, 0) * 0.5)]
        parts = [f"- {s}: {n7} alerts in 7d, {n24} in last 24h" + ("  ← accelerating" if (s, n7, n24) in rising else "")
                 for s, n7, n24 in sorted(d7.items(), key=lambda kv: -kv[1])[:8]]
        anomalies = context.get("anomalies", [])
        if anomalies:
            parts.append("Anomaly signals: " + "; ".join(f"{a['service']} ({a['kind']})" for a in anomalies[:5]))
        return ("This is a statistical extrapolation, not a guarantee. Most alerting services (7d):\n" + "\n".join(parts))
    # service_deep_dive
    prof = context.get("service_profile")
    if not prof:
        c = context["summary_counts"]
        return (f"No specific service matched your question. Platform-wide: {c['total_alerts']} alerts "
                f"({c['open_alerts']} open, {c['critical_alerts']} critical), {c['active_incidents']} active incidents. "
                "Name a service (e.g. 'Why is payment-api generating so many alerts?') for a focused analysis.")
    alerts = prof["recent_alerts"]
    kinds = Counter(a["title"] for a in alerts).most_common(3)
    lines = [f"Service '{prof['service']}' — {len(alerts)} recent alerts ({prof['open_count']} open).",
             f"Recorded dependencies: {', '.join(prof['recorded_dependencies']) or 'none recorded'}.",
             f"Last deployment: {prof['last_deploy_at'] or 'not recorded'}."]
    if kinds:
        lines.append("Most frequent alerts: " + "; ".join(f"'{t}' x{n}" for t, n in kinds))
    return "\n".join(lines)


def _slim(context: dict, cap: int = 4000) -> dict:
    text = json.dumps(context, default=str)
    if len(text) <= cap:
        return context
    return {"_note": f"context truncated for display ({len(text)} chars total)", "keys": list(context.keys())}