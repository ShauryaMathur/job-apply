"""
Jobs router - CRUD endpoints for job listings.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.deps import get_job_or_404
from backend.models import Job
from backend.schemas import JobListResponse, JobOut, JobUpdate, StatsResponse
from backend.storage import get_storage

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = structlog.get_logger("jobs_router")


@router.get("", response_model=JobListResponse)
async def list_jobs(
    category: Optional[str] = Query(None, description="Filter by role_category"),
    status: Optional[str] = Query(None, description="Filter by status"),
    h1b_likely: Optional[bool] = Query(None, description="Filter by H1B likelihood"),
    min_score: Optional[float] = Query(None, description="Minimum match score"),
    search: Optional[str] = Query(None, description="Search in title or company"),
    has_latex: Optional[bool] = Query(None, description="Filter to jobs with generated LaTeX"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List all jobs with optional filters."""
    stmt = select(Job)

    if category:
        stmt = stmt.where(Job.role_category == category)
    if status:
        stmt = stmt.where(Job.status == status)
    if h1b_likely is not None:
        stmt = stmt.where(Job.h1b_likely == h1b_likely)
    if min_score is not None:
        stmt = stmt.where(Job.match_score >= min_score)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            Job.title.ilike(like) | Job.company.ilike(like)
        )
    stmt = stmt.where(Job.deleted_at.is_(None))
    if has_latex:
        stmt = stmt.where(Job.latex_content.isnot(None))

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate and order
    stmt = (
        stmt
        .order_by(Job.match_score.desc().nullslast(), Job.scraped_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    return JobListResponse(total=total, jobs=[JobOut.model_validate(j) for j in jobs])


@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Get aggregate statistics for the dashboard."""
    # Total jobs
    total = (await db.execute(select(func.count(Job.id)).where(Job.deleted_at.is_(None)))).scalar_one()

    # By category
    cat_result = await db.execute(
        select(Job.role_category, func.count(Job.id)).where(Job.deleted_at.is_(None)).group_by(Job.role_category)
    )
    by_category = {row[0]: row[1] for row in cat_result}

    # By status
    status_result = await db.execute(
        select(Job.status, func.count(Job.id)).where(Job.deleted_at.is_(None)).group_by(Job.status)
    )
    by_status = {row[0]: row[1] for row in status_result}

    # H1B likely
    h1b_count = (
        await db.execute(select(func.count(Job.id)).where(Job.deleted_at.is_(None)).where(Job.h1b_likely == True))
    ).scalar_one()

    # Resumes generated
    resumes = (
        await db.execute(select(func.count(Job.id)).where(Job.deleted_at.is_(None)).where(Job.resume_file.isnot(None)))
    ).scalar_one()

    applied = by_status.get("applied", 0)
    interviews = by_status.get("interview", 0)

    return StatsResponse(
        total_jobs=total,
        by_category=by_category,
        by_status=by_status,
        h1b_likely_count=h1b_count,
        resumes_generated=resumes,
        applied_count=applied,
        interview_count=interviews,
    )


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job: Job = Depends(get_job_or_404)):
    """Get a single job by its Indeed job_id."""
    return JobOut.model_validate(job)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    payload: JobUpdate,
    job: Job = Depends(get_job_or_404),
    db: AsyncSession = Depends(get_db),
):
    """Update job status, notes, or other fields."""

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    for field, value in update_data.items():
        setattr(job, field, value)

    await db.flush()
    await db.refresh(job)

    logger.info("job_updated", job_id=job.job_id, fields=list(update_data.keys()))
    return JobOut.model_validate(job)


@router.get("/{job_id}/resume")
async def download_resume(
    job: Job = Depends(get_job_or_404),
):
    """
    Download the tailored resume PDF for a job.
    Tries local file first, falls back to S3.
    """

    # Try local file
    if job.resume_file:
        local_path = Path(job.resume_file)
        if local_path.exists():
            download_name = local_path.name  # e.g. "Shaurya Mathur - Software Engineer.pdf"
            if local_path.suffix.lower() == ".pdf":
                return FileResponse(
                    path=str(local_path),
                    media_type="application/pdf",
                    filename=download_name,
                )
            # PDF compilation failed — serve the raw .tex so it's still accessible
            return FileResponse(
                path=str(local_path),
                media_type="text/plain",
                filename=download_name,
                headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
            )

    # Try S3 pre-signed URL redirect
    if job.s3_resume_url:
        from fastapi.responses import RedirectResponse
        storage = get_storage()
        # Extract the S3 key from the stored URL: https://{bucket}.s3.{region}.amazonaws.com/{key}
        s3_key = job.s3_resume_url.split(".amazonaws.com/", 1)[-1]
        presigned = await storage.generate_presigned_url(s3_key)
        if presigned:
            return RedirectResponse(url=presigned)
        return RedirectResponse(url=job.s3_resume_url)

    raise HTTPException(
        status_code=404,
        detail="Resume not yet generated for this job",
    )


@router.get("/{job_id}/cover-letter")
async def download_cover_letter(
    job: Job = Depends(get_job_or_404),
):
    """Download the cover letter for a job."""

    if job.cover_letter_file:
        local_path = Path(job.cover_letter_file)
        if local_path.exists():
            suffix = local_path.suffix.lower()
            media_type = "text/plain" if suffix == ".txt" else "application/pdf"
            download_name = local_path.name
            return FileResponse(
                path=str(local_path),
                media_type=media_type,
                filename=download_name,
                headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
            )

    if job.s3_cover_letter_url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=job.s3_cover_letter_url)

    raise HTTPException(
        status_code=404,
        detail="Cover letter not yet generated for this job",
    )


@router.get("/{job_id}/email")
async def download_email(
    job: Job = Depends(get_job_or_404),
):
    """Download the outreach email for a job."""

    if job.email_file:
        local_path = Path(job.email_file)
        if local_path.exists():
            download_name = local_path.name
            return FileResponse(
                path=str(local_path),
                media_type="text/plain",
                filename=download_name,
                headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
            )

    raise HTTPException(
        status_code=404,
        detail="Outreach email not yet generated for this job",
    )


@router.post("/{job_id}/generate/resume", response_model=JobOut)
async def generate_resume(
    job: Job = Depends(get_job_or_404),
    db: AsyncSession = Depends(get_db),
):
    """Generate tailored LaTeX resume for a job and save to DB. PDF is compiled in the editor."""

    from backend.config import get_full_config
    from agents.resume_tailor import ResumeTailorAgent

    config = get_full_config()

    try:
        tailor = ResumeTailorAgent(config)
        latex = await tailor.tailor_from_description(
            title=job.title,
            company=job.company,
            description=job.description or "",
            category=job.role_category,
        )
    except Exception as e:
        logger.error("resume_tailor_failed", job_id=job.job_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Resume generation failed: {e}")

    job.latex_content = latex

    # Score tailored resume vs JD (non-fatal — always rescore with the new tailored output)
    try:
        from agents.ranker import RankerAgent, job_score_input
        ranker = RankerAgent(config)
        scored = await ranker.score_tailored_resume(
            job=job_score_input(job.job_id, job.title, job.company, job.description, job.role_category),
            tailored_latex=latex,
        )
        job.match_score = scored.get("match_score")
        if job.h1b_likely is None:
            job.h1b_likely = scored.get("h1b_likely")
            job.h1b_notes = scored.get("h1b_notes")
    except Exception as e:
        logger.warning("generate_resume_scoring_failed", job_id=job.job_id, error=str(e))

    await db.flush()
    await db.refresh(job)

    return JobOut.model_validate(job)


@router.post("/{job_id}/rescore", response_model=JobOut)
async def rescore_job(
    job: Job = Depends(get_job_or_404),
    db: AsyncSession = Depends(get_db),
):
    """Rescore a job's match score against its current latex_content."""
    if not job.latex_content:
        raise HTTPException(status_code=400, detail="No LaTeX content to score against")

    from backend.config import get_full_config
    from agents.ranker import RankerAgent, job_score_input

    config = get_full_config()
    try:
        ranker = RankerAgent(config)
        scored = await ranker.score_tailored_resume(
            job=job_score_input(job.job_id, job.title, job.company, job.description, job.role_category),
            tailored_latex=job.latex_content,
        )
        job.match_score = scored.get("match_score")
    except Exception as e:
        logger.error("rescore_failed", job_id=job.job_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Rescore failed: {e}")

    await db.flush()
    await db.refresh(job)
    logger.info("job_rescored", job_id=job.job_id, score=job.match_score)
    return JobOut.model_validate(job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job: Job = Depends(get_job_or_404),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a job by setting deleted_at."""
    from datetime import datetime, timezone
    job.deleted_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("job_soft_deleted", job_id=job.job_id)


@router.post("/{job_id}/generate/cover-letter", response_model=JobOut)
async def generate_cover_letter_doc(
    job: Job = Depends(get_job_or_404),
    db: AsyncSession = Depends(get_db),
):
    """Generate cover letter and outreach email for a specific job."""

    from backend.config import get_full_config
    from agents.cover_letter import CoverLetterAgent

    config = get_full_config()
    job_dict = {
        "job_id": job.job_id,
        "title": job.title,
        "company": job.company,
        "description": job.description,
        "role_category": job.role_category,
        "match_score": job.match_score,
    }

    try:
        job_dict = await CoverLetterAgent(config).generate_cover_letter(job_dict)
    except Exception as e:
        logger.error("cover_letter_failed", job_id=job.job_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Cover letter generation failed: {e}")

    for field in ("cover_letter_file", "email_file", "s3_cover_letter_url",
                  "cover_letter_latex", "company_address", "hiring_manager"):
        val = job_dict.get(field)
        if val is not None:
            setattr(job, field, val)
    await db.flush()
    await db.refresh(job)

    return JobOut.model_validate(job)
