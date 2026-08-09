"""
FastAPI application entry point.

Routes:
    POST /api/pipeline/trigger     - Start pipeline run
    GET  /api/pipeline/{run_id}/logs - SSE log stream
    GET  /api/pipeline/runs        - List recent runs
    GET  /api/jobs                 - List jobs with filters
    PATCH /api/jobs/{job_id}       - Update job status/notes
    GET  /api/jobs/{job_id}/resume - Download resume PDF
    GET  /api/jobs/{job_id}/cover-letter - Download cover letter
    GET  /api/jobs/stats           - Dashboard aggregate stats
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Logging config (must happen before any other imports use structlog)
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer() if os.environ.get("LOG_FORMAT") != "json"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), os.environ.get("LOG_LEVEL", "INFO"))
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("main")

# ---------------------------------------------------------------------------
# Ensure project root is on PYTHONPATH so agents/ can be imported from backend
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    from backend.config import get_settings
    from backend.database import engine, Base

    settings = get_settings()
    logger.info("startup", debug=settings.debug, database_url=settings.database_url[:30] + "...")

    # Create tables if they don't exist (init.sql handles this in Docker,
    # but this allows running locally without Docker too)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database_tables_ready")
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS latex_content TEXT"
            ))
            await conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE"
            ))
        logger.info("database_migration_ok")
    except Exception as e:
        logger.warning("db_create_tables_failed", error=str(e))

    yield

    # Cleanup
    await engine.dispose()
    logger.info("shutdown")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Job Apply API",
    description="Multi-agent job finder and application automation system",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS - allow frontend dev server and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://frontend:3000",
        os.environ.get("FRONTEND_URL", ""),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from backend.routers import jobs, applications, pipeline, tools

app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(pipeline.router)
app.include_router(tools.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "service": "job-apply-backend"}


@app.get("/", tags=["health"])
async def root():
    return {
        "service": "Job Apply API",
        "version": "1.0.0",
        "docs": "/docs",
    }


# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
