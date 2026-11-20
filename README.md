# 5ch Archive Explorer (PostgreSQL版)

5chのJSONLアーカイブデータをPostgreSQLに高速差分取り込みし、全文検索および統計分析・可視化を行うWebシステムです。

---

## 1. システム全体構成

```bash
~/docker/5ch_project/
│
├── docker-compose.yml
│
├── app/                          # Webアプリケーション (Flask/WSGI)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
│
├── importer/                     # データ取り込みエンジン
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.py                 # 環境変数設定
│   ├── logging_config.py         # ログ設定
│   ├── parser.py                 # JSONL解析・正規化
│   ├── database.py               # PostgreSQL接続・Stagingバッチ登録
│   ├── scanner.py                # 差分・更新ファイル検出
│   └── import_jsonl.py           # 全体オーケストレーション
│
├── db/                           # PostgreSQL関連
│   ├── init/
│   │   └── 001_schema.sql        # 初期化DDL
│   └── maintenance/
│       └── vacuum_analyze.sql    # メンテナンスクエリ
│
├── data/
│   └── postgres/                 # PostgreSQL永続化実データ
│
├── logs/
│   └── importer.log              # インポーターログ
│
├── backups/                      # DBバックアップ格納先
│
├── 5ch_project_beyond/
│   └── jsonl/                    # raw原本 (JSONLファイル群)
│
└── scripts/
    ├── backup_db.sh              # データベースバックアップ
    ├── restore_db.sh             # データベースリストア
    └── check_db.sh               # DB状態・件数・サイズ確認
```

### コンテナ一覧

| コンテナ名 | サービス名 | 役割 | 起動方式 |
| --- | --- | --- | --- |
| `5ch-archive-db` | `archive-db` | PostgreSQL 16 データベース (`pg_trgm` GIN検索) | 常時稼働 |
| `5ch-archive-importer` | `archive-importer` | JSONL差分インポートエンジン | 手動ワンショットまたは定期監視 |
| `5ch-archive-web` | `archive-web` | Web検索・分析ダッシュボード (Port 5000) | 常時稼働 |

---

## 2. データベース仕様

- **Database**: `archive`
- **User / Role**: `muraoka`
- **Password**: `postgre1234`
- **Port**: `5432`

### テーブル構成

- `threads`: スレッド情報（ID、タイトル、板、元URL、ハッシュ）
- `posts`: 投稿データ（スレッドID、レス番、投稿者名、UID、日時、本文、種別、リンク、アンカー）
- `boards`: 板一覧
- `source_files`: JSONL原本ファイルの管理（ファイルサイズ、更新時刻 `mtime_ns`、取り込み状態）
- `thread_source_files`: スレッドと原本ファイルの関連付け
- `import_logs`: 各ファイルのインポート実行履歴
- `posts_staging`: バッチ登録用の `UNLOGGED` 一時テーブル

### 検索インデックス

- 基本インデックス: `thread_id`, `post_datetime`, `uid`, `board`
- 全文検索: PostgreSQLの標準拡張 `pg_trgm` を利用した GIN インデックス（`content gin_trgm_ops`, `title gin_trgm_ops`）により、日本語の部分一致検索を高速に実行します。

---

## 3. 利用方法

### サービスの起動

```bash
# DBおよびWebサービスの起動
docker compose up -d archive-db archive-web
```
ブラウザで [http://localhost:5000](http://localhost:5000) にアクセスすると、アーカイブ探索ダッシュボードが開きます。

### データのインポート

#### 手動実行（ワンショット）
```bash
# 新規・更新ファイルのみを差分取り込み
docker compose run --rm archive-importer

# テスト実行（先頭10ファイルのみ）
docker compose run --rm archive-importer python import_jsonl.py --limit 10

# 全ファイルを強制再取り込み
docker compose run --rm archive-importer python import_jsonl.py --force
```

#### 定期監視モード（デーモン実行）
`docker-compose.yml` 内の `archive-importer` の環境変数 `IMPORT_INTERVAL_SEC` に秒数（例: `300`）を指定し、コンテナを常駐起動します：
```bash
docker compose up -d archive-importer
```

---

## 4. 運用・保守スクリプト

`scripts/` ディレクトリに運用保守用のユーティリティスクリプトが用意されています：

```bash
# データベースの状態確認（接続情報、テーブル件数、サイズ等）
./scripts/check_db.sh

# データベースのバックアップ（backups/archive_backup_YYYYMMDD_HHMMSS.sql.gz を生成）
./scripts/backup_db.sh

# バックアップからのリストア
./scripts/restore_db.sh backups/archive_backup_20261120_172931.sql.gz
```
