"""Safe, event-driven alert status automation with an audit trail."""
from app.models import AlertAutomationPolicy, utcnow


DEFAULT_POLICY = {
    "auto_ack_low_info": True,
    "auto_resolve_recovery_events": True,
}

_RECOVERY_FIELDS = {"alert_state", "monitor_state", "health_state", "event_state"}
_RECOVERY_VALUES = {"resolved", "recovered", "cleared", "ok", "healthy", "normal"}


def get_policy(db) -> dict:
    row = db.get(AlertAutomationPolicy, 1)
    if row is None:
        return dict(DEFAULT_POLICY)
    return {
        "auto_ack_low_info": row.auto_ack_low_info,
        "auto_resolve_recovery_events": row.auto_resolve_recovery_events,
    }


def is_explicit_recovery(row: dict) -> bool:
    if str(row.get("status", "")).strip().lower() == "resolved":
        return True
    labels = row.get("labels") or {}
    for key in _RECOVERY_FIELDS:
        value = labels.get(key)
        if isinstance(value, str) and value.strip().lower() in _RECOVERY_VALUES:
            return True
    return False


def audit_status(alert, action: str, reason: str, source: str) -> None:
    labels = dict(alert.labels or {})
    labels["alertiq_automation"] = {
        "action": action,
        "reason": reason,
        "source": source,
        "applied_at": utcnow().isoformat() + "Z",
    }
    alert.labels = labels


def audit_row(row: dict, action: str, reason: str) -> None:
    labels = dict(row.get("labels") or {})
    labels["alertiq_automation"] = {
        "action": action,
        "reason": reason,
        "source": row.get("source", "api"),
        "applied_at": utcnow().isoformat() + "Z",
    }
    row["labels"] = labels
