"""
Resume Tailor Agent

Reads master LaTeX resumes, tailors them per job via LLM,
compiles to PDF via the pdfworker service, and uploads to S3.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

import httpx
import structlog

from agents.base import BaseAgent
from agents.constants import CANDIDATE_NAME, RESUME_CATEGORY_MAP

logger = structlog.get_logger(__name__)

TAILOR_SYSTEM_PROMPT = """You are an expert resume writer and ATS optimization specialist with deep LaTeX knowledge.

Your task: Tailor the provided LaTeX resume to maximize ATS score for the given job description.

STRICT RULES — follow exactly:

FORMAT (non-negotiable):
- Use the EXACT same LaTeX document class, packages, commands, and visual structure
- Do NOT add new sections, rename sections, or change section order
- Do NOT change contact information, education, or company names/dates
- Do NOT change any LaTeX formatting commands, spacing, or layout macros
- Do NOT remove ANY line from the preamble (everything before \begin{document}). Copy it verbatim.
- Return ONLY the complete raw LaTeX — no markdown fences, no explanations, no comments added by you
- Do NOT use em dashes (— or \textemdash) or en dashes (– or \textendash); use a comma, semicolon, or rewrite the phrase instead

PAGE LIMIT:
- The resume MUST fit within 1 page
- To stay within 1 page: REMOVE or COMMENT OUT less relevant bullet points using % in LaTeX
- Prioritize bullet points that match the job description keywords; suppress weaker ones
- Only exceed 1 page if the job requires so many distinct skills that omitting any would critically hurt the match score

CONTENT CHANGES ALLOWED:
- Update the resume headline role title (the \section{...} immediately after \begin{document}) to match the target role title from the job description exactly (e.g., change "Full Stack Software Engineer" → "Senior Software Engineer" if that is the JD title). This is an ATS keyword-match requirement, not fabrication.
- Reword bullet points to naturally incorporate keywords and phrases from the job description
- Reorder bullet points within a role to surface the most relevant ones first
- Update the summary/objective/profile section to directly mirror the role's language, seniority level, and key requirements
- Adjust the skills section: reorder, add closely adjacent technologies, remove irrelevant ones

CONTROLLED FABRICATION (allowed in these specific cases only):
1. NUMBERS: If an achievement has no metric (e.g. "improved query performance"), you MAY add a plausible approximate figure (e.g. "improved query performance by ~35%"). Use "~" prefix to signal approximation. Keep it realistic and conservative.
2. AI CODING TOOLS BULLET: The RoundTechSquare experience contains a commented-out bullet with the placeholder [AI_TOOL]. If the job description explicitly mentions AI coding assistants or developer productivity tools (e.g. GitHub Copilot, Claude, Cursor, Codeium, Tabnine, Amazon CodeWhisperer, or similar), you MUST uncomment that bullet and replace [AI_TOOL] with the exact tool(s) named in the JD. Remove the two comment lines above it (the instruction comment). If the JD does not mention any such tool, leave the bullet commented out.
3. ADJACENT TECHNOLOGIES: If the job description mentions a tool that is a direct equivalent or common companion to something already in the resume, you MAY add it alongside the existing one. Examples:
   - Resume has "ELK Stack" → JD mentions "Kibana" → add "Kibana" explicitly
   - Resume has "distributed tracing" → JD mentions "OpenTelemetry" → add "OpenTelemetry"
   - Resume has "Prometheus" → JD mentions "Grafana" → add "Grafana"
   - Only do this for technologies you are certain are adjacent — do NOT invent unrelated skills

NEVER fabricate: work history job titles, companies, employment dates, degrees, certifications, or entirely new projects. (The resume headline is NOT a work history title — updating it to match the JD is required.)"""

TAILOR_USER_TEMPLATE = """MASTER RESUME (LaTeX):
{resume_tex}

JOB TITLE: {job_title}
COMPANY: {company}

JOB DESCRIPTION:
{job_description}

Instructions:
1. Identify the top keywords, required skills, and technologies from the job description
2. Update the resume headline role title (\section{{...}}) to exactly match the JD job title
3. Comment out (%) bullet points least relevant to this role to stay within 1 page
4. Reword remaining bullets to naturally use the JD's language and keywords
5. Add plausible approximate metrics (~N%) where achievements lack numbers
6. Add adjacent technologies where the JD explicitly calls for tools closely related to what's already in the resume
7. Update the summary to directly mirror the role's language, seniority, and key requirements
8. If the JD mentions AI coding assistants or developer productivity tools (GitHub Copilot, Claude, Cursor, Codeium, Tabnine, CodeWhisperer, etc.), uncomment the AI tools bullet in the RoundTechSquare section and replace [AI_TOOL] with the tool(s) named in the JD; also remove the two comment lines above it. If not mentioned, leave it commented.

Return the complete tailored LaTeX code only. No explanations."""

# Cached variant — resume block is separated so cache_control can be applied to it alone
TAILOR_RESUME_BLOCK = "MASTER RESUME (LaTeX):\n{resume_tex}"

TAILOR_JOB_BLOCK = """JOB TITLE: {job_title}
COMPANY: {company}

JOB DESCRIPTION:
{job_description}

Instructions:
1. Identify the top keywords, required skills, and technologies from the job description
2. Update the resume headline role title (\section{{...}}) to exactly match the JD job title
3. Comment out (%) bullet points least relevant to this role to stay within 1 page
4. Reword remaining bullets to naturally use the JD's language and keywords
5. Add plausible approximate metrics (~N%) where achievements lack numbers
6. Add adjacent technologies where the JD explicitly calls for tools closely related to what's already in the resume
7. Update the summary to directly mirror the role's language, seniority, and key requirements
8. If the JD mentions AI coding assistants or developer productivity tools (GitHub Copilot, Claude, Cursor, Codeium, Tabnine, CodeWhisperer, etc.), uncomment the AI tools bullet in the RoundTechSquare section and replace [AI_TOOL] with the tool(s) named in the JD; also remove the two comment lines above it. If not mentioned, leave it commented.

Return the complete tailored LaTeX code only. No explanations."""

_CACHE_CONTROL_1H = {"type": "ephemeral", "ttl": "1h"}


class ResumeTailorAgent(BaseAgent):
    """
    Tailors LaTeX resumes for specific job descriptions using LLM,
    compiles them via the pdfworker service, and uploads to S3.
    """

    def __init__(self, config: dict):
        super().__init__(config, "resume_tailor_agent")
        self.resumes_dir = Path(config.get("resumes_dir", "./resumes"))
        self.generated_dir = Path(config.get("storage", {}).get("generated_dir", "./generated"))
        self.generated_dir.mkdir(parents=True, exist_ok=True)

        self.pdf_worker_url = config.get("pdf_worker", {}).get(
            "service_url", "http://pdfworker:8001"
        )

        # HTTP client for PDF worker
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def tailor_resume(
        self,
        job: dict,
        log_callback=None,
    ) -> dict:
        """
        Generate a tailored resume for a single job.

        Args:
            job: Job dict with keys: job_id, title, company, description, role_category
            log_callback: Optional async callable for progress logs

        Returns:
            Updated job dict with: resume_file, s3_resume_url
        """
        job_id = job.get("job_id", "unknown")
        category = job.get("role_category", "backend")
        title = job.get("title", "")
        company = job.get("company", "")
        description = job.get("description", "") or ""

        if log_callback:
            await log_callback(f"Tailoring resume for {title} at {company} ({job_id})")

        self.log.info(
            "tailoring_resume",
            job_id=job_id,
            title=title,
            company=company,
            category=category,
        )

        # Load master resume
        master_tex = self._load_master_resume(category)
        if not master_tex:
            self.log.error("master_resume_not_found", category=category)
            job["resume_file"] = None
            job["s3_resume_url"] = None
            return job

        # Build human-readable names
        safe_company = re.sub(r"[^\w\s-]", "", company).strip()
        safe_title   = re.sub(r"[^\w\s-]", "", title).strip()
        folder_name  = f"{safe_company} - {safe_title}"[:120]
        resume_name  = f"{CANDIDATE_NAME} - {safe_company} - {safe_title} Resume"[:150]

        # Create output directory (keyed by job_id for uniqueness; human name used for files)
        job_dir = self.generated_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Tailor via LLM
        try:
            tailored_tex = await self._tailor_with_llm(
                master_tex=master_tex,
                job_title=title,
                company=company,
                job_description=description[:4000],  # Trim for token budget
                use_cache=True,
            )
        except RuntimeError as e:
            self.log.error("resume_tailor_failed", job_id=job_id, error=str(e))
            job["resume_file"] = None
            job["s3_resume_url"] = None
            return job

        # Save .tex file
        tex_path = job_dir / f"{resume_name}.tex"
        tex_path.write_text(tailored_tex, encoding="utf-8")
        self.log.info("tex_saved", path=str(tex_path))
        job["latex_content"] = tailored_tex

        # Compile to PDF via pdfworker
        pdf_path = await self._compile_pdf(
            tex_content=tailored_tex,
            output_name=resume_name,
            job_dir=job_dir,
        )

        # Upload to S3  — folder: "company - role title", file: "{CANDIDATE_NAME} - role title.pdf"
        s3_url = None
        if pdf_path:
            from backend.storage import get_storage
            s3_url = await get_storage().upload_file(
                local_path=pdf_path,
                s3_key=f"resumes/{folder_name}/{resume_name}.pdf",
                content_type="application/pdf",
            )

        job["resume_file"] = str(pdf_path) if pdf_path else str(tex_path)
        job["s3_resume_url"] = s3_url

        if log_callback:
            await log_callback(
                f"Resume generated for {job_id}: {job['resume_file']}"
            )

        return job

    async def tailor_resumes_batch(
        self,
        jobs: list[dict],
        max_concurrent: int = 3,
        log_callback=None,
    ) -> list[dict]:
        """
        Tailor resumes for multiple jobs with controlled concurrency.

        Args:
            jobs: List of job dicts
            max_concurrent: Max parallel tailoring tasks
            log_callback: Optional async callable for progress logs

        Returns:
            List of jobs updated with resume paths.
        """
        if log_callback:
            await log_callback(f"Tailoring resumes for {len(jobs)} jobs (max {max_concurrent} concurrent)...")

        semaphore = asyncio.Semaphore(max_concurrent)

        async def tailor_with_semaphore(job: dict) -> dict:
            async with semaphore:
                try:
                    return await self.tailor_resume(job, log_callback=log_callback)
                except Exception as e:
                    self.log.error(
                        "tailor_failed",
                        job_id=job.get("job_id"),
                        error=str(e),
                    )
                    job["resume_file"] = None
                    job["s3_resume_url"] = None
                    return job

        tasks = [tailor_with_semaphore(job) for job in jobs]
        results = await asyncio.gather(*tasks)

        generated_count = sum(1 for j in results if j.get("resume_file"))
        if log_callback:
            await log_callback(f"Resume tailoring complete: {generated_count}/{len(jobs)} succeeded")

        return list(results)

    async def tailor_from_description(
        self,
        title: str,
        company: str,
        description: str,
        category: str = "backend",
    ) -> str:
        """
        Return a tailored LaTeX string without saving any files.
        Used by the Apply Tool for the interactive editor flow.

        Args:
            title: Job title
            company: Company name
            description: Job description text
            category: Role category (backend/fullstack/aiml)

        Returns:
            Tailored LaTeX string ready for compilation.

        Raises:
            ValueError: If master resume not found for category.
            RuntimeError: If LLM request fails.
        """
        master_tex = self._load_master_resume(category)
        if not master_tex:
            raise ValueError(f"Master resume not found for category: {category}")

        latex = await self._tailor_with_llm(
            master_tex=master_tex,
            job_title=title,
            company=company,
            job_description=description[:4000],
            use_cache=True,
        )
        return self._ensure_preamble_fixes(latex)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_master_resume(self, category: str) -> Optional[str]:
        """Load master LaTeX resume for a category, stripping comment-only lines."""
        filename = RESUME_CATEGORY_MAP.get(category)
        if not filename:
            self.log.warning("unknown_category", category=category)
            filename = RESUME_CATEGORY_MAP["backend"]

        path = self.resumes_dir / filename
        if not path.exists():
            self.log.error("resume_file_missing", path=str(path))
            return None

        raw = path.read_text(encoding="utf-8")
        return self._strip_tex_comments(raw)

    def _strip_tex_comments(self, tex: str) -> str:
        """
        Remove lines that consist entirely of a LaTeX comment (optional leading
        whitespace followed by %).  Keeps active lines that have trailing comments.
        Also collapses runs of blank lines down to one.
        """
        lines = []
        prev_blank = False
        for line in tex.split("\n"):
            stripped = line.strip()
            if stripped.startswith("%"):
                continue          # pure comment line — drop it
            is_blank = stripped == ""
            if is_blank and prev_blank:
                continue          # collapse consecutive blank lines
            lines.append(line)
            prev_blank = is_blank
        return "\n".join(lines)

    async def _tailor_with_llm(
        self,
        master_tex: str,
        job_title: str,
        company: str,
        job_description: str,
        use_cache: bool = False,
    ) -> str:
        """Use LLM to tailor the LaTeX resume for the job."""
        if use_cache:
            # Explicit cache breakpoints (1-hour TTL):
            # Block 1 — system prompt: static, cache for 1h
            # Block 2 — master resume LaTeX: static per category, cache for 1h
            # Block 3 — job-specific content: changes every call, not cached
            messages = [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": TAILOR_SYSTEM_PROMPT,
                            "cache_control": _CACHE_CONTROL_1H,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": TAILOR_RESUME_BLOCK.format(resume_tex=master_tex),
                            "cache_control": _CACHE_CONTROL_1H,
                        },
                        {
                            "type": "text",
                            "text": TAILOR_JOB_BLOCK.format(
                                job_title=job_title,
                                company=company,
                                job_description=job_description or "No description provided",
                            ),
                        },
                    ],
                },
            ]
            self.log.info("resume_tailor_cache_enabled", ttl="1h")
        else:
            messages = [
                self.build_system_message(TAILOR_SYSTEM_PROMPT),
                self.build_user_message(
                    TAILOR_USER_TEMPLATE.format(
                        resume_tex=master_tex,
                        job_title=job_title,
                        company=company,
                        job_description=job_description or "No description provided",
                    )
                ),
            ]

        try:
            raw = await self.chat(
                task="resume_tailor",
                messages=messages,
                temperature=0.2,
                max_tokens=8192,
            )
        except Exception as e:
            error_type = type(e).__name__
            # Classify common LLM errors for a clean message
            msg = str(e)
            if "rate limit" in msg.lower() or "RateLimit" in error_type:
                raise RuntimeError(f"LLM rate limit hit during resume tailoring — try again later") from None
            if "auth" in msg.lower() or "credentials" in msg.lower() or "api_key" in msg.lower():
                raise RuntimeError(f"LLM authentication error — check API key configuration") from None
            raise RuntimeError(f"LLM request failed during resume tailoring: {error_type}") from None

        # Strip any markdown fences if model added them
        raw = self._strip_markdown_fences(raw)

        # Inject preamble fixes the LLM may have accidentally dropped
        raw = self._ensure_preamble_fixes(raw)

        # Validate it looks like LaTeX
        if "\\documentclass" not in raw and "\\begin{document}" not in raw:
            self.log.warning(
                "llm_output_not_latex",
                preview=raw[:200],
            )
            # Fall back to master resume
            return master_tex

        return raw

    def _ensure_preamble_fixes(self, tex: str) -> str:
        """
        Inject required preamble lines the LLM may have dropped.
        These are essential for XeLaTeX compilation and must always be present.
        """
        # hyperref crashes on \MakeUppercase in PDF bookmarks without this.
        # The LLM sometimes regenerates the preamble without it.
        hyperref = r"\usepackage[hidelinks]{hyperref}"
        fix = r"\pdfstringdefDisableCommands{\def\MakeUppercase#1{#1}}"
        if hyperref in tex and fix not in tex:
            tex = tex.replace(hyperref, f"{hyperref}\n{fix}", 1)
            self.log.info("preamble_fix_injected", fix="pdfstringdefDisableCommands")
        return tex

    def _strip_markdown_fences(self, text: str) -> str:
        """Remove markdown code fences and extract clean LaTeX from LLM output."""
        # Remove ```latex ... ``` or ``` ... ```
        match = re.search(r"```(?:latex|tex)?\s*([\s\S]+?)```", text)
        if match:
            text = match.group(1).strip()

        # Strip any prose before \documentclass (LLM sometimes adds preamble text)
        doc_start = text.find("\\documentclass")
        if doc_start > 0:
            text = text[doc_start:]

        # If output was truncated (no \end{document}), close the document
        if "\\documentclass" in text and "\\end{document}" not in text:
            self.log.warning("latex_truncated_appending_end_document")
            # Close any open environments and the document
            text = text.rstrip() + "\n\\end{document}\n"

        return text.strip()

    async def _compile_pdf(
        self,
        tex_content: str,
        output_name: str,
        job_dir: Path,
    ) -> Optional[Path]:
        """
        Send LaTeX to pdfworker service for compilation.

        Returns local path to compiled PDF, or None on failure.
        """
        pdf_path = job_dir / f"{output_name}.pdf"

        try:
            client = await self._get_http_client()
            response = await client.post(
                f"{self.pdf_worker_url}/compile",
                json={
                    "tex_content": tex_content,
                    "filename": output_name,
                },
                timeout=90.0,
            )

            if response.status_code == 200:
                pdf_path.write_bytes(response.content)
                self.log.info("pdf_compiled", path=str(pdf_path), size=len(response.content))
                return pdf_path
            else:
                self.log.error(
                    "pdf_worker_error",
                    status=response.status_code,
                    body=response.text[:500],
                )
                return None

        except httpx.ConnectError:
            self.log.warning(
                "pdf_worker_unavailable",
                url=self.pdf_worker_url,
                msg="pdfworker service not reachable; skipping PDF compilation",
            )
            return None
        except Exception as e:
            self.log.error("pdf_compile_error", error=str(e))
            return None

