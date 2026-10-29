from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- auth
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ------------------------------------------------------------------- alerts
class AlertIn(BaseModel):
    service_name: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    status: str = "open"
    source: str = "api"
    external_id: str | None = None
    labels: dict = Field(default_factory=dict)
    metric_values: dict = Field(default_factory=dict)
    timestamp: datetime | None = None


class AlertOut(ORMModel):
    id: int
    source: str
    external_id: str | None
    service_name: str
    title: str
    description: str
    severity: str
    status: str
    fingerprint: str
    labels: dict
    metric_values: dict
    timestamp: datetime

    @computed_field
    @property
    def severity_assessment(self) -> dict:
        assessment = (self.labels or {}).get("alertiq_severity_assessment")
        if not assessment:
            from app.services.severity_classifier import classify_severity
            assessment = classify_severity(self.title, self.description or "", self.metric_values or {})
        else:
            assessment = dict(assessment)
        # Support both the current UI's term and older bundles' field name.
        strength = assessment.get("match_strength", assessment.get("confidence", 0.0))
        assessment.setdefault("match_strength", strength)
        assessment.setdefault("confidence", strength)
        return assessment


class AlertListOut(BaseModel):
    items: list[AlertOut]
    total: int
    page: int
    page_size: int


class AlertStatusUpdate(BaseModel):
    status: str = Field(pattern="^(open|acknowledged|resolved)$")


class AlertListIn(BaseModel):
    """Envelope for batch ingest; raw dicts let the normalizer skip invalid rows."""
    alerts: list[dict] = Field(min_length=1, max_length=1000)


class CSVImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


# ------------------------------------------------------------------- incidents
class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    severity: str = "high"
    affected_services: list[str] = Field(default_factory=list)
    alert_ids: list[int] = Field(default_factory=list)


class IncidentOut(ORMModel):
    id: int
    title: str
    description: str
    severity: str
    status: str
    affected_services: list
    root_cause: str | None
    root_cause_confidence: float | None
    recommended_resolution: str | None
    correlation_evidence: dict
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class IncidentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(open|investigating|mitigated|resolved)$")


class IncidentListOut(BaseModel):
    items: list[IncidentOut]
    total: int


# ------------------------------------------------------------------- services
class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    dependencies: list[str] = Field(default_factory=list)
    criticality: str = "medium"


class ServiceOut(ORMModel):
    id: int
    name: str
    description: str
    dependencies: list
    criticality: str
    last_deploy_at: datetime | None


class ServiceHealthOut(BaseModel):
    service: ServiceOut
    open_alerts: int
    critical_alerts: int
    last_alert_at: datetime | None
    health: int  # 0-100
    metrics_summary: dict
    recent_alerts: list[AlertOut]


# ------------------------------------------------------------------- dashboard
class DashboardOverview(BaseModel):
    total_alerts: int
    active_incidents: int
    critical_alerts: int
    resolved_incidents: int
    resolved_alerts: int
    system_health: int
    alert_timeseries: list[dict]
    incident_trend: list[dict]
    severity_breakdown: list[dict]
    service_health: list[dict]
    recent_alerts: list[AlertOut]
    recent_incidents: list[IncidentOut]


# ------------------------------------------------------------------- anomalies
class AnomalyOut(ORMModel):
    id: int
    alert_id: int | None
    service_name: str
    kind: str
    score: float
    confidence: float
    method: str
    explanation: str
    evidence: dict
    status: str
    created_at: datetime


class AnomalyListOut(BaseModel):
    items: list[AnomalyOut]
    total: int


class AnomalyStatusUpdate(BaseModel):
    status: str = Field(pattern="^(new|reviewed|confirmed|false_positive)$")


# ------------------------------------------------------------------- assistant / research / analysis
class AssistantQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AssistantAnswer(BaseModel):
    answer: str
    engine: str
    context_used: dict
    sources: list
    caveats: list


class AnalysisOut(ORMModel):
    id: int
    alert_id: int | None
    incident_id: int | None
    summary: str
    severity_assessment: str
    possible_root_cause: str
    evidence: list
    observed_patterns: list
    possible_causes: list
    related_alerts: list
    affected_services: list
    investigation_steps: list
    recommended_resolution: str
    risk_level: str
    confidence: float
    consequences: str
    references: list
    information_gaps: list
    engine: str
    created_at: datetime


class ResearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    alert_id: int | None = None


# ------------------------------------------------------------------- integrations
class IntegrationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(prometheus|grafana|datadog|github|webhook|csv|rest)$")
    config: dict = Field(default_factory=dict)
    credentials: dict = Field(default_factory=dict)  # plaintext in request, encrypted at rest
    enabled: bool = True


class IntegrationUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    credentials: dict | None = None
    enabled: bool | None = None


class IntegrationOut(ORMModel):
    id: int
    name: str
    kind: str
    config: dict
    enabled: bool
    last_status: str | None
    last_error: str | None
    last_sync_at: datetime | None
    webhook_token: str | None
    created_at: datetime
    has_credentials: bool = False


# ------------------------------------------------------------------- feedback / resolutions
class FeedbackIn(BaseModel):
    incident_id: int | None = None
    recommendation_id: int | None = None
    anomaly_id: int | None = None
    rating: str = Field(pattern="^(helpful|unhelpful)$")
    comment: str = ""


class ResolutionIn(BaseModel):
    incident_id: int | None = None
    service_name: str = Field(min_length=1, max_length=120)
    problem_signature: str = Field(min_length=1, max_length=500)
    solution: str = Field(min_length=1)
    verified: bool = False


class ResolutionOut(ORMModel):
    id: int
    incident_id: int | None
    service_name: str
    problem_signature: str
    solution: str
    verified: bool
    created_at: datetime


# ------------------------------------------------------------------- meta
class MetaOut(BaseModel):
    app: str
    version: str
    demo_mode: bool
    llm_engine: str
    web_research_available: bool
