"""
Job Info Extraction

Extracts structured job details (title, company, location, skills, etc.)
from raw scraped/pasted page text via LLM. Used by the manual URL ingestion
flow (Scout) in backend/routers/tools.py.
"""

from __future__ import annotations

import structlog

from agents.base import BaseAgent

logger = structlog.get_logger(__name__)

EXTRACT_PROMPT = """Extract structured job details from the page text below. Return ONLY valid JSON with these exact keys:

{{
  "title": "exact job title string",
  "company": "company name string",
  "location": "city, state or country string, or 'Remote', or null if not found",
  "seniority": "one of: entry, mid, senior, staff, principal, director",
  "h1b_likely": true or false or null,
  "key_skills": ["up to 10 most important technical skills/tools"],
  "description": "cleaned full job description preserving all requirements, responsibilities, qualifications"
}}

Rules:
- company: extract the hiring organization's name. Check ALL of these sources in order:
    1. Explicit "Company:" or "Employer:" labels
    2. "About [Company Name]" or "About [Company] & ..." sections — use the primary organization name from that heading (e.g. "About Lean In & The Sandberg Bernthal Family Foundation" → "Lean In")
    3. The domain name or logo text if visible
    4. Any "Posted by" or "Hiring at" references
  NEVER return "Not Specified", "N/A", "Unknown", or any placeholder — if truly undetectable use "Unknown Company"
- location: city and state (e.g. "Austin, TX"), country, "Remote", or null if not mentioned
- h1b_likely: true if sponsorship mentioned/offered, false if explicitly not offered, null if not mentioned
- key_skills: most important technical skills from the JD only
- description: keep ALL technical details, strip navigation/cookie/footer text

PAGE TEXT:
{text}"""

_PLACEHOLDER_VALUES = {"not specified", "n/a", "unknown", "none", "", "not available", "unspecified"}


async def extract_job_info(page_text: str) -> dict:
    """Extract structured job info from page text using the configured classifier model."""
    from backend.config import get_full_config  # lazy: agents/ doesn't depend on backend/ at module scope

    config = get_full_config()
    agent = BaseAgent(config, "tools_extraction_agent")
    try:
        return await agent.chat_json(
            task="classifier",
            messages=[{
                "role": "user",
                "content": EXTRACT_PROMPT.format(text=page_text[:6000]),
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


def clean_extracted_field(value: str | None, fallback: str) -> str:
    """Replace LLM placeholder non-answers ("Not Specified", "N/A", ...) with a real fallback."""
    if not value or value.strip().lower() in _PLACEHOLDER_VALUES:
        return fallback
    return value.strip()
