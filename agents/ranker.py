"""
Job Ranker Agent

Uses ChromaDB (local) + sentence-transformers for semantic similarity,
then LLM (gemini-2.0-flash via LiteLLM) for scoring and H1B inference.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import chromadb
import structlog
from chromadb.config import Settings
from fastembed import TextEmbedding

from agents.base import BaseAgent
from agents.constants import RESUME_CATEGORY_MAP
from agents.utils import strip_latex_to_text

logger = structlog.get_logger(__name__)

# ChromaDB collection names
COLLECTION_RESUMES = "resumes"
COLLECTION_JOBS = "job_descriptions"

COMBINED_SCORE_PROMPT = """You are an expert ATS recruiter and H1B visa analyst.

Given a candidate resume and job description, return a single JSON object covering:
1. Resume-job match score and analysis
2. H1B sponsorship likelihood

SCORING weights: skills overlap 50%, experience level 20%, domain relevance 15%, education 15%.

H1B signals —
Positive: "visa sponsorship", "sponsorship available", FAANG/big tech, consulting firms (Tata/Infosys/Wipro), staffing agencies, "will sponsor qualified candidates"
Negative: "no sponsorship", "US citizen or permanent resident only", "will not sponsor", "must have work authorization"
Neutral: no mention at all (lean slightly negative)

Return ONLY valid JSON:
{
  "score": <integer 0-100>,
  "top_matching_skills": ["skill1", "skill2", "skill3"],
  "missing_critical_skills": ["skill1", "skill2"],
  "summary": "2-3 sentence match explanation",
  "h1b_likely": true/false/null,
  "h1b_notes": "1-2 sentence explanation",
  "confidence": "high/medium/low"
}"""


class RankerAgent(BaseAgent):
    """
    Ranks job listings by relevance to the candidate's resumes.

    Pipeline:
    1. On init, load and embed master resumes into ChromaDB "resumes" collection
    2. For each job, embed description and store in "job_descriptions" collection
    3. Compute cosine similarity between job and matching category resume
    4. Use LLM to score match (0-100) and infer H1B sponsorship likelihood
    5. Return ranked jobs with scores and H1B notes
    """

    def __init__(self, config: dict):
        super().__init__(config, "ranker_agent")
        self.resumes_dir = Path(config.get("resumes_dir", "./resumes"))
        self.chroma_host = os.environ.get("CHROMA_HOST", "localhost")
        self.chroma_port = int(os.environ.get("CHROMA_PORT", "8000"))

        # Init embedding model (runs locally via ONNX Runtime, no PyTorch needed)
        self.log.info("loading_embedding_model")
        self.embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        self.log.info("embedding_model_loaded")

        # Init ChromaDB client
        self._init_chroma()

        # Load and embed master resumes
        self._resume_texts: dict[str, str] = {}
        self._resumes_embedded = False

    def _init_chroma(self) -> None:
        """Initialize ChromaDB client and collections."""
        try:
            self.chroma_client = chromadb.HttpClient(
                host=self.chroma_host,
                port=self.chroma_port,
                settings=Settings(anonymized_telemetry=False),
            )
            self.log.info("chroma_connected", host=self.chroma_host, port=self.chroma_port)
        except Exception as e:
            self.log.warning(
                "chroma_http_failed_falling_back_to_ephemeral",
                error=str(e),
            )
            self.chroma_client = chromadb.EphemeralClient(
                settings=Settings(anonymized_telemetry=False)
            )

        self.resume_collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_RESUMES,
            metadata={"hnsw:space": "cosine"},
        )
        self.job_collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_JOBS,
            metadata={"hnsw:space": "cosine"},
        )

    async def embed_resumes(self) -> None:
        """Load master LaTeX resumes and embed them into ChromaDB."""
        if self._resumes_embedded:
            return

        self.log.info("embedding_resumes")
        for category, filename in RESUME_CATEGORY_MAP.items():
            resume_path = self.resumes_dir / filename
            if not resume_path.exists():
                self.log.warning("resume_not_found", path=str(resume_path))
                continue

            text = resume_path.read_text(encoding="utf-8")
            # Strip LaTeX commands for embedding (keep semantic content)
            clean_text = strip_latex_to_text(text)
            self._resume_texts[category] = clean_text

            embedding = list(next(iter(self.embedder.embed([clean_text]))))

            try:
                self.resume_collection.upsert(
                    ids=[f"resume_{category}"],
                    embeddings=[embedding],
                    documents=[clean_text],
                    metadatas=[{"category": category, "filename": filename}],
                )
                self.log.info("resume_embedded", category=category)
            except Exception as e:
                self.log.error("resume_embed_error", category=category, error=str(e))

        self._resumes_embedded = True
        self.log.info("all_resumes_embedded", count=len(self._resume_texts))

    async def rank_jobs(
        self,
        jobs: list[dict],
        log_callback=None,
    ) -> list[dict]:
        """
        Rank a list of job dicts by relevance and add scoring fields.

        Args:
            jobs: List of job dicts from scraper
            log_callback: Optional async callable for progress logs

        Returns:
            Sorted list (descending by match_score) with added fields:
                match_score, h1b_likely, h1b_notes, similarity_score
        """
        await self.embed_resumes()

        if log_callback:
            await log_callback(f"Ranking {len(jobs)} jobs...")

        ranked_jobs = []
        for i, job in enumerate(jobs):
            try:
                scored_job = await self._score_job(job)
                ranked_jobs.append(scored_job)

                if log_callback and (i + 1) % 5 == 0:
                    await log_callback(f"Ranked {i + 1}/{len(jobs)} jobs...")

            except Exception as e:
                self.log.error(
                    "job_scoring_error",
                    job_id=job.get("job_id"),
                    error=str(e),
                )
                # Keep job with default scores
                job["match_score"] = 0.0
                job["h1b_likely"] = False
                job["h1b_notes"] = "Scoring failed"
                job["similarity_score"] = 0.0
                ranked_jobs.append(job)

        # Sort by match_score descending
        ranked_jobs.sort(key=lambda j: j.get("match_score", 0), reverse=True)

        if log_callback:
            await log_callback(
                f"Ranking complete. Top score: {ranked_jobs[0].get('match_score', 0):.0f}"
                if ranked_jobs else "No jobs to rank"
            )

        return ranked_jobs

    async def _score_job(self, job: dict) -> dict:
        """Score a single job against the matching resume."""
        category = job.get("role_category", "backend")
        description = job.get("description", "") or ""
        title = job.get("title", "")
        company = job.get("company", "")

        # Combine title + description for embedding
        combined_text = f"{title}\n{company}\n{description}"
        if not combined_text.strip():
            job["match_score"] = 0.0
            job["h1b_likely"] = False
            job["h1b_notes"] = "No description available"
            job["similarity_score"] = 0.0
            return job

        # Embed and store job description
        job_embedding = list(next(iter(self.embedder.embed([combined_text]))))
        try:
            self.job_collection.upsert(
                ids=[job["job_id"]],
                embeddings=[job_embedding],
                documents=[combined_text[:2000]],  # Store truncated for ChromaDB
                metadatas=[{
                    "title": title,
                    "company": company,
                    "category": category,
                }],
            )
        except Exception as e:
            self.log.debug("job_upsert_error", job_id=job.get("job_id"), error=str(e))

        # Compute cosine similarity with the matching resume
        similarity_score = self._compute_similarity(category, job_embedding)

        # Single LLM call: score match + H1B inference together
        resume_text = self._resume_texts.get(category, "")
        h1b_from_source = job.get("_h1b_from_source")
        combined = await self._llm_score_and_infer(
            resume_text=resume_text,
            job_title=title,
            job_company=company,
            job_description=description[:3000],
        )

        # H1B — prefer source data (jobright USCIS) over LLM inference
        if h1b_from_source is not None:
            h1b_likely = h1b_from_source
            h1b_notes = job.get("_h1b_notes_from_source") or "Source: jobright USCIS data"
            self.log.debug("h1b_from_source", job_id=job.get("job_id"), h1b=h1b_from_source)
        else:
            h1b_likely = combined.get("h1b_likely", False)
            h1b_notes = combined.get("h1b_notes", "")

        # Blend similarity (30%) + LLM score (70%)
        llm_score = combined.get("score", 50)
        blended_score = round(0.3 * (similarity_score * 100) + 0.7 * llm_score, 1)

        job["match_score"] = blended_score
        job["similarity_score"] = round(similarity_score, 4)
        job["h1b_likely"] = h1b_likely
        job["h1b_notes"] = h1b_notes
        # Clean up internal fields
        job.pop("_h1b_from_source", None)
        job.pop("_h1b_notes_from_source", None)
        job["match_summary"] = combined.get("summary", "")
        job["top_matching_skills"] = combined.get("top_matching_skills", [])
        job["missing_critical_skills"] = combined.get("missing_critical_skills", [])

        self.log.info(
            "job_scored",
            job_id=job.get("job_id"),
            title=title,
            company=company,
            score=blended_score,
            h1b=job["h1b_likely"],
        )
        return job

    def _compute_similarity(self, category: str, job_embedding: list[float]) -> float:
        """Compute cosine similarity between job embedding and resume embedding."""
        try:
            results = self.resume_collection.query(
                query_embeddings=[job_embedding],
                n_results=1,
                where={"category": category},
            )
            if results and results.get("distances") and results["distances"][0]:
                # ChromaDB returns cosine distance (0=identical, 2=opposite)
                # Convert to similarity: similarity = 1 - distance/2
                distance = results["distances"][0][0]
                return max(0.0, 1.0 - distance / 2.0)
        except Exception as e:
            self.log.debug("similarity_error", category=category, error=str(e))

        return 0.5  # Default neutral score

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Pure-Python cosine similarity between two embedding vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b) if norm_a * norm_b > 0 else 0.5

    async def score_tailored_resume(self, job: dict, tailored_latex: str) -> dict:
        """
        Score a job against the *tailored* resume rather than the base resume.

        Uses direct embedding cosine similarity between the stripped tailored
        LaTeX and the job description, combined with LLM scoring — same 30/70
        blend as _score_job but measured against the actual output resume.
        """
        title = job.get("title", "")
        company = job.get("company", "")
        description = job.get("description", "") or ""

        resume_text = strip_latex_to_text(tailored_latex)
        combined_jd = f"{title}\n{company}\n{description}"

        # Embed both sides
        resume_emb = list(next(iter(self.embedder.embed([resume_text]))))
        jd_emb = list(next(iter(self.embedder.embed([combined_jd[:2000]]))))
        similarity = self._cosine_similarity(resume_emb, jd_emb)

        # LLM score against tailored resume text
        llm_result = await self._llm_score_and_infer(
            resume_text=resume_text[:2000],
            job_title=title,
            job_company=company,
            job_description=description[:3000],
        )

        llm_score = llm_result.get("score", 50)
        blended = round(0.3 * (similarity * 100) + 0.7 * llm_score, 1)

        self.log.info(
            "tailored_resume_scored",
            job_id=job.get("job_id"),
            score=blended,
            similarity=round(similarity, 3),
        )

        return {
            **job,
            "match_score": blended,
            "h1b_likely": llm_result.get("h1b_likely", job.get("h1b_likely")),
            "h1b_notes": llm_result.get("h1b_notes", job.get("h1b_notes", "")),
            "similarity_score": round(similarity, 4),
        }

    async def _llm_score_and_infer(
        self,
        resume_text: str,
        job_title: str,
        job_company: str,
        job_description: str,
    ) -> dict:
        """Single LLM call: score resume-job match AND infer H1B likelihood."""
        if not job_description:
            return {
                "score": 40, "top_matching_skills": [], "missing_critical_skills": [],
                "summary": "No description", "h1b_likely": False,
                "h1b_notes": "No description to analyze", "confidence": "low",
            }

        messages = [
            self.build_system_message(COMBINED_SCORE_PROMPT),
            self.build_user_message(
                f"JOB TITLE: {job_title}\n"
                f"COMPANY: {job_company}\n\n"
                f"JOB DESCRIPTION:\n{job_description}\n\n"
                f"CANDIDATE RESUME:\n{resume_text[:2000] if resume_text else 'Resume not available'}"
            ),
        ]

        try:
            result = await self.chat_json(
                task="ranker",
                messages=messages,
                temperature=0.0,
                max_tokens=512,
            )
            result["score"] = max(0, min(100, int(result.get("score", 50))))
            return result
        except Exception as e:
            self.log.warning("score_and_infer_failed", error=str(e))
            return {
                "score": 50, "top_matching_skills": [], "missing_critical_skills": [],
                "summary": f"Scoring failed: {str(e)[:100]}",
                "h1b_likely": False, "h1b_notes": "Inference failed", "confidence": "low",
            }
