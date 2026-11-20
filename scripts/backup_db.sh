#!/usr/bin/env bash
set -euo pipefail

# スクリプトのディレクトリとプロジェクトルートの取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_DIR="${PROJECT_ROOT}/backups"

mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/archive_backup_${TIMESTAMP}.sql.gz"

echo "=== 5ch Archive PostgreSQL バックアップ開始 ==="
echo "保存先: ${BACKUP_FILE}"

# archive-db コンテナ内の pg_dump を実行して gzip 圧縮
docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T archive-db \
    pg_dump -U muraoka -d archive --clean --if-exists | gzip > "${BACKUP_FILE}"

FILE_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "=== バックアップ完了 (サイズ: ${FILE_SIZE}) ==="
