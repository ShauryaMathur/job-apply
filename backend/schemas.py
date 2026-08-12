"""
Pydantic schemas for API request/response models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Job schemas
# ---------------------------------------------------------------------------

class JobBase(BaseModel):
    job_id: str
    title: str
    company: str
    location: Optional[str] = None
    link: str
    description: Optional[str] = None
    role_category: str
    source: str = "indeed"


class JobCreate(JobBase):
    posted_at: Optional[datetime] = None
    scraped_at: Optional[datetime] = None


class JobUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    match_score: Optional[float] = None
    h1b_likely: Optional[bool] = None
    h1b_notes: Optional[str] = None
    resume_file: Optional[str] = None
    cover_letter_file: Optional[str] = None
    latex_content: Optional[str] = None
    cover_letter_latex: Optional[str] = None
    company_address: Optional[str] = None
    hiring_manager: Optional[str] = None


class JobOut(JobBase):
    id: UUID
    posted_at: Optional[datetime] = None
    scraped_at: Optional[datetime] = None
    match_score: Optional[float] = None
    h1b_likely: Optional[bool] = None
    h1b_notes: Optional[str] = None
    status: str = "new"
    resume_file: Optional[str] = None
    cover_letter_file: Optional[str] = None
    email_file: Optional[str] = None
    s3_resume_url: Optional[str] = None
    s3_cover_letter_url: Optional[str] = None
    latex_content: Optional[str] = None
    cover_letter_latex: Optional[str] = None
    company_address: Optional[str] = None
    hiring_manager: Optional[str] = None
    deleted_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    total: int
    jobs: List[JobOut]


# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------

class RoleConfig(BaseModel):
    """Per-role search configuration override."""
    enabled: bool = True
    count: int = Field(default=10, ge=1, le=100)


class PipelineTriggerRequest(BaseModel):
    """Optional parameters to customize a pipeline run. Overrides config.yaml values."""
    source: Optional[str] = Field(default=None, description="Job source: jobright | indeed")
    hours_old: Optional[int] = Field(default=None, ge=1, le=72, description="Max age of jobs in hours")
    roles: Optional[dict[str, RoleConfig]] = Field(default=None, description="Per-role enable/count overrides (indeed only)")


class PipelineTriggerResponse(BaseModel):
    run_id: str
    message: str = "Pipeline started"


class PipelineRunOut(BaseModel):
    id: UUID
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str
    jobs_found: int = 0
    jobs_ranked: int = 0
    resumes_generated: int = 0
    error: Optional[str] = None

    model_config = {"from_attributes": True}


class PipelineRunListResponse(BaseModel):
    runs: List[PipelineRunOut]


# ---------------------------------------------------------------------------
# Log schemas
# ---------------------------------------------------------------------------

class PipelineLogOut(BaseModel):
    id: UUID
    run_id: Optional[UUID] = None
    timestamp: datetime
    level: str
    agent: Optional[str] = None
    message: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Stats schema
# ---------------------------------------------------------------------------

class StatsResponse(BaseModel):
    total_jobs: int
    by_category: dict[str, int]
    by_status: dict[str, int]
    h1b_likely_count: int
    resumes_generated: int
    applied_count: int
    interview_count: int


# ---------------------------------------------------------------------------
# Tools schemas (manual job URL ingestion + live LaTeX compilation)
# ---------------------------------------------------------------------------

class IngestUrlRequest(BaseModel):
    url: Optional[str] = None
    role_category: str = "backend"
    description: Optional[str] = None  # manual override — skips scraping when provided
    source: Optional[str] = None       # explicit source override; auto-detected from URL if omitted


class JobInfoOut(BaseModel):
    title: str
    company: str
    location: Optional[str] = None
    description: str
    h1b_likely: Optional[bool] = None
    seniority: Optional[str] = None
    key_skills: List[str] = []


class IngestUrlResponse(BaseModel):
    job_id: str
    job_info: JobInfoOut
    latex: str
    pdf_base64: Optional[str] = None
    compile_error: Optional[str] = None


class CompileLatexRequest(BaseModel):
    tex_content: str


class CompileLatexResponse(BaseModel):
    pdf_base64: str
    size: int
