"""
Applications router - manage application lifecycle and status transitions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Job
from backend.schemas import JobOut, JobUpdate

router = APIRouter(prefix="/api/applications", tags=["applications"])
logger = structlog.get_logger("applications_router")

VALID_STATUSES = ["new", "reviewed", "applying", "applied", "rejected", "interview", "offer"]


@router.get("", response_model=List[JobOut])
async def list_applications(
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
):
    """List jobs that are being actively tracked as applications."""
    stmt = select(Job).where(Job.status != "new")
    if status:
        stmt = stmt.where(Job.status == status)
    stmt = stmt.order_by(Job.updated_at.desc())

    result = await db.execute(stmt)
    jobs = result.scalars().all()
    return [JobOut.model_validate(j) for j in jobs]


@router.post("/{job_id}/mark-applied", response_model=JobOut)
async def mark_applied(
    job_id: str,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Mark a job as applied."""
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job.status = "applied"
    if notes:
        job.notes = notes

    await db.flush()
    await db.refresh(job)

    logger.info("job_marked_applied", job_id=job_id)

    # Optionally sync status to Google Sheets
    # sheets_sync = SheetsSyncAgent(config)
    # await sheets_sync.update_job_status(job_id, "applied", notes=notes)

    return JobOut.model_validate(job)


@router.post("/{job_id}/mark-interview", response_model=JobOut)
async def mark_interview(
    job_id: str,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Mark a job as interview scheduled."""
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job.status = "interview"
    if notes:
        job.notes = notes

    await db.flush()
    await db.refresh(job)

    logger.info("job_marked_interview", job_id=job_id)
    return JobOut.model_validate(job)


@router.post("/{job_id}/mark-rejected", response_model=JobOut)
async def mark_rejected(
    job_id: str,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Mark a job as rejected."""
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job.status = "rejected"
    if notes:
        job.notes = notes

    await db.flush()
    await db.refresh(job)

    logger.info("job_marked_rejected", job_id=job_id)
    return JobOut.model_validate(job)


@router.patch("/{job_id}/status", response_model=JobOut)
async def update_status(
    job_id: str,
    status: str,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Update job application status to any valid status."""
    if status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}",
        )

    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job.status = status
    if notes:
        job.notes = notes

    await db.flush()
    await db.refresh(job)

    logger.info("job_status_updated", job_id=job_id, status=status)
    return JobOut.model_validate(job)
