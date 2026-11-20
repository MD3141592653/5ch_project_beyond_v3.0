-- 001_schema.sql: 5ch Archive PostgreSQL Schema
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- スレッド情報テーブル
CREATE TABLE IF NOT EXISTS threads (
    id BIGSERIAL PRIMARY KEY,
    thread_id VARCHAR(64) NOT NULL UNIQUE,
    board VARCHAR(100) NOT NULL,
    title TEXT,
    url TEXT,
    raw_sha256 VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 投稿データテーブル
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

-- 板情報テーブル
CREATE TABLE IF NOT EXISTS boards (
    board VARCHAR(100) PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- JSONL原本ファイル管理テーブル
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

-- スレッドと原本ファイルの対応テーブル
CREATE TABLE IF NOT EXISTS thread_source_files (
    thread_id VARCHAR(64) NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, source_file_id)
);

-- インポート実行ログテーブル
CREATE TABLE IF NOT EXISTS import_logs (
    id BIGSERIAL PRIMARY KEY,
    source_file_id BIGINT REFERENCES source_files(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

-- バッチ取り込み用 Staging テーブル (UNLOGGEDで高速化)
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

-- 基本インデックス
CREATE INDEX IF NOT EXISTS idx_posts_thread ON posts(thread_id);
CREATE INDEX IF NOT EXISTS idx_posts_datetime ON posts(post_datetime);
CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts(uid);
CREATE INDEX IF NOT EXISTS idx_posts_board ON posts(board);
CREATE INDEX IF NOT EXISTS idx_threads_board ON threads(board);

-- 全文検索用 pg_trgm GIN インデックス（本文・タイトル）
CREATE INDEX IF NOT EXISTS idx_posts_content_trgm ON posts USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_threads_title_trgm ON threads USING gin (title gin_trgm_ops);
