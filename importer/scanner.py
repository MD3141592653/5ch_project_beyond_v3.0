"""Scanner module for identifying new and updated JSONL files."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


def scan_jsonl_dir(
    jsonl_dir: Path,
    recorded_map: dict[str, dict[str, Any]],
    force: bool = False,
    logger: logging.Logger | None = None,
) -> tuple[list[tuple[Path, bool]], int]:
    """Scans jsonl_dir and compares with recorded_map.

    Returns:
        (files_to_process, skipped_count)
        where each item in files_to_process is (file_path, is_update)
    """
    if not jsonl_dir.exists():
        if logger:
            logger.warning(f"JSONL directory does not exist: {jsonl_dir}")
        return [], 0

    all_files = sorted(jsonl_dir.glob("*.jsonl"))
    if logger:
        logger.info(f"Scanning {len(all_files)} JSONL files in {jsonl_dir}...")

    files_to_process: list[tuple[Path, bool]] = []
    skipped_count = 0

    for path in all_files:
        try:
            stat = path.stat()
        except OSError as exc:
            if logger:
                logger.warning(f"Could not stat {path.name}: {exc}")
            continue

        file_size = stat.st_size
        mtime_ns = stat.st_mtime_ns
        recorded = recorded_map.get(path.name)

        if force:
            files_to_process.append((path, True))
        elif recorded is None:
            # Brand new file
            files_to_process.append((path, False))
        elif (
            recorded.get("status") != "done"
            or recorded.get("file_size") != file_size
            or recorded.get("mtime_ns") != mtime_ns
        ):
            # File modified or previous run failed
            files_to_process.append((path, True))
        else:
            # Unchanged
            skipped_count += 1

    return files_to_process, skipped_count
