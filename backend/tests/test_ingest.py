"""Tests for alert ingestion: normalization, validation, dedup, batch errors."""
import uuid
from datetime import datetime, timedelta, timezone


def _alert(**over):
    base = {"service_name": "svc-a", "title": "High CPU on worker", "severity": "high",
            "description": "cpu > 90%", "timestamp": datetime.now(timezone.utc).isoformat()}
    base.update(over)
    return base


def test_create_alert(client, auth_headers):
    r = client.post("/api/alerts", json=_alert(), headers=auth_headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["service_name"] == "svc-a"
    assert data["severity"] == "high"
    assert len(data["fingerprint"]) == 64


def test_alert_validation_missing_fields(client, auth_headers):
    r = client.post("/api/alerts", json={"title": "no service"}, headers=auth_headers)
    assert r.status_code == 422
    r = client.post("/api/alerts", json={"service_name": "svc"}, headers=auth_headers)
    assert r.status_code == 422


def test_severity_normalization(client, auth_headers):
    r = client.post("/api/alerts", json=_alert(title="Sev1 page", severity="SEV1"), headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["severity"] == "critical"
    r = client.post("/api/alerts", json=_alert(title="weird severity", severity="totally-unknown"),
                    headers=auth_headers)
    assert r.json()["severity"] == "medium"


def test_duplicate_alert_rejected(client, auth_headers):
    r1 = client.post("/api/alerts", json=_alert(title="Exact same title"), headers=auth_headers)
    assert r1.status_code == 201
    r2 = client.post("/api/alerts", json=_alert(title="Exact same title"), headers=auth_headers)
    assert r2.status_code == 409


def test_batch_ingest_partial_errors(client, auth_headers):
    svc = f"batch-svc-{uuid.uuid4().hex[:6]}"  # unique service: no cross-test dedupe collisions
    payload = {"alerts": [_alert(service_name=svc), {"title": "missing service"},
                          _alert(service_name=svc, title="Other alert", severity="low")]}
    r = client.post("/api/alerts/batch", json=payload, headers=auth_headers)
    assert r.status_code == 201
    data = r.json()
    assert data["imported"] == 2
    assert len(data["errors"]) == 1
    assert "row 1" in data["errors"][0]


def test_timestamp_validation_future(client, auth_headers):
    far_future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    r = client.post("/api/alerts", json=_alert(title="future", timestamp=far_future), headers=auth_headers)
    assert r.status_code == 422


def test_unauthorized_rejected(client):
    assert client.post("/api/alerts", json=_alert()).status_code == 401
    assert client.get("/api/alerts").status_code == 401


def test_metric_values_sanitized(client, auth_headers):
    r = client.post("/api/alerts", json=_alert(title="metrics", metric_values={"cpu": 92.5, "bad": "nope"}),
                    headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["metric_values"] == {"cpu": 92.5}