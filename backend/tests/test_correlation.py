"""Incident correlation tests: time + dependency + signature clustering."""
from datetime import timedelta

from app.models import Alert, Service, utcnow
from app.services.correlation import correlate_open_alerts, pair_score
from app.services.normalizer import fingerprint


def _add(db, service, title, minutes_ago, severity="high", **kw):
    a = Alert(service_name=service, title=title, severity=severity,
              fingerprint=fingerprint(service, title),
              timestamp=utcnow() - timedelta(minutes=minutes_ago), **kw)
    db.add(a)
    return a


def _setup_services(db):
    for name, deps in [("db-core", []), ("payment-api", ["db-core"]), ("unrelated-svc", [])]:
        existing = db.query(Service).filter(Service.name == name).first()
        if not existing:
            db.add(Service(name=name, dependencies=deps))
    db.commit()


def test_dependency_chain_correlates_into_incident(db):
    _setup_services(db)
    _add(db, "db-core", "PostgreSQL too many connections", 20, "critical")
    _add(db, "payment-api", "Payment API p95 latency spike", 15, "critical")
    _add(db, "payment-api", "Payment API 5xx error rate elevated", 10)
    db.commit()
    created = correlate_open_alerts(db)
    assert len(created) >= 1
    inc = created[0]
    assert set(["db-core", "payment-api"]).issubset(set(inc["services"]))
    assert len(inc["alert_ids"]) == 3


def test_unrelated_distant_alerts_not_grouped(db):
    _setup_services(db)
    _add(db, "unrelated-svc", "Disk usage 80%", 100, "medium")
    _add(db, "unrelated-svc", "Certificate renewal reminder", 2000, "info")  # far outside window
    db.commit()
    created = correlate_open_alerts(db)
    # no incident should link the two distant/unrelated alerts
    for inc in created:
        assert len(inc["alert_ids"]) != 2 or "unrelated-svc" not in inc["services"]


def test_pair_score_signals(db):
    _setup_services(db)
    a1 = _add(db, "payment-api", "Payment API latency spike", 5)
    a2 = _add(db, "payment-api", "Payment API latency spike retry storms", 3)
    a3 = _add(db, "unrelated-svc", "Certificate renewal reminder", 3)
    db.commit()
    score_related, reasons = pair_score(db, a1, a2)
    assert score_related >= 0.5 and any("same service" in r for r in reasons)
    score_unrelated, _ = pair_score(db, a1, a3)
    assert score_unrelated < score_related


def test_correlation_hypothesis_is_labeled_as_hypothesis(db):
    _setup_services(db)
    _add(db, "db-core", "DB connection exhaustion", 20, "critical")
    _add(db, "payment-api", "Payment API timeouts", 15, "critical")
    db.commit()
    created = correlate_open_alerts(db)
    assert created
    from app.models import Incident
    inc = db.get(Incident, created[0]["incident_id"])
    hyps = inc.correlation_evidence.get("causal_hypotheses", [])
    assert hyps
    assert all("Hypothesis" in h and "correlation only" in h for h in hyps)


def test_resolving_incident_resolves_alerts(client, auth_headers, db):
    _setup_services(db)
    a1 = _add(db, "db-core", "DB down", 30, "critical")
    a2 = _add(db, "payment-api", "API failing", 25, "critical")
    db.commit()
    correlate_open_alerts(db)
    from app.models import Incident
    inc = db.query(Incident).order_by(Incident.id.desc()).first()
    r = client.patch(f"/api/incidents/{inc.id}/status", json={"status": "resolved"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["resolved_at"] is not None
    db.expire_all()
    assert db.get(type(a1), a1.id).status == "resolved"