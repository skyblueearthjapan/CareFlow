# CareFlow Secrets Rotation Runbook (Wave 5-E)

対象: Hostinger Malaysia VPS (`72.60.211.213`) / 公開ドメイン `https://carelink.kaipoke-api.net`
範囲: `/opt/carelink/.env` に格納された全 secret + GitHub Actions Secrets

> **基本原則**:
> - rotation の前に **必ず `/opt/carelink/.env` を `.env.bak.YYYYMMDD` にコピー** して退避
> - 失敗時はバックアップからの restore + container recreate で即時復旧可能であることを確認してから本実行
> - 旧 secret は鍵払い出し元 (GCP / GitHub / kaipoke 等) で **rotation 完了確認後 (= 24-48 時間 monitoring 後) に削除** する。同時切替は不可。
> - 新旧の二重運用ができる secret (GCP API key 等) は **新旧並行期間を 24 時間以上**設けて Cloudflare キャッシュ等の漏れを吸収する

## Rotation policy 一覧

| # | Secret | 配置場所 | 推奨頻度 | 即時 rotation トリガ |
|---|---|---|---|---|
| 1 | `JWT_SECRET` | `/opt/carelink/.env` | **90 日** | コード/.env 漏洩疑い、admin の不正アクセス検知 |
| 2 | `KAIPOKE_API_TOKEN` | `/opt/carelink/.env` | **180 日** | kaipoke-api 側で revoke 通知、token 漏洩疑い |
| 3 | `GEMINI_API_KEY` | `/opt/carelink/.env` | **90 日** | quota 異常、Google Cloud Console アラート |
| 4 | `GOOGLE_MAPS_API_KEY` | `/opt/carelink/.env` | **90 日** | quota 異常、不明 referer からの呼び出し検知 |
| 5 | `POSTGRES_PASSWORD` | `/opt/carelink/.env` | **365 日** | DB ホスト侵害疑い、退職者あり |
| 6 | `NEXTAUTH_SECRET` | `/opt/carelink/.env` | **90 日** | session token 漏洩疑い |
| 7 | `VPS_SSH_KEY` | GitHub → Settings → Secrets and variables → Actions | **180 日** | GitHub アクセス異常、運用者交替 |

> **注**: 推奨頻度を経過した場合、月次オペで通知 → 翌月度内で rotation 実施。緊急 trigger 時は 24 時間以内に実施。

---

## 共通: rotation 直前の保険手順

```bash
ssh root@72.60.211.213
cd /opt/carelink

# 1) .env を退避 (戻し用)
sudo cp .env .env.bak.$(date +%Y%m%d-%H%M)
sudo chmod 600 .env.bak.*

# 2) DB バックアップを最新化 (POSTGRES_PASSWORD rotation 時は必須)
sudo /opt/carelink/docs/deployment/scripts/backup-carelink-db.sh
ls -lh /opt/carelink/backups/ | tail -3

# 3) 現在動作中の commit と alembic head を記録
docker exec carelink-backend git rev-parse HEAD 2>/dev/null || git -C /opt/carelink rev-parse HEAD
docker exec carelink-backend alembic current
```

---

## 1. `JWT_SECRET` (90 日)

**影響範囲**: 全 user の access token / refresh token が **即時無効化** → 全ユーザーが再ログイン必要。

```bash
ssh root@72.60.211.213
cd /opt/carelink

# 1) 新 secret 生成 (32 byte 以上必須、config.py の field_validator が起動時にチェック)
NEW_JWT="$(openssl rand -base64 32)"
echo "$NEW_JWT" | wc -c    # >= 33 (改行込み) を確認

# 2) .env 更新 (バックアップ取得後)
sudo cp .env .env.bak.$(date +%Y%m%d-%H%M)
sudo sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$NEW_JWT|" .env
sudo grep '^JWT_SECRET=' .env    # 反映確認
sudo chmod 600 .env

# 3) backend のみ recreate (frontend は JWT を扱わないので影響無)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend

# 4) 起動確認 (起動時に jwt_secret 32 文字バリデーションが走る)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env logs --tail=30 backend | grep -i 'uvicorn\|error'
curl -fsS http://127.0.0.1:18001/api/v1/healthz

# 5) 全ユーザーへ「再ログインのお願い」を Slack/メール通知
```

**戻し方**: `sudo cp .env.bak.<latest> .env && docker compose ... up -d --force-recreate backend`

---

## 2. `KAIPOKE_API_TOKEN` (180 日)

**影響範囲**: kaipoke-api との中継 14 endpoints (W4-A) が **新トークン反映まで 401**。kaipoke 側で旧トークンを **当面残す** ことで切替時間 0 にできる。

```bash
# 1) kaipoke-api 側で新 token を生成 (kaipoke-api 管理者依頼 or kaipoke-api 管理 UI)
#    → 旧 token は **新 token 検証完了後** に revoke。並行期間 24 時間推奨。

# 2) VPS で .env 更新
ssh root@72.60.211.213
cd /opt/carelink
sudo cp .env .env.bak.$(date +%Y%m%d-%H%M)
sudo sed -i "s|^KAIPOKE_API_TOKEN=.*|KAIPOKE_API_TOKEN=<new-token-here>|" .env
sudo grep '^KAIPOKE_API_TOKEN=' .env
sudo chmod 600 .env

# 3) backend recreate
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend

# 4) kaipoke 中継 smoke test (例: 患者一覧取得)
curl -fsS http://127.0.0.1:18001/api/v1/kaipoke/patients -H "Authorization: Bearer <admin-jwt>" | head -50

# 5) 24 時間 monitor 後、kaipoke-api 管理者に旧 token revoke を依頼
```

**戻し方**: `.env.bak.*` から復元 + backend recreate。kaipoke 側で旧 token を残しておけば DB 操作不要。

---

## 3. `GEMINI_API_KEY` (90 日)

**影響範囲**: 自然言語入力 → 構造化 (W4-B) のみ。Gemini API 呼び出しが新キー反映まで 401/403。

```bash
# 1) Google AI Studio (https://aistudio.google.com/app/apikey) で新キー生成
#    プロジェクトと既存キーを確認、同じ Cloud project に紐づけ
#    → 旧キーは **削除せず**、並行運用 24 時間後に削除

# 2) VPS で .env 更新
ssh root@72.60.211.213
cd /opt/carelink
sudo cp .env .env.bak.$(date +%Y%m%d-%H%M)
sudo sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=<new-key-here>|" .env
sudo grep '^GEMINI_API_KEY=' .env
sudo chmod 600 .env

# 3) backend recreate (Gemini key は backend のみ使用)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend

# 4) 動作確認
curl -fsS http://127.0.0.1:18001/api/v1/healthz
# UI 側で Gemini 自然言語入力フォーム経由で 1 件 parse させて成功確認

# 5) 24 時間 monitor 後、AI Studio で旧キーを削除
```

**戻し方**: `.env.bak.*` から復元 + backend recreate。

---

## 4. `GOOGLE_MAPS_API_KEY` (90 日)

**影響範囲**: 住所 → 緯度経度 geocoding (W4-C) のみ。

> **注意**: 新キーには **必ず旧キーと同じ IP / referer 制限を設定**する。GCP Console の制限漏れで unauthorized リクエストを許してしまう事故が頻発。

```bash
# 1) GCP Console (https://console.cloud.google.com/apis/credentials) で新キー生成
#    → IP 制限: 72.60.211.213 (VPS) / referer 制限: *.kaipoke-api.net
#    → API 制限: Geocoding API のみ enable
#    → 旧キーは並行運用 24 時間後に削除

# 2) VPS で .env 更新
ssh root@72.60.211.213
cd /opt/carelink
sudo cp .env .env.bak.$(date +%Y%m%d-%H%M)
sudo sed -i "s|^GOOGLE_MAPS_API_KEY=.*|GOOGLE_MAPS_API_KEY=<new-key-here>|" .env
sudo grep '^GOOGLE_MAPS_API_KEY=' .env
sudo chmod 600 .env

# 3) backend recreate
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend

# 4) 動作確認 (患者登録画面で住所を入れて緯度経度自動補完を確認)
curl -fsS http://127.0.0.1:18001/api/v1/healthz

# 5) 24 時間 monitor 後、GCP Console で旧キーを削除
```

**戻し方**: `.env.bak.*` から復元 + backend recreate。

---

## 5. `POSTGRES_PASSWORD` (365 日) - **最も慎重に**

**影響範囲**: DB 接続が新パスワード反映まで全停止。**RPO ゼロ目標** のため必ず `pg_dump` 直前に取得し、復旧経路を確保。

```bash
ssh root@72.60.211.213
cd /opt/carelink

# 0) ★ 必須: 直前 backup を取得 (RPO ゼロ確保)
sudo /opt/carelink/docs/deployment/scripts/backup-carelink-db.sh
ls -lh /opt/carelink/backups/ | tail -3

# 1) 新パスワード生成
NEW_PG="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-24)"
echo "new pg password length: $(echo -n "$NEW_PG" | wc -c)"   # 24 を確認

# 2) Postgres 内で ALTER (container は停止せず実施可能)
docker exec carelink-postgres psql -U carelink -d postgres \
  -c "ALTER USER carelink WITH PASSWORD '$NEW_PG';"

# 3) .env 更新 (DATABASE_URL の path 部 + POSTGRES_PASSWORD の 2 箇所を更新)
sudo cp .env .env.bak.$(date +%Y%m%d-%H%M)
# DATABASE_URL=postgresql+asyncpg://carelink:<old>@postgres:5432/carelink → <new> に置換
sudo sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$NEW_PG|" .env
sudo sed -i "s|postgresql+asyncpg://carelink:[^@]*@|postgresql+asyncpg://carelink:$NEW_PG@|" .env
sudo grep -E '^(POSTGRES_PASSWORD|DATABASE_URL)=' .env    # 両方反映を確認
sudo chmod 600 .env

# 4) backend / frontend を recreate (= 新接続文字列で再接続)
#    postgres container 自体は recreate 不要 (in-DB の ALTER で password は反映済)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend frontend

# 5) 接続確認
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env exec backend \
  python -c "import asyncpg, asyncio, os; asyncio.run(asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg','')))" \
  && echo "DB connect OK"
curl -fsS http://127.0.0.1:18001/api/v1/healthz
```

**戻し方** (緊急):
```bash
# 1) .env と postgres password を旧値に同期して戻す
sudo cp .env.bak.<latest> .env
OLD_PG=$(grep '^POSTGRES_PASSWORD=' .env.bak.<latest> | cut -d= -f2-)
docker exec carelink-postgres psql -U carelink -d postgres \
  -c "ALTER USER carelink WITH PASSWORD '$OLD_PG';"
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate backend frontend
```

---

## 6. `NEXTAUTH_SECRET` (90 日)

**影響範囲**: 全 user のセッション cookie が無効化 → 再ログイン必要。frontend のみ。

```bash
ssh root@72.60.211.213
cd /opt/carelink

# 1) 新 secret 生成 (32 byte base64)
NEW_NEXT="$(openssl rand -base64 32)"

# 2) .env 更新
sudo cp .env .env.bak.$(date +%Y%m%d-%H%M)
sudo sed -i "s|^NEXTAUTH_SECRET=.*|NEXTAUTH_SECRET=$NEW_NEXT|" .env
sudo grep '^NEXTAUTH_SECRET=' .env
sudo chmod 600 .env

# 3) frontend のみ recreate (NextAuth の secret は frontend で消費)
docker compose -f docs/deployment/docker-compose.production.yml --env-file .env up -d --force-recreate frontend

# 4) 動作確認
curl -fsSI https://carelink.kaipoke-api.net/        # 200 or 307 (login redirect) 期待
# UI で再ログインができることを確認
```

**戻し方**: `.env.bak.*` から復元 + frontend recreate。

---

## 7. `VPS_SSH_KEY` (GitHub Secret, 180 日)

**影響範囲**: GitHub Actions `deploy.yml` が新鍵反映まで失敗。VPS への手動 SSH には影響なし (個別の運用者鍵を使用しているため)。

```bash
# 1) ローカル (運用者 PC) で新鍵生成
ssh-keygen -t ed25519 -f ~/.ssh/carelink_deploy_$(date +%Y%m%d) -N "" -C "github-actions-deploy"
# 公開鍵: ~/.ssh/carelink_deploy_YYYYMMDD.pub
# 秘密鍵: ~/.ssh/carelink_deploy_YYYYMMDD

# 2) VPS の authorized_keys に新公開鍵を追加 (旧鍵を残したまま並行運用)
cat ~/.ssh/carelink_deploy_YYYYMMDD.pub | ssh root@72.60.211.213 'cat >> ~/.ssh/authorized_keys'
ssh root@72.60.211.213 'wc -l ~/.ssh/authorized_keys'    # 行数増加を確認

# 3) GitHub Settings → Secrets and variables → Actions
#    → VPS_SSH_KEY を編集 → 新秘密鍵 (~/.ssh/carelink_deploy_YYYYMMDD の中身) を貼り付け → Update secret
#    cat ~/.ssh/carelink_deploy_YYYYMMDD で内容を確認 (BEGIN OPENSSH PRIVATE KEY ... END OPENSSH PRIVATE KEY を含む全文)

# 4) GitHub Actions で deploy.yml を手動実行 (Run workflow ボタン → main を指定)
#    → 緑チェックで成功確認

# 5) 旧公開鍵を VPS の authorized_keys から削除
ssh root@72.60.211.213
vi ~/.ssh/authorized_keys
# 旧公開鍵の行 (運用者ノートで前回 rotation 時の fingerprint を確認) を削除
ssh-keygen -l -f ~/.ssh/authorized_keys    # fingerprint で残存鍵を確認

# 6) ローカルで旧秘密鍵を削除
shred -u ~/.ssh/carelink_deploy_<old-date>
```

**戻し方**:
- GitHub Secret 更新後 deploy が失敗 → GitHub Secret に旧秘密鍵を再貼付 + Run workflow で再試行
- VPS の authorized_keys を vi で誤編集 → 別運用者の SSH 鍵で root login して `~/.ssh/authorized_keys.bak` から復元 (rotation 前に必ず backup を取る習慣を)

---

## Rotation 履歴管理 (推奨)

`/opt/carelink/.env.rotation-log` を VPS 上に保管 (Git には commit しない):

```text
# Format: YYYY-MM-DD HH:MM <secret-name> rotated by <operator>
2026-05-05 14:00 JWT_SECRET rotated by admin-01@lineworks-local.info (90日定期)
2026-05-05 14:30 NEXTAUTH_SECRET rotated by admin-01@lineworks-local.info (90日定期)
```

将来的には `vault` / `1Password CLI` で完全自動化を検討 (W6 以降)。

---

## 監査連携

- AuditLog (W4-F): rotation 直前/直後で `audit_log` テーブルに operator (admin user) 名で `secret_rotation` action を記録 (現在 manual。W6 で自動化検討)
- 通知: `NOTIFY_WEBHOOK_URL` 経由で rotation 完了を Slack に投稿 (script 化未実装、W6 検討)
