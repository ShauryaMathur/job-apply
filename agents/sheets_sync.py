"""
Google Sheets Sync Agent

Syncs job application data to a configured Google Spreadsheet.
Supports both inserting new jobs and updating existing rows.

Setup Instructions:
-------------------
1. Go to Google Cloud Console -> APIs & Services -> Enable "Google Sheets API"
2. Create a Service Account under IAM -> Service Accounts
3. Download the JSON key file
4. Share your Google Spreadsheet with the service account email (Editor role)
5. Set env vars:
   - GOOGLE_SHEETS_CREDENTIALS_JSON = path to JSON file OR inline JSON string
   - GOOGLE_SPREADSHEET_ID = from spreadsheet URL
   - (optional) GOOGLE_SHEETS_WORKSHEET_NAME = default "Applications"
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from agents.base import BaseAgent

logger = structlog.get_logger(__name__)

# Column order for the spreadsheet
SHEET_COLUMNS = [
    "job_id",
    "title",
    "company",
    "location",
    "link",
    "role_category",
    "match_score",
    "h1b_likely",
    "h1b_notes",
    "status",
    "resume_file",
    "cover_letter_file",
    "email_file",
    "s3_resume_url",
    "s3_cover_letter_url",
    "posted_at",
    "scraped_at",
    "applied_date",
    "notes",
    "source",
]

HEADER_ROW = [col.replace("_", " ").title() for col in SHEET_COLUMNS]


class SheetsSyncAgent(BaseAgent):
    """
    Syncs job application data to Google Sheets.

    Uses the Google Sheets API v4 via google-api-python-client.
    Handles both insert (new job) and update (status change) operations.
    """

    def __init__(self, config: dict):
        super().__init__(config, "sheets_sync_agent")
        self.sheets_config = config.get("sheets", {})
        self.spreadsheet_id = os.environ.get(
            "GOOGLE_SPREADSHEET_ID",
            self.sheets_config.get("spreadsheet_id", ""),
        )
        self.worksheet_name = self.sheets_config.get("worksheet_name", "Applications")
        self._service = None
        self._job_id_to_row: dict[str, int] = {}  # Cache: job_id -> sheet row number

    def _get_credentials(self):
        """Load Google credentials from env var (path or inline JSON)."""
        from google.oauth2.service_account import Credentials

        creds_env = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON", "")
        if not creds_env:
            raise ValueError(
                "GOOGLE_SHEETS_CREDENTIALS_JSON environment variable not set. "
                "See docstring for setup instructions."
            )

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
        ]

        # Check if it's a file path or inline JSON
        if creds_env.startswith("{"):
            # Inline JSON
            info = json.loads(creds_env)
            return Credentials.from_service_account_info(info, scopes=scopes)
        else:
            # File path
            return Credentials.from_service_account_file(creds_env, scopes=scopes)

    def _get_service(self):
        """Lazily initialize the Google Sheets API service."""
        if self._service is not None:
            return self._service

        try:
            from googleapiclient.discovery import build
            creds = self._get_credentials()
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            self.log.info("sheets_service_initialized", spreadsheet_id=self.spreadsheet_id)
        except ImportError:
            raise ImportError(
                "google-api-python-client not installed. "
                "Run: pip install google-api-python-client google-auth"
            )

        return self._service

    async def ensure_header_row(self) -> None:
        """Create or verify the header row exists in the worksheet."""
        import asyncio

        service = self._get_service()
        range_name = f"{self.worksheet_name}!A1:Z1"

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: service.spreadsheets().values().get(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                ).execute()
            )

            existing = result.get("values", [[]])
            if not existing or existing[0] != HEADER_ROW:
                # Write header
                await loop.run_in_executor(
                    None,
                    lambda: service.spreadsheets().values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"{self.worksheet_name}!A1",
                        valueInputOption="RAW",
                        body={"values": [HEADER_ROW]},
                    ).execute()
                )
                self.log.info("header_row_written")

        except Exception as e:
            self.log.error("ensure_header_error", error=str(e))
            raise

    async def _load_existing_jobs(self) -> None:
        """Load existing job_ids from the sheet to build row index cache."""
        import asyncio

        service = self._get_service()
        range_name = f"{self.worksheet_name}!A:A"  # job_id column

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: service.spreadsheets().values().get(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                ).execute()
            )

            values = result.get("values", [])
            for i, row in enumerate(values):
                if row and i > 0:  # Skip header row
                    job_id = row[0]
                    self._job_id_to_row[job_id] = i + 1  # Sheets rows are 1-indexed

        except Exception as e:
            self.log.warning("load_existing_jobs_error", error=str(e))

    def _job_to_row(self, job: dict) -> list[Any]:
        """Convert a job dict to a spreadsheet row."""
        def fmt(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, bool):
                return "Yes" if val else "No"
            if isinstance(val, float):
                return f"{val:.1f}"
            if isinstance(val, list):
                return ", ".join(str(v) for v in val)
            return str(val)

        return [fmt(job.get(col, "")) for col in SHEET_COLUMNS]

    async def sync_jobs(
        self,
        jobs: list[dict],
        log_callback=None,
    ) -> None:
        """
        Sync a list of jobs to Google Sheets.
        Inserts new jobs and skips existing ones (by job_id).

        Args:
            jobs: List of job dicts to sync
            log_callback: Optional async callable for progress logs
        """
        import asyncio

        if not self.spreadsheet_id:
            self.log.warning(
                "sheets_sync_skipped",
                reason="GOOGLE_SPREADSHEET_ID not configured",
            )
            if log_callback:
                await log_callback("Sheets sync skipped: spreadsheet ID not configured")
            return

        try:
            service = self._get_service()
        except Exception as e:
            self.log.error("sheets_service_error", error=str(e))
            if log_callback:
                await log_callback(f"Sheets sync failed: {str(e)}")
            return

        if log_callback:
            await log_callback(f"Syncing {len(jobs)} jobs to Google Sheets...")

        try:
            await self.ensure_header_row()
            await self._load_existing_jobs()
        except Exception as e:
            self.log.error("sheets_setup_error", error=str(e))
            if log_callback:
                await log_callback(f"Sheets setup failed: {str(e)}")
            return

        new_rows = []
        updated_count = 0
        loop = asyncio.get_running_loop()

        for job in jobs:
            job_id = job.get("job_id", "")
            if not job_id:
                continue

            row_data = self._job_to_row(job)

            if job_id in self._job_id_to_row:
                # Update existing row
                row_num = self._job_id_to_row[job_id]
                range_name = f"{self.worksheet_name}!A{row_num}"
                try:
                    await loop.run_in_executor(
                        None,
                        lambda r=range_name, d=row_data: service.spreadsheets().values().update(
                            spreadsheetId=self.spreadsheet_id,
                            range=r,
                            valueInputOption="RAW",
                            body={"values": [d]},
                        ).execute()
                    )
                    updated_count += 1
                except Exception as e:
                    self.log.error("row_update_error", job_id=job_id, error=str(e))
            else:
                # Queue for batch append
                new_rows.append(row_data)

        # Batch append new rows
        if new_rows:
            try:
                await loop.run_in_executor(
                    None,
                    lambda: service.spreadsheets().values().append(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"{self.worksheet_name}!A1",
                        valueInputOption="RAW",
                        insertDataOption="INSERT_ROWS",
                        body={"values": new_rows},
                    ).execute()
                )
                self.log.info("rows_appended", count=len(new_rows))
            except Exception as e:
                self.log.error("batch_append_error", error=str(e))
                if log_callback:
                    await log_callback(f"Sheets append failed: {str(e)}")
                return

        msg = f"Sheets sync complete: {len(new_rows)} new, {updated_count} updated"
        self.log.info("sheets_sync_complete", new=len(new_rows), updated=updated_count)
        if log_callback:
            await log_callback(msg)

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        notes: Optional[str] = None,
        applied_date: Optional[str] = None,
    ) -> None:
        """
        Update the status of a single job in Google Sheets.

        Args:
            job_id: The job's unique ID
            status: New status string
            notes: Optional notes to set
            applied_date: Optional ISO date string for when applied
        """
        import asyncio

        if not self.spreadsheet_id:
            return

        await self._load_existing_jobs()

        if job_id not in self._job_id_to_row:
            self.log.warning("job_not_in_sheets", job_id=job_id)
            return

        service = self._get_service()
        row_num = self._job_id_to_row[job_id]
        loop = asyncio.get_running_loop()

        # Update status column (index 9 = column J)
        status_col_letter = chr(ord("A") + SHEET_COLUMNS.index("status"))
        try:
            await loop.run_in_executor(
                None,
                lambda: service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"{self.worksheet_name}!{status_col_letter}{row_num}",
                    valueInputOption="RAW",
                    body={"values": [[status]]},
                ).execute()
            )

            if notes:
                notes_col_letter = chr(ord("A") + SHEET_COLUMNS.index("notes"))
                await loop.run_in_executor(
                    None,
                    lambda: service.spreadsheets().values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"{self.worksheet_name}!{notes_col_letter}{row_num}",
                        valueInputOption="RAW",
                        body={"values": [[notes]]},
                    ).execute()
                )

            if applied_date:
                applied_col_letter = chr(ord("A") + SHEET_COLUMNS.index("applied_date"))
                await loop.run_in_executor(
                    None,
                    lambda: service.spreadsheets().values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"{self.worksheet_name}!{applied_col_letter}{row_num}",
                        valueInputOption="RAW",
                        body={"values": [[applied_date]]},
                    ).execute()
                )

            self.log.info("status_updated_in_sheets", job_id=job_id, status=status)

        except Exception as e:
            self.log.error("status_update_error", job_id=job_id, error=str(e))
