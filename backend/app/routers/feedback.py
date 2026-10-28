"""User feedback and verified historical resolutions (learning loop inputs)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Feedback, Incident, Resolution
from app.schemas import FeedbackIn, ResolutionIn, ResolutionOut
from app.security import get_current_user

router = APIRouter(prefix="/api", tags=["learning"])


@router.post("/resolutions", response_model=ResolutionOut, status_code=201)
def add_resolution(body: ResolutionIn, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    if body.incident_id is not None and not db.get(Incident, body.incident_id):
        raise HTTPException(status_code=404, detail="Incident not found")
    row = Resolution(incident_id=body.incident_id, service_name=body.service_name,
                     problem_signature=body.problem_signature, solution=body.solution,
                     verified=body.verified, verified_by="user")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/resolutions", response_model=list[ResolutionOut])
def list_resolutions(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    rows = db.scalars(select(Resolution).order_by(Resolution.created_at.desc()).limit(200)).all()
    return [ResolutionOut.model_validate(r) for r in rows]


@router.post("/feedback", status_code=201)
def add_feedback(body: FeedbackIn, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    db.add(Feedback(user_id=user, **body.model_dump()))
    db.commit()
    # unhelpful ratings on a verified resolution's pattern do NOT auto-delete it;
    # they are stored for review (no automatic retraining on unverified data).
    return {"ok": True, "note": "Feedback stored. Verified resolutions are only revised by explicit user action."}


@router.get("/feedback")
def list_feedback(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    rows = db.scalars(select(Feedback).order_by(Feedback.created_at.desc()).limit(100)).all()
    return [{"id": f.id, "rating": f.rating, "comment": f.comment, "incident_id": f.incident_id,
             "recommendation_id": f.recommendation_id, "anomaly_id": f.anomaly_id,
             "created_at": f.created_at.isoformat()} for f in rows]