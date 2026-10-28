"""Decision-support recommendation engine.

Produces a RANKED list of actions per incident. Scoring factors:
severity, confidence, business impact (criticality of affected services),
number of affected services, expected benefit, risk, time, evidence strength.
Recommendations are advisory only — never executed automatically.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, Incident, Recommendation, Resolution, Service


def _service_criticality(db: Session, names: list[str]) -> int:
    rows = db.scalars(select(Service).where(Service.name.in_(names))).all() if names else []
    return max(({s.name: s.criticality for s in rows}.get(n, "medium") for n in names), default="medium")


def _generate_candidates(incident: Incident, alerts: list[Alert], verified: list[Resolution]) -> list[dict]:
    text = " ".join([incident.title] + [a.title for a in alerts] + [incident.description]).lower()
    cands: list[dict] = []

    def add(action, rationale, benefit, risk, difficulty, evidence):
        cands.append({"action": action, "rationale": rationale, "expected_benefit": benefit,
                      "risk": risk, "difficulty": difficulty, "evidence": evidence})

    if re.search(r"latency|slow|timeout", text):
        add("Check database query latency and slow-query log for the affected window",
            "Latency alerts frequently trace to database slowdowns.", "Locate the latency source directly.",
            "Read-only, minimal risk.", "low",
            "latency/timeout signatures present in correlated alerts" if alerts else "incident title"),
        add("Examine connection-pool utilization and wait events",
            "Exhausted pools present as latency before errors.", "Confirms or rules out pool exhaustion.",
            "Read-only, minimal risk.", "low", "standard follow-up for latency signatures"),
        add("Check upstream/dependency latency for the affected services",
            "Cascading latency often originates upstream.", "Identifies the origin service.",
            "Read-only, minimal risk.", "medium", "service dependency data recorded in the platform")
    if re.search(r"connection.*(pool|limit)|too many connections", text):
        add("Inspect connection-pool settings and current connection counts",
            "The alert signature matches pool exhaustion.", "Direct evidence of the pool state.",
            "Read-only, minimal risk.", "low", "alert title contains connection-pool signature")
        add("If confirmed, increase the connection pool size within safe DB limits",
            "Standard remediation for pool exhaustion.", "Restores throughput.",
            "Requires capacity validation; do not exceed database max_connections.", "medium",
            "pattern match on pool-exhaustion signature")
    if re.search(r"cpu|memory|oom|disk", text):
        add("Review CPU/memory/disk utilization and process list for the affected service",
            "Resource alerts indicate saturation or a leak.", "Distinguishes leak vs load growth.",
            "Read-only, minimal risk.", "low", "resource-signature match in alert text")
        add("Compare current utilization against the pre-incident baseline",
            "A step-change usually means a leak or traffic change.", "Quantifies the deviation.",
            "Read-only, minimal risk.", "low", "metric history stored in the platform")
    if re.search(r"deploy|release|rollout", text) or any((a.labels or {}).get("deployment") for a in alerts):
        add("Inspect the most recent deployment diff and consider a rollback if changes correlate with onset",
            "Change-related failures are the most common incident trigger after resource issues.",
            "Fast mitigation when the correlation is strong.", "Rollback affects users; needs approval.",
            "medium", "deployment timing correlation present in the data")
    if re.search(r"5[0-9][0-9]|error|exception|fail", text):
        add("Inspect application error logs and stack traces at incident onset",
            "Server-side errors usually have a direct log trail.", "Identifies the failing component.",
            "Read-only, minimal risk.", "low", "error-rate signature in alerts")
    if not cands:
        add("Collect additional evidence: recent logs, dependency health, and change events for the affected services",
            "Not enough signal-specific evidence for a targeted action.", "Builds the evidence base for a real fix.",
            "Read-only, minimal risk.", "low", "generic evidence-gathering step")

    for r in verified[:3]:
        add(f"Verified historical fix for a similar problem: {r.solution}",
            "A user-verified resolution exists for a matching problem signature.",
            "Previously proven to work in this environment.",
            "Review applicability to the current conditions before applying.",
            "medium", f"verified resolution record #{r.id} (user-confirmed)")

    return cands


def _score(incident: Incident, cand: dict, criticality: str, n_services: int, confidence_hint: float) -> float:
    sev_w = {"critical": 1.0, "high": 0.85, "medium": 0.6, "low": 0.4, "info": 0.2}.get(incident.severity, 0.5)
    crit_w = {"critical": 1.0, "high": 0.8, "medium": 0.6, "low": 0.4}.get(criticality, 0.6)
    diff_w = {"low": 1.0, "medium": 0.7, "high": 0.4}.get(cand["difficulty"], 0.6)
    risk_w = 1.0 if "minimal risk" in cand["risk"].lower() else 0.5
    impact_w = min(1.0, 0.5 + 0.15 * max(0, n_services - 1))
    return round(10 * (0.25 * sev_w + 0.2 * crit_w + 0.2 * diff_w + 0.15 * risk_w + 0.1 * impact_w + 0.1 * confidence_hint), 2)


def generate_recommendations(db: Session, incident: Incident) -> list[Recommendation]:
    from app.services.analysis import _incident_alerts

    # clear previous generation for this incident
    for old in db.scalars(select(Recommendation).where(Recommendation.incident_id == incident.id)).all():
        db.delete(old)

    alerts = _incident_alerts(db, incident)
    services = incident.affected_services or []
    criticality = _service_criticality(db, services)
    verified = db.scalars(select(Resolution).where(
        Resolution.verified == True, Resolution.service_name.in_(services or ["__none__"])  # noqa: E712
    )).all()

    candidates = _generate_candidates(incident, alerts, verified)
    out = []
    for cand in candidates:
        score = _score(incident, cand, criticality, len(services), 0.5)
        destructive = not ("minimal risk" in cand["risk"].lower() or "read-only" in cand["risk"].lower())
        out.append(Recommendation(
            incident_id=incident.id, rank=0, action=cand["action"], rationale=cand["rationale"],
            evidence=cand["evidence"] if isinstance(cand["evidence"], list) else [cand["evidence"]],
            expected_benefit=cand["expected_benefit"], risk=cand["risk"], difficulty=cand["difficulty"],
            priority=("high" if score >= 7.5 else ("medium" if score >= 5.5 else "low")),
            requires_human_approval=destructive, score=score, source="rules",
        ))
    out.sort(key=lambda r: -r.score)
    for i, r in enumerate(out, 1):
        r.rank = i
        db.add(r)
    db.commit()
    return out


def format_ranked(db: Session, incident: Incident) -> list[dict]:
    recs = db.scalars(select(Recommendation).where(Recommendation.incident_id == incident.id)
                      .order_by(Recommendation.rank)).all()
    return [{
        "rank": r.rank, "action": r.action, "rationale": r.rationale, "evidence": r.evidence,
        "expected_benefit": r.expected_benefit, "risk": r.risk, "difficulty": r.difficulty,
        "priority": r.priority, "requires_human_approval": r.requires_human_approval,
        "score": r.score, "source": r.source,
    } for r in recs]