"""Ingest pipeline: normalize -> dedupe -> persist -> trigger correlation +
anomaly scan in a background thread (never blocks the API response)."""
import logging
from datetime import timedelta

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, Incident, utcnow
from app.services.normalizer import fingerprint

log = logging.getLogger("alertiq.ingest")


def ingest_alerts(db: Session, rows: list[dict], source: str = "api") -> dict:
    imported, duplicates = 0, 0
    saved: list[Alert] = []
    for row in rows:
        row["source"] = row.get("source") or source
        fp = fingerprint(row["service_name"], row["title"])
        ts = row.get("timestamp")
        exists = db.scalar(
            select(Alert).where(
                Alert.fingerprint == fp,
                Alert.timestamp >= ts - timedelta(seconds=30) if ts else Alert.timestamp >= utcnow() - timedelta(minutes=5),
            ).order_by(Alert.timestamp.desc()).limit(1)
        )
        if exists:
            duplicates += 1
            continue
        alert = Alert(fingerprint=fp, **row)
        db.add(alert)
        saved.append(alert)
        imported += 1
    db.commit()
    for a in saved:
        db.refresh(a)
    return {"imported": imported, "duplicates": duplicates, "alert_ids": [a.id for a in saved]}


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