import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AnomalyResult, ChatMessage, User
from app.schemas import (AnomalyListOut, AnomalyOut, AnomalyStatusUpdate, AssistantAnswer,
                         AssistantQuestion, MetaOut, ResearchRequest)
from app.security import get_current_user, rate_limit
from app.services import anomaly as anomaly_svc
from app.services import assistant as assistant_svc
from app.services import research as research_svc

log = logging.getLogger("alertiq.api")
router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/meta", response_model=MetaOut)
def meta(user: int = Depends(get_current_user)):
    from app.services import llm
    return MetaOut(app=settings.app_name, version="1.0.0", demo_mode=settings.demo_mode,
                   llm_engine=llm.engine_name(), web_research_available=research_svc.available())


@router.get("/anomalies", response_model=AnomalyListOut)
def list_anomalies(db: Session = Depends(get_db), user: int = Depends(get_current_user),
                   status: str | None = None, page: int = 1, page_size: int = 50):
    q = select(AnomalyResult)
    if status:
        q = q.where(AnomalyResult.status == status)
    rows = db.scalars(q.order_by(AnomalyResult.created_at.desc())).all()
    start = (page - 1) * page_size
    return AnomalyListOut(items=[AnomalyOut.model_validate(a) for a in rows[start:start + page_size]],
                          total=len(rows))


@router.post("/anomalies/scan")
def run_anomaly_scan(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    result = anomaly_svc.run_full_scan(db)
    return result


@router.patch("/anomalies/{anomaly_id}/status", response_model=AnomalyOut)
def update_anomaly_status(anomaly_id: int, body: AnomalyStatusUpdate, db: Session = Depends(get_db),
                          user: int = Depends(get_current_user)):
    row = db.get(AnomalyResult, anomaly_id)
    if not row:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    row.status = body.status
    db.commit()
    db.refresh(row)
    return row


@router.post("/anomalies/{anomaly_id}/explain")
def explain_anomaly(anomaly_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    """Unfamiliar-alert workflow: history comparison + optional web research."""
    row = db.get(AnomalyResult, anomaly_id)
    if not row:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    from app.models import Alert
    alert = db.get(Alert, row.alert_id) if row.alert_id else None
    if not alert:
        alert = Alert(service_name=row.service_name, title=row.explanation[:500],
                      severity="medium", fingerprint="synthetic", timestamp=row.created_at)
    triage = anomaly_svc.unknown_alert_triage(db, alert)
    research = None
    if research_svc.available():
        try:
            research = research_svc.research_and_summarize(
                research_svc.build_research_query(alert.title, alert.service_name), {"alert": alert.title})
        except Exception as exc:  # noqa: BLE001
            log.warning("anomaly research failed: %s", exc)
    return {"triage": triage, "research": research}


@router.post("/ask", response_model=AssistantAnswer)
def ask(body: AssistantQuestion, request: Request, db: Session = Depends(get_db),
        user: int = Depends(get_current_user)):
    rate_limit(request.client.host if request.client else "unknown")
    result = assistant_svc.answer(db, body.question)
    db.add(ChatMessage(role="user", content=body.question, user_id=user))
    db.add(ChatMessage(role="assistant", content=result["answer"], user_id=user,
                       meta={"engine": result["engine"]}))
    db.commit()
    return result


@router.get("/chat/history")
def chat_history(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    rows = db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(50)).all()
    return [{"role": r.role, "content": r.content, "created_at": r.created_at.isoformat()}
            for r in reversed(rows)]


@router.post("/research")
def research(body: ResearchRequest, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    if not research_svc.available():
        raise HTTPException(status_code=503,
                            detail="Web research not configured. Set SEARCH_PROVIDER (tavily|serper) and "
                                   "SEARCH_API_KEY in the backend .env — see .env.example.")
    from app.models import Alert
    context = {}
    if body.alert_id:
        alert = db.get(Alert, body.alert_id)
        if alert:
            context = {"alert": {"title": alert.title, "service": alert.service_name,
                                 "description": alert.description, "metric_values": alert.metric_values}}
    try:
        return research_svc.research_and_summarize(body.query, context)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Research failed: {exc}")
