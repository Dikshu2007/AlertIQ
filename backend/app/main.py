import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import Base, engine
from app.routers import ai, alerts, auth, dashboard, feedback, incidents, integrations, services, webhooks, ws

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="AlertIQ API",
    version="1.0.0",
    description="AI-powered incident monitoring and decision-support platform.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logging.getLogger("alertiq.api").exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


Base.metadata.create_all(bind=engine)

# demo mode: seed clearly-labeled sample data ONLY into an empty database
if settings.demo_mode:
    from app.seed import seed_if_empty
    seed_if_empty()

app.include_router(auth.router)
app.include_router(alerts.router)
app.include_router(incidents.router)
app.include_router(services.router)
app.include_router(dashboard.router)
app.include_router(ai.router)
app.include_router(integrations.router)
app.include_router(webhooks.router)
app.include_router(feedback.router)
app.include_router(ws.router)


@app.get("/api/meta", tags=["meta"])
def root_meta():
    from app.services import llm, research
    return {
        "app": settings.app_name, "version": "1.0.0", "demo_mode": settings.demo_mode,
        "llm_engine": llm.engine_name(), "web_research_available": research.available(),
    }


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}
