"""
Indeed job scraper using Playwright + BeautifulSoup4.

Navigates Indeed search results for each keyword/category, extracts job data,
handles pagination, and deduplicates by job_id.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from agents.base import BaseAgent

logger = structlog.get_logger(__name__)

INDEED_BASE = "https://www.indeed.com"

# Map Indeed's relative date strings to approximate hours
POSTED_DATE_MAP = {
    "just posted": 0,
    "today": 0,
    "1 day ago": 24,
    "2 days ago": 48,
    "3 days ago": 72,
}


def parse_indeed_posted_at(text: str) -> Optional[datetime]:
    """Parse Indeed's human-readable posted date into a UTC datetime."""
    if not text:
        return None
    text = text.lower().strip()

    if "just posted" in text or "today" in text:
        return datetime.now(timezone.utc)

    match = re.search(r"(\d+)\s+day", text)
    if match:
        days = int(match.group(1))
        return datetime.now(timezone.utc) - timedelta(days=days)

    match = re.search(r"(\d+)\s+hour", text)
    if match:
        hours = int(match.group(1))
        return datetime.now(timezone.utc) - timedelta(hours=hours)

    match = re.search(r"(\d+)\s+minute", text)
    if match:
        minutes = int(match.group(1))
        return datetime.now(timezone.utc) - timedelta(minutes=minutes)

    return None


def extract_job_id_from_url(url: str) -> Optional[str]:
    """Extract Indeed job key (jk) from a URL."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    if "jk" in params:
        return params["jk"][0]
    # Try from path pattern /viewjob?jk=...
    match = re.search(r"jk=([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    # Fallback: hash the URL
    return hashlib.md5(url.encode()).hexdigest()[:16]


class ScraperAgent(BaseAgent):
    """
    Scrapes Indeed.com for job listings based on config-defined categories and keywords.

    Uses Playwright for browser automation (handles JS rendering) and
    BeautifulSoup4 for HTML parsing.
    """

    def __init__(self, config: dict):
        super().__init__(config, "scraper_agent")
        self.search_config = config.get("search", {})
        self.hours_old = self.search_config.get("hours_old", 4)
        self.location = self.search_config.get("location", "United States")
        self.categories = self.search_config.get("job_categories", [])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scrape_all_categories(
        self,
        log_callback=None,
    ) -> list[dict]:
        """
        Scrape all configured job categories and return deduplicated job list.

        Args:
            log_callback: Optional async callable(message: str) for progress logs

        Returns:
            List of job dicts with keys:
                job_id, title, company, location, link, description,
                role_category, source, posted_at, scraped_at
        """
        all_jobs: list[dict] = []
        seen_ids: set[str] = set()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--no-first-run",
                    "--no-zygote",
                    "--single-process",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )

            try:
                for category in self.categories:
                    cat_name = category.get("name", "unknown")
                    keywords = category.get("keywords", [])
                    count_needed = category.get("count", 10)

                    if log_callback:
                        await log_callback(
                            f"Scraping category '{cat_name}' with {len(keywords)} keywords, target {count_needed} jobs"
                        )
                    self.log.info(
                        "scraping_category",
                        category=cat_name,
                        keywords=keywords,
                        count_needed=count_needed,
                    )

                    cat_jobs: list[dict] = []
                    for keyword in keywords:
                        if len(cat_jobs) >= count_needed:
                            break
                        try:
                            jobs = await self._scrape_keyword(
                                context=context,
                                keyword=keyword,
                                category=cat_name,
                                max_jobs=count_needed - len(cat_jobs),
                            )
                            for job in jobs:
                                if job["job_id"] not in seen_ids:
                                    seen_ids.add(job["job_id"])
                                    cat_jobs.append(job)
                        except Exception as e:
                            self.log.error(
                                "keyword_scrape_error",
                                keyword=keyword,
                                error=str(e),
                            )
                            continue

                    all_jobs.extend(cat_jobs)
                    if log_callback:
                        await log_callback(
                            f"Category '{cat_name}': found {len(cat_jobs)} jobs"
                        )
                    self.log.info(
                        "category_done",
                        category=cat_name,
                        jobs_found=len(cat_jobs),
                    )

            finally:
                await browser.close()

        self.log.info("scraping_complete", total_jobs=len(all_jobs))
        return all_jobs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _scrape_keyword(
        self,
        context: BrowserContext,
        keyword: str,
        category: str,
        max_jobs: int,
    ) -> list[dict]:
        """Scrape Indeed for a single keyword, up to max_jobs results."""
        jobs: list[dict] = []
        page = await context.new_page()

        try:
            start = 0
            while len(jobs) < max_jobs:
                url = self._build_search_url(keyword, start)
                self.log.debug("navigating", url=url, collected=len(jobs))

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    # Wait for job cards to appear
                    try:
                        await page.wait_for_selector(
                            "[data-jk], .job_seen_beacon, .jobsearch-ResultsList li",
                            timeout=10000,
                        )
                    except Exception:
                        self.log.warning("no_job_cards_found", url=url)
                        break

                    # Small delay to let dynamic content load
                    await asyncio.sleep(1.5)

                    html = await page.content()
                except Exception as e:
                    self.log.error("page_load_error", url=url, error=str(e))
                    break

                page_jobs = self._parse_search_page(html, category)
                if not page_jobs:
                    self.log.info("no_more_jobs", keyword=keyword, start=start)
                    break

                for job in page_jobs:
                    if len(jobs) >= max_jobs:
                        break
                    # Fetch full description if available
                    if job.get("link") and not job.get("description"):
                        desc = await self._fetch_job_description(context, job["link"])
                        job["description"] = desc

                    jobs.append(job)

                # Check if there's a next page
                has_next = await self._has_next_page(page)
                if not has_next:
                    break

                start += 10
                await asyncio.sleep(2)  # Polite crawl delay

        finally:
            await page.close()

        return jobs

    def _build_search_url(self, keyword: str, start: int = 0) -> str:
        """Build Indeed search URL with date filter."""
        # fromage=1 = last 24 hours (Indeed doesn't have 4hr filter)
        params = urllib.parse.urlencode(
            {
                "q": keyword,
                "l": self.location,
                "fromage": "1",  # Last 24 hours
                "sort": "date",
                "start": str(start),
            }
        )
        return f"{INDEED_BASE}/jobs?{params}"

    def _parse_search_page(self, html: str, category: str) -> list[dict]:
        """Parse Indeed search results page HTML into job dicts."""
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        scraped_at = datetime.now(timezone.utc)

        # Indeed uses several possible container patterns
        # Try mosaic job card pattern (most common)
        job_cards = soup.select("li.css-5lfssm, li[class*='css-'] .job_seen_beacon")

        if not job_cards:
            # Fallback: find all job cards by data-jk attribute
            job_cards = soup.find_all(attrs={"data-jk": True})

        if not job_cards:
            # Another fallback: find result list items
            results_list = soup.select_one("#mosaic-provider-jobcards ul, .jobsearch-ResultsList")
            if results_list:
                job_cards = results_list.find_all("li", recursive=False)

        self.log.debug("found_job_cards", count=len(job_cards), category=category)

        for card in job_cards:
            try:
                job = self._parse_job_card(card, category, scraped_at)
                if job:
                    jobs.append(job)
            except Exception as e:
                self.log.debug("card_parse_error", error=str(e))
                continue

        return jobs

    def _parse_job_card(
        self,
        card,
        category: str,
        scraped_at: datetime,
    ) -> Optional[dict]:
        """Extract job data from a single Indeed job card."""
        # Job ID
        job_id = card.get("data-jk")
        if not job_id:
            # Look for link with jk param
            link_tag = card.select_one("a[data-jk], a[id*='job_']")
            if link_tag:
                job_id = link_tag.get("data-jk") or extract_job_id_from_url(
                    link_tag.get("href", "")
                )

        # Title
        title_el = card.select_one(
            "h2.jobTitle a span[title], h2.jobTitle span[title], "
            "[data-testid='job-title'] span, h2 a span[title], "
            ".jobTitle span[title]"
        )
        title = title_el.get_text(strip=True) if title_el else None

        if not title:
            title_el = card.select_one("h2.jobTitle, [data-testid='job-title']")
            title = title_el.get_text(strip=True) if title_el else None

        if not title:
            return None

        # Link
        link_el = card.select_one("h2.jobTitle a, [data-testid='job-title'] a, a.jcs-JobTitle")
        href = link_el.get("href", "") if link_el else ""
        if href and not href.startswith("http"):
            href = INDEED_BASE + href

        if not job_id and href:
            job_id = extract_job_id_from_url(href)

        if not job_id:
            return None

        # Company
        company_el = card.select_one(
            "[data-testid='company-name'], .companyName, "
            "span.companyName, [class*='companyName']"
        )
        company = company_el.get_text(strip=True) if company_el else "Unknown"

        # Location
        location_el = card.select_one(
            "[data-testid='text-location'], .companyLocation, "
            "div.companyLocation, [class*='companyLocation']"
        )
        location = location_el.get_text(strip=True) if location_el else ""

        # Posted date
        date_el = card.select_one(
            "[data-testid='myJobsStateDate'], span.date, "
            ".result-link-bar-container span.date, [class*='date']"
        )
        posted_text = date_el.get_text(strip=True) if date_el else ""
        posted_at = parse_indeed_posted_at(posted_text)

        # Snippet / description preview
        snippet_el = card.select_one(
            ".job-snippet, [data-testid='job-snippet'], "
            "ul.jobCardShelfContainer, div.underShelfFooter"
        )
        description = snippet_el.get_text(separator=" ", strip=True) if snippet_el else ""

        return {
            "job_id": job_id,
            "title": title,
            "company": company,
            "location": location,
            "link": href,
            "description": description,
            "role_category": category,
            "source": "indeed",
            "posted_at": posted_at.isoformat() if posted_at else None,
            "scraped_at": scraped_at.isoformat(),
        }

    async def _fetch_job_description(
        self, context: BrowserContext, url: str
    ) -> str:
        """Fetch and extract the full job description from the job detail page."""
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_selector(
                    "#jobDescriptionText, .jobsearch-JobComponent-description",
                    timeout=8000,
                )
            except Exception:
                pass

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            desc_el = soup.select_one(
                "#jobDescriptionText, "
                ".jobsearch-JobComponent-description, "
                "[data-testid='job-description'], "
                ".jobDescriptionContent"
            )
            if desc_el:
                return desc_el.get_text(separator="\n", strip=True)

            return ""

        except Exception as e:
            self.log.debug("description_fetch_error", url=url, error=str(e))
            return ""
        finally:
            await page.close()

    async def _has_next_page(self, page: Page) -> bool:
        """Check if there's a next page of results."""
        try:
            next_btn = await page.query_selector(
                "a[data-testid='pagination-page-next'], "
                "a[aria-label='Next Page'], "
                ".pagination a:last-child"
            )
            return next_btn is not None
        except Exception:
            return False
