# D5: DevOps & QA 実装計画書

## 1. 概要・目的

CareLink を、既存の `kaipoke-api` が稼働する Hostinger Malaysia VPS（72.60.211.213）に**並存デプロイ**する。既存サービスの運用を一切妨害せず、`carelink.kaipoke-api.net` で公開する。最初は手動デプロイ・最低限のCI（lint/typecheck/test）で立ち上げ、運用が安定したら GitHub Actions による自動デプロイに段階移行する。医療情報を扱うため、HTTPS必須・最小権限・日次バックアップを初期から整備する。

**成功定義**：
- 既存 kaipoke-api の応答時間・稼働率に劣化がない
- `https://carelink.kaipoke-api.net` で dev/prod 両環境にアクセス可能
- PR ごとに自動 lint/test が走り、main マージで本番へ手動反映できる
- DB 日次バックアップ・障害時の復旧手順が文書化されている

## 2. 全体インフラ図

```
[Internet]
    │
    ▼
[Cloudflare Edge] ── HTTPS終端・WAF・レートリミット
    │
    ▼ (Cloudflare Tunnel: 既存トンネルを再利用)
[VPS: 72.60.211.213 / Hostinger Malaysia]
    │
    ├─ cloudflared (既存コンテナ)
    │     ├─ kaipoke-api.net          → kaipoke-api:80    (既存・不可侵)
    │     └─ carelink.kaipoke-api.net → carelink-nginx:80 (新規)
    │
    ├─ [Network: kaipoke-net]   既存・触らない
    │     └─ kaipoke-api コンテナ群
    │
    └─ [Network: carelink-net]  新規・隔離
          ├─ carelink-nginx     :80     (リバースプロキシ)
          │      ├─ /          → frontend:3000
          │      └─ /api/      → backend:8000
          ├─ carelink-frontend  :3000   (Next.js)
          ├─ carelink-backend   :8000   (FastAPI)
          └─ carelink-postgres  :5432   (内部のみ)

[ホストOS cron]
  ├─ healthcheck-kaipoke.sh      (既存)
  ├─ healthcheck-carelink.sh     (新規・5分毎)
  └─ backup-carelink-db.sh       (新規・日次02:30)
```

dev/prod は同一 VPS 上で**別 compose プロジェクト名**で共存：
- prod: `carelink-prod` / コンテナ `carelink-*` / volume `carelink_pgdata_prod`
- dev: `carelink-dev` / コンテナ `carelink-dev-*` / volume `carelink_pgdata_dev` / `dev-carelink.kaipoke-api.net`

## 3. 依存関係

| 依存先 | 種別 | 影響 |
|---|---|---|
| 既存 cloudflared コンテナ | 必須・不可侵 | tunnel 設定追加のみ |
| 既存 kaipoke-net | 隔離対象 | CareLink からは到達不可 |
| Cloudflare DNS | 必須 | CNAME 追加権限が必要 |
| Backend D1 | 入力 | Dockerfile + requirements.txt を要求 |
| Frontend D2 | 入力 | next.config.js の `output: 'standalone'` 化 |
| GitHub リポジトリ | 必須 | Actions secrets 設定権限 |
| VPS リソース | 制約 | 既存使用量を測定後、上限決定 |

## 4. タスク分解

### Phase 0: 事前調査（0.5日）

1. **VPS リソース棚卸し** — `docker stats`/`free -h`/`df -h`、CareLink への割当上限決定（残メモリ60%以下目安）
2. **既存 cloudflared 設定確認** — `config.yml` バックアップ、追加方式確認

### Phase 1: ローカル Docker 化（2日）

3. **backend/Dockerfile** — multi-stage、`python:3.12-slim`、non-root、uvicorn
4. **frontend/Dockerfile** — multi-stage、`node:20-alpine`、`next build` standalone
5. **nginx/Dockerfile + nginx.conf** — / → frontend、/api/ → backend、gzip、client_max_body_size
6. **docker-compose.base.yml** — 4サービス + healthcheck + リソース制限 + networks 分離
7. **docker-compose.dev.yml / .prod.yml** — 環境差分（ポート/ボリューム/ログレベル/hot reload）
8. **ローカル検証** — `docker compose up` でヘルスチェック、`/api/health` 200、フロント描画

### Phase 2: VPS 初期セットアップ（1日）

9. **デプロイユーザ作成** — `carelink` ユーザ、docker グループ、`/opt/carelink/`
10. **git clone + 初回 build** — prod ブランチを `/opt/carelink/prod`、dev ブランチを `/opt/carelink/dev`
11. **.env ファイル配備** — 1Password/手動転送、600権限、carelink 所有

### Phase 3: Cloudflare Tunnel 統合（0.5日）

12. **DNS CNAME 追加** — `carelink.kaipoke-api.net` と `dev-carelink.kaipoke-api.net` を既存トンネルへ
13. **cloudflared config 追記** — ingress 配列に2エントリ
14. **疎通確認** — `curl -I https://carelink.kaipoke-api.net/api/health` 200、既存 kaipoke-api 影響なし

### Phase 4: CI（GitHub Actions）（1日）

15. **backend-ci.yml** — push/PR で ruff、mypy、black --check、pytest（PostgreSQL service container）
16. **frontend-ci.yml** — push/PR で ESLint、`tsc --noEmit`、Vitest、`next build`
17. **e2e.yml** — main PR時のみ Playwright 主要シナリオ
18. **branch protection** — main は CI green 必須、レビュー1件必須

### Phase 5: 監視・バックアップ（1日）

19. **healthcheck-carelink.sh** — kaipoke 雛形複製、`/api/health` 5分毎、失敗時 Discord Webhook
20. **backup-carelink-db.sh** — `pg_dump` で `/backups/carelink/YYYY-MM-DD.sql.gz`、14日ローテ、別ディスク rsync
21. **構造化ログ整備** — backend python-json-logger、nginx JSON フォーマット
22. **cron 登録** — healthcheck（5分）、backup（02:30）、ログローテ（週次）

### Phase 6: セキュリティ・運用ドキュメント（0.5日）

23. **CORS / レートリミット** — backend は carelink.kaipoke-api.net のみ allow、nginx で /api/auth/login limit_req
24. **Secrets ローテ手順書** — `docs/runbook/rotate-secrets.md`
25. **デプロイ手順書** — `docs/runbook/deploy.md`
26. **障害復旧手順書** — `docs/runbook/disaster-recovery.md`

### Phase 7: 自動デプロイ移行（後期 0.5日）

27. **Actions による prod デプロイ** — main push 時 SSH で `git pull && docker compose up -d --build`、失敗時 rollback

合計 **約8人日**

## 5. docker-compose.yml の構造

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ${DB_NAME}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${DB_USER}"]
      interval: 10s
    networks: [carelink-net]
    deploy:
      resources:
        limits: { memory: 512M, cpus: '0.5' }

  backend:
    build: ./backend
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
    networks: [carelink-net]
    deploy:
      resources:
        limits: { memory: 768M, cpus: '1.0' }

  frontend:
    build: ./frontend
    restart: unless-stopped
    env_file: .env
    networks: [carelink-net]
    deploy:
      resources:
        limits: { memory: 512M, cpus: '0.5' }

  nginx:
    build: ./nginx
    restart: unless-stopped
    ports: ["127.0.0.1:8080:80"]   # cloudflared からのみ到達
    depends_on: [frontend, backend]
    networks: [carelink-net]
    deploy:
      resources:
        limits: { memory: 128M, cpus: '0.2' }

volumes:
  pgdata:

networks:
  carelink-net:
    driver: bridge
    name: carelink-net   # kaipoke-net と完全分離
```

リソース上限合計：メモリ約 1.9GB、CPU 約 2.2 コア。Phase 0 の棚卸しで要調整。

## 6. 環境変数一覧

| キー | 用途 | dev | prod | 機密 |
|---|---|---|---|---|
| `NODE_ENV` | Next.js | development | production | - |
| `NEXT_PUBLIC_API_BASE` | フロント→API URL | http://localhost/api | https://carelink.kaipoke-api.net/api | - |
| `NEXTAUTH_URL` | NextAuth | dev URL | prod URL | - |
| `NEXTAUTH_SECRET` | セッション暗号化 | dev用乱数 | 強乱数 | **機密** |
| `DB_HOST/USER/NAME` | DB | postgres / carelink / carelink | 同 | - |
| `DB_PASSWORD` | DB | dev用 | 強乱数 | **機密** |
| `DATABASE_URL` | SQLAlchemy 接続 | 合成 | 合成 | **機密** |
| `KAIPOKE_API_TOKEN` | カイポケ連携 | dev tenant | prod tenant | **機密** |
| `GEMINI_API_KEY` | 音声 AI | 開発キー | 本番キー | **機密** |
| `MAPS_API_KEY` | Google Maps | 制限付き dev | 制限付き prod | **機密** |
| `LOG_LEVEL` | ログ | DEBUG | INFO | - |
| `SENTRY_DSN` | エラー追跡 | - | 設定推奨 | **機密** |
| `DISCORD_WEBHOOK_URL` | 障害通知 | - | 設定 | **機密** |

**運用ルール**：
- `.env.example` のみ git 管理（値ダミー）
- `.env` 系は VPS 上のみ、chmod 600、carelink 所有
- GitHub Actions secrets は別管理
- ローテ周期：DB 90日、外部 API は事業者ポリシー準拠

## 7. CI/CD パイプライン仕様

### PR時（必須通過）
```yaml
on: [pull_request]
jobs:
  backend:
    services: { postgres: { image: postgres:16-alpine, env:... } }
    steps: [ruff check, black --check, mypy, pytest --cov]
  frontend:
    steps: [npm ci, lint, typecheck, vitest, next build]
```

### main マージ後（Phase 7）
```yaml
jobs:
  e2e: Playwright (login → weekly → AI input)
  deploy-prod:
    needs: [backend, frontend, e2e]
    steps:
      - SSH → /opt/carelink/prod
      - git pull && docker compose -p carelink-prod up -d --build
      - healthcheck 60秒待機
      - 失敗時 git revert + 再デプロイ
```

### develop ブランチ → dev 環境
- dev push で `dev-carelink.kaipoke-api.net` に反映（最初は手動、Phase 7 で自動化）

## 8. テスト戦略詳細

### Backend (pytest)
- unit: ドメインロジック（割当・スケジューリング）／純粋関数
- integration: SQLAlchemy + FastAPI、`pytest-postgresql` または compose の test DB
- fixtures: `db_session` `client` `auth_headers`、トランザクションロールバック
- カバレッジ: クリティカル箇所80%、全体60%
- lint/format: ruff + black + mypy strict

### Frontend
- Vitest（コンポーネント単体）: shadcn 拡張、フォーム検証、状態管理フック
- Playwright（E2E）: ログイン／週ビュー／患者作成／AI入力（モックGemini）
- ESLint: next/core-web-vitals + import order
- TypeScript strict: `strict: true`, `noUncheckedIndexedAccess: true`

### テスト用 DB
- CI: GitHub Actions の postgres service container（揮発）
- ローカル: `docker compose -f docker-compose.test.yml up postgres-test -d`、別ポート 5433
- 各テストは独立トランザクション + ロールバックで分離

## 9. 監視・ログ・バックアップ計画

### 監視
- **healthcheck-carelink.sh**（5分毎 cron）— `curl -f /api/health` + `docker ps`、失敗時 Discord
- **メトリクス（最小）** — `docker stats` を1分毎追記、週次サマリ。後期に Prometheus/Grafana 検討

### ログ
- 構造化 JSON: backend python-json-logger、フィールド `ts/level/request_id/user_id/path`
- ローテ: docker `--log-opt max-size=50m max-file=5`
- 検索: docker logs + grep。後期に Loki 検討

### バックアップ
- **対象**: PostgreSQL `carelink-prod` のみ
- **方式**: `pg_dump -Fc` → gzip → `/mnt/backup/carelink/YYYY-MM-DD.dump.gz`
- **頻度**: 日次 02:30
- **保持**: 14日、月初分は3か月
- **オフサイト**: 週次で外部 S3 互換（Cloudflare R2、暗号化）
- **検証**: 月次で `pg_restore` を別 DB に流す自動テスト
- **RPO 24時間 / RTO 4時間** が目標

## 10. セキュリティ対策

1. **HTTPS 必須** — Cloudflare Tunnel 経由のみ、VPS の 80/443 は外部閉鎖
2. **CORS** — backend は carelink.kaipoke-api.net のみ allow
3. **レートリミット** — nginx で `/api/auth/login` 5req/min/IP、`/api/*` 60req/min/IP、Cloudflare WAF
4. **認証** — NextAuth.js セッション、Cookie Secure+HttpOnly+SameSite=Lax、NEXTAUTH_SECRET 32B
5. **認可** — backend で全 endpoint 認証必須（health のみ例外）、admin/staff/viewer
6. **個人情報（医療情報）**:
   - 最小化（不要な氏名・住所は別テーブル、論理削除）
   - 監査ログ（誰がいつどの患者を閲覧したか）
   - ログに PII 不出力（request_id のみ）
   - バックアップは暗号化（age or gpg）してからオフサイト
7. **コンテナ** — non-root、read-only fs、`--cap-drop=ALL`
8. **依存性監査** — Dependabot、`npm audit` / `pip-audit` 週次 CI

## 11. 受入基準

- [ ] `https://carelink.kaipoke-api.net/api/health` 200
- [ ] `https://carelink.kaipoke-api.net` でログイン画面描画
- [ ] dev / prod が独立 DB で動作
- [ ] 既存 kaipoke-api.net の応答時間が変動 ±10% 以内
- [ ] PR で backend/frontend CI 自動実行、green
- [ ] main マージ後、手順書通り手動デプロイで prod 反映
- [ ] 日次バックアップ取得、月次復元テスト成功
- [ ] healthcheck 失敗時に Discord 通知
- [ ] secrets が git に含まれない（gitleaks pass）
- [ ] runbook 4種（deploy / rollback / rotate-secrets / disaster-recovery）完成

## 12. リスク + 対策

| リスク | 確度 | 影響 | 対策 |
|---|---|---|---|
| **VPS リソース不足**（メモリ/CPU 競合） | 中 | 既存 kaipoke-api 劣化 | Phase 0 で実測、compose limits 厳守、超過時はプラン昇格 or DB を Supabase 等外部化 |
| **Cloudflare Tunnel 障害** | 低 | 全サービス停止 | tunnel config 変更前にバックアップ、構文チェック、メンテ枠で適用 |
| **DB データ消失** | 低 | 致命的 | 日次バックアップ + 別ディスク + 週次オフサイト + 月次復元テスト |
| **既存 kaipoke-net への意図せぬ到達** | 低 | セキュリティ | carelink-net を独立 bridge、firewall 確認、テストで分離検証 |
| **secrets 流出** | 中 | 致命的 | gitleaks pre-commit + CI、ローテ手順、漏洩時即時失効 |
| **個人情報漏洩** | 低 | 致命的・法的 | 暗号化バックアップ、監査ログ、最小権限、医療情報取扱規程 |
| **手動デプロイのオペミス** | 中 | dev/prod 混同 | compose プロジェクト名 `-p carelink-prod` 明示、deploy.sh ラッパ |
| **Cloudflare DNS 設定ミス** | 中 | アクセス不可 | 変更前に `dig` で旧値記録、CNAME 形式運用 |
| **CI 不安定（flaky test）** | 中 | デプロイ遅延 | リトライ1回まで、flaky 検知タグ、Playwright trace 保存 |

## 13. 想定工数

| Phase | タスク数 | 工数 |
|---|---|---|
| 0 事前調査 | 2 | 0.5d |
| 1 ローカル Docker 化 | 6 | 2d |
| 2 VPS 初期セットアップ | 3 | 1d |
| 3 Cloudflare Tunnel 統合 | 3 | 0.5d |
| 4 CI（GitHub Actions） | 4 | 1d |
| 5 監視・バックアップ | 4 | 1d |
| 6 セキュリティ・運用ドキュメント | 4 | 0.5d |
| 7 自動デプロイ移行（後期） | 1 | 0.5d |
| バッファ（手戻り・調査） | - | 1d |
| **合計** | **27** | **約 8 人日（1.5〜2週間）** |
