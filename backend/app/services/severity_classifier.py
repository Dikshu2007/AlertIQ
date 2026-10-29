"""Explainable local severity classifier for newly ingested alerts.

This deliberately uses transparent keyword and metric rules. It does not claim
to be a trained ML model, and records the signals that led to each decision.
"""
import re

_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def classify_severity(title: str, description: str = "", metrics: dict | None = None) -> dict:
    text = f"{title} {description}".lower()
    candidates: list[tuple[str, str, str]] = []

    keyword_rules = [
        ("critical", r"\b(total outage|service unavailable|complete failure|data loss|security breach|sev[ -]?1|fatal|critical)\b",
         "Text indicates a major outage, data/security impact, or critical priority."),
        ("high", r"\b(error rate|errors? spike|failed|failure|degraded|backlog|sev[ -]?2|high(?: priority)?)\b",
         "Text indicates failures, elevated errors, degradation, or a growing backlog."),
        ("medium", r"\b(timeout|latency|slow|warning|elevated|increasing|pressure|near capacity)\b",
         "Text indicates a warning, latency, or rising resource pressure."),
        ("low", r"\b(minor|slight|intermittent|low priority|routine)\b",
         "Text describes a minor, intermittent, or routine condition."),
        ("info", r"\b(recovered|resolved|back to normal|healthy|cleared|informational)\b",
         "Text indicates recovery or an informational state."),
    ]
    for severity, pattern, reason in keyword_rules:
        if re.search(pattern, text):
            candidates.append((severity, "keyword", reason))

    values = {str(key).lower(): value for key, value in (metrics or {}).items()}

    def number(*keys: str) -> float | None:
        for key, value in values.items():
            if any(alias in key for alias in keys):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    def metric(severity: str, evidence: str) -> None:
        candidates.append((severity, "metric", evidence))

    error_rate = number("error_rate_pct", "error_rate_percent", "5xx_rate_pct")
    if error_rate is not None:
        if error_rate >= 5:
            metric("critical", f"Error rate is {error_rate:g}%, at least 5%.")
        elif error_rate >= 1:
            metric("high", f"Error rate is {error_rate:g}%, at least 1%.")
        elif error_rate >= 0.1:
            metric("medium", f"Error rate is {error_rate:g}%, above 0.1%.")

    latency = number("latency_p95_ms", "p95_latency_ms", "page_load_p95_ms")
    if latency is not None:
        if latency >= 5000:
            metric("critical", f"95th percentile latency is {latency:g} ms, at least 5000 ms.")
        elif latency >= 1500:
            metric("high", f"95th percentile latency is {latency:g} ms, at least 1500 ms.")
        elif latency >= 750:
            metric("medium", f"95th percentile latency is {latency:g} ms, above 750 ms.")

    for key, label in (("cpu", "CPU"), ("memory", "Memory")):
        percent = number(f"{key}_usage_pct", f"{key}_percent", f"{key}_pct")
        if percent is not None:
            if percent >= 95:
                metric("critical", f"{label} usage is {percent:g}%, at least 95%.")
            elif percent >= 85:
                metric("high", f"{label} usage is {percent:g}%, at least 85%.")
            elif percent >= 70:
                metric("medium", f"{label} usage is {percent:g}%, at least 70%.")

    used, maximum = number("connections_used"), number("connections_max")
    if used is not None and maximum and maximum > 0:
        ratio = used / maximum
        if ratio >= 0.95:
            metric("critical", f"Connection pool is {ratio:.0%} full.")
        elif ratio >= 0.8:
            metric("high", f"Connection pool is {ratio:.0%} full.")
        elif ratio >= 0.65:
            metric("medium", f"Connection pool is {ratio:.0%} full.")

    queue_depth = number("queue_depth", "retry_queue_depth", "backlog")
    if queue_depth is not None:
        if queue_depth >= 5000:
            metric("critical", f"Queue/backlog depth is {queue_depth:g}, at least 5000.")
        elif queue_depth >= 1000:
            metric("high", f"Queue/backlog depth is {queue_depth:g}, at least 1000.")
        elif queue_depth >= 250:
            metric("medium", f"Queue/backlog depth is {queue_depth:g}, at least 250.")

    if candidates:
        severity = max((item[0] for item in candidates), key=lambda value: _ORDER[value])
        strongest = [item for item in candidates if item[0] == severity]
        evidence = list(dict.fromkeys(item[2] for item in strongest))
        methods = {item[1] for item in strongest}
        confidence = 0.92 if "metric" in methods else 0.82
        reason = " ".join(evidence)
    else:
        severity = "medium"
        confidence = 0.35
        reason = "No decisive impact signal matched; assigned medium severity as a cautious default."
        evidence = ["No recognized severity keyword or configured metric threshold matched."]

    return {
        "severity": severity,
        "reason": reason,
        "evidence": evidence,
        "match_strength": confidence,
        # Keep this alias while older deployed frontend bundles still read it.
        "confidence": confidence,
        "method": "AlertIQ local rules",
    }


def backfill_existing_alerts(db) -> int:
    """Assign and record severity for alerts created before this classifier existed."""
    from sqlalchemy import select

    from app.models import Alert

    updated = 0
    for alert in db.scalars(select(Alert)).all():
        labels = alert.labels or {}
        if labels.get("alertiq_severity_assessment"):
            continue
        assessment = classify_severity(alert.title, alert.description or "", alert.metric_values or {})
        alert.severity = assessment["severity"]
        alert.labels = {**labels, "alertiq_severity_assessment": assessment}
        updated += 1
    if updated:
        db.commit()
    return updated
