"""
Tools router — manual job URL ingestion and live LaTeX compilation.

POST /api/tools/ingest-url   Scrape any job URL → extract info → tailor resume → compile PDF
POST /api/tools/compile      Compile LaTeX → return PDF as base64
"""
from __future__ import annotations

import base64
import os
from typing import Optional

import httpx
import structlog
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_full_config

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/tools", tags=["tools"])

PDF_WORKER_URL = os.environ.get("PDF_WORKER_URL", "http://pdfworker:8001")

# ── Job page scraping ──────────────────────────────────────────────────────────

_JOB_SELECTORS = [
    "#content", ".job__description", ".posting-requirements",
    ".posting-description", ".section-wrapper",
    "[data-automation-id='jobPostingDescription']",
    ".ashby-job-posting-brief-description",
    ".description__text", ".show-more-less-html",
    "#jobDescriptionText",
    '[class*="job-description"]', '[class*="jobDescription"]',
    '[class*="description"]', "article", "main",
]


def _extract_text_from_html(html: str) -> str:
    """Parse HTML with BeautifulSoup and return the best job content found."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "iframe"]):
        tag.decompose()
    for sel in _JOB_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text[:8000]
    body = soup.find("body")
    return body.get_text(separator="\n", strip=True)[:8000] if body else ""


async def _scrape_with_httpx(url: str) -> str | None:
    """Fast static fetch. Returns text if content is rich enough, else None."""
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("scrape_httpx_non200", url=url, status=resp.status_code)
                return None
            text = _extract_text_from_html(resp.text)
            if len(text) < 200:
                logger.info("scrape_httpx_thin", url=url, chars=len(text))
                return None
            return text
    except Exception as e:
        logger.info("scrape_httpx_failed", url=url, error=str(e))
        return None


async def _scrape_with_playwright(url: str) -> str | None:
    """Full browser render via Playwright. Used as fallback for JS-heavy ATS pages."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],  # required in Docker
            )
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            # domcontentloaded is more reliable than networkidle —
            # many ATS portals keep background XHR alive indefinitely
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)  # let React/Angular hydrate
            html = await page.content()
            # inner_text gives cleaner output than BeautifulSoup on rendered DOM
            body_text = await page.inner_text("body")
            await browser.close()

        # Prefer inner_text (already clean); fall back to HTML parsing
        text = body_text.strip() if len(body_text.strip()) > 200 else _extract_text_from_html(html)
        if len(text) < 200:
            logger.warning("scrape_playwright_thin", url=url, chars=len(text))
            return None
        logger.info("scrape_playwright_ok", url=url, chars=len(text))
        return text[:8000]
    except Exception as e:
        logger.warning("scrape_playwright_failed", url=url, error=str(e))
        return None


async def _scrape_url(url: str) -> str:
    """
    Fetch a job URL and return the main page text (up to 8000 chars).
    Strategy: try fast httpx first; fall back to Playwright for JS-rendered pages.
    """
    # 1. Fast path — static HTML (works for Greenhouse, Lever, Workday static, etc.)
    text = await _scrape_with_httpx(url)
    if text:
        logger.info("scrape_url_ok_httpx", url=url, chars=len(text))
        return text

    # 2. Slow path — full browser render (Dayforce, iCIMS, Taleo, React SPAs, etc.)
    logger.info("scrape_url_falling_back_to_playwright", url=url)
    text = await _scrape_with_playwright(url)
    if text:
        return text

    raise HTTPException(
        status_code=422,
        detail="Could not extract content from this job URL even with browser rendering. The page may require login or block automated access. Try pasting the job description manually.",
    )


# ── Job info extraction via LLM ───────────────────────────────────────────────

_EXTRACT_PROMPT = """Extract structured job details from the page text below. Return ONLY valid JSON with these exact keys:

{{
  "title": "exact job title string",
  "company": "company name string",
  "seniority": "one of: entry, mid, senior, staff, principal, director",
  "h1b_likely": true or false or null,
  "key_skills": ["up to 10 most important technical skills/tools"],
  "description": "cleaned full job description preserving all requirements, responsibilities, qualifications"
}}

Rules:
- h1b_likely: true if sponsorship mentioned/offered, false if explicitly not offered, null if not mentioned
- key_skills: most important technical skills from the JD only
- description: keep ALL technical details, strip navigation/cookie/footer text

PAGE TEXT:
{text}"""


async def _extract_job_info(page_text: str) -> dict:
    """Extract structured job info from page text using the configured classifier model."""
    from agents.base import BaseAgent
    config = get_full_config()
    agent = BaseAgent(config, "tools_extraction_agent")
    try:
        return await agent.chat_json(
            task="classifier",
            messages=[{
                "role": "user",
                "content": _EXTRACT_PROMPT.format(text=page_text[:6000]),
            }],
            max_tokens=2000,
        )
    except Exception as e:
        logger.warning("job_info_extraction_failed", error=str(e))
        return {
            "title": "Unknown Role",
            "company": "Unknown Company",
            "description": page_text[:4000],
            "key_skills": [],
            "h1b_likely": None,
            "seniority": None,
        }


# ── PDF compilation ───────────────────────────────────────────────────────────

async def _call_pdfworker(tex_content: str) -> bytes:
    """Send LaTeX to the pdfworker service and return raw PDF bytes."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{PDF_WORKER_URL}/compile",
            json={"tex_content": tex_content, "filename": "resume_preview"},
        )
        if resp.status_code == 200:
            return resp.content
        try:
            detail = resp.json().get("detail", resp.text[:500])
        except Exception:
            detail = resp.text[:500]
        raise HTTPException(status_code=422, detail=f"LaTeX compilation failed: {detail}")


# ── Request / Response models ─────────────────────────────────────────────────

class IngestUrlRequest(BaseModel):
    url: str
    role_category: str = "backend"
    description: Optional[str] = None  # manual override — skips scraping when provided


class JobInfoOut(BaseModel):
    title: str
    company: str
    description: str
    h1b_likely: Optional[bool] = None
    seniority: Optional[str] = None
    key_skills: list[str] = []


class IngestUrlResponse(BaseModel):
    job_info: JobInfoOut
    latex: str
    pdf_base64: Optional[str] = None
    compile_error: Optional[str] = None


class CompileLatexRequest(BaseModel):
    tex_content: str


class CompileLatexResponse(BaseModel):
    pdf_base64: str
    size: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/ingest-url", response_model=IngestUrlResponse)
async def ingest_url(request: IngestUrlRequest):
    """
    Scrape any job URL → extract structured info via LLM →
    tailor resume LaTeX → compile to PDF.
    """
    # 1. Scrape (or use manually provided description)
    logger.info("ingest_url_start", url=request.url, manual_description=bool(request.description))
    if request.description and request.description.strip():
        page_text = request.description.strip()
        logger.info("ingest_url_using_manual_description", chars=len(page_text))
    else:
        page_text = await _scrape_url(request.url)
        if not page_text:
            raise HTTPException(status_code=422, detail="Could not extract content from the job URL")

    # 2. Extract structured job info
    info = await _extract_job_info(page_text)
    job_info = JobInfoOut(
        title=info.get("title", "Unknown Role"),
        company=info.get("company", "Unknown Company"),
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

    # 4. Compile PDF (non-fatal — return latex even if compile fails)
    pdf_base64 = None
    compile_error = None
    try:
        pdf_bytes = await _call_pdfworker(latex)
        pdf_base64 = base64.b64encode(pdf_bytes).decode()
        logger.info("preview_compiled", bytes=len(pdf_bytes))
    except Exception as e:
        compile_error = str(e)
        logger.warning("preview_compile_failed", error=compile_error)

    return IngestUrlResponse(
        job_info=job_info,
        latex=latex,
        pdf_base64=pdf_base64,
        compile_error=compile_error,
    )


@router.post("/compile", response_model=CompileLatexResponse)
async def compile_latex(request: CompileLatexRequest):
    """Compile LaTeX source to PDF and return as base64."""
    try:
        pdf_bytes = await _call_pdfworker(request.tex_content)
        return CompileLatexResponse(
            pdf_base64=base64.b64encode(pdf_bytes).decode(),
            size=len(pdf_bytes),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
