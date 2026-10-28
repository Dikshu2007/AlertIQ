"""Inbound webhook receiver for generic integrations.

Auth model: the per-integration webhook token is the secret; no JWT is required
(external systems cannot obtain one). Accepts {"alerts": [...]}, a bare list,
or a single alert object.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Integration
from app.security import rate_limit
from app.services.correlation import correlate_open_alerts
from app.services.ingest import ingest_alerts, schedule_post_ingest
from app.services.normalizer import normalize_many

log = logging.getLogger("alertiq.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/{token}")
async def receive(token: str, request: Request, background: BackgroundTasks,
                  db: Session = Depends(get_db)):
    rate_limit(request.client.host if request.client else "unknown", limit=120)
    integration = db.scalar(select(Integration).where(Integration.webhook_token == token))
    if not integration or not integration.enabled:
        raise HTTPException(status_code=401, detail="Invalid or disabled webhook token")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Body must be valid JSON: {exc}")

    if isinstance(payload, dict) and isinstance(payload.get("alerts"), list):
        raw_rows = payload["alerts"]
    elif isinstance(payload, list):
        raw_rows = payload
    elif isinstance(payload, dict):
        raw_rows = [payload]
    else:
        raise HTTPException(status_code=422, detail="Payload must be an alert object, a list, or {'alerts': [...]}")

    valid, errors = normalize_many(raw_rows)
    result = ingest_alerts(db, valid, source="webhook")
    schedule_post_ingest(background, db, result["alert_ids"])
    return {"imported": result["imported"], "duplicates": result["duplicates"],
            "alert_ids": result["alert_ids"], "errors": errors[:20]}