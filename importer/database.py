"""Database operations and batch staging for 5ch Archive Importer."""
from __future__ import annotations

import logging
from typing import Any
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS threads (
    id BIGSERIAL PRIMARY KEY,
    thread_id VARCHAR(64) NOT NULL UNIQUE,
    board VARCHAR(100) NOT NULL,
    title TEXT,
    url TEXT,
    raw_sha256 VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS posts (
    id BIGSERIAL PRIMARY KEY,
    thread_id VARCHAR(64) NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    post_id INTEGER NOT NULL,
    board VARCHAR(100),
    name TEXT,
    uid VARCHAR(100),
    post_datetime TIMESTAMP,
    content TEXT NOT NULL,
    post_type VARCHAR(30),
    content_length INTEGER,
    links JSONB DEFAULT '[]'::jsonb,
    anchors JSONB DEFAULT '[]'::jsonb,

    UNIQUE (thread_id, post_id)
);

CREATE TABLE IF NOT EXISTS boards (
    board VARCHAR(100) PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_files (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    file_size BIGINT NOT NULL,
    mtime_ns BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    imported_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS thread_source_files (
    thread_id VARCHAR(64) NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, source_file_id)
);

CREATE TABLE IF NOT EXISTS import_logs (
    id BIGSERIAL PRIMARY KEY,
    source_file_id BIGINT REFERENCES source_files(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE UNLOGGED TABLE IF NOT EXISTS posts_staging (
    thread_id VARCHAR(64),
    post_id INTEGER,
    board VARCHAR(100),
    name TEXT,
    uid VARCHAR(100),
    post_datetime TIMESTAMP,
    content TEXT,
    post_type VARCHAR(30),
    content_length INTEGER,
    links JSONB,
    anchors JSONB
);

CREATE INDEX IF NOT EXISTS idx_posts_thread ON posts(thread_id);
CREATE INDEX IF NOT EXISTS idx_posts_datetime ON posts(post_datetime);
CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts(uid);
CREATE INDEX IF NOT EXISTS idx_posts_board ON posts(board);
CREATE INDEX IF NOT EXISTS idx_threads_board ON threads(board);
CREATE INDEX IF NOT EXISTS idx_posts_content_trgm ON posts USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_threads_title_trgm ON threads USING gin (title gin_trgm_ops);
"""


class Database:
    def __init__(self, host: str, port: int, dbname: str, user: str, password: str, logger: logging.Logger):
        self.conn_kwargs = {
            "host": host,
            "port": port,
            "dbname": dbname,
            "user": user,
            "password": password,
        }
        self.logger = logger
        self.conn: psycopg.Connection | None = None

    def connect(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(**self.conn_kwargs, autocommit=False)
        return self.conn

    def close(self) -> None:
        if self.conn and not self.conn.closed:
            self.conn.close()
            self.conn = None

    def init_schema(self) -> None:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()

    def get_recorded_files(self) -> dict[str, dict[str, Any]]:
        conn = self.connect()
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, filename, file_size, mtime_ns, status FROM source_files")
            rows = cur.fetchall()
        return {r["filename"]: r for r in rows}

    def start_file_import(self, filename: str, file_size: int, mtime_ns: int) -> tuple[int, int]:
        """Registers file as pending in source_files and creates a running log entry."""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_files (filename, file_size, mtime_ns, status, updated_at)
                VALUES (%s, %s, %s, 'pending', NOW())
                ON CONFLICT (filename) DO UPDATE SET
                    file_size = EXCLUDED.file_size,
                    mtime_ns = EXCLUDED.mtime_ns,
                    status = 'pending',
                    error_message = NULL,
                    updated_at = NOW()
                RETURNING id;
                """,
                (filename, file_size, mtime_ns),
            )
            source_file_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO import_logs (source_file_id, status, started_at)
                VALUES (%s, 'running', NOW())
                RETURNING id;
                """,
                (source_file_id,),
            )
            log_id = cur.fetchone()[0]

        conn.commit()
        return source_file_id, log_id

    def finish_file_import(
        self, source_file_id: int, log_id: int, status: str, error_message: str | None = None
    ) -> None:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_files
                SET status = %s,
                    imported_at = CASE WHEN %s = 'done' THEN NOW() ELSE imported_at END,
                    error_message = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (status, status, error_message, source_file_id),
            )
            cur.execute(
                """
                UPDATE import_logs
                SET status = %s,
                    finished_at = NOW(),
                    error_message = %s
                WHERE id = %s;
                """,
                (status, error_message, log_id),
            )
        conn.commit()

    def upsert_threads(self, threads: list[dict[str, Any]], source_file_id: int) -> None:
        if not threads:
            return
        conn = self.connect()
        with conn.cursor() as cur:
            # Upsert boards
            boards = list({t["board"] for t in threads if t.get("board")})
            if boards:
                cur.executemany(
                    "INSERT INTO boards (board) VALUES (%s) ON CONFLICT (board) DO NOTHING",
                    [(b,) for b in boards],
                )

            # Upsert threads
            thread_params = [
                (t["thread_id"], t["board"], t["title"], t["url"], t["raw_sha256"])
                for t in threads
            ]
            cur.executemany(
                """
                INSERT INTO threads (thread_id, board, title, url, raw_sha256)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET
                    board = EXCLUDED.board,
                    title = EXCLUDED.title,
                    url = EXCLUDED.url,
                    raw_sha256 = EXCLUDED.raw_sha256;
                """,
                thread_params,
            )

            # Link thread to source_file
            link_params = [(t["thread_id"], source_file_id) for t in threads]
            cur.executemany(
                """
                INSERT INTO thread_source_files (thread_id, source_file_id)
                VALUES (%s, %s)
                ON CONFLICT (thread_id, source_file_id) DO NOTHING;
                """,
                link_params,
            )
        conn.commit()

    def ensure_threads_exist_for_posts(self, posts: list[dict[str, Any]], source_file_id: int) -> None:
        """Ensures all thread_ids referenced by posts exist in threads table before inserting posts."""
        needed_threads = {}
        for p in posts:
            tid = p.get("thread_id")
            if tid and tid not in needed_threads:
                needed_threads[tid] = p.get("board") or ""

        if not needed_threads:
            return

        conn = self.connect()
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO threads (thread_id, board, title)
                VALUES (%s, %s, '(タイトル未登録)')
                ON CONFLICT (thread_id) DO NOTHING;
                """,
                [(tid, board) for tid, board in needed_threads.items()],
            )
            cur.executemany(
                """
                INSERT INTO thread_source_files (thread_id, source_file_id)
                VALUES (%s, %s)
                ON CONFLICT (thread_id, source_file_id) DO NOTHING;
                """,
                [(tid, source_file_id) for tid in needed_threads.keys()],
            )
        conn.commit()

    def insert_posts_via_staging(self, posts: list[dict[str, Any]], source_file_id: int | None = None) -> None:
        """Phase A: Load into posts_staging UNLOGGED table.
        Phase B: Upsert from staging into main posts table and truncate staging.
        """
        if not posts:
            return

        if source_file_id is not None:
            self.ensure_threads_exist_for_posts(posts, source_file_id)

        conn = self.connect()
        with conn.cursor() as cur:
            staging_rows = [
                (
                    p["thread_id"],
                    p["post_id"],
                    p["board"],
                    p["name"],
                    p["uid"],
                    p["post_datetime"],
                    p["content"],
                    p["post_type"],
                    p["content_length"],
                    Jsonb(p["links"]),
                    Jsonb(p["anchors"]),
                )
                for p in posts
            ]

            # Phase A: insert into staging
            cur.executemany(
                """
                INSERT INTO posts_staging (
                    thread_id, post_id, board, name, uid, post_datetime,
                    content, post_type, content_length, links, anchors
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                staging_rows,
            )

            # Phase B: merge from staging into main posts table
            cur.execute(
                """
                INSERT INTO posts (
                    thread_id, post_id, board, name, uid, post_datetime,
                    content, post_type, content_length, links, anchors
                )
                SELECT
                    thread_id, post_id, board, name, uid, post_datetime,
                    content, post_type, content_length, links, anchors
                FROM posts_staging
                ON CONFLICT (thread_id, post_id)
                DO UPDATE SET
                    board = EXCLUDED.board,
                    name = EXCLUDED.name,
                    uid = EXCLUDED.uid,
                    post_datetime = EXCLUDED.post_datetime,
                    content = EXCLUDED.content,
                    post_type = EXCLUDED.post_type,
                    content_length = EXCLUDED.content_length,
                    links = EXCLUDED.links,
                    anchors = EXCLUDED.anchors;
                """
            )
            cur.execute("TRUNCATE posts_staging;")

        conn.commit()

    def reset_all_for_force(self) -> None:
        """Clears tracking records when --force is requested."""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute("UPDATE source_files SET status = 'pending';")
        conn.commit()
