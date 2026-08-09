"""
S3 storage utilities for generated documents.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class S3Storage:
    """
    Helper class for S3 operations on generated job application documents.

    S3 key conventions (examples):
    - resumes/{company} - {title}/{candidate} - {title}.pdf
    - covers/{job_id}/cover_letter.pdf
    """

    def __init__(self):
        self.bucket = os.environ.get("S3_BUCKET_NAME", "")
        self.region = os.environ.get("AWS_REGION", "us-east-1")
        self._s3_client = None

    def _get_client(self):
        """Lazily create boto3 S3 client."""
        if self._s3_client is None:
            try:
                import boto3
                self._s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                    region_name=self.region,
                )
            except ImportError:
                raise ImportError("boto3 not installed. Run: pip install boto3")
        return self._s3_client

    def _public_url(self, s3_key: str) -> str:
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{s3_key}"

    async def upload_file(
        self,
        local_path: Path,
        s3_key: str,
        content_type: str = "application/octet-stream",
    ) -> Optional[str]:
        """
        Upload a local file to S3.

        Args:
            local_path: Path to local file
            s3_key: S3 object key
            content_type: MIME type

        Returns:
            Public URL or None if bucket not configured / upload failed.
        """
        if not self.bucket:
            logger.warning("s3_upload_skipped", reason="S3_BUCKET_NAME not configured")
            return None

        if not local_path.exists():
            logger.error("upload_file_not_found", path=str(local_path))
            return None

        try:
            client = self._get_client()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: client.upload_file(
                    str(local_path),
                    self.bucket,
                    s3_key,
                    ExtraArgs={"ContentType": content_type},
                ),
            )
            url = self._public_url(s3_key)
            logger.info("s3_upload_success", s3_key=s3_key, url=url)
            return url
        except Exception as e:
            logger.error("s3_upload_error", s3_key=s3_key, error=str(e))
            return None

    async def download_file_bytes(self, s3_key: str) -> Optional[bytes]:
        """Download a file from S3 and return as bytes."""
        if not self.bucket:
            return None
        try:
            client = self._get_client()
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.get_object(Bucket=self.bucket, Key=s3_key),
            )
            return response["Body"].read()
        except Exception as e:
            logger.error("s3_download_error", s3_key=s3_key, error=str(e))
            return None

    async def generate_presigned_url(
        self, s3_key: str, expiry_seconds: int = 3600
    ) -> Optional[str]:
        """Generate a pre-signed URL for temporary access to an S3 object."""
        if not self.bucket:
            return None
        try:
            client = self._get_client()
            loop = asyncio.get_running_loop()
            url = await loop.run_in_executor(
                None,
                lambda: client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": s3_key},
                    ExpiresIn=expiry_seconds,
                ),
            )
            return url
        except Exception as e:
            logger.error("presigned_url_error", s3_key=s3_key, error=str(e))
            return None

# Module-level singleton
_storage: Optional[S3Storage] = None


def get_storage() -> S3Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage()
    return _storage
