from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    # naive UTC: SQLite has no tz-aware column type, so we store/compare naive UTC everywhere
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Service(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    dependencies: Mapped[list] = mapped_column(JSON, default=list)  # names of upstream services this one depends on
    criticality: Mapped[str] = mapped_column(String(20), default="medium")
    last_deploy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="api")  # api|webhook|prometheus|datadog|csv|grafana|github
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_name: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(20), index=True)  # critical|high|medium|low|info
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|acknowledged|resolved
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    metric_values: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|investigating|mitigated|resolved
    affected_services: Mapped[list] = mapped_column(JSON, default=list)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    alerts: Mapped[list["AlertIncidentLink"]] = relationship(back_populates="incident")


class AlertIncidentLink(Base):
    __tablename__ = "alert_incident_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), index=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    relation: Mapped[str] = mapped_column(String(20), default="correlated")  # primary|correlated

    incident: Mapped[Incident] = relationship(back_populates="alerts")


class Metric(Base):
    __tablename__ = "metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    service_name: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)  # cpu_usage, memory_usage, latency_p95_ms, error_rate, request_volume
    value: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AnomalyResult(Base):
    __tablename__ = "anomaly_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"), nullable=True)
    service_name: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    # frequency_spike | new_alert_type | metric_zscore | seasonal_deviation |
    # isolation_forest | correlation_shift | cascade_sequence
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(60))  # honest labeling: statistical | statistical+seasonal | isolation_forest | rule_based
    explanation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|reviewed|confirmed|false_positive
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"), nullable=True, index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text)
    severity_assessment: Mapped[str] = mapped_column(String(20))
    possible_root_cause: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)          # confirmed facts w/ alert ids
    observed_patterns: Mapped[list] = mapped_column(JSON, default=list)
    possible_causes: Mapped[list] = mapped_column(JSON, default=list)   # hypotheses
    related_alerts: Mapped[list] = mapped_column(JSON, default=list)
    affected_services: Mapped[list] = mapped_column(JSON, default=list)
    investigation_steps: Mapped[list] = mapped_column(JSON, default=list)
    recommended_resolution: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String(20), default="unknown")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    consequences: Mapped[str] = mapped_column(Text, default="")
    references: Mapped[list] = mapped_column(JSON, default=list)        # [{title,url,why}]
    information_gaps: Mapped[list] = mapped_column(JSON, default=list)
    engine: Mapped[str] = mapped_column(String(40))  # llm:<provider> | local_rules
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    expected_benefit: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(40), default="rules")  # llm|rules|historical
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))  # prometheus|grafana|datadog|github|webhook|csv|rest
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret config (endpoints, queries)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON {key: fernet(...)}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ok|error
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    webhook_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IntegrationEvent(Base):
    __tablename__ = "integration_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id"), index=True)
    level: Mapped[str] = mapped_column(String(20))  # info|error
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Resolution(Base):
    """Historical incident resolutions used as evidence for future analysis.

    Only VERIFIED resolutions (explicitly confirmed by a user) are used to
    influence future recommendations.
    """
    __tablename__ = "resolutions"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    service_name: Mapped[str] = mapped_column(String(120), index=True)
    problem_signature: Mapped[str] = mapped_column(String(500))
    solution: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verified_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    anomaly_id: Mapped[int | None] = mapped_column(ForeignKey("anomaly_results.id"), nullable=True)
    rating: Mapped[str] = mapped_column(String(20))  # helpful|unhelpful
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(20))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
