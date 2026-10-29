"""Integration connectors for external monitoring systems.

All connectors fetch data, translate it into the common normalized alert format,
and return rows ready for the normalizer. Credentials are decrypted only here
(backend memory) and never logged.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import Integration, IntegrationEvent, utcnow
from app.services.normalizer import normalize_alert, normalize_many

log = logging.getLogger("alertiq.integrations")


def _creds(integration: Integration) -> dict:
    from app.security import decrypt_secret
    import json
    if not integration.encrypted_credentials:
        return {}
    try:
        raw = json.loads(decrypt_secret(integration.encrypted_credentials))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        log.error("Credential decryption failed for integration %s: %s", integration.id, type(exc).__name__)
        return {}


def _event(db: Session, integration: Integration, level: str, message: str) -> None:
    db.add(IntegrationEvent(integration_id=integration.id, level=level, message=message[:2000]))
    integration.last_status = "ok" if level == "info" else "error"
    integration.last_error = message[:2000] if level == "error" else None
    integration.last_sync_at = utcnow()


# ------------------------------------------------------------------ connectors
def fetch_prometheus(integration: Integration) -> list[dict]:
    """Reads firing alerts from the Prometheus alerts API."""
    cfg = integration.config or {}
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise ValueError("Prometheus integration requires config.base_url")
    url = f"{base}/api/v1/alerts"
    auth = None
    creds = _creds(integration)
    if creds.get("username") and creds.get("password"):
        auth = (creds["username"], creds["password"])
    with httpx.Client(timeout=15, auth=auth) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    rows = []
    for a in (data.get("data", {}) or {}).get("alerts", []):
        labels = a.get("labels", {}) or {}
        monitor_state = str(a.get("state") or "").strip().lower()
        rows.append({
            "service_name": labels.get("job") or labels.get("service") or labels.get("instance") or "unknown",
            "title": a.get("annotations", {}).get("summary") or labels.get("alertname") or "Prometheus alert",
            "description": a.get("annotations", {}).get("description", ""),
            "severity": labels.get("severity", "medium"),
            # Prometheus /api/v1/alerts returns active alerts; disappearance
            # from this polling response is not enough evidence to close one.
            "status": "open",
            "labels": {**labels, "monitor_state": monitor_state},
            "metric_values": {k: float(v) for k, v in (a.get("values", {}) or {}).items()
                              if isinstance(v, (int, float))},
            "timestamp": a.get("activeAt"),
            "source": "prometheus",
            "external_id": str(a.get("labels", {}).get("alertname", "")) + "@" + str(a.get("activeAt", "")),
        })
    return rows


def fetch_datadog(integration: Integration) -> list[dict]:
    cfg = integration.config or {}
    base = (cfg.get("base_url") or "https://api.datadoghq.com").rstrip("/")
    creds = _creds(integration)
    if not creds.get("api_key") or not creds.get("app_key"):
        raise ValueError("Datadog integration requires api_key and app_key credentials")
    with httpx.Client(timeout=20) as client:
        resp = client.get(
            f"{base}/api/v1/monitor",
            headers={"DD-API-KEY": creds["api_key"], "DD-APPLICATION-KEY": creds["app_key"]},
            params={"group_states": "all"},
        )
        resp.raise_for_status()
        monitors = resp.json()
    rows = []
    for m in monitors[:100]:
        monitor_state = str(m.get("overall_state") or "").strip().lower()
        if monitor_state not in {"alert", "warn", "no data", "ok"}:
            continue
        tags = m.get("tags", []) or []
        service = next((t.split(":", 1)[1] for t in tags if t.startswith("service:")), "unknown")
        rows.append({
            "service_name": service,
            "title": m.get("name", "Datadog monitor alert"),
            "description": m.get("message", "")[:2000],
            "severity": "high" if "critical" in str(m.get("overall_state", "")).lower() else "medium",
            "status": "resolved" if monitor_state == "ok" else "open",
            "labels": {"tags": tags, "monitor_id": m.get("id"), "monitor_state": monitor_state},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "datadog",
            "external_id": f"dd-monitor-{m.get('id')}",
        })
    return rows


def fetch_github(integration: Integration) -> list[dict]:
    """Maps failing CI runs / open issues of a repo as alerts."""
    cfg = integration.config or {}
    repo = cfg.get("repo")
    if not repo:
        raise ValueError("GitHub integration requires config.repo (e.g. 'org/repo')")
    creds = _creds(integration)
    headers = {"Accept": "application/vnd.github+json"}
    if creds.get("token"):
        headers["Authorization"] = f"Bearer {creds['token']}"
    rows = []
    with httpx.Client(timeout=20, headers=headers) as client:
        resp = client.get(f"https://api.github.com/repos/{repo}/actions/runs?per_page=20")
        resp.raise_for_status()
        for run in resp.json().get("workflow_runs", []):
            if run.get("conclusion") == "failure":
                rows.append({
                    "service_name": cfg.get("service_name") or repo.split("/")[-1],
                    "title": f"CI failure: {run.get('name', 'workflow')} ({run.get('head_branch', '')})",
                    "description": run.get("html_url", ""),
                    "severity": "high",
                    "labels": {"deployment": True, "workflow": run.get("name")},
                    "timestamp": run.get("created_at"),
                    "source": "github",
                    "external_id": f"gh-run-{run.get('id')}",
                })
    return rows


CONNECTORS = {"prometheus": fetch_prometheus, "datadog": fetch_datadog, "github": fetch_github}


def test_connection(db: Session, integration: Integration) -> dict:
    """Runs the connector in dry-run (no rows persisted)."""
    if integration.kind in CONNECTORS:
        rows = CONNECTORS[integration.kind](integration)
        _event(db, integration, "info", f"Connection test OK: {len(rows)} alerts would be ingested")
        db.commit()
        return {"ok": True, "sample_count": len(rows),
                "sample": rows[0] if rows else None}
    if integration.kind in ("webhook", "csv", "rest", "grafana"):
        ok = bool((integration.config or {}).get("base_url") or integration.kind in ("webhook", "csv"))
        msg = "Configuration present" if ok else "Missing config"
        _event(db, integration, "info" if ok else "error", msg)
        db.commit()
        return {"ok": ok, "message": msg}
    return {"ok": False, "message": f"No connector implemented for kind '{integration.kind}'"}


def sync_integration(db: Session, integration: Integration) -> dict:
    """Pulls data from the source, normalizes, and ingests it."""
    from app.services.ingest import ingest_alerts

    if integration.kind not in CONNECTORS:
        return {"ok": False, "message": f"Pull-sync is not implemented for '{integration.kind}' "
                                        "(webhook/CSV sources push data instead)."}
    if not integration.enabled:
        return {"ok": False, "message": "Integration is disabled."}
    try:
        rows = CONNECTORS[integration.kind](integration)
        valid, errors = normalize_many(rows)
        result = ingest_alerts(db, valid, source=integration.kind)
        _event(db, integration, "info",
               f"Sync OK: {result['imported']} ingested, {result['duplicates']} duplicates, "
               f"{result['auto_acknowledged']} auto-acknowledged, {result['auto_resolved']} auto-resolved")
        db.commit()
        return {"ok": True, **result, "errors": errors[:10]}
    except Exception as exc:  # noqa: BLE001
        log.error("Integration %s sync failed: %s", integration.id, exc)
        _event(db, integration, "error", f"{type(exc).__name__}: {exc}")
        db.commit()
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
