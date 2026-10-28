# AlertIQ

AI-powered intelligent incident monitoring and decision-support platform.

FastAPI backend · React + Vite + TypeScript + Tailwind CSS frontend · SQLite (swap to PostgreSQL via `DATABASE_URL`).

## What it does

- **Ingests alerts** from your monitoring stack: REST API, batch endpoint, generic webhooks (token-authenticated), CSV upload, Prometheus, Datadog, GitHub CI.
- **Correlates alerts into incidents** using time proximity, service dependency links, and alert-signature similarity — with causal hypotheses explicitly labeled as hypotheses.
- **Detects anomalies** without overclaiming: rolling z-score frequency spikes, first-seen alert types, metric z-scores, Isolation Forest (only when ≥ 50 samples exist), cascade detection across services, and metric-correlation shifts. Every result states the exact method used and the amount of history behind it.
- **Analyzes alerts/incidents** with a pluggable LLM (OpenAI / Anthropic / Gemini / any OpenAI-compatible endpoint) — or an honest local rule-based engine when no provider is configured. The LLM only sees system-computed context; untrusted alert text and web content are delimited against prompt injection; it is forbidden from inventing logs or metrics.
- **Researches unfamiliar problems** on the web (Tavily / Serper) and returns ranked, relevance-filtered sources with confirmed-vs-possibility labeling.
- **Recommends ranked actions** per incident with rationale, evidence, benefit, risk, difficulty, and human-approval flags. The AI is read-only: it never executes remediation.
- **Learns from history**: user-verified resolutions are surfaced as evidence for future matching incidents; feedback is stored for evaluation (no silent auto-retraining).
- **Realtime updates** via an authenticated WebSocket feed.

## Repository layout

```
backend/
  app/
    main.py               FastAPI app, CORS, demo-mode seed hook
    config.py             env-driven settings (pydantic-settings)
    database.py           SQLAlchemy engine/session
    models.py             Users, Alerts, Incidents, Metrics, Anomalies, AIAnalyses,
                          Recommendations, Integrations, Resolutions, Feedback, Chat
    schemas.py            Pydantic request/response models
    security.py           PBKDF2 passwords, HS256 tokens, Fernet credential vault, rate limiting
    seed.py               DEMO_MODE sample data (all prefixed "demo-")
    routers/              auth, alerts, incidents, services, dashboard, ai, integrations, webhooks, feedback, ws
    services/             normalizer, ingest, correlation, anomaly, llm, analysis,
                          research, recommendations, assistant, integrations
  tests/                  pytest suite (ingest, CSV, anomaly, correlation, AI, API/security)
frontend/
  src/
    pages/                Overview, Alerts, Incidents, Services, Assistant, Anomalies, Integrations, Login
    components/           Layout (sidebar), UI primitives (states, badges)
    api.ts, auth.tsx      typed API client + auth context
```

## Setup

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # then edit: SECRET_KEY is required, add LLM/search keys if desired
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev                # http://localhost:5173 (proxies /api to :8000)
npm run build              # production build
```

### Demo mode

Set `DEMO_MODE=true` in `backend/.env` and start with an empty database: clearly-labeled `demo-*` sample data is seeded (baseline history, a frequency spike, first-seen alert types, a correlated cascade, one resolved incident with a verified resolution). Demo mode never touches a database that already contains data.

## Environment variables (backend/.env)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL (default SQLite; PostgreSQL supported) |
| `SECRET_KEY` | **Required in production** — JWT signing + credential-encryption fallback |
| `ENCRYPTION_KEY` | Optional dedicated Fernet key for stored credentials |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `DEMO_MODE` | `true` seeds sample data into an empty DB only |
| `LLM_PROVIDER` | `none` \| `openai` \| `anthropic` \| `gemini` |
| `LLM_API_KEY` | Provider key — **backend only, never sent to the frontend** |
| `LLM_MODEL` / `LLM_BASE_URL` | Optional model override / OpenAI-compatible endpoint |
| `SEARCH_PROVIDER` | `none` \| `tavily` \| `serper` |
| `SEARCH_API_KEY` | Web-research API key |
| `CORRELATION_WINDOW_MINUTES`, `ANOMALY_IFOREST_MIN_SAMPLES`, `ANOMALY_SCAN_HISTORY_DAYS`, `RATE_LIMIT_PER_MINUTE` | Tuning |

Generate an encryption key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## Tests

```bash
cd backend
python -m pytest
```

Covers: alert normalization/validation/dedup, CSV import validation, anomaly detection (spikes, first-seen, z-score, Isolation-Forest gating, cascades), incident correlation + dependency chains, AI analysis honesty (local engine labeling, information gaps, prompt-injection delimiting), recommendations, webhook auth, credential encryption at rest, rate limiting, 404/401 handling.

## API overview (see /docs for full schemas)

- `POST /api/auth/register|login` — JWT auth
- `POST /api/alerts` · `POST /api/alerts/batch` · `POST /api/alerts/import-csv` · `GET /api/alerts` (filters: severity, service, status, search, hours) · `GET /api/alerts/export.csv`
- `PATCH /api/alerts/{id}/status` · `POST /api/alerts/{id}/analyze` · `GET /api/alerts/{id}/history`
- `GET/POST /api/incidents` · `PATCH /api/incidents/{id}/status` · `GET /api/incidents/{id}/timeline|alerts|analysis|recommendations` · `POST /api/incidents/{id}/analyze|link-alerts`
- `GET /api/services/health` · `POST /api/services` · `GET/POST /api/services/{name}/metrics` · `POST /api/services/{id}/deploy`
- `GET /api/dashboard/overview`
- `GET /api/ai/anomalies` · `POST /api/ai/anomalies/scan` · `POST /api/ai/anomalies/{id}/explain` · `PATCH /api/ai/anomalies/{id}/status`
- `POST /api/ai/ask` · `POST /api/ai/research` · `GET /api/ai/chat/history` · `GET /api/ai/meta`
- `CRUD /api/integrations` · `POST /api/integrations/{id}/test|sync` · `GET /api/integrations/{id}/events`
- `POST /api/webhooks/{token}` — token-authenticated generic ingestion
- `POST /api/resolutions` · `POST /api/feedback`
- `WS /ws?token=<jwt>` — realtime alert/incident feed

## Merging with an existing AlertIQ codebase

This build was produced without access to the original repository (it was not attached to the session). To merge into your repo:

1. **Backend**: keep your existing `app/` and add the `services/` and missing `routers/` modules; the API contract is additive — every router lives under `/api/*` and does not modify existing endpoint paths you already have. Drop `models.py` tables in via the same `Base.metadata.create_all` bootstrap (SQLite auto-migrates by adding tables; for PostgreSQL run Alembic or create the new tables).
2. **Config**: merge `config.py` fields into your settings class; keep your existing `SECRET_KEY`/`DATABASE_URL` values.
3. **Frontend**: pages under `src/pages` are self-contained; keep your router and add routes pointing at them. `api.ts` is a thin fetch client — replace it with your existing client if you have one, keeping the endpoint paths.
4. If your existing project already has endpoints with the same paths but different payloads, keep yours and rename the new ones (e.g. `/api/v2/alerts`) — the new routers are isolated files.

## Honest limitations

- **No LLM provider configured → no LLM calls.** Analysis falls back to the rule-based engine and says so in every response (`engine: local_rules`).
- **No search provider configured → web research disabled** (HTTP 503 with setup instructions, not fake results).
- **Isolation Forest runs only with ≥ 50 samples** per series (configurable); below that, z-scores/insufficient-data labels are used.
- **Correlation ≠ causation**: causal chains are always labeled as hypotheses.
- **Read-only AI**: nothing is executed automatically; destructive actions always carry a human-approval flag.
- SQLite is fine for single-node use; for multi-user production use PostgreSQL and run Alembic migrations.
- The Datadog/Prometheus connectors map the most common fields; enrich mappings to fit your alert taxonomy.
