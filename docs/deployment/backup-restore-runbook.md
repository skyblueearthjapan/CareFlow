# CareLink Backup / Restore Runbook (Wave 5-B)

対象環境: Hostinger Malaysia VPS (`72.60.211.213`) / Postgres 16 (`carelink-postgres` container)
バックアップ作成元: `docs/deployment/scripts/backup-carelink-db.sh` (cron 日次 02:30)
バックアップ保管場所: `/opt/carelink/backups/daily-YYYYMMDD-HHMM.sql.gz` (7 日保持)

## RTO / RPO

| 指標 | 値 | 根拠 |
|---|---|---|
| **RTO** (Recovery Time Objective) | **15 分以内** | 本手順 step 2-7 の標準所要時間 (DB ~200MB 想定) |
| **RPO** (Recovery Point Objective) | **24 時間** | 日次 backup (02:30 JST) 前提。重要リリース直前は手動 backup を推奨 |

> RTO/RPO を更に厳しくする場合は WAL アーカイブ (PITR) + 1 時間ごと差分 backup の導入が必要。Wave 5-B 範囲外。

## 前提

- リストアは **管理者権限** (root or docker グループ) が必要。
- カレンタイム中、frontend は 5xx を返す (backend が DROP DATABASE 中)。事前にメンテナンスバナーを表示するか、Cloudflare で一時的に 503 を返すこと。
- `pg_dump --format=plain` 出力を gunzip して `psql` に流し込む方式。`pg_restore` は使わない (custom フォーマットを採用していないため)。

## ステップバイステップ手順

### Step 0: メンテ宣言 (任意)

可能なら Cloudflare ダッシュボードで `carelink.kaipoke-api.net` に **"Under Attack" or 503 Page Rule** を一時設定してエンドユーザに通知。Wave 5-B では自動化していないので口頭/Slack 通知でも可。

### Step 1: 最新 backup を確認

```bash
ssh root@72.60.211.213
ls -lt /opt/carelink/backups/daily-*.sql.gz | head -5
# 一番上の行の (例) daily-20260505-0230.sql.gz を BACKUP_FILE として使う
export BACKUP_FILE=/opt/carelink/backups/daily-20260505-0230.sql.gz
ls -lh "$BACKUP_FILE"   # サイズが想定 (数 MB ~ 数 GB) 内であることを目視
```

異常 (10KB 未満 or 直近 24h 内にファイルがない) なら、まず `backup-carelink-db.sh` を手動再実行できないか検討:

```bash
/opt/carelink/docs/deployment/scripts/backup-carelink-db.sh
```

### Step 2: 現在状態のスナップショットを取得 (保険)

リストア中に判断ミスがあっても直近の状態に戻せるよう、必ず実行する。

```bash
docker exec carelink-postgres pg_dump -U carelink -d carelink \
  > /tmp/pre-restore-snap-$(date +%Y%m%d-%H%M).sql
ls -lh /tmp/pre-restore-snap-*.sql
```

### Step 3: backend を停止 (接続を切る)

```bash
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env stop backend frontend
```

### Step 4: 既存 DB を破壊して作り直す

```bash
# 接続が残っていると DROP が "database in use" でブロックされるので WITH (FORCE) を使用 (PG13+)
docker exec carelink-postgres psql -U carelink -d postgres \
  -c "DROP DATABASE carelink WITH (FORCE);"

docker exec carelink-postgres psql -U carelink -d postgres \
  -c "CREATE DATABASE carelink OWNER carelink;"
```

### Step 5: backup を流し込む

```bash
gunzip -c "$BACKUP_FILE" | docker exec -i carelink-postgres psql -U carelink -d carelink

# 正常終了の確認 (最後の行が CREATE INDEX などで終わり、ERROR を含まないこと)
echo "exit=$?"
```

エラーが出た場合は `psql -v ON_ERROR_STOP=1` を渡して中断するパターンも検討:

```bash
gunzip -c "$BACKUP_FILE" \
  | docker exec -i carelink-postgres psql -U carelink -d carelink -v ON_ERROR_STOP=1
```

### Step 6: alembic revision の整合性を確認

restore した DB が現在の image に bake-in された alembic head と一致していない場合がある。差分があれば手動マイグレーション。

```bash
# backend を一旦起動 (frontend はまだ stop のまま)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d backend
sleep 10  # uvicorn 起動待ち

docker exec carelink-backend alembic current
docker exec carelink-backend alembic heads

# DB head < image head の場合のみ実行 (forward migration)
docker exec carelink-backend alembic upgrade head
```

DB の方が image より **新しい** revision を持っている (= rollback restore) 場合は、image を当該 revision を含むタグに合わせて再 build する方が安全。`alembic downgrade` を本番で打つのは非推奨。

### Step 7: smoke test

```bash
curl -fsS http://127.0.0.1:18001/api/v1/healthz
curl -fsS http://127.0.0.1:18000/api/healthz

# 起動確認 OK なら frontend も up
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d frontend

curl -fsSI https://carelink.kaipoke-api.net/
```

3 つすべて 200 を返したらリストア完了。`/var/log/carelink/healthcheck.log` の次回 cron (5 分以内) も成功することを確認。

### Step 8: VisitPhoto bind mount を併せて復元する場合

`backup-carelink-db.sh` は `/opt/carelink/backups/visit_photos/` に rsync ミラーを保持している。

```bash
# 既存ファイルを退避
mv /opt/carelink/data/visit_photos /opt/carelink/data/visit_photos.bak.$(date +%Y%m%d-%H%M)
rsync -a /opt/carelink/backups/visit_photos/ /opt/carelink/data/visit_photos/
chown -R 1000:1000 /opt/carelink/data/visit_photos   # backend container の UID に合わせる
```

DB 上の `visit_photos.path` カラムと整合していないと参照エラーになるため、原則 **DB と photo は同一時刻 backup から同時にリストア** すること。

## 失敗時のフォールバック

- Step 5 でリストアが ERROR で止まった場合:
  1. `pre-restore-snap-*.sql` から元の状態に戻す:
     ```bash
     docker exec carelink-postgres psql -U carelink -d postgres -c "DROP DATABASE carelink WITH (FORCE);"
     docker exec carelink-postgres psql -U carelink -d postgres -c "CREATE DATABASE carelink OWNER carelink;"
     cat /tmp/pre-restore-snap-*.sql | docker exec -i carelink-postgres psql -U carelink -d carelink
     ```
  2. backend / frontend を up し直す。
  3. 別 backup file (1 つ前の世代) で再試行。
- container が `unhealthy` のまま戻らない場合: `docker logs carelink-backend --tail 200` を確認し、`runbook.md` の Phase J 緊急 rollback 手順 (image 再 build) に切り替える。

## 定期リハーサル (推奨)

- **月 1 回**、ステージング (= 別 VPS or 別 docker network) に直近 backup を流し込み、smoke test までの時間を計測する。15 分を超える場合は手順を見直す。
- リハーサル結果は `docs/audit/` 配下に `restore-rehearsal-YYYYMM.md` として記録する。
