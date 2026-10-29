"""Ingest pipeline: normalize -> dedupe -> persist -> trigger correlation +
anomaly scan in a background thread (never blocks the API response)."""
import logging
from datetime import timedelta

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, Incident, utcnow
from app.services.automation import audit_row, audit_status, get_policy, is_explicit_recovery
from app.services.normalizer import fingerprint

log = logging.getLogger("alertiq.ingest")


def ingest_alerts(db: Session, rows: list[dict], source: str = "api") -> dict:
    imported, duplicates = 0, 0
    auto_acknowledged, auto_resolved = 0, 0
    auto_resolved_ids: list[int] = []
    policy = get_policy(db)
    saved: list[Alert] = []
    for row in rows:
        acknowledged_by_rule = False
        row["source"] = row.get("source") or source
        fp = fingerprint(row["service_name"], row["title"])
        ts = row.get("timestamp")

        # A monitor's explicit recovery event can resolve the matching active
        # alert even when it arrives outside the duplicate-suppression window.
        if policy["auto_resolve_recovery_events"] and is_explicit_recovery(row):
            active = db.scalar(
                select(Alert).where(
                    Alert.fingerprint == fp,
                    Alert.status.in_(["open", "acknowledged"]),
                ).order_by(Alert.timestamp.desc()).limit(1)
            )
            if active and ts and ts < active.timestamp:
                active = None  # ignore a stale recovery event for a newer firing
            if active:
                active.status = "resolved"
                audit_status(
                    active, "auto_resolved",
                    "An explicit recovery state arrived for the same service and alert fingerprint.",
                    row["source"],
                )
                auto_resolved += 1
                auto_resolved_ids.append(active.id)
                continue
            if is_explicit_recovery(row):
                row["status"] = "resolved"
                audit_row(row, "recovery_event_resolved", "The incoming event explicitly reports recovery.")

        # Routine alerts are acknowledged automatically, never resolved merely
        # because their severity is low. This keeps acknowledgement distinct
        # from proof that the underlying issue has recovered.
        if (policy["auto_ack_low_info"] and row.get("status") == "open"
                and row.get("severity") in {"low", "info"}):
            row["status"] = "acknowledged"
            audit_row(row, "auto_acknowledged", "Automatic acknowledgement is enabled for low/info severity.")
            auto_acknowledged += 1
            acknowledged_by_rule = True

        exists = db.scalar(
            select(Alert).where(
                Alert.fingerprint == fp,
                Alert.timestamp >= ts - timedelta(seconds=30) if ts else Alert.timestamp >= utcnow() - timedelta(minutes=5),
            ).order_by(Alert.timestamp.desc()).limit(1)
        )
        if exists:
            if (policy["auto_ack_low_info"] and row.get("status") == "acknowledged"
                    and row.get("severity") in {"low", "info"} and exists.status == "open"):
                exists.status = "acknowledged"
                audit_status(
                    exists, "auto_acknowledged",
                    "A repeated low/info alert matched an open alert while automatic acknowledgement is enabled.",
                    row["source"],
                )
                if not acknowledged_by_rule:
                    auto_acknowledged += 1
            duplicates += 1
            continue
        alert = Alert(fingerprint=fp, **row)
        db.add(alert)
        saved.append(alert)
        imported += 1
    db.commit()
    for a in saved:
        db.refresh(a)
    return {
        "imported": imported,
        "duplicates": duplicates,
        "alert_ids": [a.id for a in saved],
        "auto_acknowledged": auto_acknowledged,
        "auto_resolved": auto_resolved,
        "auto_resolved_ids": auto_resolved_ids,
    }


def schedule_post_ingest(background: BackgroundTasks, db: Session, alert_ids: list[int], incidents: list[dict] | None = None):
    """Queue expensive analysis (correlation + anomaly scan) off the request path."""
    correlation_result = None
    if alert_ids:
        from app.services.correlation import correlate_open_alerts
        correlation_result = correlate_open_alerts(db)
    if correlation_result or (incidents is None and alert_ids):
        background.add_task(_background_anomaly_scan)
    return {"correlated": correlation_result}


def _background_anomaly_scan():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        result = run_scan(db)
        log.info("Background anomaly scan: %s", result)
    finally:
        db.close()


def run_scan(db: Session) -> dict:
    from app.services.anomaly import run_full_scan
    return run_full_scan(db)


def link_alert_to_incident(db: Session, alert_id: int, incident_id: int) -> None:
    from app.models import AlertIncidentLink
    exists = db.scalar(select(AlertIncidentLink).where(
        AlertIncidentLink.alert_id == alert_id, AlertIncidentLink.incident_id == incident_id))
    if not exists:
        db.add(AlertIncidentLink(alert_id=alert_id, incident_id=incident_id, relation="correlated"))
        db.commit()
