"""
Jobright.ai scraper — Recommended feed → job detail pages.

Auth setup (one-time):
  1. Log in to jobright.ai in Chrome
  2. Open DevTools → Console, run:
       copy(JSON.stringify([...document.cookie.split(';').map(c=>{const[n,...v]=c.trim().split('=');return{name:n,value:v.join('='),domain:'.jobright.ai',path:'/',secure:true,httpOnly:false,sameSite:'Lax'}})]))
  3. python scripts/save_cookies.py  (paste when prompted)
  4. docker-compose restart backend

Flow:
  Phase 1 — Load /jobs/recommend, collect all job links (/jobs/info/<id>), scroll for more
  Phase 2 — Open each detail page concurrently (max 4 at a time), extract full description
  Phase 3 — Return merged list with descriptions for ranker
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
import structlog
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, async_playwright

from agents.base import BaseAgent

logger = structlog.get_logger(__name__)

RECOMMEND_URL = "https://jobright.ai/jobs/recommend?from=homepage"
JOBRIGHT_BASE = "https://jobright.ai"
PROFILE_DIR   = Path(os.environ.get("JOBRIGHT_PROFILE_DIR", "/jobright-profile"))
COOKIES_FILE  = PROFILE_DIR / "cookies.json"

DETAIL_CONCURRENCY = 4   # parallel detail page fetches

# ── Category inference ────────────────────────────────────────────────────────

_AIML = {"machine learning","ml engineer","ai engineer","data scientist","llm","nlp",
          "natural language","computer vision","deep learning","pytorch","tensorflow",
          "generative ai","gen ai","mlops","applied scientist","research scientist"}
_FS   = {"full stack","fullstack","full-stack","frontend","front end","front-end",
          "react engineer","angular","vue","next.js","node.js engineer","web engineer"}

def _category(title: str) -> str:
    t = title.lower()
    if any(k in t for k in _AIML): return "aiml"
    if any(k in t for k in _FS):   return "fullstack"
    return "backend"

# ── Date helpers ──────────────────────────────────────────────────────────────

_DATE = [
    (re.compile(r"(\d+)\s+minute", re.I), lambda m: timedelta(minutes=int(m.group(1)))),
    (re.compile(r"(\d+)\s+hour",   re.I), lambda m: timedelta(hours=int(m.group(1)))),
    (re.compile(r"(\d+)\s+day",    re.I), lambda m: timedelta(days=int(m.group(1)))),
    (re.compile(r"(\d+)\s+week",   re.I), lambda m: timedelta(weeks=int(m.group(1)))),
    (re.compile(r"just\s+now|today", re.I), lambda _: timedelta(0)),
]

def _posted(text: str) -> Optional[datetime]:
    for pat, fn in _DATE:
        m = pat.search(text)
        if m:
            try: return datetime.now(timezone.utc) - fn(m)
            except: pass
    return None

def _job_id(href: str) -> str:
    m = re.search(r"/jobs/info/([^/?#]+)", href)
    return m.group(1) if m else hashlib.md5(href.encode()).hexdigest()[:16]


# ── Scraper ───────────────────────────────────────────────────────────────────

class JobrightScraper(BaseAgent):

    def __init__(self, config: dict):
        super().__init__(config, "jobright_scraper")
        search          = config.get("search", {})
        self.categories = search.get("job_categories", [])
        # Build enabled set from config (support per-category enable flag)
        self._enabled   = {c["name"] for c in self.categories if c.get("enabled", True)}
        self._total     = sum(
            c.get("count", 10) for c in self.categories if c.get("enabled", True)
        )
        # Collect 2x more stubs than needed to absorb losses from:
        # category misclassification, detail-page failures, and already-in-DB jobs.
        self._collect_target = self._total * 2

    # ── Public API ────────────────────────────────────────────────────────────

    async def scrape_all_categories(self, log_callback=None) -> list[dict]:
        if not COOKIES_FILE.exists():
            raise RuntimeError(
                f"No cookies at {COOKIES_FILE}.\n"
                "Run the DevTools one-liner then  python scripts/save_cookies.py"
            )

        cookies = json.loads(COOKIES_FILE.read_text())

        if log_callback:
            await log_callback(f"[jobright] Collecting links from Recommended feed (want {self._total}, fetching up to {self._collect_target})")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            await context.add_cookies(cookies)

            # Phase 1 — collect stub cards from recommend feed
            page   = await context.new_page()
            stubs  = await self._collect_stubs(page, log_callback)
            await page.close()

            if log_callback:
                await log_callback(f"[jobright] {len(stubs)} links found — fetching job details...")

            # Phase 2 — open each detail page for full description
            jobs = await self._fetch_details(context, stubs, log_callback)

            await context.close()
            await browser.close()

        # Filter to enabled categories and apply per-category count cap
        jobs = self._filter_and_cap(jobs)

        if log_callback:
            cats = {c: sum(1 for j in jobs if j["role_category"] == c)
                    for c in ("backend", "fullstack", "aiml")}
            await log_callback(
                f"[jobright] Done — {len(jobs)} jobs  "
                f"(backend:{cats['backend']} fullstack:{cats['fullstack']} aiml:{cats['aiml']})"
            )

        return jobs

    # ── Phase 1: collect stubs from recommend feed ────────────────────────────

    async def _collect_stubs(self, page: Page, log_callback=None) -> list[dict]:
        """Navigate recommend feed, scroll, collect all job card stubs."""
        await page.goto(RECOMMEND_URL, wait_until="domcontentloaded", timeout=30000)

        try:
            # Wait for at least one job link to appear
            await page.wait_for_selector('a[href*="/jobs/info/"]', timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2)

        if "/login" in page.url or "/signin" in page.url:
            raise RuntimeError(
                "Jobright session expired. Re-run the DevTools one-liner "
                "→ scripts/save_cookies.py → docker-compose restart backend"
            )

        stubs: list[dict] = []
        seen:  set[str]   = set()

        for attempt in range(15):
            new = self._parse_stubs(await page.content(), seen)
            stubs.extend(new)
            for s in new: seen.add(s["job_id"])

            if log_callback and new:
                await log_callback(f"[jobright] {len(stubs)} links collected...")

            if len(stubs) >= self._collect_target:
                break

            # Scroll to load more cards
            await page.evaluate("""
                const el = document.querySelector(
                    '[class*="jobs-list"], [class*="job-list"], [class*="jobList"]'
                ) || document.documentElement;
                el.scrollTop = el.scrollHeight;
                window.scrollTo(0, document.body.scrollHeight);
            """)
            await asyncio.sleep(2.5)

            if not new and attempt > 2:
                break   # no new cards after several scrolls

        return stubs[:self._collect_target]

    def _parse_stubs(self, html: str, seen: set[str]) -> list[dict]:
        """Extract job card stubs from the recommend feed HTML."""
        soup       = BeautifulSoup(html, "lxml")
        scraped    = datetime.now(timezone.utc).isoformat()
        stubs:     list[dict] = []
        seen_local: set[str]  = set()  # deduplicate within this parse call

        # From DevTools: links are  a[href^="/jobs/info/<id>"]
        for link_el in soup.select('a[href*="/jobs/info/"]'):
            href = link_el.get("href", "")
            if not re.search(r"/jobs/info/[^/?#]{4,}", href):
                continue
            if not href.startswith("http"):
                href = JOBRIGHT_BASE + href

            job_id = _job_id(href)
            if job_id in seen or job_id in seen_local:
                continue

            # Job title — must have an h2 inside; skip score-panel / nav links
            h2 = link_el.find("h2")
            if not h2:
                continue
            title = h2.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            # Skip if h2 contains score badge text (e.g. "97%STRONG MATCH")
            if "%" in title or "match" in title.lower():
                continue

            seen_local.add(job_id)

            # Walk up to find card root (contains company, location, H1B badge)
            card = link_el.parent
            for _ in range(6):
                if card is None: break
                if len(card.get_text(strip=True)) > 100: break
                card = card.parent

            card_text = card.get_text(" ", strip=True) if card else ""

            # Company — first text block AFTER the h2 (skip "alumni/applicant" rows above)
            company = "Unknown"
            for sib in h2.find_next_siblings():
                t = sib.get_text(strip=True)
                if (t and len(t) > 2 and len(t) < 120
                        and "alumn" not in t.lower()
                        and "applicant" not in t.lower()
                        and "%" not in t
                        and "$" not in t
                        and "ago" not in t.lower()):
                    company = t.split("/")[0].split("·")[0].strip()
                    break

            # Location
            # Jobright cards prefix city names with a work-type label ("Company" = on-site,
            # "Hybrid", "Stage", etc.) — strip those before accepting the match.
            _WORK_TYPE_PREFIX = re.compile(
                r"^(Company|Stage|Hybrid|Contract|Part[\-\s]time|Full[\-\s]time)\s+", re.I
            )
            location = ""
            m = re.search(
                r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?,\s*[A-Z]{2}|Remote|United States)\b",
                card_text,
            )
            if m:
                location = _WORK_TYPE_PREFIX.sub("", m.group(1)).strip()

            # Posted time
            posted_at = None
            m2 = re.search(r"\d+\s+(?:minute|hour|day|week)s?\s+ago", card_text, re.I)
            if m2: posted_at = _posted(m2.group(0))

            # H1B badge on card
            h1b_likely: Optional[bool] = None
            h1b_notes = ""
            if re.search(r"H1B\s+Sponsor", card_text, re.I):
                h1b_likely = True
                h1b_notes  = "jobright badge"
            elif re.search(r"No\s+(?:H1B|Sponsor)", card_text, re.I):
                h1b_likely = False
                h1b_notes  = "jobright badge"

            stubs.append({
                "job_id":        job_id,
                "title":         title,
                "company":       company,
                "location":      location,
                "link":          href,
                "description":   "",
                "role_category": _category(title),
                "source":        "jobright",
                "posted_at":     posted_at,
                "scraped_at":    scraped,
                "_h1b_from_source":       h1b_likely,
                "_h1b_notes_from_source": h1b_notes,
            })

        return stubs

    # ── Phase 2: fetch detail pages ───────────────────────────────────────────

    async def _fetch_details(
        self,
        context: BrowserContext,
        stubs: list[dict],
        log_callback=None,
    ) -> list[dict]:
        """Open each job detail page and merge in description + extra fields."""
        sem  = asyncio.Semaphore(DETAIL_CONCURRENCY)
        done = 0

        async def fetch_one(stub: dict) -> dict:
            nonlocal done
            async with sem:
                detail = await self._fetch_detail_page(context, stub["link"])
                stub.update(detail)
                done += 1
                if log_callback and done % 5 == 0:
                    await log_callback(f"[jobright] {done}/{len(stubs)} detail pages fetched...")
                return stub

        results = await asyncio.gather(*[fetch_one(s) for s in stubs], return_exceptions=True)

        jobs = []
        for r in results:
            if isinstance(r, Exception):
                self.log.warning("detail_fetch_error", error=str(r))
            else:
                jobs.append(r)
        return jobs

    async def _fetch_detail_page(self, context: BrowserContext, url: str) -> dict:
        """Open a single /jobs/info/<id> page and extract description + fields."""
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(1.5)

            # Try __NEXT_DATA__ — Next.js embeds page props as JSON
            next_data_raw: Optional[str] = await page.evaluate("""
                () => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? el.textContent : null;
                }
            """)
            if next_data_raw:
                detail = self._parse_next_data(next_data_raw)
                if detail.get("description"):
                    return await self._enrich_with_original(detail)

            # DOM fallback
            detail = self._parse_detail_dom(await page.content())
            return await self._enrich_with_original(detail)

        except Exception as e:
            self.log.debug("detail_page_error", url=url, error=str(e))
            return {}
        finally:
            await page.close()

    def _parse_next_data(self, raw: str) -> dict:
        """Extract job fields from Next.js __NEXT_DATA__ JSON."""
        try:
            data = json.loads(raw)
        except Exception:
            return {}

        # Walk the props tree to find a dict with description/jobDescription
        def search(obj, depth=0):
            if depth > 8:
                return None
            if isinstance(obj, dict):
                desc = (obj.get("description") or obj.get("jobDescription")
                        or obj.get("content") or obj.get("summary"))
                if desc and len(str(desc)) > 50:
                    return obj
                for v in obj.values():
                    r = search(v, depth + 1)
                    if r: return r
            elif isinstance(obj, list):
                for item in obj:
                    r = search(item, depth + 1)
                    if r: return r
            return None

        record = search(data)
        if not record:
            return {}

        desc = (record.get("description") or record.get("jobDescription")
                or record.get("content") or record.get("summary") or "")

        # Salary
        salary = record.get("salary") or record.get("salaryRange") or ""
        if isinstance(salary, dict):
            lo = salary.get("min") or salary.get("from") or ""
            hi = salary.get("max") or salary.get("to") or ""
            salary = f"${lo}K–${hi}K" if lo or hi else ""

        # H1B
        h1b_likely: Optional[bool] = None
        h1b_notes = ""
        for key in ("h1b", "h1bSponsor", "visaSponsorship", "sponsorship"):
            val = record.get(key)
            if val is None: continue
            if isinstance(val, bool):
                h1b_likely = val
            elif isinstance(val, str):
                lo_v = val.lower()
                if any(x in lo_v for x in ("yes","true","likely","sponsor","available")):
                    h1b_likely = True
                elif any(x in lo_v for x in ("no","false","not","none","unlikely")):
                    h1b_likely = False
            h1b_notes = "jobright detail API"
            break

        original_link = (
            record.get("originalLink") or record.get("applyUrl") or
            record.get("externalUrl") or record.get("jobUrl") or
            record.get("applicationUrl") or ""
        )
        # Only keep external links (not jobright-internal)
        if isinstance(original_link, str) and "jobright" in original_link.lower():
            original_link = ""

        return {
            "description":            str(desc).strip(),
            "salary":                 str(salary),
            "original_link":          str(original_link) if original_link else "",
            "_h1b_from_source":       h1b_likely,
            "_h1b_notes_from_source": h1b_notes,
        }

    def _parse_detail_dom(self, html: str) -> dict:
        """DOM extraction fallback for detail pages."""
        soup      = BeautifulSoup(html, "lxml")
        page_text = soup.get_text(" ", strip=True)

        # Description — look for large text blocks
        desc = ""
        for sel in (
            '[class*="description"]', '[class*="job-detail"]',
            '[class*="jobDetail"]',   '[class*="job_desc"]',
            'section', 'article',
        ):
            el = soup.select_one(sel)
            if el:
                t = el.get_text(separator="\n", strip=True)
                if len(t) > 200:
                    desc = t
                    break

        # Salary
        salary = ""
        m = re.search(r"\$[\d,]+[Kk]?\s*[-–]\s*\$[\d,]+[Kk]?", page_text)
        if m: salary = m.group(0)

        # H1B
        h1b_likely: Optional[bool] = None
        h1b_notes = ""
        if re.search(r"H1B\s+Sponsor", page_text, re.I):
            h1b_likely = True
            h1b_notes  = "jobright detail page"
        elif re.search(r"No\s+(?:H1B|Sponsor)", page_text, re.I):
            h1b_likely = False
            h1b_notes  = "jobright detail page"

        # Original job link — anchor whose visible text mentions "original" or "apply"
        original_link = ""
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(strip=True).lower()
            if (
                href.startswith("http")
                and "jobright" not in href.lower()
                and (
                    ("original" in text and "job" in text)
                    or text in ("apply now", "view original", "original posting")
                )
            ):
                original_link = href
                break

        return {
            "description":            desc,
            "salary":                 salary,
            "original_link":          original_link,
            "_h1b_from_source":       h1b_likely,
            "_h1b_notes_from_source": h1b_notes,
        }

    # ── Original job post enrichment ─────────────────────────────────────────

    async def _enrich_with_original(self, detail: dict) -> dict:
        """
        Fetch the original job post URL found on the jobright detail page
        and append its content to the description.
        """
        original_link = detail.pop("original_link", "") or ""
        if not original_link:
            return detail

        original_desc = await self._fetch_original_post(original_link)
        if original_desc:
            existing = (detail.get("description") or "").strip()
            detail["description"] = (
                f"{existing}\n\n--- Original Job Post ---\n{original_desc}"
                if existing else original_desc
            )
            self.log.info(
                "original_post_fetched",
                url=original_link,
                chars=len(original_desc),
            )
        return detail

    async def _fetch_original_post(self, url: str) -> str:
        """
        Fetch the original job posting URL via httpx and extract the JD text
        using common ATS/job-board CSS selectors.
        """
        # Common selectors for popular ATS and job boards
        _SELECTORS = [
            # Greenhouse
            "#content", ".job__description", ".posting-requirements",
            # Lever
            ".posting-description", ".section-wrapper",
            # Workday
            "[data-automation-id='jobPostingDescription']",
            # Ashby
            ".ashby-job-posting-brief-description",
            # LinkedIn (public)
            ".description__text", ".show-more-less-html",
            # Indeed
            "#jobDescriptionText",
            # Generic fallbacks
            '[class*="job-description"]', '[class*="jobDescription"]',
            '[class*="job-detail"]', '[class*="description"]',
            "article", "main",
        ]

        try:
            async with httpx.AsyncClient(
                timeout=12.0,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    self.log.debug(
                        "original_post_non200",
                        url=url,
                        status=resp.status_code,
                    )
                    return ""

                soup = BeautifulSoup(resp.text, "lxml")
                # Strip noise nodes
                for tag in soup(["script", "style", "nav", "header", "footer",
                                  "noscript", "iframe", "form"]):
                    tag.decompose()

                for sel in _SELECTORS:
                    el = soup.select_one(sel)
                    if el:
                        text = el.get_text(separator="\n", strip=True)
                        if len(text) > 150:
                            return text[:6000]

                # Last resort — body text
                body = soup.find("body")
                return body.get_text(separator="\n", strip=True)[:6000] if body else ""

        except Exception as e:
            self.log.debug("original_post_fetch_failed", url=url, error=str(e))
            return ""

    # ── Post-processing ───────────────────────────────────────────────────────

    def _filter_and_cap(self, jobs: list[dict]) -> list[dict]:
        """Keep only enabled categories, respect per-category count cap."""
        counts: dict[str, int] = {}
        caps   = {c["name"]: c.get("count", 10) for c in self.categories}
        result = []
        for job in jobs:
            cat = job["role_category"]
            if cat not in self._enabled:
                continue
            counts[cat] = counts.get(cat, 0) + 1
            if counts[cat] <= caps.get(cat, 10):
                result.append(job)
        return result
