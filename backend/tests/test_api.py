"""API error handling, auth, security, and integration management tests."""
import json


def test_404_unknown_alert(client, auth_headers):
    assert client.get("/api/alerts/999999", headers=auth_headers).status_code == 404
    assert client.get("/api/incidents/999999", headers=auth_headers).status_code == 404


def test_invalid_token_rejected(client):
    r = client.get("/api/alerts", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_rate_limiting(client, auth_headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_minute", 5)
    codes = []
    for _ in range(10):
        r = client.post("/api/ai/ask", json={"question": "overview"}, headers=auth_headers)
        codes.append(r.status_code)
    assert 429 in codes


def test_passwords_hashed_not_stored_plaintext(db):
    from app.models import User
    u = db.query(User).first()
    assert u.password_hash.startswith("pbkdf2$")
    assert "password123" not in u.password_hash


def test_integration_credentials_never_returned(client, auth_headers):
    r = client.post("/api/integrations", json={
        "name": "prod-datadog", "kind": "datadog",
        "config": {"base_url": "https://api.datadoghq.com"},
        "credentials": {"api_key": "super-secret-key", "app_key": "app-secret"}},
        headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert "super-secret-key" not in json.dumps(body)
    assert "app-secret" not in json.dumps(body)
    assert body["has_credentials"] is True


def test_integration_credentials_encrypted_at_rest(db):
    from app.models import Integration
    row = db.query(Integration).filter(Integration.name == "prod-datadog").first()
    assert row.encrypted_credentials
    assert "super-secret-key" not in row.encrypted_credentials
    from app.security import decrypt_secret
    creds = json.loads(decrypt_secret(row.encrypted_credentials))
    assert creds["api_key"] == "super-secret-key"


def test_webhook_ingest_with_token(client, auth_headers, db):
    from app.models import Integration
    r = client.post("/api/integrations", json={"name": "my-hook", "kind": "webhook", "config": {}},
                    headers=auth_headers)
    token = r.json()["webhook_token"]
    assert token
    payload = {"alerts": [{"service_name": "hook-svc", "title": "Webhook alert", "severity": "high"}]}
    ok = client.post(f"/api/webhooks/{token}", json=payload)
    assert ok.status_code == 200 and ok.json()["imported"] == 1
    bad = client.post("/api/webhooks/wrong-token", json=payload)
    assert bad.status_code == 401


def test_webhook_rejects_malformed_payload(client, auth_headers):
    r = client.post("/api/integrations", json={"name": "hook2", "kind": "webhook", "config": {}},
                    headers=auth_headers)
    token = r.json()["webhook_token"]
    bad = client.post(f"/api/webhooks/{token}", json={"alerts": [{"title": "no service"}]})
    assert bad.status_code == 200  # webhook accepts partial failures, reports them
    assert bad.json()["errors"]


def test_prometheus_connector_missing_config_fails_gracefully(client, auth_headers):
    r = client.post("/api/integrations", json={"name": "prom", "kind": "prometheus", "config": {}},
                    headers=auth_headers)
    iid = r.json()["id"]
    t = client.post(f"/api/integrations/{iid}/test", headers=auth_headers)
    assert t.status_code == 200
    assert t.json()["ok"] is False


def test_meta_reports_engines_honestly(client, auth_headers):
    meta = client.get("/api/meta", headers=auth_headers).json()
    assert meta["llm_engine"] == "none"
    assert meta["web_research_available"] is False
    assert meta["demo_mode"] is False