"""
Thin HTTP client for the pdfworker service (LaTeX -> PDF compilation).

Used directly by the live-preview/compile endpoints in
backend/routers/tools.py. Note: agents/resume_tailor.py and
agents/cover_letter.py each also call pdfworker for their own generation
flows, with their own retry/error handling — this client is intentionally
the simple, synchronous-style variant for the interactive preview path.
"""

from __future__ import annotations

import os

import httpx
import structlog
from fastapi import HTTPException

logger = structlog.get_logger(__name__)

PDF_WORKER_URL = os.environ.get("PDF_WORKER_URL", "http://pdfworker:8001")


async def call_pdfworker(tex_content: str) -> bytes:
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
