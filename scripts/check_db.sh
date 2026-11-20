#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== 5ch Archive PostgreSQL 状態確認 ==="

docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T archive-db \
    psql -U muraoka -d archive << 'EOF'
\echo '--- [1] データベース基本情報 ---'
SELECT current_database() AS db, current_user AS "user", version();

\echo '--- [2] テーブル件数 ---'
SELECT 'threads' AS table_name, count(*) AS count FROM threads
UNION ALL
SELECT 'posts', count(*) FROM posts
UNION ALL
SELECT 'source_files', count(*) FROM source_files
UNION ALL
SELECT 'boards', count(*) FROM boards
UNION ALL
SELECT 'thread_source_files', count(*) FROM thread_source_files
UNION ALL
SELECT 'import_logs', count(*) FROM import_logs;

\echo '--- [3] source_files 状態別件数 ---'
SELECT status, count(*) AS file_count FROM source_files GROUP BY status;

\echo '--- [4] テーブルサイズとインデックスサイズ ---'
SELECT relname AS table_name,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       pg_size_pretty(pg_relation_size(relid)) AS table_size,
       pg_size_pretty(pg_indexes_size(relid)) AS index_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
EOF
