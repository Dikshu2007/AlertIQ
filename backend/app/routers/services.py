from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Alert, AnomalyResult, Incident, Metric, Service, utcnow
from app.schemas import ServiceCreate, ServiceHealthOut, ServiceOut
from app.security import get_current_user

router = APIRouter(prefix="/api/services", tags=["services"])


def _health_score(open_alerts: int, critical_alerts: int, incident_open: bool) -> int:
    score = 100
    score -= min(40, open_alerts * 4)
    score -= min(45, critical_alerts * 15)
    if incident_open:
        score -= 20
    return max(0, score)


@router.get("", response_model=list[ServiceOut])
def list_services(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    rows = db.scalars(select(Service).order_by(Service.name)).all()
    return [ServiceOut.model_validate(s) for s in rows]


@router.post("", response_model=ServiceOut, status_code=201)
def create_service(body: ServiceCreate, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    if db.scalar(select(Service).where(Service.name == body.name)):
        raise HTTPException(status_code=409, detail="Service name already exists")
    svc = Service(name=body.name, description=body.description,
                  dependencies=body.dependencies, criticality=body.criticality)
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return svc


@router.post("/{service_id}/deploy")
def record_deploy(service_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    svc.last_deploy_at = utcnow()
    db.commit()
    return {"ok": True, "last_deploy_at": svc.last_deploy_at.isoformat()}


@router.get("/health", response_model=list[ServiceHealthOut])
def services_health(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    services = db.scalars(select(Service).order_by(Service.name)).all()
    active_incidents = db.scalars(select(Incident).where(Incident.status != "resolved")).all()
    out = []
    for svc in services:
        open_alerts = db.scalar(select(func.count(Alert.id)).where(Alert.service_name == svc.name,
                                                                   Alert.status.in_(["open", "acknowledged"]))) or 0
        critical = db.scalar(select(func.count(Alert.id)).where(Alert.service_name == svc.name,
                                                                Alert.severity == "critical",
                                                                Alert.status.in_(["open", "acknowledged"]))) or 0
        last_alert = db.scalar(select(Alert.timestamp).where(Alert.service_name == svc.name)
                               .order_by(Alert.timestamp.desc()).limit(1))
        incident_open = any(svc.name in (i.affected_services or []) for i in active_incidents)
        metric_names = db.execute(select(Metric.name).where(Metric.service_name == svc.name)
                                  .distinct()).scalars().all()
        metrics_summary = {}
        for name in metric_names[:8]:
            vals = db.scalars(select(Metric.value).where(Metric.service_name == svc.name, Metric.name == name)
                              .order_by(Metric.timestamp.desc()).limit(20)).all()
            if vals:
                metrics_summary[name] = {"last": vals[0], "avg": round(sum(vals) / len(vals), 3),
                                         "min": min(vals), "max": max(vals), "n": len(vals)}
        recent = db.scalars(select(Alert).where(Alert.service_name == svc.name)
                            .order_by(Alert.timestamp.desc()).limit(5)).all()
        out.append(ServiceHealthOut(
            service=ServiceOut.model_validate(svc), open_alerts=open_alerts, critical_alerts=critical,
            last_alert_at=last_alert, health=_health_score(open_alerts, critical, incident_open),
            metrics_summary=metrics_summary,
            recent_alerts=[{
                "id": a.id, "source": a.source, "external_id": a.external_id, "service_name": a.service_name,
                "title": a.title, "description": a.description, "severity": a.severity, "status": a.status,
                "fingerprint": a.fingerprint, "labels": a.labels, "metric_values": a.metric_values,
                "timestamp": a.timestamp} for a in recent],
        ))
    return out


@router.get("/{service_name}/metrics")
def service_metrics(service_name: str, hours: int = 168, db: Session = Depends(get_db),
                    user: int = Depends(get_current_user)):
    from datetime import timedelta
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(select(Metric).where(Metric.service_name == service_name, Metric.timestamp >= since)
                      .order_by(Metric.timestamp)).all()
    series: dict[str, list] = {}
    for m in rows:
        series.setdefault(m.name, []).append({"value": m.value, "timestamp": m.timestamp.isoformat()})
    return {"service": service_name, "series": series}


@router.post("/{service_name}/metrics")
def push_metric(service_name: str, name: str, value: float, timestamp: str | None = None,
                db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    from app.services.normalizer import parse_timestamp
    db.add(Metric(service_name=service_name, name=name, value=value,
                  timestamp=parse_timestamp(timestamp) if timestamp else utcnow()))
    db.commit()
    return {"ok": True}


@router.get("/{service_name}/anomalies")
def service_anomalies(service_name: str, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    rows = db.scalars(select(AnomalyResult).where(AnomalyResult.service_name == service_name)
                      .order_by(AnomalyResult.created_at.desc()).limit(50)).all()
    from app.schemas import AnomalyOut
    return [AnomalyOut.model_validate(a) for a in rows]
