from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Alert, Incident, utcnow
from app.schemas import (IncidentCreate, IncidentListOut, IncidentOut, IncidentStatusUpdate)
from app.security import get_current_user
from app.services import analysis as analysis_svc
from app.services import recommendations as rec_svc
from app.services.correlation import correlate_alerts_for_csv
from app.services.ingest import link_alert_to_incident

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.get("", response_model=IncidentListOut)
def list_incidents(db: Session = Depends(get_db), user: int = Depends(get_current_user),
                   status: str | None = None, severity: str | None = None, service: str | None = None):
    q = select(Incident)
    if status:
        q = q.where(Incident.status == status)
    if severity:
        q = q.where(Incident.severity == severity)
    rows = db.scalars(q.order_by(Incident.created_at.desc())).all()
    if service:
        rows = [i for i in rows if service in (i.affected_services or [])]
    return IncidentListOut(items=[IncidentOut.model_validate(i) for i in rows], total=len(rows))


@router.post("", response_model=IncidentOut, status_code=201)
def create_incident(body: IncidentCreate, background: BackgroundTasks, db: Session = Depends(get_db),
                    user: int = Depends(get_current_user)):
    incident = Incident(title=body.title, description=body.description, severity=body.severity,
                        affected_services=body.affected_services,
                        correlation_evidence={"method": "manually created", "alert_ids": body.alert_ids})
    db.add(incident)
    db.flush()
    for aid in body.alert_ids:
        link_alert_to_incident(db, aid, incident.id)
    db.commit()
    db.refresh(incident)
    # generate recommendations immediately
    rec_svc.generate_recommendations(db, incident)
    return incident


@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.get("/{incident_id}/timeline")
def incident_timeline(incident_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    from app.models import AlertIncidentLink
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    links = db.scalars(select(AlertIncidentLink).where(AlertIncidentLink.incident_id == incident_id)).all()
    events = []
    for link in links:
        a = db.get(Alert, link.alert_id)
        if a:
            events.append({"type": "alert", "alert_id": a.id, "relation": link.relation,
                           "service": a.service_name, "title": a.title, "severity": a.severity,
                           "timestamp": a.timestamp.isoformat(), "metric_values": a.metric_values})
    events.append({"type": "incident_created", "timestamp": incident.created_at.isoformat()})
    if incident.resolved_at:
        events.append({"type": "incident_resolved", "timestamp": incident.resolved_at.isoformat()})
    events.sort(key=lambda e: e["timestamp"])
    return {"incident_id": incident_id, "events": events}


@router.get("/{incident_id}/alerts")
def incident_alerts(incident_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    from app.models import AlertIncidentLink
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    links = db.scalars(select(AlertIncidentLink).where(AlertIncidentLink.incident_id == incident_id)).all()
    out = []
    for link in links:
        a = db.get(Alert, link.alert_id)
        if a:
            from app.schemas import AlertOut
            out.append({**AlertOut.model_validate(a).model_dump(), "relation": link.relation})
    return out


@router.patch("/{incident_id}/status", response_model=IncidentOut)
def update_status(incident_id: int, body: IncidentStatusUpdate, db: Session = Depends(get_db),
                  user: int = Depends(get_current_user)):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.status = body.status
    if body.status == "resolved":
        incident.resolved_at = utcnow()
        # auto-resolve linked alerts too
        from app.models import AlertIncidentLink
        links = db.scalars(select(AlertIncidentLink).where(AlertIncidentLink.incident_id == incident_id)).all()
        for link in links:
            a = db.get(Alert, link.alert_id)
            if a and a.status != "resolved":
                a.status = "resolved"
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/{incident_id}/link-alerts", response_model=IncidentOut)
def link_alerts(incident_id: int, alert_ids: list[int], db: Session = Depends(get_db),
                user: int = Depends(get_current_user)):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    for aid in alert_ids:
        link_alert_to_incident(db, aid, incident_id)
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/{incident_id}/analyze")
def analyze_incident(incident_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    context = analysis_svc.collect_incident_context(db, incident)
    result = analysis_svc.analyze(db, context)
    return analysis_svc.persist_analysis(db, context, result, alert_id=None, incident_id=incident.id)


@router.get("/{incident_id}/analysis")
def incident_analysis(incident_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    from app.models import AIAnalysis
    row = db.scalars(select(AIAnalysis).where(AIAnalysis.incident_id == incident_id)
                     .order_by(AIAnalysis.created_at.desc()).limit(1)).first()
    if not row:
        raise HTTPException(status_code=404, detail="No analysis yet — POST /analyze first")
    return row


@router.get("/{incident_id}/recommendations")
def incident_recommendations(incident_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    existing = rec_svc.format_ranked(db, incident)
    return existing if existing else rec_svc.format_ranked(db, incident) or _generate(db, incident)


def _generate(db: Session, incident: Incident):
    rec_svc.generate_recommendations(db, incident)
    return rec_svc.format_ranked(db, incident)
