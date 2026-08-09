"""
Pipeline router - trigger job search pipeline and stream logs via SSE.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from backend.config import get_full_config
from backend.database import AsyncSessionFactory, get_db
from backend.models import PipelineLog, PipelineRun
from backend.schemas import (
    PipelineLogOut,
    PipelineRunListResponse,
    PipelineRunOut,
    PipelineTriggerRequest,
    PipelineTriggerResponse,
)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
logger = structlog.get_logger("pipeline_router")

# Active SSE queues keyed by run_id
_active_queues: dict[str, asyncio.Queue] = {}

# Run IDs that have been requested to cancel
_cancelled_runs: set[str] = set()


def is_run_cancelled(run_id: str) -> bool:
    return run_id in _cancelled_runs


@router.post("/trigger", response_model=PipelineTriggerResponse)
async def trigger_pipeline(
    payload: PipelineTriggerRequest = PipelineTriggerRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Start a new pipeline run asynchronously.
    Returns run_id immediately; use /pipeline/{run_id}/logs for SSE stream.
    """
    run_id = str(uuid.uuid4())

    # Create pipeline_runs record
    run = PipelineRun(id=run_id, status="running")
    db.add(run)
    await db.flush()

    logger.info("pipeline_triggered", run_id=run_id)

    # Create SSE queue for this run
    log_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _active_queues[run_id] = log_queue

    # Launch pipeline in background task
    import copy
    config = copy.deepcopy(get_full_config())

    # Apply payload overrides
    if payload.source is not None:
        config.setdefault("search", {})["source"] = payload.source

    if payload.hours_old is not None:
        config.setdefault("search", {})["hours_old"] = payload.hours_old

    if payload.roles:
        categories = config.get("search", {}).get("job_categories", [])
        enabled_categories = []
        for cat in categories:
            role_override = payload.roles.get(cat["name"])
            if role_override is None:
                enabled_categories.append(cat)
            elif role_override.enabled:
                cat = dict(cat)
                cat["count"] = role_override.count
                enabled_categories.append(cat)
            # else: role disabled, skip it
        config["search"]["job_categories"] = enabled_categories

    asyncio.create_task(
        _run_pipeline_background(
            run_id=run_id,
            config=config,
            log_queue=log_queue,
        )
    )

    return PipelineTriggerResponse(run_id=run_id, message="Pipeline started")


@router.post("/{run_id}/cancel")
async def cancel_pipeline(run_id: str, db: AsyncSession = Depends(get_db)):
    """Signal a running pipeline to stop after the current node completes."""
    stmt = select(PipelineRun).where(PipelineRun.id == run_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status != "running":
        raise HTTPException(status_code=400, detail=f"Run {run_id} is not running (status={run.status})")

    _cancelled_runs.add(run_id)
    logger.info("pipeline_cancel_requested", run_id=run_id)
    return {"run_id": run_id, "message": "Cancellation requested"}


@router.get("/runs", response_model=PipelineRunListResponse)
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List recent pipeline runs."""
    stmt = select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)
    result = await db.execute(stmt)
    runs = result.scalars().all()
    return PipelineRunListResponse(runs=[PipelineRunOut.model_validate(r) for r in runs])


@router.get("/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single pipeline run by ID."""
    stmt = select(PipelineRun).where(PipelineRun.id == run_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return PipelineRunOut.model_validate(run)


@router.get("/{run_id}/logs")
async def stream_pipeline_logs(run_id: str, db: AsyncSession = Depends(get_db)):
    """
    SSE endpoint that streams live pipeline log events for a run.

    If the run is still active, streams from the live queue.
    If the run is completed, streams historical logs from DB.

    Client usage:
        const es = new EventSource('/api/pipeline/{run_id}/logs');
        es.onmessage = (e) => console.log(JSON.parse(e.data));
    """
    # Check if run exists
    stmt = select(PipelineRun).where(PipelineRun.id == run_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    async def event_generator() -> AsyncGenerator[dict, None]:
        # If active queue exists, stream from it (live run)
        if run_id in _active_queues:
            queue = _active_queues[run_id]
            try:
                while True:
                    try:
                        entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                        if entry is None:  # Sentinel: done
                            yield {"data": json.dumps({"type": "done", "message": "Pipeline completed"})}
                            break
                        yield {"data": json.dumps(entry)}
                    except asyncio.TimeoutError:
                        # Heartbeat to keep connection alive
                        yield {"data": json.dumps({"type": "heartbeat"})}
            finally:
                # Clean up queue if consumer disconnects
                _active_queues.pop(run_id, None)
        else:
            # Historical: stream from DB logs
            log_stmt = (
                select(PipelineLog)
                .where(PipelineLog.run_id == run_id)
                .order_by(PipelineLog.timestamp.asc())
            )
            log_result = await db.execute(log_stmt)
            logs = log_result.scalars().all()
            for log_entry in logs:
                yield {
                    "data": json.dumps({
                        "timestamp": log_entry.timestamp.isoformat() if log_entry.timestamp else None,
                        "level": log_entry.level,
                        "agent": log_entry.agent,
                        "message": log_entry.message,
                    })
                }
            yield {"data": json.dumps({"type": "done", "message": "Historical logs complete"})}

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------

async def _run_pipeline_background(
    run_id: str,
    config: dict,
    log_queue: asyncio.Queue,
) -> None:
    """
    Background coroutine that runs the full pipeline.
    Runs independently of the HTTP request that triggered it.
    """
    try:
        from agents.orchestrator import JobPipelineOrchestrator

        orchestrator = JobPipelineOrchestrator(
            config=config,
            db_session_factory=AsyncSessionFactory,
            cancellation_check=lambda: is_run_cancelled(run_id),
        )

        await orchestrator.run(run_id=run_id, log_queue=log_queue)
        _cancelled_runs.discard(run_id)

    except Exception as e:
        logger.error("background_pipeline_error", run_id=run_id, error=str(e))
        # Push error to queue so SSE client sees it
        if run_id in _active_queues:
            await log_queue.put({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "error",
                "agent": "orchestrator",
                "message": f"Pipeline failed: {str(e)}",
            })
            await log_queue.put(None)  # Sentinel

    finally:
        # Always clean up queue
        _active_queues.pop(run_id, None)
