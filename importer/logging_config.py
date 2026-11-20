"""Logging configuration for 5ch Archive Importer."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logger(log_dir: Path | None) -> logging.Logger:
    logger = logging.getLogger("importer")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler
    if log_dir:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "importer.log"
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception as exc:
            logger.warning(f"Could not initialize file log at {log_dir}: {exc}")

    return logger
