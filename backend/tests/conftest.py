import os
import tempfile

# Configure a throwaway DB BEFORE app import
_TEST_DIR = tempfile.mkdtemp(prefix="alertiq-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DEMO_MODE"] = "false"
os.environ["LLM_PROVIDER"] = "none"
os.environ["SEARCH_PROVIDER"] = "none"
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"

import uuid

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client):
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def db():
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
