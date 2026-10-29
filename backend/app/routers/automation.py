from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AlertAutomationPolicy
from app.schemas import AlertAutomationSettings
from app.security import get_current_user
from app.services.automation import DEFAULT_POLICY, get_policy

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/settings", response_model=AlertAutomationSettings)
def read_settings(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    return get_policy(db)


@router.put("/settings", response_model=AlertAutomationSettings)
def update_settings(body: AlertAutomationSettings, db: Session = Depends(get_db),
                    user: int = Depends(get_current_user)):
    row = db.get(AlertAutomationPolicy, 1)
    if row is None:
        row = AlertAutomationPolicy(id=1, **DEFAULT_POLICY)
        db.add(row)
    row.auto_ack_low_info = body.auto_ack_low_info
    row.auto_resolve_recovery_events = body.auto_resolve_recovery_events
    db.commit()
    db.refresh(row)
    return row
