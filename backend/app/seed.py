"""Demo-mode seeding. Runs ONLY when DEMO_MODE=true and the DB has no users.

All demo services are prefixed "demo-" so they are instantly distinguishable
from real data. The demo dataset deliberately contains:
  - a baseline period with normal alert volumes (gives statistics real history)
  - a frequency spike on demo-payment-api
  - a first-seen alert type (never observed in the baseline)
  - a correlated cascade: demo-db -> demo-payment-api -> demo-checkout
"""
import logging
from datetime import timedelta
from random import Random

log = logging.getLogger("alertiq.seed")


def seed_if_empty() -> None:
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import (Alert, Incident, Metric, Service, User, utcnow)
    from app.security import hash_password
    from app.services.normalizer import fingerprint

    db = SessionLocal()
    try:
        if db.scalar(select(User).limit(1)):
            log.info("Demo seed skipped: database already has data")
            return
        rng = Random(42)
        now = utcnow()

        db.add(User(email="demo@alertiq.local", password_hash=hash_password("demo-password-123")))

        services = [
            Service(name="demo-db", description="[DEMO] PostgreSQL cluster", dependencies=[],
                    criticality="critical"),
            Service(name="demo-payment-api", description="[DEMO] Payment processing API",
                    dependencies=["demo-db"], criticality="critical"),
            Service(name="demo-checkout", description="[DEMO] Checkout frontend service",
                    dependencies=["demo-payment-api"], criticality="high"),
            Service(name="demo-auth", description="[DEMO] Authentication service", dependencies=[],
                    criticality="high"),
        ]
        services[2].last_deploy_at = now - timedelta(hours=5)
        for s in services:
            db.add(s)
        db.flush()

        def add_alert(service, title, severity, hours_ago, metrics=None, labels=None, description=""):
            ts = now - timedelta(hours=hours_ago)
            db.add(Alert(service_name=service, title=title, description=description, severity=severity,
                         status="open" if hours_ago < 20 else "resolved", fingerprint=fingerprint(service, title),
                         labels=labels or {}, metric_values=metrics or {}, timestamp=ts, source="demo"))

        # baseline: 10 days of calm, low-volume alerts
        for day in range(10, 1, -1):
            n = rng.randint(0, 2)
            for i in range(n):
                svc = rng.choice(["demo-db", "demo-auth", "demo-payment-api"])
                add_alert(svc, "Periodic health-check warning", "info", day * 24 + rng.randint(0, 23))

        # stable metric history for demo-payment-api (gives z-scores real baseline)
        for day in range(10, 0, -1):
            for hour in (2, 10, 18):
                db.add(Metric(service_name="demo-payment-api", name="latency_p95_ms",
                              value=rng.uniform(120, 180), timestamp=now - timedelta(days=day, hours=hour)))
                db.add(Metric(service_name="demo-payment-api", name="error_rate_pct",
                              value=rng.uniform(0.1, 0.6), timestamp=now - timedelta(days=day, hours=hour)))

        # cascade ~6h ago: db -> payment-api -> checkout
        add_alert("demo-db", "PostgreSQL too many connections", "critical", 6.0,
                  {"connections": 198}, description="Connection count exceeded max_connections * 0.9")
        add_alert("demo-payment-api", "Payment API p95 latency spike", "critical", 5.9,
                  {"latency_p95_ms": 2400, "error_rate_pct": 7.4})
        add_alert("demo-payment-api", "Payment API 5xx error rate elevated", "high", 5.7,
                  {"error_rate_pct": 9.1})
        add_alert("demo-checkout", "Checkout conversion drop", "high", 5.5, {"conversion_pct": 41})
        add_alert("demo-auth", "Auth service token validation latency", "medium", 5.4,
                  {"latency_p95_ms": 800})

        # frequency spike on demo-payment-api: 9 alerts in the last hour (vs baseline ~0-2)
        for i in range(9):
            add_alert("demo-payment-api", "Payment gateway timeout (upstream acquirer)",
                      rng.choice(["high", "critical"]), rng.uniform(0.1, 0.9),
                      {"latency_p95_ms": 1900 + i * 60, "error_rate_pct": 5 + i})

        # first-seen alert type (never in the baseline window)
        add_alert("demo-payment-api", "Novel alert: nonce replayguard cache thrash", "medium", 1.0,
                  {"cache_hit_ratio": 0.31},
                  description="First occurrence of this alert signature in recorded history.")
        add_alert("demo-checkout", "Novel alert: edge-worker cold-start pile-up", "low", 2.0, {})

        # one resolved incident with a verified resolution (historical learning demo)
        incident = Incident(
            title="[DEMO] Correlated alerts across demo-db, demo-payment-api",
            description="Auto-correlated demo incident from 4 alerts sharing time/service/dependency signals.",
            severity="critical", status="resolved", affected_services=["demo-db", "demo-payment-api", "demo-checkout"],
            root_cause="Connection pool exhaustion on demo-db after a traffic batch job overlapped peak traffic.",
            root_cause_confidence=0.6,
            recommended_resolution="Increase the connection pool size within safe DB limits; stagger the batch job.",
            correlation_evidence={"method": "demo seed (pre-built correlation)", "causal_hypotheses": [
                "Hypothesis: 'demo-db' preceded 'demo-payment-api' and demo-payment-api depends on demo-db."],
                "alert_ids": []},
            created_at=now - timedelta(days=3), resolved_at=now - timedelta(days=2, hours=22),
        )
        db.add(incident)
        db.flush()
        from app.models import AlertIncidentLink, Resolution
        first_alert = db.scalar(select(Alert).where(Alert.service_name == "demo-db").limit(1))
        if first_alert:
            db.add(AlertIncidentLink(alert_id=first_alert.id, incident_id=incident.id, relation="primary"))
        db.add(Resolution(incident_id=incident.id, service_name="demo-db",
                          problem_signature="PostgreSQL too many connections",
                          solution="Increased PgBouncer pool size from 50 to 120 and moved the nightly batch job "
                                   "outside peak traffic. Verified by the on-call engineer.",
                          verified=True, verified_by="demo@alertiq.local"))

        db.commit()
        log.info("Demo data seeded (services, alerts, metrics, one resolved incident + verified resolution)")
    finally:
        db.close()