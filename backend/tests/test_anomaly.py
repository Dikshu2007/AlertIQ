"""Anomaly detection tests: honesty labeling, statistical methods, thresholds."""
from datetime import timedelta

from app.models import Alert, utcnow
from app.services.normalizer import fingerprint
from app.services import anomaly as anomaly_svc


def _seed_alerts(db, service, titles_counts, hours_back_start=72):
    """titles_counts: list of (title, n_occurrences) spread over hours_back_start."""
    for title, n in titles_counts:
        for i in range(n):
            ts = utcnow() - timedelta(hours=hours_back_start) + timedelta(hours=i * max(1, hours_back_start // max(1, n)))
            db.add(Alert(service_name=service, title=title, severity="medium",
                         fingerprint=fingerprint(service, title), timestamp=ts))
    db.commit()


def test_frequency_spike_detected_with_enough_history(db):
    # 60 calm hours then a burst of 15 alerts in the last 3 hours
    for h in range(60):
        db.add(Alert(service_name="spike-svc", title="routine check", severity="info",
                     fingerprint=fingerprint("spike-svc", "routine check"),
                     timestamp=utcnow() - timedelta(hours=70 - h)))
    burst_ts = utcnow() - timedelta(hours=2)
    for i in range(15):
        db.add(Alert(service_name="spike-svc", title="routine check", severity="high",
                     fingerprint=fingerprint("spike-svc", "routine check"),
                     timestamp=burst_ts + timedelta(minutes=i * 5)))
    db.commit()
    results = anomaly_svc.detect_frequency_spikes(db, hours_back=72)
    spikes = [r for r in results if r["kind"] == "frequency_spike" and r["score"] is not None]
    assert spikes, "expected a frequency spike detection"
    top = max(spikes, key=lambda r: r["score"])
    assert top["service"] == "spike-svc"
    assert "z-score" in top["method"]  # honest method labeling
    assert top["score"] >= 3.0


def test_insufficient_history_clearly_labeled(db):
    for i in range(5):
        db.add(Alert(service_name="young-svc", title="new service alert", severity="low",
                     fingerprint=fingerprint("young-svc", "new service alert"),
                     timestamp=utcnow() - timedelta(hours=i)))
    db.commit()
    results = anomaly_svc.detect_frequency_spikes(db, hours_back=72)
    insuf = [r for r in results if r["service"] == "young-svc"]
    assert insuf and insuf[0]["score"] is None
    assert "insufficient" in insuf[0]["method"]
    assert "at least 12 hours" in insuf[0]["explanation"]


def test_new_alert_type_flagged_as_first_seen_not_malfunction(db):
    # one old known alert, one brand-new fingerprint now
    db.add(Alert(service_name="t-svc", title="known alert", severity="low",
                 fingerprint=fingerprint("t-svc", "known alert"),
                 timestamp=utcnow() - timedelta(days=20)))
    db.add(Alert(service_name="t-svc", title="brand new alert kind", severity="medium",
                 fingerprint=fingerprint("t-svc", "brand new alert kind"),
                 timestamp=utcnow() - timedelta(hours=1)))
    db.commit()
    results = anomaly_svc.detect_new_alert_types(db, days_back=14)
    new = [r for r in results if r["service"] == "t-svc"]
    assert len(new) == 1
    assert new[0]["kind"] == "new_alert_type"
    assert "first-seen" in new[0]["explanation"]
    assert "may be entirely legitimate" in new[0]["explanation"]
    assert "rule_based" in new[0]["method"]


def test_metric_zscore_detection(db):
    from app.models import Metric
    # baseline 30 samples ~100
    for i in range(30):
        db.add(Metric(service_name="m-svc", name="latency_p95_ms", value=100.0,
                      timestamp=utcnow() - timedelta(hours=30 - i)))
    db.add(Metric(service_name="m-svc", name="latency_p95_ms", value=900.0, timestamp=utcnow()))
    db.commit()
    results = anomaly_svc.detect_metric_anomalies(db, days_back=14)
    hits = [r for r in results if r["service"] == "m-svc" and r.get("evidence", {}).get("metric") == "latency_p95_ms"]
    assert hits and hits[0]["score"] is not None
    assert hits[0]["method"].startswith("statistical")


def test_iforest_only_with_enough_samples(db):
    """Isolation Forest must run only at >= configured min samples, else z-score/insufficient."""
    from app.models import Metric
    n_small = 10
    for i in range(n_small):
        db.add(Metric(service_name="small-svc", name="cpu", value=50.0,
                      timestamp=utcnow() - timedelta(hours=n_small - i)))
    db.commit()
    results = anomaly_svc.detect_metric_anomalies(db, days_back=14)
    for r in results:
        if r["service"] == "small-svc":
            assert "isolation_forest" not in r["method"]


def test_cascade_detection(db):
    t0 = utcnow() - timedelta(hours=1)
    for i, svc in enumerate(["cascade-a", "cascade-b", "cascade-c"]):
        db.add(Alert(service_name=svc, title=f"{svc} failing", severity="critical",
                     fingerprint=fingerprint(svc, f"{svc} failing"),
                     timestamp=t0 + timedelta(minutes=i * 3)))
    db.commit()
    results = anomaly_svc.detect_cascades(db, minutes_window=15)
    ours = [r for r in results if set(["cascade-a", "cascade-b", "cascade-c"]).issubset(set(r["evidence"]["services"]))]
    assert ours, "expected a cascade covering the three seeded services"
    assert "does not prove causation" in ours[0]["explanation"]


def test_full_scan_persists_results(db):
    for i in range(3):
        db.add(Alert(service_name="scan-svc", title="unique never seen before", severity="medium",
                     fingerprint=fingerprint("scan-svc", "unique never seen before"),
                     timestamp=utcnow() - timedelta(hours=i)))
    db.commit()
    summary = anomaly_svc.run_full_scan(db)
    assert summary["created"] >= 1
    from app.models import AnomalyResult
    rows = db.query(AnomalyResult).all()
    assert all(r.method for r in rows)  # every persisted anomaly is method-labeled


def test_unknown_alert_triage(db):
    a = Alert(service_name="triage-svc", title="mystery failure mode", severity="high",
              fingerprint=fingerprint("triage-svc", "mystery failure mode"), timestamp=utcnow())
    db.add(a)
    db.commit()
    triage = anomaly_svc.unknown_alert_triage(db, a)
    assert triage["verdict"] == "first-seen"
    assert triage["anomaly_score"] > 0.5
    assert triage["needs_more_data"] is True  # < 1 day of history
    assert "no model training" in triage["method"]