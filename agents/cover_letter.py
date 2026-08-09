"""
Cover Letter Agent

Generates personalized cover letters and outreach emails using an LLM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import structlog

from agents.base import BaseAgent
from agents.constants import CANDIDATE_NAME, RESUME_CATEGORY_MAP

logger = structlog.get_logger(__name__)

COVER_LETTER_SYSTEM_PROMPT = """You are an expert career coach and professional writer specializing in tech industry cover letters.

You write concise, high-signal cover letters that sound human, not robotic. Your letters are confident, specific, and impact-driven.

STRICT STYLE RULES:
- No em dashes (— or –). Use commas or restructure the sentence instead.
- No generic openers like "I am writing to express my interest..." or "I am applying for..."
- No filler phrases ("I am interested in", "I am looking for", "I am excited to")
- No fluff or padding. Every sentence earns its place.
- Human tone. Confident but not arrogant.
- Maximum 1 page (~350 words).

OUTPUT FORMAT:
Return ONLY the cover letter body text. No date, no address block, no "Dear Hiring Manager" salutation, no sign-off. Just the body paragraphs starting with the hook."""

COVER_LETTER_USER_TEMPLATE = """Write a cover letter body for this application:

ROLE: {job_title}
COMPANY: {company}

JOB DESCRIPTION:
{job_description}

CANDIDATE RESUME (LaTeX — use the experience, metrics, and skills here):
{resume_text}

STRUCTURE TO FOLLOW (write in this exact order):

1. OPENING HOOK (1-2 sentences)
Show you have done your homework on {company}. Use one of:
- A recent announcement, product launch, funding round, partnership, or milestone
- A relevant industry trend tied to their mission
- Something specific and impressive about their tech, culture, or customers
Format: "I was particularly impressed to read about [specific event / thing]. [Why it matters / what it signals]."
If you are not certain of a specific recent event, reference something concrete and verifiable about the company (their product scale, a known technical challenge they solve, a public initiative). Do NOT invent fake news.

2. MY EXPERIENCE (3-4 sentences)
Tight, high-signal paragraph covering:
- What I have done (relevant experience with metrics from the resume)
- What I am doing now
- Why I am moving toward this role (frame positively — growth, scale, impact)
- How my experience directly maps to {company}'s engineering challenges or product

3. WHY THIS COMPANY (2-3 sentences)
Why specifically {company} and this role. Focus on:
- Scale, ownership, or product impact
- Engineering culture or tech approach
- Customer-centric iteration or AI-assisted development (if relevant to the JD)

4. CLOSING (1 sentence)
"Thank you for your consideration. I would welcome the opportunity to speak if you feel my background could be a strong fit for this role or for any other opportunities within your organization."

Write the cover letter body now. No salutation, no sign-off."""


OUTREACH_EMAIL_SYSTEM_PROMPT = """You write concise, direct cold outreach emails for software engineering job applications.
Keep it under 120 words. No filler, no em dashes, no generic phrases.
Subject line + 3-4 sentence body. Professional but human."""

OUTREACH_EMAIL_USER_TEMPLATE = """Write a cold outreach email for this application:

ROLE: {job_title}
COMPANY: {company}
CANDIDATE: {candidate_name}

Key highlights from resume:
{resume_highlights}

Format:
Subject: [subject line]

[email body — 3-4 sentences max, no "I am writing to..." opener]"""


class CoverLetterAgent(BaseAgent):

    def __init__(self, config: dict):
        super().__init__(config, "cover_letter_agent")
        self.resumes_dir = Path(config.get("resumes_dir", "./resumes"))
        self.generated_dir = Path(
            config.get("storage", {}).get("generated_dir", "./generated")
        )
        self.generated_dir.mkdir(parents=True, exist_ok=True)

    def _load_resume_text(self, category: str) -> str:
        filename = RESUME_CATEGORY_MAP.get(category, RESUME_CATEGORY_MAP["backend"])
        path = self.resumes_dir / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _strip_latex(self, tex: str) -> str:
        """Minimal LaTeX stripping to extract readable text for the LLM prompt."""
        tex = re.sub(r"%.*$", "", tex, flags=re.MULTILINE)
        tex = re.sub(r"\\[a-zA-Z]+\*?\[.*?\]\{([^}]*)\}", r"\1", tex)
        tex = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", tex)
        tex = re.sub(r"\\[a-zA-Z]+\*?", " ", tex)
        tex = re.sub(r"[{}\\$&#^_~]", " ", tex)
        tex = re.sub(r"\s+", " ", tex)
        return tex.strip()

    async def generate_cover_letter(
        self,
        job: dict,
        log_callback=None,
    ) -> dict:
        job_id    = job.get("job_id", "unknown")
        title     = job.get("title", "Software Engineer")
        company   = job.get("company", "Company")
        category  = job.get("role_category", "backend")
        description = (job.get("description") or "")[:3500]

        if log_callback:
            await log_callback(f"Generating cover letter for {title} at {company}...")

        raw_tex      = self._load_resume_text(category)
        resume_text  = self._strip_latex(raw_tex)[:3000] if raw_tex else ""

        safe_company = re.sub(r"[^\w\s-]", "", company).strip()
        safe_title   = re.sub(r"[^\w\s-]", "", title).strip()
        doc_base     = f"{CANDIDATE_NAME} - {safe_company} - {safe_title}"[:150]

        job_dir = self.generated_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # ── Cover letter body ─────────────────────────────────────────────────
        cover_letter_path = job_dir / f"{doc_base} Cover Letter.txt"
        try:
            cover_letter_body = await self._generate_body(
                job_title=title,
                company=company,
                job_description=description,
                resume_text=resume_text,
            )
            cover_letter_path.write_text(cover_letter_body, encoding="utf-8")
            job["cover_letter_file"] = str(cover_letter_path)
            self.log.info("cover_letter_saved", job_id=job_id, path=str(cover_letter_path))
        except Exception as e:
            self.log.error("cover_letter_llm_error", job_id=job_id, error=str(e))
            job["cover_letter_file"] = None

        # ── Outreach email ────────────────────────────────────────────────────
        email_path = job_dir / f"{doc_base} Outreach Email.txt"
        try:
            outreach_email = await self._generate_email(
                job_title=title,
                company=company,
                resume_text=resume_text,
            )
            email_path.write_text(outreach_email, encoding="utf-8")
            job["email_file"] = str(email_path)
            self.log.info("outreach_email_saved", job_id=job_id, path=str(email_path))
        except Exception as e:
            self.log.error("outreach_email_llm_error", job_id=job_id, error=str(e))
            job["email_file"] = None

        job["s3_cover_letter_url"] = None
        return job

    async def generate_cover_letters_batch(
        self,
        jobs: list[dict],
        log_callback=None,
    ) -> list[dict]:
        if log_callback:
            await log_callback(f"Generating cover letters for {len(jobs)} jobs...")

        results = []
        for job in jobs:
            try:
                updated = await self.generate_cover_letter(job, log_callback=log_callback)
                results.append(updated)
            except Exception as e:
                self.log.error("cover_letter_error", job_id=job.get("job_id"), error=str(e))
                job["cover_letter_file"]  = None
                job["email_file"]         = None
                job["s3_cover_letter_url"] = None
                results.append(job)

        if log_callback:
            succeeded = sum(1 for j in results if j.get("cover_letter_file"))
            await log_callback(f"Cover letters complete: {succeeded}/{len(results)} succeeded")

        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _generate_body(
        self,
        job_title: str,
        company: str,
        job_description: str,
        resume_text: str,
    ) -> str:
        messages = [
            self.build_system_message(COVER_LETTER_SYSTEM_PROMPT),
            self.build_user_message(
                COVER_LETTER_USER_TEMPLATE.format(
                    job_title=job_title,
                    company=company,
                    job_description=job_description or "No description provided.",
                    resume_text=resume_text or "Resume not available.",
                )
            ),
        ]
        body = await self.chat(
            task="cover_letter",
            messages=messages,
            temperature=0.4,
            max_tokens=1024,
        )
        return body.strip()

    async def _generate_email(
        self,
        job_title: str,
        company: str,
        resume_text: str,
    ) -> str:
        # Pull a short highlight snippet (first 500 chars of stripped resume)
        highlights = resume_text[:500] if resume_text else "5 years backend engineering experience."

        messages = [
            self.build_system_message(OUTREACH_EMAIL_SYSTEM_PROMPT),
            self.build_user_message(
                OUTREACH_EMAIL_USER_TEMPLATE.format(
                    job_title=job_title,
                    company=company,
                    candidate_name=CANDIDATE_NAME,
                    resume_highlights=highlights,
                )
            ),
        ]
        email = await self.chat(
            task="cover_letter",
            messages=messages,
            temperature=0.3,
            max_tokens=300,
        )
        return email.strip()
