"""
Cover Letter Agent

Generates personalized cover letters as LaTeX PDFs and outreach emails using an LLM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import structlog

from agents.base import BaseAgent
from agents.constants import CANDIDATE_INFO, CANDIDATE_NAME, RESUME_CATEGORY_MAP
from agents.utils import escape_latex, strip_latex_to_text

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# LaTeX template constants
# ---------------------------------------------------------------------------

_CL_PREAMBLE = r"""\documentclass[12pt]{letter}
\usepackage[empty]{fullpage}
\usepackage[hidelinks]{hyperref}
\usepackage{graphicx}
\usepackage{fontawesome5}
\usepackage{eso-pic}
\usepackage{charter}

\addtolength{\topmargin}{-0.5in}
\addtolength{\textheight}{1.0in}
\definecolor{gr}{RGB}{225,225,225}

"""

_CL_DOC_HEADER = r"""
\begin{document}

\AddToShipoutPictureBG{%
\color{gr}
\AtPageUpperLeft{\rule[-1.5in]{\paperwidth}{1.5in}}
}

\begin{center}
\textbf{\Huge\selectfont\scshape \myname}

\href{mailto:\myemail}{\faEnvelope\enspace \myemail}\hfill
\href{https://linkedin.com/in/\mylinkedin}{\faLinkedinIn\enspace linkedin.com/\mylinkedin}\hfill\\
\href{tel:\myphone}{\faPhone\enspace \myphone}\hfill
\faMapMarker*\enspace \mylocation
\end{center}

\vspace{0.2in}

"""

_CL_DOC_FOOTER = r"""

\vspace{0.1in}
\vfill

\begin{flushright}
\closer,

\myname
\end{flushright}

\end{document}"""

# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

COVER_LETTER_SYSTEM_PROMPT = """You are an expert career coach and professional writer specializing in tech industry cover letters.

You write concise, high-signal cover letters that sound human, not robotic. Your letters are confident, specific, and impact-driven.

STRICT STYLE RULES:
- No em dashes (-- or -) used as dashes. Use commas or restructure the sentence instead.
- No en dashes used as dashes.
- No generic openers like "I am writing to express my interest..." or "I am applying for..."
- No filler phrases ("I am interested in", "I am looking for", "I am excited to")
- No fluff or padding. Every sentence earns its place.
- Human tone. Confident but not arrogant.
- Maximum 1 page (~350 words).
- Use plain text only. No markdown, no asterisks, no special formatting. No LaTeX commands.
- Use double newlines between paragraphs.

OUTPUT FORMAT:
Return ONLY the cover letter body text. No date, no address block, no "Dear Hiring Manager" salutation, no sign-off. Just the body paragraphs starting with the hook."""

COVER_LETTER_USER_TEMPLATE = """Write a cover letter body for this application:

ROLE: {job_title}
COMPANY: {company}

JOB DESCRIPTION:
{job_description}

CANDIDATE RESUME (LaTeX -- use the experience, metrics, and skills here):
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
- Why I am moving toward this role (frame positively -- growth, scale, impact)
- How my experience directly maps to {company}'s engineering challenges or product

3. WHY THIS COMPANY (2-3 sentences)
Why specifically {company} and this role. Focus on:
- Scale, ownership, or product impact
- Engineering culture or tech approach
- Customer-centric iteration or AI-assisted development (if relevant to the JD)

4. CLOSING (1 sentence)
"Thank you for your consideration. I would welcome the opportunity to speak if you feel my background could be a strong fit for this role or for any other opportunities within your organization."

Write the cover letter body now. No salutation, no sign-off. Use plain text only, no markdown, no em dashes."""


ADDRESS_EXTRACTION_SYSTEM_PROMPT = """You extract structured information from job descriptions and return valid JSON only.
No prose, no markdown fences, just raw JSON."""

ADDRESS_EXTRACTION_USER_TEMPLATE = """Extract from the job description below. If the address is not in the description but you know this company's well-known headquarters address with high confidence, provide it. Return null for any field you are uncertain about.

COMPANY: {company}
JOB TITLE: {title}

JOB DESCRIPTION:
{job_description}

Return a JSON object with exactly these fields:
{{
  "hiring_manager": "Full name of hiring manager or recruiter if mentioned, else null",
  "street": "Street address or null",
  "city": "City name or null",
  "state": "State abbreviation (2 letters) or null"
}}

Return only the JSON object. No explanation."""


OUTREACH_EMAIL_SYSTEM_PROMPT = """You write concise, direct cold outreach emails for software engineering job applications.
Keep it under 120 words. No filler, no em dashes, no en dashes used as dashes, no generic phrases.
Subject line + 3-4 sentence body. Professional but human."""

OUTREACH_EMAIL_USER_TEMPLATE = """Write a cold outreach email for this application:

ROLE: {job_title}
COMPANY: {company}
CANDIDATE: {candidate_name}

Key highlights from resume:
{resume_highlights}

Format:
Subject: [subject line]

[email body -- 3-4 sentences max, no "I am writing to..." opener]"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_cover_letter_latex(
    company: str,
    recipient: str,
    recruiter_title: str,
    address: Optional[dict],
    body_text: str,
) -> str:
    """
    Assemble the full LaTeX document for a cover letter.

    Args:
        company: Company name (plain text, will be escaped)
        recipient: Recipient name (plain text, will be escaped)
        recruiter_title: Recruiter/hiring manager title (plain text, will be escaped)
        address: dict with keys 'street', 'city', 'state' — or None
        body_text: Plain prose body (will be escaped and wrapped in paragraphs)
    """
    info = CANDIDATE_INFO

    def cmd(name: str, value: str) -> str:
        return f"\\newcommand{{\\{name}}}{{{value}}}"

    commands = "\n".join([
        cmd("myname", escape_latex(info["name"])),
        cmd("myemail", escape_latex(info["email"])),
        cmd("mylinkedin", escape_latex(info["linkedin"])),
        cmd("myphone", escape_latex(info["phone"])),
        cmd("mylocation", escape_latex(info["location"])),
        cmd("closer", "Sincerely"),
        cmd("recipient", escape_latex(recipient)),
        cmd("recruiterTitle", escape_latex(recruiter_title)),
        cmd("company", escape_latex(company)),
    ])

    if address:
        commands += "\n" + "\n".join([
            cmd("street", escape_latex(address["street"])),
            cmd("city", escape_latex(address["city"])),
            cmd("state", escape_latex(address["state"])),
        ])
        opening_block = (
            "\\today\n\n"
            "\\recruiterTitle\\\\\n"
            "\\company\\\\\n"
            "\\street\\\\\n"
            "\\city, \\state\n\n"
            "Dear \\recipient,\n\n"
        )
    else:
        commands += "\n" + "\n".join([
            "%" + cmd("street", ""),
            "%" + cmd("city", ""),
            "%" + cmd("state", ""),
        ])
        opening_block = (
            "\\today\n\n"
            "\\recruiterTitle\\\\\n"
            "\\company\n\n"
            "Dear \\recipient,\n\n"
        )

    # Escape body and convert double newlines to LaTeX paragraph breaks
    escaped_body = escape_latex(body_text)
    # Convert paragraph breaks (double newlines) to LaTeX \par
    paragraphs = re.split(r"\n\n+", escaped_body)
    latex_body = "\n\n".join(p.strip() for p in paragraphs if p.strip())

    return (
        _CL_PREAMBLE
        + commands
        + "\n"
        + _CL_DOC_HEADER
        + opening_block
        + latex_body
        + _CL_DOC_FOOTER
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class CoverLetterAgent(BaseAgent):

    def __init__(self, config: dict):
        super().__init__(config, "cover_letter_agent")
        self.resumes_dir = Path(config.get("resumes_dir", "./resumes"))
        self.generated_dir = Path(
            config.get("storage", {}).get("generated_dir", "./generated")
        )
        self.generated_dir.mkdir(parents=True, exist_ok=True)

        self.pdf_worker_url = config.get("pdf_worker", {}).get(
            "service_url", "http://pdfworker:8001"
        )

    def _load_resume_text(self, category: str) -> str:
        filename = RESUME_CATEGORY_MAP.get(category, RESUME_CATEGORY_MAP["backend"])
        path = self.resumes_dir / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    async def _extract_address_and_contact(
        self,
        job_description: str,
        company: str,
        title: str,
    ) -> dict:
        """Single LLM call to extract hiring manager and address from job description."""
        messages = [
            self.build_system_message(ADDRESS_EXTRACTION_SYSTEM_PROMPT),
            self.build_user_message(
                ADDRESS_EXTRACTION_USER_TEMPLATE.format(
                    company=company,
                    title=title,
                    job_description=job_description or "No description provided.",
                )
            ),
        ]
        try:
            data = await self.chat_json(
                task="address_extraction",
                messages=messages,
                temperature=0.0,
                max_tokens=256,
            )
        except Exception:
            self.log.warning("address_extraction_parse_error")
            data = {}
        return {
            "hiring_manager": data.get("hiring_manager"),
            "street": data.get("street"),
            "city": data.get("city"),
            "state": data.get("state"),
        }

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

    async def _compile_cover_letter_pdf(
        self,
        tex_content: str,
        output_name: str,
        job_dir: Path,
    ) -> Optional[Path]:
        """Send LaTeX to pdfworker for compilation. Returns local PDF path or None."""
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
                self.log.info(
                    "cover_letter_pdf_compiled",
                    path=str(pdf_path),
                    size=len(response.content),
                )
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
                msg="pdfworker service not reachable; skipping cover letter PDF compilation",
            )
            return None
        except Exception as e:
            self.log.error("cover_letter_pdf_compile_error", error=str(e))
            return None

    async def generate_cover_letter(
        self,
        job: dict,
        log_callback=None,
    ) -> dict:
        """
        Generate a cover letter PDF and outreach email for a single job.

        Returns the job dict updated with:
          cover_letter_file, cover_letter_latex, s3_cover_letter_url,
          company_address, hiring_manager, email_file
        """
        job_id    = job.get("job_id", "unknown")
        title     = job.get("title", "Software Engineer")
        company   = job.get("company", "Company")
        category  = job.get("role_category", "backend")
        description = (job.get("description") or "")[:3500]

        if log_callback:
            await log_callback(f"Generating cover letter for {title} at {company}...")

        safe_company = re.sub(r"[^\w\s-]", "", company).strip()
        safe_title   = re.sub(r"[^\w\s-]", "", title).strip()
        doc_base     = f"{CANDIDATE_NAME} - {safe_company} - {safe_title}"[:150]

        job_dir = self.generated_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        raw_tex     = self._load_resume_text(category)
        resume_text = strip_latex_to_text(raw_tex)[:3000] if raw_tex else ""

        # ── 1. Extract address + hiring manager ───────────────────────────
        extraction: dict = {}
        try:
            extraction = await self._extract_address_and_contact(
                job_description=description,
                company=company,
                title=title,
            )
            self.log.info("address_extracted", job_id=job_id, result=extraction)
        except Exception as e:
            self.log.warning("address_extraction_failed", job_id=job_id, error=str(e))

        hiring_manager = extraction.get("hiring_manager")
        recipient = hiring_manager or "Hiring Manager"
        recruiter_title = "Hiring Manager"

        # Build address dict only if all three parts are present
        address_dict: Optional[dict] = None
        if extraction.get("street") and extraction.get("city") and extraction.get("state"):
            address_dict = {
                "street": extraction["street"],
                "city": extraction["city"],
                "state": extraction["state"],
            }

        company_address: Optional[str] = None
        if address_dict:
            company_address = f"{address_dict['street']}, {address_dict['city']}, {address_dict['state']}"

        # ── 2. Generate body text ─────────────────────────────────────────
        cover_letter_latex: Optional[str] = None
        cover_letter_pdf_path: Optional[Path] = None
        try:
            body_text = await self._generate_body(
                job_title=title,
                company=company,
                job_description=description,
                resume_text=resume_text,
            )

            # ── 3. Build LaTeX document ───────────────────────────────────
            cover_letter_latex = _build_cover_letter_latex(
                company=company,
                recipient=recipient,
                recruiter_title=recruiter_title,
                address=address_dict,
                body_text=body_text,
            )

            # Save .tex file
            tex_path = job_dir / f"{doc_base} Cover Letter.tex"
            tex_path.write_text(cover_letter_latex, encoding="utf-8")

            # ── 4. Compile to PDF ─────────────────────────────────────────
            cover_letter_pdf_path = await self._compile_cover_letter_pdf(
                tex_content=cover_letter_latex,
                output_name=f"{doc_base} Cover Letter",
                job_dir=job_dir,
            )

        except Exception as e:
            self.log.error("cover_letter_generation_error", job_id=job_id, error=str(e))

        # ── 5. Upload to S3 ───────────────────────────────────────────────
        s3_cover_letter_url: Optional[str] = None
        if cover_letter_pdf_path and cover_letter_pdf_path.exists():
            try:
                from backend.storage import get_storage
                folder_name = f"{safe_company} - {safe_title}"[:120]
                s3_cover_letter_url = await get_storage().upload_file(
                    local_path=cover_letter_pdf_path,
                    s3_key=f"cover-letters/{folder_name}/{doc_base} Cover Letter.pdf",
                    content_type="application/pdf",
                )
            except Exception as e:
                self.log.warning("cover_letter_s3_upload_failed", job_id=job_id, error=str(e))

        job["cover_letter_file"] = str(cover_letter_pdf_path) if cover_letter_pdf_path else None
        job["cover_letter_latex"] = cover_letter_latex
        job["s3_cover_letter_url"] = s3_cover_letter_url
        job["company_address"] = company_address
        job["hiring_manager"] = hiring_manager

        # ── 6. Outreach email ─────────────────────────────────────────────
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

        if log_callback:
            await log_callback(
                f"Cover letter complete for {job_id}: "
                f"{'PDF generated' if cover_letter_pdf_path else 'LaTeX only'}"
            )

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
                job["cover_letter_file"]   = None
                job["cover_letter_latex"]  = None
                job["email_file"]          = None
                job["s3_cover_letter_url"] = None
                job["company_address"]     = None
                job["hiring_manager"]      = None
                results.append(job)

        if log_callback:
            succeeded = sum(1 for j in results if j.get("cover_letter_file"))
            await log_callback(f"Cover letters complete: {succeeded}/{len(results)} succeeded")

        return results
