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

> **前提条件**: VPS への SSH (root) 接続済み / `docker` `docker compose` インストール済み / `/opt/carelink/preflight-check.sh` がリポ内にあるか scp 済み
> **所要時間**: 2 分
> **失敗時の戻し方**: スクリプトが exit 1 → 該当項目 (ディスク空き / cloudflared 等) を解消するまで Phase B 以降に進まない。VPS への変更は何もしていないため戻し作業は不要。

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

> **前提条件**: GitHub deploy key (read) を VPS の `~/.ssh/id_ed25519_carelink` に配置済み、もしくは public repo 化済み
> **所要時間**: 1 分
> **失敗時の戻し方**: clone 後の `/opt/carelink` を `sudo rm -rf /opt/carelink` で削除して再試行。既存サービスへの影響なし。

```bash
sudo mkdir -p /opt/carelink && sudo chown $USER:$USER /opt/carelink
git clone https://github.com/skyblueearthjapan/CareLink.git /opt/carelink
cd /opt/carelink
git checkout main      # 本番は main を使用 (develop は staging)
git rev-parse HEAD     # commit hash を作業ログに記録 (例: <your-commit-sha>)
```

成功条件: `develop` HEAD が GitHub と一致 (`git status` clean)。
失敗時: ネットワーク経由の clone 失敗なら deploy key 認証を確認。再デプロイ時に `/opt/carelink` が空でない場合は、本番データを退避してから `rm -rf` するか、別ディレクトリに clone して `rsync` で上書きする。

## Phase C: `.env` 作成 (統合 1 ファイル)

> **前提条件**: VPS で `openssl` が使える (Ubuntu 標準) / 各 secret 値の本番用値が準備済み (Phase C-0 参照)
> **所要時間**: 5 分 (値の貼り付け + chmod 確認)
> **失敗時の戻し方**: `/opt/carelink/.env` を削除して再生成。既存サービスへの影響なし。

### Phase C-0: 必要な secret 値を生成 (コピペで使用可)

```bash
# JWT_SECRET / NEXTAUTH_SECRET (32 byte base64)
openssl rand -base64 32     # 例出力: <your-jwt-secret>

# POSTGRES_PASSWORD (24 chars 英数)
openssl rand -base64 18 | tr -d '/+=' | cut -c1-24    # 例: <your-pg-password>

# 外部 API token (GEMINI_API_KEY / GOOGLE_MAPS_API_KEY) は各コンソールで発行 → コピペ
# KAIPOKE_API_TOKEN は kaipoke-api 側で発行された値を使用
```

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

> **前提条件**: Phase C 完了 (`/opt/carelink/.env` 存在 + 600 perm)
> **所要時間**: 1 分 (image pull 済の場合) / 3 分 (初回 pull)
> **失敗時の戻し方**: `docker compose ... down -v postgres` でボリューム削除 (= 初回のみ安全。Phase H 通過後は backup-restore-runbook.md を参照)。

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

> **前提条件**: Phase D で postgres が `Health=healthy` (= `pg_isready` 通過)
> **所要時間**: 30 秒 (8 revisions、空 DB) / 数分 (運用中の DB)
> **失敗時の戻し方**: 初回デプロイなら `down -v` で破壊。本番運用後は `backup-restore-runbook.md` Step 4-5 で直前 backup を復元。

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

> **前提条件**: Phase E まで成功 (alembic head が単一)、`/opt/carelink/.env` 設定済み、`playwrighttest1_default` external network が VPS 上に存在 (`docker network ls | grep playwrighttest1`)
> **所要時間**: 5〜8 分 (frontend pnpm install + Next.js build がボトルネック)
> **失敗時の戻し方**: `docker compose ... stop backend frontend` → 直前タグの image に切り戻して up -d (Phase J ③)。

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

### Phase F 完了チェックリスト (dry-run → 本実行)

**dry-run / 確認** (まだ既存 traffic に影響なし):
- [ ] `docker compose ... config` で interpolated YAML が想定通り (env が全て埋まっている)
- [ ] `docker compose ... build --dry-run` (compose v2.20+) または `docker compose ... build --no-cache backend` を staging 等の別ホストで成功させる
- [ ] backend image の `RUN alembic check` が通る (= bake-in alembic と DB schema が一致)
- [ ] `docker compose ... ps postgres` が `Health=healthy`

**本実行**:
- [ ] `docker compose ... build backend frontend` 成功 (両方とも `Successfully tagged`)
- [ ] `docker compose ... up -d --force-recreate backend frontend` 完了
- [ ] `docker compose ... ps` で `backend` `frontend` 共に `Health=healthy` (60 秒以内)
- [ ] `curl -fsS http://localhost:18001/api/v1/healthz` が 200 + `{"status":"ok"}`
- [ ] `curl -fsSI http://localhost:18000/` が 200
- [ ] `docker compose ... logs --tail=50 backend` に Traceback / ERROR 行が無い

## Phase G: Cloudflared ingress 追加

> **前提条件**: Phase F 通過 (localhost:18000 が 200)、Cloudflare ダッシュボード (zone `kaipoke-api.net`) への管理権限
> **所要時間**: 5 分 (DNS 反映含む)
> **失敗時の戻し方**: ダッシュボードモードなら追加した hostname 行を削除して save (即時反映)。ローカルモードなら `config.yml.bak.YYYYMMDD` から復元 + `systemctl restart cloudflared`。**既存 `kaipoke-api.net` の疎通を必ず先に確認すること**。

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

> **前提条件**: Phase G 完了 (cloudflared に carelink hostname 追加済)
> **所要時間**: 2 分 (DNS 伝搬待ちで前後)
> **失敗時の戻し方**: Phase G の rollback (hostname 削除) を実施。frontend/backend container の再起動 (`docker compose ... restart backend frontend`) でも改善しない場合は Phase J 緊急 rollback。


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

> **前提条件**: Phase H 完了 (`/api/v1/healthz` が 200)
> **所要時間**: 1 分
> **失敗時の戻し方**: 作成した admin row を `DELETE FROM users WHERE email='<email>'` で削除して再試行。

```bash
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec backend python scripts/create_admin.py
# 対話プロンプトで email / password を入力
# password は最小 12 文字 (script 側でも 12 文字未満を拒否)
```

成功条件: `admin user upserted: <email>` のログ。`SELECT email, role FROM users` で `role=admin` を確認。
失敗時: 既存 admin がある場合はスクリプトが select-then-update で password を上書き更新する仕様 (詳細は `initial-admin-seed.md`)。

## Phase J: 緊急 Rollback 手順

> **前提条件**: VPS root SSH、`/opt/carelink/backups/` に直近 backup あり
> **所要時間**: ① コード rollback 5 分 / ② DB rollback 15 分 / ③ image rollback 5 分
> **失敗時の戻し方**: 既存 `kaipoke-api.net` 疎通が失われた場合は **cloudflared 設定 (Phase G ダッシュボード変更 or `/etc/cloudflared/config.yml.bak.*`) を最優先で revert**。CareFlow 自身の rollback より優先。

### J-0. 判断フローチャート

```
[障害検知 (5xx / unhealthy / smoke fail)]
        │
        ▼
   原因はどこか?
        │
   ┌────┴────┬─────────────┬──────────────┐
   ▼         ▼             ▼              ▼
 コード     migration    image不整合    インフラ
 (新bug)   (DB破壊)     (build失敗)    (network/cf)
   │         │             │              │
   ▼         ▼             ▼              ▼
 ① revert  ② DB restore  ③ image tag    Phase G/H
 (5min)    (15min)        切替 (5min)    revert
   │         │             │              │
   ▼         ▼             ▼              ▼
 healthz?  healthz?       healthz?       既存サービス疎通?
   │         │             │              │
   OK        OK            OK             OK
   ▼         ▼             ▼              ▼
 完了      完了            完了           原因継続調査
```

判断基準:
- **① コード rollback で復旧**: 直前デプロイの commit に application bug が含まれており、migration は無害 (DB schema が前後で互換)。最も多いケース。
- **② DB rollback で復旧**: migration が schema 破壊的 (DROP COLUMN / 型変換失敗) で、forward migration では戻せない。**RPO 24 時間のデータロスを伴う**ため、business 判断 (失われる更新分の許容) を必ず確認。
- **③ image rollback で復旧**: build 段階での dependency 解決ミス (pnpm-lock 不整合、新規追加 Python lib の wheel が無い等)。コード自体は OK だが image 構築が失敗しているケース。
- **既存サービス影響あり**: cloudflared の ingress 変更が原因の可能性。Phase G の手順で hostname 削除 or `config.yml.bak` 復元を最優先。

---

### J-①: コード Rollback (application 層障害、5 分)

新リリースの `/api/v1/*` が 5xx を吐いている、UI が真っ白、等。**最も多い & 最速** の手段。

```bash
# 1) VPS で直前の安定 commit を特定
ssh root@72.60.211.213
cd /opt/carelink
git log --oneline -10
# 出力例: <bad-sha>  feat(W5-X): broken change
#        <good-sha> feat(W5-Y): last known good

# 2) revert commit を作って push (force reset より安全、GitHub Actions の deploy.yml も再実行可能)
git revert <bad-sha> --no-edit
git push origin main      # GitHub Actions deploy.yml が手動 dispatch で再実行可能

# 3) GitHub Actions で deploy を手動再実行 (Run workflow ボタン)、または VPS 上で直接 build + recreate
export GIT_SHA="$(git rev-parse HEAD)"
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env build backend frontend
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend frontend

# 4) 確認
curl -fsS http://127.0.0.1:18001/api/v1/healthz   # 200 + {"status":"ok"} 期待
curl -fsSI https://carelink.kaipoke-api.net/      # 200 期待
```

成功条件: healthz 200 + UI 表示復旧。
**この方法で復旧しない場合は migration 起因 → ② へ。**

---

### J-②: DB Rollback (migration / DB 破壊起因、15 分目安)

`alembic upgrade` が走った後にデータ不整合が発覚した場合。**直前 backup に戻すため RPO 24 時間 (= 最大 1 日分のデータロス)。**
詳細手順は `docs/deployment/backup-restore-runbook.md` Step 1〜7 を参照。要約:

```bash
ssh root@72.60.211.213

# 1) 最新 backup を確認
ls -lt /opt/carelink/backups/daily-*.sql.gz | head -3
export BACKUP_FILE=/opt/carelink/backups/daily-<YYYYMMDD-HHMM>.sql.gz

# 2) 現状スナップショット (保険、必須)
docker exec carelink-postgres pg_dump -U carelink -d carelink \
  > /tmp/pre-restore-snap-$(date +%Y%m%d-%H%M).sql

# 3) backend / frontend 停止 (接続を切る)
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env stop backend frontend

# 4) DB DROP + CREATE (PG13+ の WITH (FORCE) で接続強制切断)
docker exec carelink-postgres psql -U carelink -d postgres -c "DROP DATABASE carelink WITH (FORCE);"
docker exec carelink-postgres psql -U carelink -d postgres -c "CREATE DATABASE carelink OWNER carelink;"

# 5) backup 流し込み (ON_ERROR_STOP で途中エラー時は中断)
gunzip -c "$BACKUP_FILE" | docker exec -i carelink-postgres psql -U carelink -d carelink -v ON_ERROR_STOP=1

# 6) backend 起動 + alembic head と DB の整合確認 → 必要なら upgrade
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d backend
sleep 10
docker exec carelink-backend alembic current
docker exec carelink-backend alembic heads
# DB head が image head より古い場合のみ:
docker exec carelink-backend alembic upgrade head

# 7) frontend 再開 + smoke
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d frontend
curl -fsS http://127.0.0.1:18001/api/v1/healthz
curl -fsSI https://carelink.kaipoke-api.net/
```

成功条件: healthz 200 + UI 表示復旧 + 主要画面 (患者一覧/スタッフ一覧/週カレンダー) で過去データ表示。
**復旧しない場合は ③ へ。**

---

### J-③: コンテナ Image Rollback (build 失敗 / dependency 起因、5 分)

`docker compose build` 自体が失敗、もしくは新 image が起動直後に panic、healthcheck timeout 等。
**前提**: 本番 image を `carelink-backend:<sha>` `carelink-frontend:<sha>` で tag 化する運用が必要 (W6 で導入提案)。
現時点では `latest` のみ tag 付与しているため、**ローカル docker registry に直前の image が `<none>` として残っている前提**で操作する。

```bash
ssh root@72.60.211.213

# 1) 利用可能な image を確認 (直前の <none> tag を探す)
docker images | grep -E '(carelink|<none>)' | head -10
# 期待出力例:
# carelink-backend  latest    abc123...   2 hours ago   500MB
# <none>            <none>    def456...   1 day ago     500MB   ← これが直前 image

# 2) 直前 image を tag 付与
docker tag def456... carelink-backend:rollback-$(date +%Y%m%d-%H%M)
docker tag def456... carelink-backend:latest

# 3) container を recreate (image は :latest を参照しているため up -d で切替わる)
cd /opt/carelink
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend
curl -fsS http://127.0.0.1:18001/api/v1/healthz

# frontend 側も同様に rollback する場合は backend と同じ手順
```

> **W6 提案**: deploy.yml に `docker tag carelink-backend:latest carelink-backend:${{ github.sha }}` を追加し、過去 image を明示的に tag 保持することで本手順を「直前 sha tag に戻すだけ」に単純化できる。

---

### J-終了後: 後片付け

- **revert commit がある場合**: 後続 PR で原因 commit を fix して再 merge → 通常 deploy で解消
- **DB rollback を行った場合**: business 側に「失われたデータ範囲」を共有し、必要なら手動再入力依頼
- **既存 `kaipoke-api.net` 疎通**: 必ず `curl -fsSI https://kaipoke-api.net` で確認 (CareFlow rollback 中に巻き込み事故が起きていないか)
- **AuditLog 確認**: `docker exec carelink-postgres psql -U carelink -d carelink -c "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 20;"` で異常操作の有無を確認

---

## Phase 5: 監視・バックアップ自動化 (Wave 5-B)

> **前提条件**: Phase H 通過 (本番疎通 OK)、root SSH、`webhook` URL (Slack/Discord) を払い出し済み (5-5 の通知用、未設定でも noop で動作)
> **所要時間**: 15 分 (cron 登録 + 動作確認 healthcheck 1 周期)
> **失敗時の戻し方**: `sudo rm /etc/cron.d/carelink-healthcheck /etc/cron.d/carelink-backup /etc/logrotate.d/carelink` で全 cron / logrotate を撤去 (既存サービス影響なし)。`/var/log/carelink/` `/var/lib/carelink/` `/opt/carelink/backups/` は残しても害なし。

本 Phase は **初回デプロイ完了後 (Phase H 通過後)** に 1 度だけ VPS 上で設定する。
スクリプト本体はリポジトリ配下にあるため、`git pull` で更新は自動反映される。
cron / logrotate などの **OS 側エントリだけ** を初回手動登録する。

設置するもの:
- `docs/deployment/scripts/healthcheck-carelink.sh` (5 分毎)
- `docs/deployment/scripts/backup-carelink-db.sh` (日次 02:30)
- `docs/deployment/scripts/notify-failure.sh` (上記 2 つから呼び出し)
- `docs/deployment/scripts/logrotate-carelink.conf`
- `docs/deployment/backup-restore-runbook.md` (リストア手順)

### 5-1. ログ / state ディレクトリ作成

```bash
sudo mkdir -p /var/log/carelink /var/lib/carelink /opt/carelink/backups
sudo chmod 0755 /var/log/carelink /var/lib/carelink
sudo chmod 0750 /opt/carelink/backups
```

### 5-2. スクリプトに実行権限を付与

`git clone` 直後は実行ビットが落ちている可能性があるため明示的に付与する。

```bash
sudo chmod +x /opt/carelink/docs/deployment/scripts/healthcheck-carelink.sh
sudo chmod +x /opt/carelink/docs/deployment/scripts/backup-carelink-db.sh
sudo chmod +x /opt/carelink/docs/deployment/scripts/notify-failure.sh
```

### 5-3. healthcheck cron 登録 (5 分毎)

```bash
sudo tee /etc/cron.d/carelink-healthcheck >/dev/null <<'EOF'
# CareLink container + endpoint healthcheck (Wave 5-B)
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root /opt/carelink/docs/deployment/scripts/healthcheck-carelink.sh
EOF
sudo chmod 644 /etc/cron.d/carelink-healthcheck
```

動作確認:
```bash
sudo /opt/carelink/docs/deployment/scripts/healthcheck-carelink.sh && echo OK
tail -n 5 /var/log/carelink/healthcheck.log
```

**チェック内容:**
1. `curl http://127.0.0.1:18001/api/v1/healthz` が 200
2. `curl http://127.0.0.1:18000/api/healthz` が 200
3. `carelink-postgres` / `carelink-backend` / `carelink-frontend` の 3 container が `Up ... (healthy)`

**失敗判定:** `/var/lib/carelink/healthcheck.failcount` をインクリメントし、**3 連続失敗** で `notify-failure.sh` を起動 (= 15 分間継続失敗で alert)。成功すると counter は 0 にリセット。

### 5-4. backup cron 登録 (日次 02:30 JST)

```bash
sudo tee /etc/cron.d/carelink-backup >/dev/null <<'EOF'
# CareLink daily PostgreSQL backup (Wave 5-B). Server TZ assumed JST.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 2 * * * root /opt/carelink/docs/deployment/scripts/backup-carelink-db.sh
EOF
sudo chmod 644 /etc/cron.d/carelink-backup
```

動作確認 (任意のタイミングで手動実行):
```bash
sudo /opt/carelink/docs/deployment/scripts/backup-carelink-db.sh
ls -lh /opt/carelink/backups/daily-*.sql.gz | tail -3
tail -n 10 /var/log/carelink/backup.log
```

**保持期間:** 7 日 (`find -mtime +7 -delete`)。
**サイズ目安:** 初期は数 MB、運用 1 年後でも数百 MB を想定。`/opt/carelink/backups` 配下が 10GB を超えたら別ストレージ (object store) を検討。

### 5-5. webhook 通知の設定 (任意)

Slack / Discord / generic webhook URL を `/opt/carelink/.env` に追記:

```bash
echo 'NOTIFY_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ' | sudo tee -a /opt/carelink/.env
sudo chmod 600 /opt/carelink/.env
```

`notify-failure.sh` は cron 起動時に `/opt/carelink/.env` を source して env を取得する。
未設定時は noop + log のみで cron は失敗扱いにならない。

メール (sendmail / postfix) は **本 Phase ではサポートしない**。MTA 構築は別途。

### 5-6. logrotate 設置

```bash
sudo ln -sf /opt/carelink/docs/deployment/scripts/logrotate-carelink.conf /etc/logrotate.d/carelink
sudo logrotate -d /etc/logrotate.d/carelink   # dry-run; "log does not need rotating" が出ればOK
```

ローテーションポリシー:
- `/var/log/carelink/healthcheck.log`: 週次、4 世代 (= 約 1 ヶ月分)
- `/var/log/carelink/backup.log`: 月次、3 世代 (= 約 3 ヶ月分)

### 5-7. Restore リハーサル

リストア手順は `docs/deployment/backup-restore-runbook.md` を参照。
**RTO 15 分 / RPO 24 時間** を前提に運用する。月 1 回ステージング環境でリハーサルを実施し、所要時間を測定して runbook を更新すること。

### 5-8. W5-A `deploy.yml` smoke との連携

GitHub Actions `deploy.yml` (Wave 5-A) の最終 step `Smoke test` は `/api/v1/healthz` を 30 秒以内に確認する。
deploy 完了後 5 分以内に `healthcheck-carelink.sh` も成功することを **継続 smoke** として扱う。失敗が観測された場合は自動 alert (5-3) が発火する想定。

deploy 直後の 1 サイクル (5 分後) を手動で確認する手順:

```bash
ssh root@72.60.211.213 'tail -n 5 /var/log/carelink/healthcheck.log'
# 直近行が "OK backend=200 frontend=200 containers=3/3 healthy" であれば成功
```

### Phase 5 完了チェックリスト (dry-run → 本実行)

**dry-run / 確認**:
- [ ] `sudo /opt/carelink/docs/deployment/scripts/healthcheck-carelink.sh && echo OK` が手動成功
- [ ] `sudo /opt/carelink/docs/deployment/scripts/backup-carelink-db.sh` を手動実行 → `/opt/carelink/backups/daily-*.sql.gz` が作成され、サイズが想定範囲 (数 MB 以上)
- [ ] `sudo logrotate -d /etc/logrotate.d/carelink` (dry-run) で構文エラーなし
- [ ] webhook URL 設定時、`curl -X POST $NOTIFY_WEBHOOK_URL -d 'test'` で Slack/Discord に届く

**本実行 (cron 登録後)**:
- [ ] `sudo cat /etc/cron.d/carelink-healthcheck` `sudo cat /etc/cron.d/carelink-backup` 両方が存在 + 0644
- [ ] cron 登録から 5 分後に `tail /var/log/carelink/healthcheck.log` に 1 行追加
- [ ] 翌朝 02:30 JST 以降に `ls /opt/carelink/backups/` で当日分の `daily-*.sql.gz` が存在
- [ ] `restore リハーサル`: 月 1 回 `backup-restore-runbook.md` Step 1〜7 を staging で実施 → `restore-rehearsal-YYYYMM.md` を audit/ に記録

### 5-9. orphan network `deployment_default` 復旧手順 (W5-F)

`docker compose ... up -d --force-recreate backend` を実行した後、
古い `deployment_default` network が残ったまま frontend が古い network ID
を参照し続ける症状が観測されている (compose v2 の force-recreate 仕様)。
発症すると frontend の起動ログに以下のような stale network エラーが出る:

```
Error response from daemon: network <old-network-id> not found
```

**復旧手順 (本番影響最小):**

```bash
cd /opt/carelink

# 1) 該当 orphan network を確認 (出力に deployment_default が含まれる)
docker network ls | grep -E '(deployment_default|carelink)'

# 2) 該当 orphan network を削除 (使用中の container があれば自動で
#    disconnect される。失敗するなら 4) を先に実行)
docker network rm deployment_default

# 3) compose 全体を再起動して network を再作成 + 全 service を再 attach
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d

# 4) frontend が古い network ID を抱えたままなら force-recreate で完全に作り直し
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate frontend

# 5) 疎通確認
curl -fsSI http://localhost:18000/
curl -fsS  http://localhost:18001/api/v1/healthz
docker network inspect playwrighttest1_default | grep carelink-backend
```

**予防策:**
- `up -d --force-recreate backend` のような service 単位の force-recreate
  は network 切替を伴うときに不整合を起こしやすい。原則として
  `up -d --force-recreate` は **全 service まとめて** 実行する
- どうしても backend 単独再生成が必要なときは、後段で `up -d frontend` を
  追加実行して frontend を新 network に再 attach させる
- 既存 kaipoke external network (`playwrighttest1_default`) には影響しないが、
  操作前に必ず `docker network inspect playwrighttest1_default` で kaipoke
  側 container が disconnect されていないことを確認する

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
