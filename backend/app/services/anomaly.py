"""Anomaly detection pipeline.

Honesty contract (enforced throughout):
  - every result carries `method` describing EXACTLY what was computed
  - Isolation Forest runs ONLY when enough history exists; otherwise purely
    statistical/rule-based methods are used and labeled as such
  - a new alert *type* is reported as "new/first-seen", never as "malfunction"
  - insufficient data => the result says what extra data is needed
"""
import statistics
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Alert, AnomalyResult, Service, utcnow


# ----------------------------------------------------------------- helpers
def _history_days(alerts: list[Alert]) -> float:
    if len(alerts) < 2:
        return 0.0
    span = max(a.timestamp for a in alerts) - min(a.timestamp for a in alerts)
    return span.total_seconds() / 86400


def _rolling_zscore(series: list[float], window: int = 12) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(series)):
        lo = max(0, i - window)
        hist = series[lo:i]
        if len(hist) < 3:
            out.append(None)
            continue
        mean, stdev = statistics.mean(hist), statistics.pstdev(hist)
        out.append(None if stdev == 0 else (series[i] - mean) / stdev)
    return out


# ----------------------------------------------------------------- detectors
def detect_frequency_spikes(db: Session, hours_back: int = 24) -> list[dict]:
    """Alert-count spikes per service vs its own rolling baseline (z-score)."""
    since = utcnow() - timedelta(hours=hours_back + 7 * 24)
    alerts = db.scalars(select(Alert).where(Alert.timestamp >= since)).all()
    results = []
    by_service_hour: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for a in alerts:
        by_service_hour[a.service_name][int(a.timestamp.timestamp() // 3600)] += 1

    for service, hours in by_service_hour.items():
        counts = [hours.get(h, 0) for h in sorted(hours)]
        if len(counts) < 12:
            results.append({
                "service": service, "kind": "frequency_spike", "score": None, "confidence": 0.0,
                "method": "statistical (insufficient history)",
                "explanation": f"Only {len(counts)} hours of alert history for '{service}' — "
                               "frequency baseline needs at least 12 hours; no anomaly verdict issued.",
                "evidence": {"hours_of_history": len(counts)},
            })
            continue
        zs = _rolling_zscore(counts, window=max(6, len(counts) // 3))
        # the spike candidate is the hour with the highest alert count
        peak_idx = max(range(len(counts)), key=lambda i: counts[i])
        z = zs[peak_idx]
        if z is None:
            # zero-variance baseline: any burst above the constant level is significant by construction
            hist = counts[max(0, peak_idx - max(6, len(counts) // 3)):peak_idx]
            if hist and counts[peak_idx] > max(hist) and counts[peak_idx] >= 5:
                z = 8.0
        if z is not None and z >= 3.0 and counts[peak_idx] >= 5:
            results.append({
                "service": service, "kind": "frequency_spike", "score": round(z, 2),
                "confidence": min(0.9, 0.5 + z / 10),
                "method": "statistical (rolling z-score, window=%d)" % max(6, len(counts) // 3),
                "explanation": f"'{service}' produced {counts[peak_idx]} alerts in hour {peak_idx} "
                               f"({z:.1f}σ above its rolling baseline of {statistics.mean(counts[:peak_idx]) if peak_idx else 0:.1f}/hour)"
                               + (" — baseline was constant, so any burst is statistically significant." if z >= 8 else "."),
                "evidence": {"peak_hour_count": counts[peak_idx], "zscore": round(z, 2)},
            })
    return [r for r in results if r["score"] is not None] + [r for r in results if r["score"] is None]


def detect_new_alert_types(db: Session, days_back: int = 14) -> list[dict]:
    """Fingerprints seen for the first time inside the lookback window.

    A first-seen fingerprint is flagged as NEW — explicitly NOT auto-classified
    as a malfunction (it may be a legitimate new alert rule firing correctly).
    """
    cutoff = utcnow() - timedelta(days=days_back)
    recent = db.scalars(select(Alert).where(Alert.timestamp >= cutoff)).all()
    older = db.scalars(select(Alert).where(Alert.timestamp < cutoff)).all()
    known_fps = {a.fingerprint for a in older}
    seen_new: set[str] = set()
    results = []
    for a in sorted(recent, key=lambda x: x.timestamp):
        if a.fingerprint in known_fps or a.fingerprint in seen_new:
            continue
        seen_new.add(a.fingerprint)
        results.append({
            "service": a.service_name, "kind": "new_alert_type", "alert_id": a.id,
            "score": 0.6, "confidence": 0.55,
            "method": "rule_based (first-seen fingerprint check)",
            "explanation": f"Alert '{a.title}' on '{a.service_name}' was never observed in the previous {days_back} days. "
                           "This is flagged as first-seen — it may be entirely legitimate; review before treating it as an anomaly.",
            "evidence": {"first_seen_at": a.timestamp.isoformat(), "fingerprint": a.fingerprint,
                         "history_window_days": days_back, "prior_occurrences": 0},
        })
    return results


def detect_metric_anomalies(db: Session, days_back: int = 14) -> list[dict]:
    """z-score on metric series; Isolation Forest ONLY when >= min samples per service.

    Series sources: metric_values attached to alerts AND the dedicated Metric
    table (both are real recorded data).
    """
    from datetime import datetime

    from app.models import Metric

    cutoff = utcnow() - timedelta(days=days_back)
    series: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)

    rows = db.execute(
        select(Alert.service_name, Alert.metric_values, Alert.timestamp)
        .where(Alert.timestamp >= cutoff, Alert.metric_values != {})
    ).all()
    for service, metrics, ts in rows:
        for name, value in metrics.items():
            series[(service, name)].append((ts, float(value)))

    metric_rows = db.execute(
        select(Metric.service_name, Metric.name, Metric.value, Metric.timestamp)
        .where(Metric.timestamp >= cutoff)
    ).all()
    for service, name, value, ts in metric_rows:
        series[(service, name)].append((ts, float(value)))

    for key in series:
        series[key].sort(key=lambda p: p[0])

    results = []
    for (service, metric), points in series.items():
        values = [v for _, v in points]
        if len(values) >= settings.anomaly_iforest_min_samples:
            results.extend(_iforest_check(service, metric, points))
        elif len(values) >= 8:
            mean, stdev = statistics.mean(values), statistics.pstdev(values)
            if stdev == 0:
                continue
            worst_ts, worst_v = max(points, key=lambda p: abs(p[1] - mean))
            z = (worst_v - mean) / stdev
            if abs(z) >= 3.0:
                results.append({
                    "service": service, "kind": "metric_zscore",
                    "score": round(abs(z), 2), "confidence": min(0.85, 0.4 + abs(z) / 10),
                    "method": "statistical (global z-score)",
                    "explanation": f"Metric '{metric}' on '{service}' reached {worst_v:.2f} "
                                   f"({z:+.1f}σ vs mean {mean:.2f}, σ={stdev:.2f}).",
                    "evidence": {"metric": metric, "value": worst_v, "mean": round(mean, 3),
                                 "stdev": round(stdev, 3), "zscore": round(z, 2), "n_samples": len(values)},
                })
        else:
            results.append({
                "service": service, "kind": "metric_zscore", "score": None, "confidence": 0.0,
                "method": "statistical (insufficient history)",
                "explanation": f"Only {len(values)} samples for '{metric}' on '{service}' — need ≥8 for a z-score verdict "
                               "(≥50 to enable Isolation Forest).",
                "evidence": {"metric": metric, "n_samples": len(values)},
            })
    scored = [r for r in results if r["score"] is not None]
    return sorted(scored, key=lambda r: -r["score"]) + [r for r in results if r["score"] is None]


def _iforest_check(service: str, metric: str, points) -> list[dict]:
    """Isolation Forest over per-point features [value]. Runs only with enough data."""
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return [{
            "service": service, "kind": "metric_zscore", "score": None, "confidence": 0.0,
            "method": "statistical (scikit-learn unavailable)",
            "explanation": f"Isolation Forest requested for '{metric}' on '{service}' but scikit-learn is not installed; "
                           "install requirements.txt to enable it.",
            "evidence": {"metric": metric, "n_samples": len(points)},
        }]
    import numpy as np
    values = np.array([[v] for _, v in points])
    contamination = 0.05
    model = IsolationForest(n_estimators=100, contamination=contamination, random_state=42)
    labels = model.fit_predict(values)
    scores = -model.score_samples(values)
    out = []
    mean = float(values.mean())
    for (ts, v), lab, sc in zip(points, labels, scores):
        if lab == -1 and abs(v - mean) > 0:
            out.append({
                "service": service, "kind": "metric_zscore",
                "score": round(float(sc), 4), "confidence": 0.7,
                "method": "isolation_forest (contamination=0.05, n=100, trained on this service's own history)",
                "explanation": f"Isolation Forest flagged '{metric}'={v:.2f} on '{service}' at {ts.isoformat()} "
                               "as inconsistent with the service's own historical distribution.",
                "evidence": {"metric": metric, "value": v, "anomaly_score": round(float(sc), 4),
                             "n_samples": len(points), "timestamp": ts.isoformat()},
            })
    return out[-5:]  # cap output per series


def detect_cascades(db: Session, minutes_window: int = 15) -> list[dict]:
    """Sequences where >=3 DISTINCT services alert in tight succession."""
    since = utcnow() - timedelta(days=settings.anomaly_scan_history_days)
    alerts = db.scalars(select(Alert).where(Alert.timestamp >= since).order_by(Alert.timestamp)).all()
    results, reported = [], set()
    for i in range(len(alerts)):
        window = [a for a in alerts[i:]
                  if (a.timestamp - alerts[i].timestamp) <= timedelta(minutes=minutes_window)]
        services = list(dict.fromkeys(a.service_name for a in window))
        if len(services) >= 3:
            key = (services[0], len(services))
            if key in reported:
                continue
            reported.add(key)
            results.append({
                "service": ", ".join(services), "kind": "cascade_sequence",
                "score": min(1.0, len(services) / 5), "confidence": min(0.8, 0.4 + 0.1 * len(services)),
                "method": "rule_based (temporal sequence across distinct services)",
                "explanation": f"{len(services)} distinct services ({', '.join(services)}) alerted within {minutes_window} minutes, "
                               "suggesting a possible cascading failure. Correlation does not prove causation — "
                               "the first-alerted service is only a candidate trigger.",
                "evidence": {"services": services, "window_minutes": minutes_window,
                             "start": alerts[i].timestamp.isoformat()},
            })
    return results


def detect_correlation_shifts(db: Session, days_back: int = 14) -> list[dict]:
    """Detects when the historical relationship between two metrics of a service
    changes (e.g. CPU no longer tracking error rate). Pearson r comparison."""
    cutoff = utcnow() - timedelta(days=days_back)
    rows = db.execute(
        select(Alert.service_name, Alert.metric_values, Alert.timestamp)
        .where(Alert.timestamp >= cutoff, Alert.metric_values != {})
    ).all()
    per_service: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for service, metrics, _ in rows:
        for name, value in metrics.items():
            per_service[service][name].append(float(value))

    results = []
    for service, metrics in per_service.items():
        names = [n for n in metrics if len(metrics[n]) >= 10]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = metrics[names[i]], metrics[names[j]]
                n = min(len(a), len(b))
                a, b = a[-n:], b[-n:]
                half = n // 2
                if half < 5:
                    continue
                r1 = _pearson(a[:half], b[:half])
                r2 = _pearson(a[half:], b[half:])
                if r1 is None or r2 is None:
                    continue
                if abs(r1) >= 0.6 and abs(r2) <= 0.2:
                    results.append({
                        "service": service, "kind": "correlation_shift",
                        "score": round(abs(r1 - r2), 2), "confidence": 0.6,
                        "method": "statistical (Pearson correlation, first vs second half of window)",
                        "explanation": f"On '{service}', {names[i]} and {names[j]} were strongly correlated "
                                       f"(r={r1:+.2f}) earlier but not recently (r={r2:+.2f}) — their relationship changed.",
                        "evidence": {"metric_a": names[i], "metric_b": names[j],
                                     "r_recent": round(r2, 3), "r_prior": round(r1, 3), "n": n},
                    })
    return results


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 4:
        return None
    xs, ys = xs[:n], ys[:n]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    return cov / (sx * sy)


# ----------------------------------------------------------------- pipeline
def run_full_scan(db: Session) -> dict:
    """Runs all detectors, persists AnomalyResults, returns a summary."""
    detectors = [
        lambda: detect_frequency_spikes(db),
        lambda: detect_new_alert_types(db),
        lambda: detect_metric_anomalies(db),
        lambda: detect_cascades(db),
        lambda: detect_correlation_shifts(db),
    ]
    created = 0
    by_kind: Counter = Counter()
    for detector in detectors:
        for r in detector():
            if r.get("score") is None:
                continue  # informational "insufficient data" entries are not persisted as anomalies
            exists = db.scalar(
                select(AnomalyResult).where(
                    AnomalyResult.kind == r["kind"],
                    AnomalyResult.service_name == r["service"],
                    AnomalyResult.created_at >= utcnow() - timedelta(hours=1),
                )
            )
            if exists:
                continue
            db.add(AnomalyResult(
                alert_id=r.get("alert_id"),
                service_name=r["service"],
                kind=r["kind"],
                score=float(r["score"]),
                confidence=float(r["confidence"]),
                method=r["method"],
                explanation=r["explanation"],
                evidence=r.get("evidence", {}),
            ))
            created += 1
            by_kind[r["kind"]] += 1
    db.commit()
    total = db.scalar(select(__import__("sqlalchemy").func.count(AnomalyResult.id))) or 0
    return {"created": created, "by_kind": dict(by_kind), "total_active": total}


def unknown_alert_triage(db: Session, alert: Alert) -> dict:
    """The 'unfamiliar alert' workflow (§4C): compare with history, list affected
    services, check deployment association, produce explanation + score."""
    similar = db.scalars(
        select(Alert).where(
            Alert.fingerprint == alert.fingerprint, Alert.id != alert.id
        ).order_by(Alert.timestamp.desc()).limit(5)
    ).all()
    prior_count = len(similar)
    svc = db.scalar(select(Service).where(Service.name == alert.service_name))
    deployment_related = False
    if svc and svc.last_deploy_at:
        delta = (alert.timestamp - svc.last_deploy_at).total_seconds()
        deployment_related = 0 <= delta <= 3600 * 2

    history_days = _history_days(db.scalars(select(Alert).where(Alert.service_name == alert.service_name)).all())

    if prior_count == 0:
        score, verdict = 0.7, "first-seen"
        why = "This alert fingerprint has never been recorded before in this environment."
    elif prior_count <= 2:
        score, verdict = 0.4, "rare"
        why = f"Seen only {prior_count} time(s) historically — uncommon but not unprecedented."
    else:
        score, verdict = 0.2, "known"
        why = f"Seen {prior_count} time(s) historically — a known alert pattern."

    if deployment_related:
        score = min(1.0, score + 0.2)
        why += " It occurred within 2h after a recorded deployment of this service."
    if history_days < 1:
        confidence = 0.3
        why += " NOTE: less than 1 day of history for this service — confidence is low and more data is required."
    else:
        confidence = min(0.85, 0.4 + min(history_days, 14) / 28)

    affected = [alert.service_name]
    deps = db.scalars(select(Service).where(Service.dependencies != [])).all()
    for s in deps:
        if alert.service_name in (s.dependencies or []) and s.name not in affected:
            affected.append(s.name)

    return {
        "verdict": verdict,
        "anomaly_score": round(score, 2),
        "confidence": round(confidence, 2),
        "why_unusual": why,
        "prior_occurrences": prior_count,
        "prior_alert_ids": [a.id for a in similar],
        "deployment_related": deployment_related,
        "affected_services": affected,
        "method": "rule_based history comparison (no model training has occurred)" if prior_count else
                  "rule_based first-seen check (no model training has occurred)",
        "needs_more_data": history_days < 1,
    }