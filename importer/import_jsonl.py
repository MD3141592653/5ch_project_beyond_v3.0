#!/usr/bin/env python3
"""Main JSONL Import Orchestrator for 5ch Archive."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from config import (
    DB_HOST,
    DB_PORT,
    DB_NAME,
    DB_USER,
    DB_PASSWORD,
    DEFAULT_JSONL_DIR,
    DEFAULT_LOG_DIR,
    DEFAULT_BATCH_SIZE,
    DEFAULT_INTERVAL_SEC,
)
from database import Database
from logging_config import setup_logger
from parser import normalize_post, normalize_thread
from scanner import scan_jsonl_dir


def import_single_file(
    db: Database,
    file_path: Path,
    batch_size: int,
    logger,
    is_update: bool = False,
) -> tuple[int, int]:
    """Imports a single JSONL file into PostgreSQL using staging and batch transactions.

    Returns:
        (thread_count, post_count)
    """
    filename = file_path.name
    stat = file_path.stat()
    file_size = stat.st_size
    mtime_ns = stat.st_mtime_ns

    source_file_id, log_id = db.start_file_import(filename, file_size, mtime_ns)

    thread_count = 0
    post_count = 0
    threads_to_insert: list[dict] = []
    posts_to_insert: list[dict] = []

    try:
        with file_path.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(obj, dict):
                    continue

                obj_type = obj.get("type")
                if obj_type == "thread":
                    t_data = normalize_thread(obj)
                    if t_data["thread_id"]:
                        threads_to_insert.append(t_data)
                        thread_count += 1
                elif obj_type == "post" and obj.get("thread_id"):
                    p_data = normalize_post(obj)
                    posts_to_insert.append(p_data)
                    post_count += 1

                    if len(posts_to_insert) >= batch_size:
                        # Flush current threads first, then posts batch
                        if threads_to_insert:
                            db.upsert_threads(threads_to_insert, source_file_id)
                            threads_to_insert.clear()

                        db.insert_posts_via_staging(posts_to_insert, source_file_id)
                        posts_to_insert.clear()

        # Flush remaining threads and posts
        if threads_to_insert:
            db.upsert_threads(threads_to_insert, source_file_id)
            threads_to_insert.clear()

        if posts_to_insert:
            db.insert_posts_via_staging(posts_to_insert, source_file_id)
            posts_to_insert.clear()

        db.finish_file_import(source_file_id, log_id, "done")
        return thread_count, post_count

    except Exception as exc:
        logger.error(f"Failed to import file {filename}: {exc}", exc_info=True)
        try:
            db.finish_file_import(source_file_id, log_id, "failed", error_message=str(exc))
        except Exception:
            pass
        raise


def run_diff_import(
    db: Database,
    jsonl_dir: Path,
    batch_size: int,
    force: bool,
    limit: int | None,
    logger,
) -> dict[str, int]:
    """Runs a single pass of differential import."""
    db.init_schema()

    if force:
        logger.warning("Force flag enabled: re-importing all files.")
        db.reset_all_for_force()

    recorded_files = db.get_recorded_files()
    files_to_process, skipped_count = scan_jsonl_dir(jsonl_dir, recorded_files, force=force, logger=logger)

    if limit and limit > 0:
        logger.info(f"Limit applied: processing at most {limit} files.")
        files_to_process = files_to_process[:limit]

    stats = {
        "new": 0,
        "updated": 0,
        "skipped": skipped_count,
        "errors": 0,
        "threads": 0,
        "posts": 0,
    }

    total_target = len(files_to_process)
    logger.info(
        f"Found {total_target} files to import/update ({skipped_count} skipped as already up-to-date)."
    )

    if total_target == 0:
        return stats

    start_time = time.time()
    for i, (file_path, is_update) in enumerate(files_to_process, 1):
        action = "Updating" if is_update else "Importing"
        try:
            t_cnt, p_cnt = import_single_file(db, file_path, batch_size, logger, is_update=is_update)
            stats["threads"] += t_cnt
            stats["posts"] += p_cnt
            if is_update:
                stats["updated"] += 1
            else:
                stats["new"] += 1

            if i % 10 == 0 or i == total_target:
                logger.info(
                    f"[{i}/{total_target}] {action} {file_path.name} "
                    f"({t_cnt} threads, {p_cnt} posts) | Progress: {i/total_target*100:.1f}%"
                )
        except Exception:
            stats["errors"] += 1
            logger.error(f"[{i}/{total_target}] Error processing {file_path.name}")

    elapsed = time.time() - start_time
    logger.info(
        f"Import completed in {elapsed:.2f}s | "
        f"New: {stats['new']}, Updated: {stats['updated']}, Skipped: {stats['skipped']}, "
        f"Errors: {stats['errors']}, Threads inserted: {stats['threads']}, Posts inserted: {stats['posts']}"
    )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Differential JSONL Importer for 5ch Archive (PostgreSQL)")
    parser.add_argument("--dir", type=Path, default=DEFAULT_JSONL_DIR, help="Path to JSONL directory")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Path to logs directory")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Batch size for transactions")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC, help="Interval in sec for polling (0=one-shot)")
    parser.add_argument("--force", action="store_true", help="Force re-import of all files")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files to process (useful for testing)")

    args = parser.parse_args()
    logger = setup_logger(args.log_dir)

    logger.info("=== 5ch PostgreSQL Differential Importer Started ===")
    logger.info(f"Target DB: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
    logger.info(f"JSONL DIR: {args.dir}")
    logger.info(f"BATCH SIZE:{args.batch_size}")
    logger.info(f"INTERVAL:  {args.interval}s")
    if args.limit:
        logger.info(f"LIMIT:     {args.limit} files")

    db = Database(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, logger)

    if args.interval <= 0:
        try:
            run_diff_import(db, args.dir, args.batch_size, args.force, args.limit, logger)
        finally:
            db.close()
    else:
        logger.info(f"Running in daemon mode. Checking for new/updated files every {args.interval} seconds.")
        while True:
            try:
                run_diff_import(db, args.dir, args.batch_size, args.force, args.limit, logger)
            except Exception as exc:
                logger.error(f"Unexpected error in polling loop: {exc}", exc_info=True)
            finally:
                db.close()
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
