# CareLink VPS デプロイ Runbook

対象: Hostinger Malaysia VPS (`72.60.211.213`) / 公開ドメイン `https://carelink.kaipoke-api.net`
前提: 既存 `kaipoke-api` (Docker) と `cloudflared` (systemd ネイティブ) が稼働中。新規導入で既存サービスを停止してはならない。

> **本 Runbook の Compose 操作はすべて以下の形式に統一**:
> ```bash
> cd /opt/carelink
> docker compose -f docs/deployment/docker-compose.production.yml --env-file .env <subcommand>
> ```
> リポジトリ内の compose ファイルを直接 `-f` で参照するため、`cp ... docker-compose.yml` のコピー操作は不要 (旧版から廃止)。
> エイリアスを切ると楽:
> ```bash
> alias dcp='docker compose -f docs/deployment/docker-compose.production.yml --env-file .env'
> ```

---

## Phase A: Pre-flight (VPS 状態確認)

`docs/deployment/preflight-check.sh` を VPS 上で実行し、以下を確認する。

- ディスク空き 5 GB (= 5120 MB) 以上 (`df -BM` ベースで精密チェック)
- メモリ free 1 GB 以上 (`free -m` の available)
- `docker --version` のメジャーが 24 以上
- `docker compose version` が v2 系
- `systemctl is-active cloudflared` が `active`
- `ufw status` が 22/tcp のみ public (80/443 は cloudflared 経由)
- ポート `18000` `18001` がローカルで未使用 (`ss -tln | grep -E '1800[01]'` が空)

成功条件: スクリプトが exit 0。
失敗時: 該当項目を Issue 起票し、デプロイ中断。空きディスクが足りない場合は `docker system prune -a` ではなく既存ログ/古いイメージを個別削除（kaipoke-api を巻き込まない）。

なお `/opt/carelink` がすでに存在する場合は **warn のみで通過** する仕様にしている (再デプロイを許容)。空でない場合の clone 衝突は Phase B 側で別途対処すること。

## Phase B: コード配置

```bash
sudo mkdir -p /opt/carelink && sudo chown $USER:$USER /opt/carelink
git clone https://github.com/skyblueearthjapan/CareLink.git /opt/carelink
cd /opt/carelink
git checkout develop
git rev-parse HEAD  # commit hash を作業ログに記録
```

成功条件: `develop` HEAD が GitHub と一致 (`git status` clean)。
失敗時: ネットワーク経由の clone 失敗なら deploy key 認証を確認。再デプロイ時に `/opt/carelink` が空でない場合は、本番データを退避してから `rm -rf` するか、別ディレクトリに clone して `rsync` で上書きする。

## Phase C: `.env` 作成 (統合 1 ファイル)

`docs/deployment/env-template.md` の手順に従い、**`/opt/carelink/.env`** を 1 ファイルだけ作成する。
backend と frontend の両方の値を 1 つの `.env` に統合する形式 (compose は実行ディレクトリの `.env` を自動 interpolation 用にロードし、`env_file: - .env` で各 container にも注入される)。

```bash
cd /opt/carelink

# 雛形を 2 ファイルから組み立てて .env に集約
cat backend/.env.example frontend/.env.example > .env

# 値を本番用に上書き編集
$EDITOR .env

# パーミッションを 600 に固定 (必須)
chmod 600 .env
ls -l .env  # -rw------- であることを確認
```

値は本番用に再生成する (`openssl rand -base64 32`)。`backend/.env.example` の `JWT_SECRET=please-change-me-...` をそのままコピー禁止。`APP_ENV=production` を必ず設定 (これにより config.py の jwt_secret 32 文字バリデーションが起動時に発火し、弱い値で起動できなくなる)。

成功条件:
- `chmod 600 /opt/carelink/.env` 設定済み
- `grep please-change /opt/carelink/.env` で何もヒットしない
- `grep -E '^(POSTGRES_PASSWORD|JWT_SECRET|NEXTAUTH_SECRET|NEXTAUTH_URL)=' /opt/carelink/.env` で 4 行ともプレースホルダでない実値が並ぶ

> 旧構成の `backend/.env` `frontend/.env` を別々に置く運用は **廃止**。
> docker compose は実行ディレクトリの `.env` だけを `${VAR}` 展開に使うため、
> service-local の `.env` を置いても interpolate されない。

## Phase D: PostgreSQL 起動

```bash
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d postgres
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env ps postgres        # State=running, Health=healthy
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env logs --tail=20 postgres

# host から疎通確認 (host port は非公開のため exec 経由)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec postgres pg_isready -U carelink
```

成功条件: `pg_isready` が `accepting connections`、ボリューム `carelink-pg-data` が作成済み。
失敗時: 本 compose は **postgres の host port を公開していない** (kaipoke-api 側 Postgres との 5432 衝突回避)。host から psql する場合は常に `docker compose ... exec postgres psql ...` を使う。

## Phase E: Alembic マイグレーション

```bash
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env run --rm backend alembic upgrade head
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec postgres psql -U carelink -d carelink -c "\dt"
```

成功条件: `alembic_version` テーブルに `0001_initial` が記録、users/staff/visit/patient 等のテーブルが存在。
失敗時: マイグレーション失敗時は `alembic downgrade base` でロールバックし、`docker compose -f docs/deployment/docker-compose.production.yml --env-file .env down -v` でボリュームごと作り直す（DB は新規なので破壊して問題なし。Phase H 後はこの手は使えない）。

## Phase F: Backend / Frontend コンテナ起動

```bash
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d backend frontend
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env ps
curl -fsSI http://localhost:18000/
curl -fsS  http://localhost:18001/api/v1/healthz
```

成功条件: `docker compose ps` で backend と frontend の双方が `Health=healthy`。`curl http://localhost:18000/` が 200 OK、`curl http://localhost:18001/api/v1/healthz` が 200 + JSON。`docker compose logs backend` に `Uvicorn running on 0.0.0.0:8000`、`docker compose logs frontend` に `Ready` 出力。
失敗時: 起動直後の jwt_secret バリデーションエラーは `.env` の `APP_ENV` と `JWT_SECRET` 長を疑う。frontend がビルドエラーで上がらない場合は `docker compose -f docs/deployment/docker-compose.production.yml --env-file .env logs frontend` を確認し、`pnpm-lock.yaml` 不在による依存解決ズレが疑わしいときは frontend 側で `pnpm install --lockfile-only` してから再 build。

## Phase G: Cloudflared ingress 追加

```bash
sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.bak.$(date +%Y%m%d)
sudo $EDITOR /etc/cloudflared/config.yml
# docs/deployment/cloudflared-config-fragment.yml の "↓↓↓ ここから下を ... 挿入 ↓↓↓"
# ブロックを、既存 `ingress:` 配下の **最上部** にそのままコピー&ペースト。
# 既存ルール (kaipoke-api 等) と末尾 catch-all (`- service: http_status:404`) は
# 絶対に変更・削除しないこと。
sudo cloudflared tunnel ingress validate
sudo systemctl reload cloudflared
sudo systemctl status cloudflared --no-pager | head -20
```

Cloudflare Zone (`kaipoke-api.net`) の DNS に `carelink` の CNAME を追加 (Cloudflare ダッシュボード → DNS → Add CNAME → Target = 既存 tunnel id `<tunnel-uuid>.cfargotunnel.com`)。

成功条件: `cloudflared tunnel ingress validate` が OK、`systemctl reload` 後に `is-active` が active のまま、かつ `curl -I https://kaipoke-api.net` で **既存 kaipoke-api サービスが 200 を返し続ける**。
失敗時: `config.yml.bak.*` で即座に revert し reload。kaipoke-api 側の疎通を `curl -I https://kaipoke-api.net` で必ず再確認 (既存サービス障害が最優先)。

## Phase H: 公開疎通確認

```bash
# DNS 反映 (60 秒程度待つ)
dig +short carelink.kaipoke-api.net

# 1) frontend ルート (Cloudflared host-only rule -> localhost:18000)
curl -fsSI https://carelink.kaipoke-api.net/
# 期待: HTTP/2 200, Next.js のレスポンス

# 2) backend healthz (Cloudflared path rule ^/api/v1/.* -> localhost:18001)
curl -fsS https://carelink.kaipoke-api.net/api/v1/healthz
# 期待: HTTP/2 200, content-type: application/json, {"status":"ok"} 相当

# 3) 既存サービスが影響を受けていないことの最終確認
curl -fsSI https://kaipoke-api.net
# 期待: HTTP/2 200 (既存挙動維持)
```

成功条件: 上記 3 つすべてが 200 を返す。
失敗時:
- `/api/v1/healthz` が frontend (Next.js) のレスポンスを返す → Cloudflared の path rule (`^/api/v1/.*`) が host-only rule の **下** に置かれている可能性。fragment の順序を再確認し、path rule が必ず上にあるよう修正後 reload。
- 502 → backend container 死活、cloudflared から `localhost:18001` への到達性を `curl -I http://localhost:18001/api/v1/healthz` を VPS 上で実行して確認。
- 既存 `https://kaipoke-api.net` が落ちている → 即 `config.yml.bak.YYYYMMDD` で revert。

## Phase I: 初期管理者 user 作成

```bash
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec backend python scripts/create_admin.py
# 対話プロンプトで email / password を入力
# password は最小 12 文字 (script 側でも 12 文字未満を拒否)
```

成功条件: `admin user upserted: <email>` のログ。`SELECT email, role FROM users` で `role=admin` を確認。
失敗時: 既存 admin がある場合はスクリプトが select-then-update で password を上書き更新する仕様 (詳細は `initial-admin-seed.md`)。

## Phase J: ロールバック手順

何か致命的な問題が出た場合の取り消し順序 (上から順に実施)。

1. **ingress revert**: `sudo cp /etc/cloudflared/config.yml.bak.YYYYMMDD /etc/cloudflared/config.yml && sudo systemctl reload cloudflared`
2. **container 停止**: `cd /opt/carelink && docker compose -f docs/deployment/docker-compose.production.yml --env-file .env down`
3. **DB 巻き戻し** (本番運用後はバックアップから):
   - 初回デプロイ直後で運用データなし → `docker compose -f docs/deployment/docker-compose.production.yml --env-file .env down -v` でボリュームごと削除
   - 運用後 → `docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec postgres pg_dump -U carelink carelink > /tmp/restore.sql` で取得済みのダンプを `psql` で流し込む
4. **DNS revert**: Cloudflare ダッシュボードで CNAME `carelink` を削除 or proxied OFF
5. **イメージ削除** (再デプロイで影響を残したくない場合): `docker image rm carelink-backend:latest carelink-frontend:latest`

成功条件: `curl https://kaipoke-api.net` (既存サービス) が引き続き 200。CareLink 側は接続エラー or 404 になる。
失敗時: 既存 kaipoke-api まで影響している場合は `cloudflared` の config.yml.bak を最優先で戻す。

---

## 既知の前提と制限

- frontend は Next.js 15 の standalone 出力 + pnpm multi-stage Dockerfile で本番ビルド (`frontend/Dockerfile`)。`pnpm-lock.yaml` が未 commit のため初回 build は `--no-frozen-lockfile` フォールバックパスを通る。安定運用前に lockfile を commit すること
- `api.carelink.kaipoke-api.net` (バックエンド直公開) は任意。NextAuth callback で必要になった時点で有効化 (現構成では `carelink.kaipoke-api.net/api/v1/*` の path rule で backend を公開しているため不要)
- Postgres は **host port を公開していない** (kaipoke-api との 5432 衝突回避)。host から psql する場合は常に `docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec postgres psql ...` を使う
- INV-4 の通り `kaipoke-net` Docker network は存在しないため default bridge を使用
- secret 値はすべて VPS 上で生成し、Git/Slack/メールに残さない
