"""
LangGraph Orchestrator

Defines the multi-agent pipeline as a LangGraph state machine:
  START -> scrape_jobs -> rank_jobs -> sync_sheets -> END

Resume tailoring, cover letter, and email generation are NOT part of the
automatic pipeline. They are triggered on-demand per job via the
POST /api/jobs/{job_id}/generate endpoint when the user manually reviews
and decides to apply.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional, TypedDict

import structlog
from langgraph.graph import StateGraph, START, END

logger = structlog.get_logger(__name__)


def _parse_posted_at(value) -> "datetime | None":
    """Coerce posted_at to datetime — accepts datetime objects or ISO strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------
# Pipeline State
# ------------------------------------------------------------------

class PipelineState(TypedDict, total=False):
    """LangGraph state passed between pipeline nodes."""
    run_id: str
    config: dict
    jobs: list[dict]           # Raw jobs from scraper
    ranked_jobs: list[dict]    # Jobs with match_score + h1b
    generated_docs: list[dict] # Jobs with resume/cover letter paths
    synced_jobs: list[dict]    # Jobs after sheets sync
    logs: list[dict]           # Log entries: {timestamp, level, agent, message}
    error: Optional[str]
    jobs_found: int
    jobs_ranked: int
    resumes_generated: int


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------

class JobPipelineOrchestrator:
    """
    LangGraph-based multi-agent orchestrator for the job application pipeline.

    The pipeline:
    1. ScraperAgent: Fetch jobs from Indeed for all configured categories
    2. RankerAgent: Score jobs by relevance + H1B inference
    3. SheetsSyncAgent: Sync all data to Google Sheets + PostgreSQL

    Resume/cover letter/email generation is on-demand via generate_for_job().

    Supports SSE streaming of log events via an asyncio.Queue.
    """

    def __init__(self, config: dict, db_session_factory=None, cancellation_check=None):
        """
        Args:
            config: Full config dict (config.yaml + env)
            db_session_factory: Async SQLAlchemy session factory for DB writes
            cancellation_check: Optional callable() -> bool, returns True if run should stop
        """
        self.config = config
        self.db_session_factory = db_session_factory
        self._cancellation_check = cancellation_check or (lambda: False)
        self.log = structlog.get_logger("orchestrator")

        # Import agents here to avoid circular imports
        from agents.ranker import RankerAgent
        from agents.resume_tailor import ResumeTailorAgent
        from agents.cover_letter import CoverLetterAgent
        from agents.sheets_sync import SheetsSyncAgent

        source = config.get("search", {}).get("source", "jobright")
        if source == "indeed":
            from agents.scraper import ScraperAgent
            self.scraper = ScraperAgent(config)
        else:
            from agents.jobright_scraper import JobrightScraper
            self.scraper = JobrightScraper(config)
        self.ranker = RankerAgent(config)
        self.resume_tailor = ResumeTailorAgent(config)
        self.cover_letter = CoverLetterAgent(config)
        self.sheets_sync = SheetsSyncAgent(config)

        # SSE log queue: populated by pipeline, consumed by SSE endpoint
        self._log_queue: Optional[asyncio.Queue] = None
        self._run_id: Optional[str] = None

        # Build the LangGraph graph
        self._graph = self._build_graph()

    def _build_graph(self):
        """Construct the LangGraph state machine."""
        builder = StateGraph(PipelineState)

        builder.add_node("scrape_jobs", self._node_scrape_jobs)
        builder.add_node("rank_jobs", self._node_rank_jobs)
        builder.add_node("sync_sheets", self._node_sync_sheets)

        builder.add_edge(START, "scrape_jobs")
        builder.add_edge("scrape_jobs", "rank_jobs")
        builder.add_edge("rank_jobs", "sync_sheets")
        builder.add_edge("sync_sheets", END)

        return builder.compile()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        run_id: Optional[str] = None,
        log_queue: Optional[asyncio.Queue] = None,
    ) -> PipelineState:
        """
        Execute the full job pipeline.

        Args:
            run_id: Optional run ID (generated if not provided)
            log_queue: asyncio.Queue to stream log events to SSE clients

        Returns:
            Final pipeline state after all nodes complete.
        """
        self._run_id = run_id or str(uuid.uuid4())
        self._log_queue = log_queue

        self.log.info("pipeline_starting", run_id=self._run_id)
        await self._emit_log("info", "orchestrator", f"Pipeline started (run_id={self._run_id})")

        initial_state: PipelineState = {
            "run_id": self._run_id,
            "config": self.config,
            "jobs": [],
            "ranked_jobs": [],
            "generated_docs": [],
            "synced_jobs": [],
            "logs": [],
            "error": None,
            "jobs_found": 0,
            "jobs_ranked": 0,
            "resumes_generated": 0,
        }

        # Update pipeline_runs record to 'running'
        if self.db_session_factory:
            await self._db_update_run_status(self._run_id, "running")

        try:
            final_state = await self._graph.ainvoke(initial_state)

            await self._emit_log(
                "info", "orchestrator",
                f"Pipeline completed successfully. "
                f"Jobs found: {final_state.get('jobs_found', 0)}, "
                f"Ranked: {final_state.get('jobs_ranked', 0)}, "
                f"Resumes: {final_state.get('resumes_generated', 0)}"
            )

            if self.db_session_factory:
                final_status = "cancelled" if self._cancellation_check() else "completed"
                await self._db_update_run_status(
                    self._run_id, final_status,
                    jobs_found=final_state.get("jobs_found", 0),
                    jobs_ranked=final_state.get("jobs_ranked", 0),
                    resumes_generated=final_state.get("resumes_generated", 0),
                )

            return final_state

        except Exception as e:
            error_msg = str(e)
            self.log.error("pipeline_failed", run_id=self._run_id, error=error_msg)
            await self._emit_log("error", "orchestrator", f"Pipeline failed: {error_msg}")

            if self.db_session_factory:
                await self._db_update_run_status(self._run_id, "failed", error=error_msg)

            raise

        finally:
            # Signal SSE stream to end
            if self._log_queue:
                await self._log_queue.put(None)  # Sentinel value

    # ------------------------------------------------------------------
    # LangGraph Nodes
    # ------------------------------------------------------------------

    async def _node_scrape_jobs(self, state: PipelineState) -> PipelineState:
        """Node 1: Scrape jobs from configured source (jobright / indeed)."""
        source = self.config.get("search", {}).get("source", "jobright")
        await self._emit_log("info", "scraper", f"Starting job scraping from {source}...")

        try:
            jobs = await self.scraper.scrape_all_categories(
                log_callback=lambda msg: self._emit_log("info", "scraper", msg),
            )

            await self._emit_log(
                "info", "scraper",
                f"Scraping complete: found {len(jobs)} jobs"
            )

            # Persist jobs to DB
            if self.db_session_factory and jobs:
                await self._db_upsert_jobs(jobs)

            state["jobs"] = jobs
            state["jobs_found"] = len(jobs)

            # Log to pipeline_logs
            await self._db_log(state.get("run_id"), "info", "scraper",
                               f"Scraped {len(jobs)} jobs from {source}")

        except Exception as e:
            error_msg = f"Scraping failed: {str(e)}"
            self.log.error("scrape_node_error", error=error_msg)
            await self._emit_log("error", "scraper", error_msg)
            state["error"] = error_msg
            state["jobs"] = []
            state["jobs_found"] = 0

        return state

    async def _node_rank_jobs(self, state: PipelineState) -> PipelineState:
        """Node 2: Rank and score jobs by relevance."""
        if self._cancellation_check():
            await self._emit_log("warning", "orchestrator", "Pipeline cancelled before ranking")
            state["ranked_jobs"] = []
            state["jobs_ranked"] = 0
            return state

        jobs = state.get("jobs", [])
        if not jobs:
            await self._emit_log("warning", "ranker", "No jobs to rank, skipping")
            state["ranked_jobs"] = []
            state["jobs_ranked"] = 0
            return state

        await self._emit_log("info", "ranker", f"Ranking {len(jobs)} jobs...")

        try:
            ranked_jobs = await self.ranker.rank_jobs(
                jobs=jobs,
                log_callback=lambda msg: self._emit_log("info", "ranker", msg),
            )

            await self._emit_log(
                "info", "ranker",
                f"Ranking complete: {len(ranked_jobs)} jobs scored"
            )

            # Update DB with scores
            if self.db_session_factory and ranked_jobs:
                await self._db_update_scores(ranked_jobs)

            state["ranked_jobs"] = ranked_jobs
            state["jobs_ranked"] = len(ranked_jobs)

            await self._db_log(state.get("run_id"), "info", "ranker",
                               f"Ranked {len(ranked_jobs)} jobs")

        except Exception as e:
            error_msg = f"Ranking failed: {str(e)}"
            self.log.error("rank_node_error", error=error_msg)
            await self._emit_log("error", "ranker", error_msg)
            state["ranked_jobs"] = state.get("jobs", [])  # Unranked fallback
            state["jobs_ranked"] = 0

        return state

    async def generate_for_job(self, job_dict: dict) -> dict:
        """
        On-demand generation: tailor resume + cover letter + email for a single job.
        Called by POST /api/jobs/{job_id}/generate when user decides to apply.

        Args:
            job_dict: Job row from DB as a dict

        Returns:
            Updated job dict with resume_file, cover_letter_file, email_file, s3 URLs
        """
        job_id = job_dict.get("job_id")
        self.log.info("on_demand_generation", job_id=job_id)

        resume_error = None
        cover_error = None

        # Tailor resume + compile PDF
        try:
            job_dict = await self.resume_tailor.tailor_resume(job_dict)
        except Exception as e:
            resume_error = str(e)
            self.log.error("resume_tailor_failed", job_id=job_id, error=resume_error)
            job_dict["resume_file"] = None
            job_dict["s3_resume_url"] = None

        # Generate cover letter + email
        try:
            job_dict = await self.cover_letter.generate_cover_letter(job_dict)
        except Exception as e:
            cover_error = str(e)
            self.log.error("cover_letter_failed", job_id=job_id, error=cover_error)
            job_dict["cover_letter_file"] = None
            job_dict["email_file"] = None

        # Persist file paths to DB
        if self.db_session_factory:
            await self._db_update_docs([job_dict])

        if resume_error and cover_error:
            raise RuntimeError(
                f"Document generation failed for {job_id}: resume={resume_error}; cover_letter={cover_error}"
            )

        return job_dict

    async def _node_sync_sheets(self, state: PipelineState) -> PipelineState:
        """Node 3: Sync ranked job data to Google Sheets."""
        if self._cancellation_check():
            await self._emit_log("warning", "orchestrator", "Pipeline cancelled before sheets sync")
            state["synced_jobs"] = []
            return state

        ranked_jobs = state.get("ranked_jobs", [])
        all_jobs = ranked_jobs

        if not all_jobs:
            await self._emit_log("warning", "sheets_sync", "No jobs to sync")
            state["synced_jobs"] = []
            return state

        await self._emit_log("info", "sheets_sync", f"Syncing {len(all_jobs)} jobs to Google Sheets...")

        try:
            await self.sheets_sync.sync_jobs(
                jobs=all_jobs,
                log_callback=lambda msg: self._emit_log("info", "sheets_sync", msg),
            )

            state["synced_jobs"] = all_jobs

            await self._db_log(state.get("run_id"), "info", "sheets_sync",
                               f"Synced {len(all_jobs)} jobs to Google Sheets")

        except Exception as e:
            error_msg = f"Sheets sync failed: {str(e)}"
            self.log.error("sheets_sync_node_error", error=error_msg)
            await self._emit_log("error", "sheets_sync", error_msg)
            state["synced_jobs"] = []

        return state

    # ------------------------------------------------------------------
    # SSE Log Streaming
    # ------------------------------------------------------------------

    async def _emit_log(self, level: str, agent: str, message: str) -> None:
        """Emit a log entry to the SSE queue and structlog."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "agent": agent,
            "message": message,
        }

        # Structured logging
        log_fn = getattr(self.log, level, self.log.info)
        log_fn("pipeline_log", agent=agent, message=message)

        # Push to SSE queue if active
        if self._log_queue:
            try:
                await self._log_queue.put(entry)
            except Exception:
                pass  # Queue may be closed

    @staticmethod
    async def stream_logs(log_queue: asyncio.Queue) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields log entries from the queue.
        Ends when a None sentinel is received.

        Usage in FastAPI SSE endpoint:
            async for entry in JobPipelineOrchestrator.stream_logs(queue):
                yield entry
        """
        while True:
            entry = await log_queue.get()
            if entry is None:  # Sentinel: pipeline finished
                break
            yield entry

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    async def _db_update_run_status(
        self,
        run_id: str,
        status: str,
        jobs_found: int = 0,
        jobs_ranked: int = 0,
        resumes_generated: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Update pipeline_runs row."""
        if not self.db_session_factory:
            return
        try:
            from sqlalchemy import text
            async with self.db_session_factory() as session:
                if status == "running":
                    await session.execute(
                        text("""
                            INSERT INTO pipeline_runs (id, status, started_at)
                            VALUES (:id, 'running', NOW())
                            ON CONFLICT (id) DO UPDATE SET status = 'running'
                        """),
                        {"id": run_id},
                    )
                elif status in ("completed", "failed", "cancelled"):
                    await session.execute(
                        text("""
                            UPDATE pipeline_runs SET
                                status = :status,
                                completed_at = NOW(),
                                jobs_found = :jobs_found,
                                jobs_ranked = :jobs_ranked,
                                resumes_generated = :resumes_generated,
                                error = :error
                            WHERE id = :id
                        """),
                        {
                            "id": run_id,
                            "status": status,
                            "jobs_found": jobs_found,
                            "jobs_ranked": jobs_ranked,
                            "resumes_generated": resumes_generated,
                            "error": error,
                        },
                    )
                await session.commit()
        except Exception as e:
            self.log.error("db_run_update_error", error=str(e))

    async def _db_log(
        self, run_id: Optional[str], level: str, agent: str, message: str
    ) -> None:
        """Insert a row into pipeline_logs."""
        if not self.db_session_factory or not run_id:
            return
        try:
            from sqlalchemy import text
            async with self.db_session_factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO pipeline_logs (run_id, level, agent, message)
                        VALUES (:run_id, :level, :agent, :message)
                    """),
                    {"run_id": run_id, "level": level, "agent": agent, "message": message},
                )
                await session.commit()
        except Exception as e:
            self.log.error("db_log_error", error=str(e))

    async def _db_upsert_jobs(self, jobs: list[dict]) -> None:
        """Upsert scraped jobs into the jobs table."""
        if not self.db_session_factory:
            return
        try:
            from sqlalchemy import text
            async with self.db_session_factory() as session:
                for job in jobs:
                    await session.execute(
                        text("""
                            INSERT INTO jobs (
                                job_id, title, company, location, link,
                                description, role_category, source,
                                posted_at, scraped_at
                            ) VALUES (
                                :job_id, :title, :company, :location, :link,
                                :description, :role_category, :source,
                                :posted_at, NOW()
                            )
                            ON CONFLICT (job_id) DO UPDATE SET
                                title = EXCLUDED.title,
                                description = COALESCE(EXCLUDED.description, jobs.description),
                                updated_at = NOW()
                        """),
                        {
                            "job_id": job.get("job_id"),
                            "title": job.get("title"),
                            "company": job.get("company"),
                            "location": job.get("location"),
                            "link": job.get("link"),
                            "description": job.get("description"),
                            "role_category": job.get("role_category"),
                            "source": job.get("source", "indeed"),
                            "posted_at": _parse_posted_at(job.get("posted_at")),
                        },
                    )
                await session.commit()
        except Exception as e:
            self.log.error("db_upsert_jobs_error", error=str(e))

    async def _db_update_scores(self, jobs: list[dict]) -> None:
        """Update match_score, ranking details, and H1B fields for ranked jobs."""
        if not self.db_session_factory:
            return
        try:
            import json
            from sqlalchemy import text
            async with self.db_session_factory() as session:
                for job in jobs:
                    await session.execute(
                        text("""
                            UPDATE jobs SET
                                match_score = :match_score,
                                match_summary = :match_summary,
                                top_matching_skills = :top_matching_skills,
                                missing_critical_skills = :missing_critical_skills,
                                similarity_score = :similarity_score,
                                h1b_likely = :h1b_likely,
                                h1b_notes = :h1b_notes,
                                updated_at = NOW()
                            WHERE job_id = :job_id
                        """),
                        {
                            "job_id": job.get("job_id"),
                            "match_score": job.get("match_score"),
                            "match_summary": job.get("match_summary"),
                            "top_matching_skills": json.dumps(job.get("top_matching_skills", [])),
                            "missing_critical_skills": json.dumps(job.get("missing_critical_skills", [])),
                            "similarity_score": job.get("similarity_score"),
                            "h1b_likely": job.get("h1b_likely"),
                            "h1b_notes": job.get("h1b_notes"),
                        },
                    )
                await session.commit()
        except Exception as e:
            self.log.error("db_update_scores_error", error=str(e))

    async def _db_update_docs(self, jobs: list[dict]) -> None:
        """Update resume/cover letter file paths for jobs."""
        if not self.db_session_factory:
            return
        try:
            from sqlalchemy import text
            async with self.db_session_factory() as session:
                for job in jobs:
                    await session.execute(
                        text("""
                            UPDATE jobs SET
                                resume_file = :resume_file,
                                cover_letter_file = :cover_letter_file,
                                email_file = :email_file,
                                s3_resume_url = :s3_resume_url,
                                s3_cover_letter_url = :s3_cover_letter_url,
                                updated_at = NOW()
                            WHERE job_id = :job_id
                        """),
                        {
                            "job_id": job.get("job_id"),
                            "resume_file": job.get("resume_file"),
                            "cover_letter_file": job.get("cover_letter_file"),
                            "email_file": job.get("email_file"),
                            "s3_resume_url": job.get("s3_resume_url"),
                            "s3_cover_letter_url": job.get("s3_cover_letter_url"),
                        },
                    )
                await session.commit()
        except Exception as e:
            self.log.error("db_update_docs_error", error=str(e))
