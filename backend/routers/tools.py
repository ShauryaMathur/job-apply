"""
Tools router — manual job URL ingestion and live LaTeX compilation.

POST /api/tools/ingest-url   Scrape any job URL → extract info → tailor resume → compile PDF
POST /api/tools/compile      Compile LaTeX → return PDF as base64
"""
from __future__ import annotations

import base64
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agents.job_extraction import clean_extracted_field, extract_job_info
from backend.config import get_full_config
from backend.database import get_db
from backend.pdfworker_client import call_pdfworker
from backend.scraper import scrape_url, source_from_url
from backend.schemas import (
    CompileLatexRequest,
    CompileLatexResponse,
    IngestUrlRequest,
    IngestUrlResponse,
    JobInfoOut,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.post("/ingest-url", response_model=IngestUrlResponse)
async def ingest_url(request: IngestUrlRequest, db: AsyncSession = Depends(get_db)):
    """
    Scrape any job URL → extract structured info via LLM →
    tailor resume LaTeX → compile to PDF.
    """
    # 1. Scrape (or use manually provided description)
    logger.info("ingest_url_start", url=request.url, manual_description=bool(request.description))
    if request.description and request.description.strip():
        page_text = request.description.strip()
        logger.info("ingest_url_using_manual_description", chars=len(page_text))
    elif request.url and request.url.strip():
        page_text = await scrape_url(request.url)
        if not page_text:
            raise HTTPException(status_code=422, detail="Could not extract content from the job URL")
    else:
        raise HTTPException(status_code=422, detail="Provide a job URL or paste a job description")

    # 2. Extract structured job info
    info = await extract_job_info(page_text)
    job_info = JobInfoOut(
        title=clean_extracted_field(info.get("title"), "Unknown Role"),
        company=clean_extracted_field(info.get("company"), "Unknown Company"),
        location=clean_extracted_field(info.get("location"), "") or None,
        description=info.get("description") or page_text[:4000],
        h1b_likely=info.get("h1b_likely"),
        seniority=info.get("seniority"),
        key_skills=info.get("key_skills") or [],
    )
    logger.info("job_info_extracted", title=job_info.title, company=job_info.company)

    # 3. Tailor resume via LLM (public API — no internal method exposure)
    from agents.resume_tailor import ResumeTailorAgent
    config = get_full_config()
    tailor = ResumeTailorAgent(config)
    try:
        latex = await tailor.tailor_from_description(
            title=job_info.title,
            company=job_info.company,
            description=job_info.description[:4000],
            category=request.role_category,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Resume tailoring failed: {e}")

    # 4. Score tailored resume vs JD (non-fatal — more accurate than base-resume score)
    match_score = None
    h1b_likely = job_info.h1b_likely
    h1b_notes = None
    try:
        from agents.ranker import RankerAgent, job_score_input
        ranker = RankerAgent(config)
        scored = await ranker.score_tailored_resume(
            job=job_score_input("scout-tmp", job_info.title, job_info.company, job_info.description, request.role_category),
            tailored_latex=latex,
        )
        match_score = scored.get("match_score")
        h1b_likely = scored.get("h1b_likely", h1b_likely)
        h1b_notes = scored.get("h1b_notes")
        logger.info("scout_job_scored", score=match_score, h1b=h1b_likely)
    except Exception as e:
        logger.warning("scout_scoring_failed", error=str(e))

    # 5. Persist job to DB so it shows up in the left pane and can be auto-saved
    from backend.models import Job
    job_id = f"manual-{uuid.uuid4().hex[:12]}"
    db_job = Job(
        job_id=job_id,
        title=job_info.title,
        company=job_info.company,
        location=job_info.location,
        link=request.url or "",
        description=job_info.description[:4000],
        role_category=request.role_category,
        source=request.source or (source_from_url(request.url) if request.url else "manual"),
        status="new",
        match_score=match_score,
        h1b_likely=h1b_likely,
        h1b_notes=h1b_notes,
        latex_content=latex,
    )
    db.add(db_job)
    await db.flush()
    logger.info("manual_job_persisted", job_id=job_id, title=job_info.title)

    # 6. Compile PDF (non-fatal — return latex even if compile fails)
    pdf_base64 = None
    compile_error = None
    try:
        pdf_bytes = await call_pdfworker(latex)
        pdf_base64 = base64.b64encode(pdf_bytes).decode()
        logger.info("preview_compiled", bytes=len(pdf_bytes))
    except Exception as e:
        compile_error = str(e)
        logger.warning("preview_compile_failed", error=compile_error)

    return IngestUrlResponse(
        job_id=job_id,
        job_info=job_info,
        latex=latex,
        pdf_base64=pdf_base64,
        compile_error=compile_error,
    )


@router.post("/compile", response_model=CompileLatexResponse)
async def compile_latex(request: CompileLatexRequest):
    """Compile LaTeX source to PDF and return as base64."""
    try:
        pdf_bytes = await call_pdfworker(request.tex_content)
        return CompileLatexResponse(
            pdf_base64=base64.b64encode(pdf_bytes).decode(),
            size=len(pdf_bytes),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
