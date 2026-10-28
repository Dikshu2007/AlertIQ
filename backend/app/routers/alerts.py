from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AIAnalysis, Alert, Incident, utcnow
from app.schemas import (AlertIn, AlertListIn, AlertListOut, AlertOut, AlertStatusUpdate,
                         AnalysisOut, CSVImportResult)
from app.security import get_current_user, rate_limit
from app.services.correlation import correlate_alerts_for_csv, correlate_open_alerts
from app.services.ingest import ingest_alerts, schedule_post_ingest
from app.services.normalizer import normalize_many
from app.services import analysis as analysis_svc

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("", response_model=AlertOut, status_code=201)
def create_alert(alert: AlertIn, background: BackgroundTasks, db: Session = Depends(get_db),
                 user: int = Depends(get_current_user)):
    valid, errors = normalize_many([alert.model_dump()])
    if errors:
        raise HTTPException(status_code=422, detail=errors[0])
    result = ingest_alerts(db, valid)
    if not result["alert_ids"]:
        raise HTTPException(status_code=409, detail="Duplicate alert (same fingerprint within the dedupe window)")
    schedule_post_ingest(background, db, result["alert_ids"])
    return db.get(Alert, result["alert_ids"][0])


@router.post("/batch", status_code=201)
def create_alerts_batch(body: AlertListIn, background: BackgroundTasks, db: Session = Depends(get_db),
                        user: int = Depends(get_current_user)):
    """Batch ingest that tolerates invalid rows: bad rows are reported, good rows import."""
    valid, errors = normalize_many(body.alerts)
    result = ingest_alerts(db, valid)
    schedule_post_ingest(background, db, result["alert_ids"])
    return {"imported": result["imported"], "duplicates": result["duplicates"],
            "alert_ids": result["alert_ids"], "errors": errors[:20]}


@router.get("", response_model=AlertListOut)
def list_alerts(
    db: Session = Depends(get_db), user: int = Depends(get_current_user),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    severity: str | None = None, service: str | None = None, status: str | None = None,
    search: str | None = None, hours: int | None = Query(None, ge=1, le=24 * 30),
):
    q = select(Alert)
    if severity:
        q = q.where(Alert.severity == severity)
    if service:
        q = q.where(Alert.service_name == service)
    if status:
        q = q.where(Alert.status == status)
    if search:
        q = q.where((Alert.title.ilike(f"%{search}%")) | (Alert.description.ilike(f"%{search}%")))
    if hours:
        q = q.where(Alert.timestamp >= utcnow() - timedelta(hours=hours))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Alert.timestamp.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return AlertListOut(items=[AlertOut.model_validate(a) for a in rows], total=total, page=page, page_size=page_size)


@router.get("/export.csv", response_class=PlainTextResponse)
def export_csv(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    import csv as _csv
    import io
    import json as _json
    buf = io.StringIO()
    writer = _csv.writer(buf, quoting=_csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(["service_name", "title", "description", "severity", "status", "timestamp", "labels"])
    for a in db.scalars(select(Alert).order_by(Alert.timestamp.desc())).all():
        writer.writerow([a.service_name, a.title, (a.description or "")[:500], a.severity, a.status,
                         a.timestamp.isoformat(), _json.dumps(a.labels or {})])
    return buf.getvalue()


@router.post("/import-csv", response_model=CSVImportResult)
def import_csv(background: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db),
               user: int = Depends(get_current_user)):
    import csv as csv_mod
    import io

    if not file.filename or not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(status_code=422, detail="Only .csv files are accepted")
    content = file.file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"File is not valid UTF-8 text: {exc}")
    reader = csv_mod.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV has no header row")

    fieldmap = {"service": "service_name", "service_name": "service_name", "host": "service_name",
                "title": "title", "summary": "title", "message": "title",
                "description": "description", "severity": "severity", "status": "status",
                "timestamp": "timestamp", "time": "timestamp", "external_id": "external_id"}
    aliases = {f: fieldmap.get(f.lower().strip()) for f in (reader.fieldnames or [])}
    if not any(aliases.values()):
        raise HTTPException(status_code=422,
                            detail=f"CSV must contain a service column ({list(fieldmap)}) and a title column")

    parsed = []
    for row in reader:
        item = {}
        for original, mapped in aliases.items():
            if mapped and row.get(original) not in (None, ""):
                item[mapped] = str(row[original]).strip()
        if item:
            item["source"] = "csv"
            parsed.append(item)

    valid, errors = normalize_many(parsed)
    result = ingest_alerts(db, valid, source="csv")

    # correlation on the freshly imported rows
    saved = [db.get(Alert, aid) for aid in result["alert_ids"]]
    saved = [a for a in saved if a]
    correlated = correlate_alerts_for_csv(db, saved) if len(saved) >= 2 else []
    schedule_post_ingest(background, db, result["alert_ids"], incidents=correlated)
    return CSVImportResult(imported=result["imported"], skipped=result["duplicates"] + len(errors),
                           errors=errors[:20])


@router.get("/{alert_id}", response_model=AlertOut)
def get_alert(alert_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.patch("/{alert_id}/status", response_model=AlertOut)
def update_alert_status(alert_id: int, body: AlertStatusUpdate, db: Session = Depends(get_db),
                        user: int = Depends(get_current_user)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = body.status
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/{alert_id}/analyze", response_model=AnalysisOut)
def analyze_alert(alert_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    context = analysis_svc.collect_alert_context(db, alert)
    return analysis_svc.analyze_json(db, context, alert_id=alert.id)


@router.get("/{alert_id}/analysis", response_model=AnalysisOut | None)
def latest_analysis(alert_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    row = db.scalars(select(AIAnalysis).where(AIAnalysis.alert_id == alert_id)
                     .order_by(AIAnalysis.created_at.desc()).limit(1)).first()
    return row


@router.get("/{alert_id}/history")
def alert_history(alert_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    """All past occurrences of the same fingerprint + incidents it took part in."""
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    from app.models import AlertIncidentLink
    occurrences = db.scalars(select(Alert).where(Alert.fingerprint == alert.fingerprint)
                             .order_by(Alert.timestamp.desc()).limit(100)).all()
    incident_ids = [lid for (lid,) in db.execute(
        select(AlertIncidentLink.incident_id).where(AlertIncidentLink.alert_id == alert_id)).all()]
    return {"fingerprint": alert.fingerprint,
            "total_occurrences": len(occurrences),
            "first_seen": min(o.timestamp for o in occurrences).isoformat() if occurrences else None,
            "last_seen": max(o.timestamp for o in occurrences).isoformat() if occurrences else None,
            "occurrences": [{"id": o.id, "severity": o.severity, "status": o.status,
                             "timestamp": o.timestamp.isoformat()} for o in occurrences],
            "incident_ids": incident_ids}


@router.post("/correlate")
def run_correlation(background: BackgroundTasks, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    created = correlate_open_alerts(db)
    return {"created_incidents": created}
