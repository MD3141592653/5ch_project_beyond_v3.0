"""Configuration for 5ch Archive Importer."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

# Database configuration
DB_HOST = os.environ.get("DB_HOST", "archive-db")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "archive")
DB_USER = os.environ.get("DB_USER", "muraoka")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgre1234")

# Paths and importer parameters
DEFAULT_JSONL_DIR = Path(os.environ.get("ARCHIVE_JSONL_DIR", PROJECT_ROOT / "5ch_project_beyond" / "jsonl"))
DEFAULT_LOG_DIR = Path(os.environ.get("LOG_DIR", PROJECT_ROOT / "logs"))
DEFAULT_BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5000"))
DEFAULT_INTERVAL_SEC = int(os.environ.get("IMPORT_INTERVAL_SEC", "0"))
