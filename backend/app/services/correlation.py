"""Incident correlation: groups related alerts into incidents.

Signals used (weighted):
  - time proximity (within a configurable window)
  - service dependency links (from Service.dependencies, both directions)
  - shared fingerprint family / token overlap in titles
  - severity escalation (a critical following highs is a strong cascade hint)
  - deployment recency (alerts shortly after last_deploy_at)

Correlation is explicitly presented as *candidate* relationships, never causation.
"""
import re
import statistics
from collections import Counter
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Alert, Incident, Service, utcnow

_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _title_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _dependency_related(db: Session, service_a: str, service_b: str) -> bool:
    if service_a == service_b:
        return False
    services = {s.name.lower(): s for s in db.scalars(select(Service)).all()}
    a, b = services.get(service_a.lower()), services.get(service_b.lower())
    if not a or not b:
        return False
    a_deps = {d.lower() for d in (a.dependencies or [])}
    b_deps = {d.lower() for d in (b.dependencies or [])}
    return service_b.lower() in a_deps or service_a.lower() in b_deps


def pair_score(db: Session, a: Alert, b: Alert) -> tuple[float, list[str]]:
    """Similarity score in [0,1] plus the human-readable reasons."""
    reasons, score = [], 0.0
    dt = abs((a.timestamp - b.timestamp).total_seconds())
    window = settings.correlation_window_minutes * 60
    if dt <= window:
        proximity = 1 - dt / window
        score += 0.35 * proximity
        reasons.append(f"time proximity ({int(dt)}s apart, window {settings.correlation_window_minutes}m)")
    if a.service_name == b.service_name:
        score += 0.25
        reasons.append("same service")
    elif _dependency_related(db, a.service_name, b.service_name):
        score += 0.30
        reasons.append(f"service dependency link ({a.service_name} <-> {b.service_name})")
    sim = _title_similarity(a.title, b.title)
    if sim >= 0.3:
        score += 0.20 * sim
        reasons.append(f"shared alert signature ({sim:.0%} token overlap)")
    sev_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    if {a.severity, b.severity} & {"critical", "high"} and abs(sev_order[a.severity] - sev_order[b.severity]) <= 1:
        score += 0.10
        reasons.append("adjacent high severities (possible escalation)")
    if a.labels.get("deployment") or b.labels.get("deployment"):
        score += 0.10
        reasons.append("deployment-related label present")
    return min(score, 1.0), reasons


def _group_alerts(db: Session, alerts: list[Alert]) -> list[list[Alert]]:
    """Single-linkage clustering over pair_score >= threshold, restricted to the time window."""
    threshold = 0.45
    n = len(alerts)
    alerts = sorted(alerts, key=lambda x: x.timestamp)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if (alerts[j].timestamp - alerts[i].timestamp) > timedelta(minutes=settings.correlation_window_minutes * 3):
                break  # sorted by time: no later alert can be within the window
            score, _ = pair_score(db, alerts[i], alerts[j])
            if score >= threshold:
                pi, pj = find(i), find(j)
                if pi != pj:
                    parent[pi] = pj

    groups: dict[int, list[Alert]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(alerts[i])
    return [g for g in groups.values() if len(g) >= 2]  # singletons don't form incidents


def _infer_causal_chain(group: list[Alert], db: Session) -> list[str]:
    """Ordered hypothesis: earliest alert's service is a candidate root cause if
    other services depend on it. Explicitly a hypothesis, not a fact."""
    ordered = sorted(group, key=lambda x: x.timestamp)
    chain = []
    first = ordered[0]
    for al in ordered[1:]:
        if al.service_name != first.service_name and _dependency_related(db, al.service_name, first.service_name):
            chain.append(
                f"Hypothesis: '{first.service_name}' ({first.title}) preceded '{al.service_name}' ({al.title}) "
                f"and {al.service_name} depends on {first.service_name} — correlation only, causation unconfirmed."
            )
    return chain


def correlate_open_alerts(db: Session) -> list[dict]:
    """Creates incidents for correlated groups of open alerts that have no incident yet."""
    from app.models import AlertIncidentLink
    open_alerts = db.scalars(select(Alert).where(Alert.status.in_(["open", "acknowledged"]))).all()
    linked_alert_ids = {lid for (lid,) in db.execute(select(AlertIncidentLink.alert_id)).all()}
    unlinked = [a for a in open_alerts if a.id not in linked_alert_ids]
    if len(unlinked) < 2:
        return []

    created = []
    for group in _group_alerts(db, unlinked):
        services = list(dict.fromkeys(a.service_name for a in group))
        sev_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        top_sev = max((a.severity for a in group), key=lambda s: sev_order[s])
        reasons = Counter()
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                _, rs = pair_score(db, group[i], group[j])
                reasons.update(rs)
        incident = Incident(
            title=f"Correlated alerts across {', '.join(services[:3])}" + ("…" if len(services) > 3 else ""),
            description=f"Auto-correlated incident from {len(group)} alerts sharing time/service/dependency signals.",
            severity=top_sev,
            status="open",
            affected_services=services,
            correlation_evidence={
                "method": "time+dependency+signature clustering (candidate relationships, not proven causation)",
                "correlation_reasons": [r for r, _ in reasons.most_common(6)],
                "causal_hypotheses": _infer_causal_chain(group, db),
                "alert_ids": [a.id for a in group],
            },
        )
        db.add(incident)
        db.flush()
        primary = min(group, key=lambda a: a.timestamp)
        for a in group:
            db.add(AlertIncidentLink(
                alert_id=a.id, incident_id=incident.id,
                relation="primary" if a.id == primary.id else "correlated",
            ))
        created.append({"incident_id": incident.id, "alert_ids": [a.id for a in group], "services": services})
    db.commit()
    return created


def correlate_alerts_for_csv(db: Session, alert_rows: list) -> list[dict]:
    """Same clustering exposed for CSV imports (already-saved Alert ORM objects)."""
    if len(alert_rows) < 2:
        return []
    created = []
    for group in _group_alerts(db, alert_rows):
        services = list(dict.fromkeys(a.service_name for a in group))
        sev_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        top_sev = max((a.severity for a in group), key=lambda s: sev_order[s])
        incident = Incident(
            title=f"Correlated CSV alerts across {', '.join(services[:3])}",
            description=f"Correlated incident from {len(group)} uploaded alerts.",
            severity=top_sev, status="open", affected_services=services,
            correlation_evidence={
                "method": "time+dependency+signature clustering (candidate relationships, not proven causation)",
                "causal_hypotheses": _infer_causal_chain(group, db),
                "alert_ids": [a.id for a in group],
            },
        )
        db.add(incident)
        db.flush()
        for a in group:
            db.add(AlertIncidentLink(alert_id=a.id, incident_id=incident.id, relation="correlated"))
        created.append({"incident_id": incident.id, "alert_ids": [a.id for a in group]})
    db.commit()
    return created


from app.models import AlertIncidentLink  # noqa: E402  (kept at bottom to avoid circular import noise)