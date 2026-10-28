import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Integration, IntegrationEvent
from app.schemas import IntegrationCreate, IntegrationOut, IntegrationUpdate
from app.security import encrypt_secret, get_current_user
from app.services import integrations as int_svc

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _to_out(row: Integration) -> IntegrationOut:
    data = IntegrationOut.model_validate(row).model_dump()
    data["has_credentials"] = bool(row.encrypted_credentials)
    return IntegrationOut(**data)


@router.get("", response_model=list[IntegrationOut])
def list_integrations(db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    rows = db.scalars(select(Integration).order_by(Integration.created_at.desc())).all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=IntegrationOut, status_code=201)
def create_integration(body: IntegrationCreate, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    webhook_token = secrets.token_urlsafe(24) if body.kind == "webhook" else None
    row = Integration(name=body.name, kind=body.kind, config=body.config,
                      enabled=body.enabled, webhook_token=webhook_token)
    if body.credentials:
        row.encrypted_credentials = encrypt_secret(__import__("json").dumps(body.credentials))
    db.add(row)
    db.flush()
    db.add(IntegrationEvent(integration_id=row.id, level="info", message="Integration created"))
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.patch("/{integration_id}", response_model=IntegrationOut)
def update_integration(integration_id: int, body: IntegrationUpdate, db: Session = Depends(get_db),
                       user: int = Depends(get_current_user)):
    row = db.get(Integration, integration_id)
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    import json
    if body.name is not None:
        row.name = body.name
    if body.config is not None:
        row.config = body.config
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.credentials is not None:
        # merge with existing credentials (only non-empty values are rotated)
        if row.encrypted_credentials:
            try:
                from app.security import decrypt_secret
                existing = json.loads(decrypt_secret(row.encrypted_credentials))
            except Exception:  # noqa: BLE001
                existing = {}
        else:
            existing = {}
        merged = {**existing, **{k: v for k, v in body.credentials.items() if v}}
        row.encrypted_credentials = encrypt_secret(json.dumps(merged))
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{integration_id}")
def delete_integration(integration_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    row = db.get(Integration, integration_id)
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    for ev in db.scalars(select(IntegrationEvent).where(IntegrationEvent.integration_id == integration_id)).all():
        db.delete(ev)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/{integration_id}/test")
def test_integration(integration_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    row = db.get(Integration, integration_id)
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    try:
        return int_svc.test_connection(db, row)
    except Exception as exc:  # noqa: BLE001
        int_svc._event(db, row, "error", f"Connection test failed: {exc}")
        db.commit()
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


@router.post("/{integration_id}/sync")
def sync_integration(integration_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    row = db.get(Integration, integration_id)
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    return int_svc.sync_integration(db, row)


@router.get("/{integration_id}/events")
def integration_events(integration_id: int, db: Session = Depends(get_db), user: int = Depends(get_current_user)):
    rows = db.scalars(select(IntegrationEvent).where(IntegrationEvent.integration_id == integration_id)
                      .order_by(IntegrationEvent.created_at.desc()).limit(50)).all()
    return [{"id": e.id, "level": e.level, "message": e.message, "created_at": e.created_at.isoformat()}
            for e in rows]
