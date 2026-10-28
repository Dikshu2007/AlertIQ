from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Alert, Incident, Service, utcnow

from app.schemas import AlertOut, DashboardOverview, IncidentOut  # noqa: E402
from app.security import get_current_user  # noqa: E402

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverview)
def overview(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    total_alerts = db.scalar(select(func.count(Alert.id))) or 0
    open_states = ["open", "acknowledged"]
    critical_alerts = db.scalar(select(func.count(Alert.id)).where(
        Alert.severity == "critical", Alert.status.in_(open_states))) or 0
    resolved_alerts = db.scalar(select(func.count(Alert.id)).where(Alert.status == "resolved")) or 0
    active_incident_count = db.scalar(select(func.count(Incident.id)).where(Incident.status != "resolved")) or 0
    resolved_incidents = db.scalar(select(func.count(Incident.id)).where(Incident.status == "resolved")) or 0

    # system health: 100 - penalties from open criticals and active incidents
    health = max(0, 100 - min(60, critical_alerts * 8) - min(30, active_incident_count * 6))

    now = utcnow()
    # alert frequency over the last 7 days (daily buckets)
    since = now - timedelta(days=7)
    alerts_week = db.scalars(select(Alert).where(Alert.timestamp >= since)).all()
    by_day: dict[str, dict] = {}
    for a in alerts_week:
        day = a.timestamp.strftime("%Y-%m-%d")
        bucket = by_day.setdefault(day, {"date": day, "total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0})
        bucket["total"] += 1
        bucket[a.severity] = bucket.get(a.severity, 0) + 1
    alert_timeseries = [by_day[d] for d in sorted(by_day)]

    incidents_all = db.scalars(select(Incident)).all()
    inc_by_day: dict[str, dict] = {}
    for i in incidents_all:
        day = i.created_at.strftime("%Y-%m-%d")
        b = inc_by_day.setdefault(day, {"date": day, "created": 0, "resolved": 0})
        b["created"] += 1
        if i.resolved_at:
            rday = i.resolved_at.strftime("%Y-%m-%d")
            rb = inc_by_day.setdefault(rday, {"date": rday, "created": 0, "resolved": 0})
            rb["resolved"] += 1
    incident_trend = [inc_by_day[d] for d in sorted(inc_by_day)]

    sev_rows = db.execute(select(Alert.severity, func.count(Alert.id)).group_by(Alert.severity)).all()
    severity_breakdown = [{"severity": s or "unknown", "count": c} for s, c in sev_rows]

    services = db.scalars(select(Service).order_by(Service.name)).all()
    active_incident_rows = db.scalars(select(Incident).where(Incident.status != "resolved")).all()
    service_health = []
    for s in services:
        open_n = db.scalar(select(func.count(Alert.id)).where(Alert.service_name == s.name,
                                                              Alert.status.in_(open_states))) or 0
        crit_n = db.scalar(select(func.count(Alert.id)).where(Alert.service_name == s.name,
                                                              Alert.severity == "critical",
                                                              Alert.status.in_(open_states))) or 0
        has_active_incident = any(s.name in (i.affected_services or []) for i in active_incident_rows)
        h = max(0, 100 - min(40, open_n * 4) - min(45, crit_n * 15) - (20 if has_active_incident else 0))
        service_health.append({"service": s.name, "criticality": s.criticality, "health": h,
                               "open_alerts": open_n, "critical_alerts": crit_n,
                               "active_incident": has_active_incident})
    # services that appear in alerts but aren't registered get an aggregate entry
    registered = {s.name for s in services}
    unregistered = db.execute(select(Alert.service_name, func.count(Alert.id))
                              .where(Alert.status.in_(open_states))
                              .group_by(Alert.service_name)).all()
    extra = [{"service": n, "criticality": "unregistered", "health": max(0, 100 - min(40, c * 4)),
              "open_alerts": c, "critical_alerts": 0, "active_incident": False}
             for n, c in unregistered if n not in registered][:10]

    recent_alerts = db.scalars(select(Alert).order_by(Alert.timestamp.desc()).limit(10)).all()
    recent_incidents = db.scalars(select(Incident).order_by(Incident.created_at.desc()).limit(10)).all()

    return DashboardOverview(
        total_alerts=total_alerts, active_incidents=active_incident_count, critical_alerts=critical_alerts,
        resolved_incidents=resolved_incidents, resolved_alerts=resolved_alerts, system_health=health,
        alert_timeseries=alert_timeseries, incident_trend=incident_trend,
        severity_breakdown=severity_breakdown, service_health=service_health + extra,
        recent_alerts=[AlertOut.model_validate(a) for a in recent_alerts],
        recent_incidents=[IncidentOut.model_validate(i) for i in recent_incidents],
    )
