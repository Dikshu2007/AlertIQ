"""Alert normalization & validation.

All ingest paths (API, webhook, CSV, integrations) funnel through `normalize_alert`
so every downstream component works with a consistent shape.
"""
from datetime import datetime, timedelta, timezone

VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}
VALID_STATUS = {"open", "acknowledged", "resolved"}

_SEVERITY_ALIASES = {
    "crit": "critical", "critical": "critical", "sev1": "critical", "sev2": "high",
    "error": "high", "fatal": "critical", "high": "high", "warn": "medium",
    "warning": "medium", "medium": "medium", "major": "high", "minor": "low",
    "low": "low", "info": "info", "notice": "info", "ok": "info", "resolved": "info",
}

MAX_STR = 200_000  # hard cap for description/log payloads (safe upload handling)


class ValidationError(Exception):
    pass


def parse_timestamp(value) -> datetime:
    """Returns a NAIVE UTC datetime (matches the DB storage convention)."""
    now_naive = datetime.utcnow()
    if value is None:
        return now_naive
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y/%m/%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise ValidationError(f"Unparseable timestamp: {value!r}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if dt > now_naive + timedelta(days=1):
        raise ValidationError(f"Timestamp too far in the future: {value!r}")
    return dt


def normalize_severity(raw) -> str:
    s = str(raw or "medium").strip().lower()
    return _SEVERITY_ALIASES.get(s, "medium")


def normalize_alert(raw: dict) -> dict:
    """Validate + normalize one incoming alert dict. Raises ValidationError."""
    if not isinstance(raw, dict):
        raise ValidationError("Alert payload must be an object")

    service = str(raw.get("service_name") or raw.get("service") or raw.get("host") or "").strip()
    title = str(raw.get("title") or raw.get("summary") or raw.get("message") or raw.get("name") or "").strip()
    if not service:
        raise ValidationError("Missing required field: service_name")
    if not title:
        raise ValidationError("Missing required field: title")
    if len(title) > 500:
        title = title[:500]
    if len(service) > 120:
        raise ValidationError("service_name too long")

    severity = normalize_severity(raw.get("severity"))
    status = str(raw.get("status") or "open").strip().lower()
    if status not in VALID_STATUS:
        status = "open"

    labels = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
    metric_values = raw.get("metric_values") if isinstance(raw.get("metric_values"), dict) else {}
    metric_values = {
        str(k)[:120]: float(v) for k, v in metric_values.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }

    description = str(raw.get("description") or "")[:MAX_STR]

    return {
        "source": str(raw.get("source") or "api")[:50],
        "external_id": (str(raw.get("external_id"))[:255] if raw.get("external_id") is not None else None),
        "service_name": service,
        "title": title,
        "description": description,
        "severity": severity,
        "status": status,
        "labels": labels,
        "metric_values": metric_values,
        "timestamp": parse_timestamp(raw.get("timestamp")),
    }


def normalize_many(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Returns (valid_rows, error_messages). Bad rows are skipped, not fatal."""
    valid, errors = [], []
    for i, row in enumerate(rows):
        try:
            valid.append(normalize_alert(row))
        except ValidationError as exc:
            errors.append(f"row {i}: {exc}")
    return valid, errors


def fingerprint(service: str, title: str) -> str:
    import hashlib
    key = f"{service.strip().lower()}|{title.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]