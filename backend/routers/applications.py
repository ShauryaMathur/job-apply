"""
Applications router - manage application lifecycle and status transitions.
"""
from __future__ import annotations

from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.deps import get_job_or_404
from backend.models import Job
from backend.schemas import JobOut

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


@router.patch("/{job_id}/status", response_model=JobOut)
async def update_status(
    status: str,
    notes: Optional[str] = None,
    job: Job = Depends(get_job_or_404),
    db: AsyncSession = Depends(get_db),
):
    """Update job application status to any valid status."""
    if status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}",
        )

    job.status = status
    if notes:
        job.notes = notes

    await db.flush()
    await db.refresh(job)

    logger.info("job_status_updated", job_id=job.job_id, status=status)
    return JobOut.model_validate(job)
