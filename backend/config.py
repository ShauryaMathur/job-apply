"""
Configuration loader.

Loads config.yaml and merges with environment variables.
Config is loaded once at startup and injected as a dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Locate config.yaml (relative to repo root)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent  # /job-apply/
_CONFIG_PATH = _REPO_ROOT / "config.yaml"


def load_yaml_config(path: Path = _CONFIG_PATH) -> dict:
    """Load and return the YAML config as a plain dict."""
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Pydantic settings (reads from .env / environment)
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    # OpenRouter
    openrouter_api_key: str = ""

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # AWS S3
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    s3_bucket_name: str = ""

    # PostgreSQL
    postgres_user: str = "jobapply"
    postgres_password: str = "jobapply"
    postgres_db: str = "jobapply"
    database_url: str = "postgresql+asyncpg://jobapply:jobapply@localhost:5432/jobapply"

    # Google Sheets
    google_sheets_credentials_json: str = ""
    google_spreadsheet_id: str = ""

    # ChromaDB
    chroma_host: str = "chromadb"
    chroma_port: int = 8000

    # PDF Worker
    pdf_worker_url: str = "http://pdfworker:8001"

    # App
    debug: bool = False
    log_level: str = "INFO"

    class Config:
        env_file = str(_REPO_ROOT / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


# Singleton instances
_settings: Optional[Settings] = None
_yaml_config: Optional[dict] = None


def get_settings() -> Settings:
    """Return singleton Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_yaml_config() -> dict:
    """Return singleton YAML config dict."""
    global _yaml_config
    if _yaml_config is None:
        _yaml_config = load_yaml_config()
    return _yaml_config


def get_full_config() -> dict:
    """
    Return merged config dict combining YAML config + env var overrides.
    This is what agents receive.
    """
    settings = get_settings()
    yaml_cfg = get_yaml_config()

    # Deep merge: env vars override YAML where applicable
    config = dict(yaml_cfg)

    # Inject runtime values that agents need
    config["resumes_dir"] = str(_REPO_ROOT / "resumes")
    config.setdefault("storage", {})
    config["storage"]["s3_bucket"] = settings.s3_bucket_name or config["storage"].get("s3_bucket", "")
    config["storage"]["generated_dir"] = str(_REPO_ROOT / "generated")

    config.setdefault("pdf_worker", {})
    config["pdf_worker"]["service_url"] = settings.pdf_worker_url

    config.setdefault("sheets", {})
    config["sheets"]["spreadsheet_id"] = settings.google_spreadsheet_id or config["sheets"].get("spreadsheet_id", "")

    config.setdefault("llm", {})
    config["llm"]["openrouter_api_key"] = settings.openrouter_api_key

    # ChromaDB
    config["chroma_host"] = settings.chroma_host
    config["chroma_port"] = settings.chroma_port

    return config
