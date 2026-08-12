"""
Shared pdfworker call for agents that compile LaTeX to a PDF file on disk.

Used by resume_tailor.py and cover_letter.py, which both compile as part of
a larger batch-generation flow and want the same non-fatal "log and return
None" behavior on failure -- unlike backend/pdfworker_client.py's
call_pdfworker, which backs the interactive preview endpoints and is meant
to raise (surfacing the compile error straight to the API caller) instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx


async def compile_pdf_to_file(
    client: httpx.AsyncClient,
    pdf_worker_url: str,
    tex_content: str,
    output_name: str,
    job_dir: Path,
    log,
    label: str = "PDF",
    log_prefix: str = "pdf",
    timeout: float = 90.0,
) -> Optional[Path]:
    """
    Compile LaTeX via pdfworker and write the result to job_dir/output_name.pdf.

    Non-fatal: logs and returns None on any failure (unreachable pdfworker,
    non-200 response, or unexpected error) rather than raising, since batch
    resume/cover-letter generation should keep going even if one PDF fails.

    Args:
        client: shared httpx.AsyncClient (from agent's _get_http_client())
        pdf_worker_url: base URL of the pdfworker service
        tex_content: full LaTeX source to compile
        output_name: filename stem for both the pdfworker request and the output file
        job_dir: directory to write "{output_name}.pdf" into
        log: structlog bound logger (agent's self.log)
        label: human-readable name for log messages, e.g. "PDF" or "cover letter PDF"
        log_prefix: event-name prefix for success/error log events, e.g. "pdf" -> "pdf_compiled"
        timeout: request timeout in seconds
    """
    pdf_path = job_dir / f"{output_name}.pdf"
    try:
        response = await client.post(
            f"{pdf_worker_url}/compile",
            json={"tex_content": tex_content, "filename": output_name},
            timeout=timeout,
        )
        if response.status_code == 200:
            pdf_path.write_bytes(response.content)
            log.info(f"{log_prefix}_compiled", path=str(pdf_path), size=len(response.content))
            return pdf_path

        log.error("pdf_worker_error", status=response.status_code, body=response.text[:500])
        return None

    except httpx.ConnectError:
        log.warning(
            "pdf_worker_unavailable",
            url=pdf_worker_url,
            msg=f"pdfworker service not reachable; skipping {label} compilation",
        )
        return None
    except Exception as e:
        log.error(f"{log_prefix}_compile_error", error=str(e))
        return None
