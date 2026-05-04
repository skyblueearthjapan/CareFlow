# CareLink VPS デプロイ Runbook

対象: Hostinger Malaysia VPS (`72.60.211.213`) / 公開ドメイン `https://carelink.kaipoke-api.net`
前提: 既存 `kaipoke-api` (Docker) と `cloudflared` (systemd ネイティブ) が稼働中。新規導入で既存サービスを停止してはならない。

---

## Phase A: Pre-flight (VPS 状態確認)

`scripts/preflight-check.sh` を VPS 上で実行し、以下を確認する。

- ディスク空き 5 GB 以上 (`df -h /`)
- メモリ free 1 GB 以上 (`free -m` の available)
- `docker --version` が 24.x 以上
- `docker compose version` が v2 系
- `systemctl is-active cloudflared` が `active`
- `ufw status` が 22/tcp のみ public (80/443 は cloudflared 経由)
- ポート `18000` `18001` がローカルで未使用 (`ss -tln | grep -E '1800[01]'` が空)

成功条件: スクリプトが exit 0。
失敗時: 該当項目を Issue 起票し、デプロイ中断。空きディスクが足りない場合は `docker system prune -a` ではなく既存ログ/古いイメージを個別削除（kaipoke-api を巻き込まない）。

## Phase B: コード配置

```bash
sudo mkdir -p /opt/carelink && sudo chown $USER:$USER /opt/carelink
git clone https://github.com/skyblueearthjapan/CareLink.git /opt/carelink
cd /opt/carelink
git checkout develop
git rev-parse HEAD  # commit hash を作業ログに記録
```

成功条件: `develop` HEAD が GitHub と一致 (`git status` clean)。
失敗時: ネットワーク経由の clone 失敗なら deploy key 認証を確認。

## Phase C: `.env` 作成

`docs/deployment/env-template.md` の手順に従い、以下を作成する。

- `/opt/carelink/backend/.env`
- `/opt/carelink/frontend/.env` (Phase 2 まで scope-out 可)

値は本番用に再生成する (`openssl rand -base64 32`)。`backend/.env.example` の `JWT_SECRET=please-change-me-...` をそのままコピー禁止。`APP_ENV=production` を必ず設定 (これにより config.py の jwt_secret 32 文字バリデーションが起動時に発火し、弱い値で起動できなくなる)。

成功条件: `chmod 600 backend/.env frontend/.env` 設定済み、`grep please-change` で何もヒットしない。

## Phase D: PostgreSQL 起動

```bash
cd /opt/carelink
cp docs/deployment/docker-compose.production.yml docker-compose.yml
docker compose up -d postgres
docker compose ps postgres        # State=running, Health=healthy
docker compose logs --tail=20 postgres
```

成功条件: `pg_isready` が `accepting connections`、ボリューム `carelink-pg-data` が作成済み。
失敗時: 5432 衝突なら kaipoke-api 側の Postgres が同 host port を使っていないか確認。本 compose は `127.0.0.1:5432` loopback bind。

## Phase E: Alembic マイグレーション

```bash
docker compose run --rm backend alembic upgrade head
docker compose exec postgres psql -U carelink -d carelink -c "\dt"
```

成功条件: `alembic_version` テーブルに `0001_initial` が記録、users/staff/visit/patient 等のテーブルが存在。
失敗時: マイグレーション失敗時は `alembic downgrade base` でロールバックし、`docker compose down -v` でボリュームごと作り直す（DB は新規なので破壊して問題なし。Phase H 後はこの手は使えない）。

## Phase F: Backend / Frontend コンテナ起動

```bash
docker compose up -d backend
# frontend は Dockerfile が未整備 (Phase 2)
# docker compose up -d frontend  ← Phase 2 で有効化
docker compose ps
```

成功条件: backend が `healthy`、`docker compose logs backend` に `Uvicorn running on 0.0.0.0:8000` が出力。
失敗時: 起動直後の jwt_secret バリデーションエラーは `.env` の `APP_ENV` と `JWT_SECRET` 長を疑う。

## Phase G: Cloudflared ingress 追加

```bash
sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.bak.$(date +%Y%m%d)
sudo $EDITOR /etc/cloudflared/config.yml
# docs/deployment/cloudflared-config-fragment.yml の通りに carelink.kaipoke-api.net rule を
# 既存 kaipoke-api rule の "前" に挿入する (ingress は上から評価されるため)。
sudo cloudflared tunnel ingress validate
sudo systemctl reload cloudflared
sudo systemctl status cloudflared --no-pager | head -20
```

Cloudflare Zone (`kaipoke-api.net`) の DNS に `carelink` の CNAME を追加 (Cloudflare ダッシュボード → DNS → Add CNAME → Target = 既存 tunnel id `<tunnel-uuid>.cfargotunnel.com`)。

成功条件: `cloudflared tunnel ingress validate` が OK、`systemctl reload` 後に `is-active` が active のまま。
失敗時: `config.yml.bak.*` で即座に revert し reload。kaipoke-api 側の疎通を `curl -I https://kaipoke-api.net` で確認。

## Phase H: 公開疎通確認

```bash
# DNS 反映 (60 秒程度待つ)
dig +short carelink.kaipoke-api.net
curl -fsSI https://carelink.kaipoke-api.net/api/v1/healthz
# 期待: HTTP/2 200, `content-type: application/json`
```

成功条件: 200 OK が返り、`{"status":"ok"}` 相当のレスポンス。
失敗時: 502 なら backend container 死活、cloudflared から `localhost:18001` への到達性 (`curl -I http://localhost:18001/api/v1/healthz` を VPS 上で実行) を確認。

## Phase I: 初期管理者 user 作成

```bash
docker compose exec backend python scripts/create_admin.py
# 対話プロンプトで email / password を入力
# password は最小 12 文字推奨
```

成功条件: `admin user upserted: <email>` のログ。`SELECT email, role FROM users` で `role=admin` を確認。
失敗時: 既存 admin がある場合は ON CONFLICT で password を更新する仕様 (詳細は `initial-admin-seed.md`)。

## Phase J: ロールバック手順

何か致命的な問題が出た場合の取り消し順序 (上から順に実施)。

1. **ingress revert**: `sudo cp /etc/cloudflared/config.yml.bak.YYYYMMDD /etc/cloudflared/config.yml && sudo systemctl reload cloudflared`
2. **container 停止**: `cd /opt/carelink && docker compose down`
3. **DB 巻き戻し** (本番運用後はバックアップから):
   - 初回デプロイ直後で運用データなし → `docker compose down -v` でボリュームごと削除
   - 運用後 → `docker compose exec postgres pg_dump -U carelink carelink > /tmp/restore.sql` で取得済みのダンプを `psql` で流し込む
4. **DNS revert**: Cloudflare ダッシュボードで CNAME `carelink` を削除 or proxied OFF
5. **イメージ削除** (再デプロイで影響を残したくない場合): `docker image rm carelink-backend:latest`

成功条件: `curl https://kaipoke-api.net` (既存サービス) が引き続き 200。CareLink 側は接続エラー or 404 になる。
失敗時: 既存 kaipoke-api まで影響している場合は `cloudflared` の config.yml.bak を最優先で戻す。

---

## 既知の前提と制限

- frontend Dockerfile は未整備のため、Phase F の frontend 起動は Phase 2 (D2 implementation 完了後)
- `api.carelink.kaipoke-api.net` (バックエンド直公開) は任意。NextAuth callback で必要になった時点で有効化
- INV-4 の通り `kaipoke-net` Docker network は存在しないため default bridge を使用
- secret 値はすべて VPS 上で生成し、Git/Slack/メールに残さない
