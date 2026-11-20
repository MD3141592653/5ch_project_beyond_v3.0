#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ $# -lt 1 ]; then
    echo "使用法: $0 <バックアップファイル (.sql または .sql.gz)>"
    exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "${BACKUP_FILE}" ]; then
    echo "エラー: ファイルが存在しません: ${BACKUP_FILE}"
    exit 1
fi

echo "=== 5ch Archive PostgreSQL リストア開始 ==="
echo "対象ファイル: ${BACKUP_FILE}"

read -p "データベース 'archive' の既存データが上書きされます。続行しますか？ (y/N): " -r CONFIRM
if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
    echo "中止しました。"
    exit 0
fi

if [[ "${BACKUP_FILE}" == *.gz ]]; then
    gzip -dc "${BACKUP_FILE}" | docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T archive-db \
        psql -U muraoka -d archive
else
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T archive-db \
        psql -U muraoka -d archive < "${BACKUP_FILE}"
fi

echo "=== リストア完了 ==="
