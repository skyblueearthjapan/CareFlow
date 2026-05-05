# CareFlow

訪問看護スケジューリングアプリ。既存 GAS UI（careflow-scheduler）+ VPS Python/Playwright（PlaywrightTest1）の後継として新規構築。

## アーキテクチャ

- **Frontend**: Next.js 15 (App Router) + TypeScript + Tailwind CSS + shadcn/ui
- **Backend**: FastAPI + SQLAlchemy 2.0 + asyncpg
- **DB**: PostgreSQL 16
- **デプロイ**: Hostinger Malaysia VPS（既存 kaipoke-api と同居）
- **公開URL**: `https://carelink.kaipoke-api.net`（Cloudflare Tunnel）
- **認証**: NextAuth.js + メール/パスワード
- **AI**: Gemini API（自然言語入力 → 構造化）
- **デザイン**: Warm & Human（Teal #0D9488 + Terracotta #D97706 + Cream #FAF7F2）

## ディレクトリ構造

```
CareFlow/
├── backend/           # FastAPI バックエンド
├── frontend/          # Next.js フロントエンド
├── docs/              # 設計仕様書・実装計画・監査レポート
│   ├── design/        # UI 設計仕様（11ファイル）
│   ├── plans/         # 実装計画（D1〜D5 + クロスレビュー + Codex）
│   │   └── wave/      # Wave 単位の実装計画書 (W1〜W5)
│   └── audit/         # 既存システム再調査（INV-1〜5 + レビュー）
├── Sampledata/        # 参考用サンプルデータ（.gitignore で除外）
└── README.md
```

> **プロジェクト名**: 当初は「CareLink」名で計画書を着手したが、GitHub
> リポジトリおよび本番ドメイン (`carelink.kaipoke-api.net`) と並行して
> プロダクト表記は **「CareFlow」** に確定済み。docs/ 内の旧計画書では
> 「CareLink」表記が一部残っているが、製品名としては CareFlow で統一する。

## 開発ステータス

**Wave 4 完了 / Wave 5 進行中** (2026-05-05 現在):

- **Wave 1**: backend skeleton (FastAPI + Alembic + 10 テーブル + Auth + RBAC)、
  frontend foundation (Next.js + tokens + Layout)、import スクリプト基礎
- **Wave 2**: 週ビュー / dashboard / mobile 4 画面 / PWA / 統合管理画面
- **Wave 3**: 患者・スタッフマスタ拡張 (area / NG staff / specified_type /
  home address / skill_level など) + special_weeks + 構造化 WeeklyPattern
  エディタ + 初期データ import スクリプト 8 本
- **Wave 4**: kaipoke-api 中継 14 endpoints + 差分プレビュー / Gemini 自然
  言語入力 / Google Maps Geocoding / モバイル 3 機能補完 / Combobox 化 +
  zodResolver / AuditLog middleware + /admin/users CRUD / Security headers
  + CORS guard
- **Wave 5 進行中**: GitHub Actions CI/CD (W5-A 完了) + 監視・バックアップ
  (W5-B 完了) + E2E Playwright (W5-C 完了) + 残: ドキュメント整備 (W5-F)

詳細: `docs/plans/MASTER-PLAN.md` および `docs/plans/wave/` 参照。

## 開発者セットアップ (pre-commit)

ローカル環境で commit 直前に CI と同じ guard (gitleaks / ruff / prettier) を回すため、
`pre-commit` を有効化する。

```bash
# 1) pre-commit を pip でインストール (一度だけ)
pip install pre-commit

# 2) このリポジトリの hook を有効化 (.git/hooks/pre-commit を生成)
pre-commit install

# 3) 初回フル走査 (任意、既存ファイルもチェック)
pre-commit run --all-files
```

設定ファイル: `.pre-commit-config.yaml`。CI 側 (`.github/workflows/ci.yml`) と
gitleaks / ruff / bearer-token 検査が二重化されているので、commit 段階で
落とせば PR 上で fail せずに済む。

### gitleaks 動作確認 (初回 setup 直後に推奨)

dummy secret を含む staged file が **commit を阻止される** ことを確認する。

```bash
# 1) dummy secret ファイルを作成 (絶対 commit しないこと)
#    例示用 fake token (`ghp_…wxyz`) は gitleaks に detect させるための
#    明示的な dummy。下の手順で stage → run → reset まで通すこと。
cat > .tmp_dummy_secret_test.txt <<'EOF'
GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz  # dummy / gitleaks:allow
EOF

# 2) stage した状態で gitleaks のみ実行
git add .tmp_dummy_secret_test.txt
pre-commit run gitleaks
# 期待出力:
#   gitleaks (secret scan)...Failed
#   Finding: GITHUB_TOKEN=REDACTED   RuleID: github-pat
#   exit code: 1

# 3) cleanup (絶対 commit しない)
git reset HEAD .tmp_dummy_secret_test.txt
rm -f .tmp_dummy_secret_test.txt
```

実 secret では絶対に試さないこと (履歴に残るリスク)。fake な GitHub PAT (`ghp_*`) や AWS access key (`AKIA*EXAMPLE`) を使う。

## VPS デプロイ

本番環境は Hostinger Malaysia VPS (`carelink.kaipoke-api.net`)。Cloudflare Tunnel 経由で公開し、既存 `kaipoke-api` と同居する。

詳細手順: `docs/deployment/runbook.md`

関連:
- `docs/deployment/preflight-check.sh` — 事前チェックスクリプト
- `docs/deployment/docker-compose.production.yml` — 本番 compose
- `docs/deployment/env-template.md` — `.env` テンプレート
- `docs/deployment/cloudflared-config-fragment.yml` — ingress 追加断片
- `docs/deployment/initial-admin-seed.md` — 初期管理者作成
- `docs/deployment/secrets-rotation-runbook.md` — 7 secret の rotation 手順
- `docs/deployment/backup-restore-runbook.md` — DB restore (RTO 15 分 / RPO 24 時間)
- `docs/deployment/g7-acceptance-checklist.md` — G1〜G7 受入チェック総括

### deploy.yml 初回起動手順 (GitHub Actions 経由の本番反映)

`.github/workflows/deploy.yml` は **手動 dispatch のみ** で起動。初回前に下記を準備:

1. **GitHub Environment `production`** を作成 (`Settings → Environments → New`)
   - **Required reviewers**: 業務責任者 (admin-01@lineworks-local.info)
   - **Deployment branches**: `main` のみ

2. **Environment Secrets を 4 件登録**:

   | Secret 名 | 値の取得方法 |
   |---|---|
   | `VPS_SSH_KEY` | `ssh-keygen -t ed25519 -f ~/.ssh/carelink_deploy -N ""` で生成 → 秘密鍵全文 (BEGIN/END 含む) を貼付 |
   | `VPS_SSH_HOST` | `72.60.211.213` |
   | `VPS_SSH_USER` | `root` |
   | `VPS_SSH_KNOWN_HOSTS` | `ssh-keyscan -H 72.60.211.213` の出力をそのまま貼付 |

   公開鍵 (`~/.ssh/carelink_deploy.pub`) は VPS の `~/.ssh/authorized_keys` に追加。

3. **VPS 側の事前準備**: `docs/deployment/runbook.md` Phase A〜I を **手動で 1 度実行** し `/opt/carelink/.env` を含む全初期セットアップを完了。deploy.yml は **既に Phase A〜I 完了済の VPS** に対して `git pull → build → recreate → smoke` を実行する設計。

4. **初回 deploy 実行**: GitHub Actions タブ → `deploy` → `Run workflow` → Branch: `main`。
   `production` environment の承認待ちで停止 → 業務責任者が `Approve and deploy` で再開。

5. **失敗時**: `docs/deployment/runbook.md` Phase J (① コード ② DB ③ image rollback) を参照。

## 既知の TODO / 移行課題

- **Gemini SDK 移行**: `backend/app/services/gemini_client.py` は現在
  `google-generativeai` (deprecated) を使用。新 SDK `google-genai` への
  移行は別 sprint で検討予定。詳細は同ファイル冒頭 docstring を参照。

## ライセンス

Private（社内利用）
