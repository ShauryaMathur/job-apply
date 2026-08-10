"""
Shared FastAPI dependencies.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Job


async def get_job_or_404(job_id: str, db: AsyncSession = Depends(get_db)) -> Job:
    """Fetch a job by job_id or raise 404."""
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job
