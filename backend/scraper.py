"""
Job page scraping and job-board/ATS source detection.

Used by the manual URL ingestion flow (Scout) in backend/routers/tools.py.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import structlog
from bs4 import BeautifulSoup
from fastapi import HTTPException

logger = structlog.get_logger(__name__)

# ── Job page scraping ──────────────────────────────────────────────────────

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


def extract_text_from_html(html: str) -> str:
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
            text = extract_text_from_html(resp.text)
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
        text = body_text.strip() if len(body_text.strip()) > 200 else extract_text_from_html(html)
        if len(text) < 200:
            logger.warning("scrape_playwright_thin", url=url, chars=len(text))
            return None
        logger.info("scrape_playwright_ok", url=url, chars=len(text))
        return text[:8000]
    except Exception as e:
        logger.warning("scrape_playwright_failed", url=url, error=str(e))
        return None


async def scrape_url(url: str) -> str:
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


# ── Source detection ─────────────────────────────────────────────────────

_SOURCE_MAP = {
    "linkedin": "linkedin",
    "lever": "lever",
    "greenhouse": "greenhouse",
    "myworkday": "workday",
    "workday": "workday",
    "ashbyhq": "ashby",
    "ashby": "ashby",
    "smartrecruiters": "smartrecruiters",
    "jobright": "jobright",
    "indeed": "indeed",
    "glassdoor": "glassdoor",
    "dice": "dice",
    "icims": "icims",
    "taleo": "taleo",
    "bamboohr": "bamboohr",
    "wellfound": "wellfound",
    "workable": "workable",
    "recruitee": "recruitee",
}


def source_from_url(url: str) -> str:
    """Detect job board / ATS source from URL hostname."""
    try:
        hostname = (urlparse(url).hostname or "").lower().replace("www.", "")
        for keyword, source in _SOURCE_MAP.items():
            if keyword in hostname:
                return source
        # fallback: use first domain label (e.g. jobs.acme.com → "acme")
        parts = hostname.split(".")
        return parts[-2] if len(parts) >= 2 else (parts[0] or "manual")
    except Exception:
        return "manual"
