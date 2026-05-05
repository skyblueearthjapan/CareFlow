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

### 本日 (2026-05-05) の本番障害から得た原則 (必読)

1. **本番 image は migrations を bake-in している**。
   `app/Dockerfile` は `alembic/` ディレクトリを image にコピーしているため、
   新しい revision を追加した場合は **必ず `up -d` の前に `build` をやり直す**。
   `git pull` 後に `up -d --force-recreate` だけ実行すると古い image (旧 migrations)
   が再生成され、`alembic upgrade head` が「revision not found」で失敗する。
2. **`--env-file /opt/carelink/.env --file docs/deployment/docker-compose.production.yml`
   は省略不可**。compose v2 は cwd の `.env` を interpolation 用には拾うが、
   `--env-file` 無しだと `env_file:` で参照される repo root `.env` のパスが
   ズレる事例が観測された。常にフル指定する。
3. **`alembic upgrade head` 後に必ず `alembic heads` を実行**し、出力が 1 行
   (= 単一 head) であることを確認する。複数 head は merge revision が
   未作成の状態。CI 側の `alembic-heads-check` job でも防御している。
4. **並列 PR で migration を作成する際は事前に revision 番号を割当**てる。
   独立に `alembic revision --autogenerate` を打つと両方が同じ親 revision を
   `down_revision` に設定し、merge 時に複数 head が発生する。Slack チャンネルに
   「次の revision は 0009」とアナウンスしてから作業を始める運用にする。

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

# ★ 必ず head 数を確認 (本日の本番障害学習)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env run --rm backend alembic heads
# 期待: 1 行のみ (例: `0008_merge_w4d_w4f (head)`). 2 行以上なら multiple heads 状態。
# その場合はデプロイを中断し、`alembic merge -m "merge heads" <revA> <revB>` で merge revision を作成してから再実行。

docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec postgres psql -U carelink -d carelink -c "\dt"
```

成功条件: `alembic_version` テーブルに最新 head が記録、`alembic heads` が単一 head を返す、users/staff/visit/patient 等のテーブルが存在。
失敗時: マイグレーション失敗時は `alembic downgrade base` でロールバックし、`docker compose -f docs/deployment/docker-compose.production.yml --env-file .env down -v` でボリュームごと作り直す（DB は新規なので破壊して問題なし。Phase H 後はこの手は使えない）。

## Phase F: Backend / Frontend コンテナ起動

> **重要**: `up -d` の前に **必ず `build`** を実行する (本日の本番障害学習)。
> 本番 image は `alembic/` を bake-in しているため、新 migration を反映するには
> 再 build が必須。`git pull` だけでは反映されない。

```bash
cd /opt/carelink
export GIT_SHA="$(git rev-parse HEAD)"  # frontend sw.js cache version
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env build backend frontend
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend frontend
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env ps
curl -fsSI http://localhost:18000/
curl -fsS  http://localhost:18001/api/v1/healthz

# kaipoke external network への接続確認 (Wave 5-A 以降は compose 側で自動接続)
docker network inspect playwrighttest1_default | grep carelink-backend
```

成功条件: `docker compose ps` で backend と frontend の双方が `Health=healthy`。`curl http://localhost:18000/` が 200 OK、`curl http://localhost:18001/api/v1/healthz` が 200 + JSON。`docker compose logs backend` に `Uvicorn running on 0.0.0.0:8000`、`docker compose logs frontend` に `Ready` 出力。
失敗時: 起動直後の jwt_secret バリデーションエラーは `.env` の `APP_ENV` と `JWT_SECRET` 長を疑う。frontend がビルドエラーで上がらない場合は `docker compose -f docs/deployment/docker-compose.production.yml --env-file .env logs frontend` を確認し、`pnpm-lock.yaml` 不在による依存解決ズレが疑わしいときは frontend 側で `pnpm install --lockfile-only` してから再 build。

## Phase G: Cloudflared ingress 追加

> **重要 (2026-05-05 実測)**: 本 VPS の cloudflared tunnel は **Cloudflare ダッシュボードのリモート管理モード** で動作している。`/etc/cloudflared/config.yml` をローカル編集してもランタイムには反映されない。`journalctl -u cloudflared -n 200 | grep "Updated to new configuration"` で実際にロードされている JSON 設定を確認できる。version=4 等が表示されればダッシュボード管理モード。

### G-1. 先に DNS CNAME を追加 (Cloudflare ダッシュボード)
1. https://dash.cloudflare.com → `kaipoke-api.net` Zone → **DNS** → **Records** → **Add record**
   - **Type**: `CNAME`
   - **Name**: `carelink`
   - **Target**: `<tunnel-uuid>.cfargotunnel.com` (例: `ff143e2a-65a9-468d-a8dd-96e56e690ae7.cfargotunnel.com`)
   - **Proxy status**: 🟠 **Proxied** (オレンジ雲、必須)
2. 確認: `nslookup carelink.kaipoke-api.net` が Cloudflare anycast IP (104.21.x.x / 172.67.x.x) を返す

### G-2-A. ダッシュボード管理モード (デフォルト)
1. https://one.dash.cloudflare.com → 左メニュー **Networks** → **Tunnels**
2. 該当 tunnel ID を選択 → **Configure** または **Edit** ボタン
3. **Public Hostname** タブ → **Add a public hostname**:

   | 項目 | 値 |
   |---|---|
   | Subdomain | `carelink` |
   | Domain | `kaipoke-api.net` |
   | Path | (空) |
   | Type | `HTTP` |
   | URL | `localhost:18000` |

4. **Save hostname**

frontend だけを公開すれば十分。`/api/v1/*` への内部呼び出しは `frontend/next.config.js` の `rewrites()` が docker network 越しに backend へ転送する構成。**API 用に `api.carelink...` のような別 hostname は不要**。

### G-2-B. ローカル管理モード (legacy fallback)
万一 `journalctl` で "Updated to new configuration" のログが見当たらず、ローカル `/etc/cloudflared/config.yml` がそのまま使われている tunnel の場合:
```bash
sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.bak.$(date +%Y%m%d)
sudo $EDITOR /etc/cloudflared/config.yml
# docs/deployment/cloudflared-config-fragment.yml の "↓↓↓ ここから下を ... 挿入 ↓↓↓"
# ブロックを、既存 `ingress:` 配下の **最上部** にコピー&ペースト。
# 既存ルール (kaipoke-api 等) と末尾 catch-all (`- service: http_status:404`) は
# 絶対に変更・削除しない。
sudo cloudflared tunnel ingress validate
sudo systemctl restart cloudflared   # reload は 2024+ で deprecated、restart 推奨
sudo systemctl status cloudflared --no-pager | head -20
```

成功条件:
- ダッシュボード管理モード: ダッシュボード保存後 30 秒以内に `curl -I https://carelink.kaipoke-api.net/` が **307 (NextAuth login redirect)** を返す
- どのモードでも: `curl -I https://kaipoke-api.net` で **既存 kaipoke-api サービスが反応し続ける** (404/200 のいずれでも、cloudflare が応答していれば OK)

失敗時:
- ダッシュボードモード: hostname 行を削除して保存 (即時ロールバック)
- ローカルモード: `config.yml.bak.*` で revert + restart
- いずれの場合も既存サービス疎通を `curl -I https://kaipoke-api.net` で必ず再確認 (既存サービス障害が最優先)

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

### 緊急 rollback (本日の本番障害学習を反映)

application 層の障害 (新リリースで /api/v1/* が 5xx) であれば、まず **コードを 1 つ前の commit に戻して再 build** が最速:

```bash
cd /opt/carelink
git log --oneline -10                  # 直前の安定 commit を特定
git revert <bad_commit_sha>            # revert commit を作る (force reset より安全)
git push origin main                   # GitHub に上げて履歴を残す (任意)
export GIT_SHA="$(git rev-parse HEAD)"
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env build backend frontend
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend frontend
curl -fsS http://localhost:18001/api/v1/healthz
```

DB 破壊を伴うマイグレーションが原因なら **DB バックアップから復元**:

```bash
# 最新のバックアップを確認 (運用開始後は cron で /opt/carelink/backups/*.sql を取得しておく)
ls -lt /opt/carelink/backups/ | head -5

# 一旦 backend を停止して接続を切る
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env stop backend

# psql に流し込む
docker exec -i carelink-postgres psql -U carelink -d carelink < /opt/carelink/backups/<latest>.sql

# backend を再起動 (image は revert 済みのものに揃えてから)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d backend
```

### 通常 rollback 手順 (上から順に実施)

1. **ingress revert**: `sudo cp /etc/cloudflared/config.yml.bak.YYYYMMDD /etc/cloudflared/config.yml && sudo systemctl reload cloudflared`
2. **container 停止**: `cd /opt/carelink && docker compose -f docs/deployment/docker-compose.production.yml --env-file .env down`
3. **DB 巻き戻し** (本番運用後はバックアップから):
   - 初回デプロイ直後で運用データなし → `docker compose -f docs/deployment/docker-compose.production.yml --env-file .env down -v` でボリュームごと削除
   - 運用後 → `docker exec -i carelink-postgres psql -U carelink -d carelink < /opt/carelink/backups/<latest>.sql` で復元 (上記 緊急 rollback 参照)
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
- ただし Wave 5-A 以降は **`playwrighttest1_default` を external network として
  compose に取り込み**、backend container が再生成 (`up -d --force-recreate`)
  されても自動で kaipoke 側 network に接続される。手動 `docker network connect`
  は不要。VPS で network 名が変わった場合は
  `docs/deployment/docker-compose.production.yml` の `networks.kaipoke.name`
  を更新する
- secret 値はすべて VPS 上で生成し、Git/Slack/メールに残さない
