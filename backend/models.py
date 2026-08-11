"""
SQLAlchemy ORM models.
Mirrors the PostgreSQL schema defined in docker/init.sql.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(String(255), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    company = Column(String(255), nullable=False)
    location = Column(String(255))
    link = Column(Text, nullable=False)
    description = Column(Text)
    role_category = Column(String(50), nullable=False, index=True)  # backend | fullstack | aiml
    source = Column(String(50), default="indeed")
    posted_at = Column(DateTime(timezone=True))
    scraped_at = Column(DateTime(timezone=True), server_default=func.now())

    # Ranking
    match_score = Column(Float)
    match_summary = Column(Text)
    top_matching_skills = Column(Text)      # JSON array stored as text
    missing_critical_skills = Column(Text)  # JSON array stored as text
    similarity_score = Column(Float)
    h1b_likely = Column(Boolean)
    h1b_notes = Column(Text)

    # Status tracking
    status = Column(
        String(50),
        default="new",
        index=True,
    )  # new | reviewed | applying | applied | rejected | interview

    # Generated documents
    resume_file = Column(Text)
    cover_letter_file = Column(Text)
    email_file = Column(Text)
    s3_resume_url = Column(Text)
    s3_cover_letter_url = Column(Text)
    latex_content = Column(Text)
    cover_letter_latex = Column(Text)
    company_address = Column(Text)   # free-text e.g. "2400 Market St, Philadelphia, PA 19103"
    hiring_manager = Column(Text)    # extracted from job description or null
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Notes
    notes = Column(Text)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Job {self.job_id}: {self.title} @ {self.company}>"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    status = Column(String(50), default="running")  # running | completed | failed
    jobs_found = Column(Integer, default=0)
    jobs_ranked = Column(Integer, default=0)
    resumes_generated = Column(Integer, default=0)
    error = Column(Text)

    logs = relationship("PipelineLog", back_populates="run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<PipelineRun {self.id} status={self.status}>"


class PipelineLog(Base):
    __tablename__ = "pipeline_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("pipeline_runs.id"), index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    level = Column(String(20), default="info")  # info | warning | error
    agent = Column(String(100))
    message = Column(Text)

    run = relationship("PipelineRun", back_populates="logs")

    def __repr__(self) -> str:
        return f"<PipelineLog [{self.level}] {self.agent}: {self.message[:50]}>"
