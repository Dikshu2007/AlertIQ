import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Alert, Incident
from app.security import decode_access_token

router = APIRouter(tags=["realtime"])


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    token = ws.query_params.get("token", "")
    try:
        decode_access_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await manager.connect(ws)
    last_alert_id = 0
    last_incident_ts = ""
    try:
        await ws.send_text(json.dumps({"type": "connected"}))
        while True:
            await asyncio.sleep(3)
            db = SessionLocal()
            try:
                newest = db.scalars(select(Alert).order_by(Alert.timestamp.desc()).limit(1)).first()
                if newest and newest.id != last_alert_id:
                    last_alert_id = newest.id
                    await ws.send_text(json.dumps({
                        "type": "new_alert",
                        "alert": {"id": newest.id, "title": newest.title, "service": newest.service_name,
                                  "severity": newest.severity, "timestamp": newest.timestamp.isoformat()},
                    }))
                newest_inc = db.scalars(select(Incident).order_by(Incident.created_at.desc()).limit(1)).first()
                if newest_inc:
                    ts = newest_inc.created_at.isoformat()
                    if ts != last_incident_ts:
                        last_incident_ts = ts
                        await ws.send_text(json.dumps({
                            "type": "incident_update",
                            "incident": {"id": newest_inc.id, "title": newest_inc.title,
                                         "status": newest_inc.status, "severity": newest_inc.severity},
                        }))
            finally:
                db.close()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)
