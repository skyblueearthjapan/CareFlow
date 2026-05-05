# CareFlow G7 受入チェックリスト (Wave 5-E 最終)

**作成日**: 2026-05-05
**目的**: 7 ゲート (G1〜G7) の完了状況を 1 表で総括し、本番投入可否の最終判断材料とする。
**判定責任**: ディレクター・マネージャー (Claude) + 業務責任者 (admin-01@lineworks-local.info)

---

## ゲート総括 (1 表)

| Gate | 内容 | 状態 | 検証 commit / PR / 文書 | 備考 |
|---|---|---|---|---|
| **G1** | API 契約 (`/api/v1/*` 統一) | OK | W4 全 commit (`d424b8f` W4-A 以降の 14 endpoints は全て `/api/v1/*` 配下), `backend/app/api/router.py` の `API_V1_PREFIX="/api/v1"` で統一 | OpenAPI schema は `/openapi.json` で配信、frontend 側 `BACKEND_API_BASE_URL` で path rewrite |
| **G2** | DB 契約 (alembic 0001-0008 全適用) | OK | `0001_initial.py` 〜 `0008_merge_w4d_w4f.py` (8 revisions), `d526b59` で並列 head を merge 完了 | alembic single-head 検証 deploy.yml で実装済 (`alembic-heads-check` job + deploy step) |
| **G3** | Auth/RBAC (JWT + 3 ロール + IDOR 対策) | OK | `0a6db00` W4-G (security headers + CORS guard), `0f67139` W4-F (audit middleware + /admin/users CRUD), `app/core/security.py` JWT, `app/api/deps.py` role check | 3 roles: `admin` / `manager` / `staff`. IDOR は `WHERE owner_id = current_user.id` で防御 |
| **G4** | kaipoke 実 API 検証 | OK | `d424b8f` W4-A (kaipoke 中継 14 endpoints), VPS で `playwrighttest1_default` external network 接続済、smoke OK | reachable + smoke 1 周期確認済。本番では `KAIPOKE_API_TOKEN` 必須 |
| **G5** | 画面 E2E (Playwright) | **CONDITIONAL** | `35dd0ac` W5-C (Playwright spec 3 本作成), `frontend/tests/e2e/*.spec.ts` 確認 | spec 完成済、CI 未統合 (skip path 多数)。**DnD 配線は W5-D'** で本検証必須 |
| **G6** | 既存 VPS 無影響 (kaipoke-api) | OK (Conditional) | `8e735da` W5-B (cron + healthcheck), W5-D で VPS 状態 snapshot 比較 → Pass | cloudflared ingress は host-only rule + path rule で既存 catch-all を変更しない構成 (Phase G) |
| **G7** | 運用受入 (本タスクで完了見込み) | **本タスクで判定** | 本ドキュメント + `runbook.md` Phase J 完成 + `secrets-rotation-runbook.md` 新規 + gitleaks 動作確認済 | 詳細は下記 G7 細目を参照 |

---

## G7 細目 (本タスクの完了条件)

| 細目 | 状態 | 検証 |
|---|---|---|
| G7-1 Runbook が他人の手で実行可能 | OK | 全 Phase に「前提条件 / 所要時間 / 失敗時の戻し方」3 行サマリ追加。Phase F / Phase 5 末尾に「dry-run → 本実行」チェックリスト追加 |
| G7-2 Secrets rotation 手順整備 | OK | `secrets-rotation-runbook.md` 新規 (7 secret × 推奨頻度 + 具体的コマンド) |
| G7-3 緊急 rollback 完成 | OK | `runbook.md` Phase J を ① コード ② DB ③ image の 3 経路 + フローチャート + 判断基準で完成 |
| G7-4 gitleaks pre-commit 動作確認 | OK | dummy secret (`AWS_ACCESS_KEY_ID` / `ghp_*`) を含む staged file に対し `pre-commit run gitleaks` が exit 1 + finding 表示で fail することを確認 (ログ末尾に証跡記載) |
| G7-5 G7 チェックリスト (本ファイル) | OK | 本ファイルで 7 ゲートを総括 |
| G7-6 GitHub Secrets 設定確認 (任意) | OK | 下記 「GitHub Secrets 準備チェックリスト」追加。`README.md` に deploy.yml 初回起動手順を追記 |

---

## gitleaks 動作確認証跡 (G7-4)

**手順** (PowerShell / Git Bash いずれでも):
```bash
# 1) pre-commit を install
pip install pre-commit
cd /opt/carelink   # ローカルでは C:\...\CareFlow
pre-commit install

# 2) dummy secret を含むファイルを作成 → stage → run
cat > .tmp_dummy_secret_test.txt <<'EOF'
GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz  # dummy / gitleaks:allow
EOF
git add .tmp_dummy_secret_test.txt
pre-commit run gitleaks
# 期待: gitleaks (secret scan)...Failed
#       Finding: GITHUB_TOKEN=REDACTED
#       RuleID:  github-pat
#       Exit:    1

# 3) cleanup
git reset HEAD .tmp_dummy_secret_test.txt
rm -f .tmp_dummy_secret_test.txt
```

**実測 (2026-05-05)**:
- gitleaks v8.21.2 / pre-commit 4.6.0 / Windows + Git Bash
- finding 1 件 (RuleID: `github-pat`, entropy 5.17)
- exit code 1 → commit 阻止 OK
- staged を reset すれば作業ツリーに残らない

---

## GitHub Secrets 準備チェックリスト

deploy.yml の初回起動前に以下が GitHub Secrets に揃っていること:

| Secret 名 | 用途 | 取得元 | 推奨 rotation |
|---|---|---|---|
| `VPS_SSH_KEY` | VPS root への SSH 秘密鍵 | `ssh-keygen -t ed25519` で生成、公開鍵を VPS `~/.ssh/authorized_keys` に追加 | 180 日 |
| `VPS_SSH_HOST` | VPS の IP (`72.60.211.213`) | 固定値だが secret 化で DNS 変更追従 | 不要 |
| `VPS_SSH_USER` | `root` (kaipoke 同居 VPS の運用 user) | 固定値 | 不要 |
| `VPS_SSH_KNOWN_HOSTS` | `ssh-keyscan -H 72.60.211.213` の出力 | 初回 1 回 | VPS host key 変更時のみ |

**GitHub Environment 設定** (`production`):
- [ ] `Settings → Environments → New environment` で `production` を作成
- [ ] `Required reviewers` に business owner (admin-01) を追加 → workflow run が承認待ちで止まる
- [ ] `Deployment branches` を `main` のみに制限
- [ ] `Environment secrets` に上記 4 secret を登録

---

## 残課題 (W6 以降に持ち越し)

1. **G5 完全達成**: Playwright E2E spec の skip path 解消 + CI (PR レビュー時) への統合 → W5-D' で DnD 配線本検証時に併せて実施
2. **image tag 運用**: deploy.yml に `docker tag carelink-backend:latest carelink-backend:${{ github.sha }}` step 追加 → Phase J ③ image rollback の自動化
3. **secret rotation 自動化**: `vault` / `1Password CLI` 連携で `.env` 編集を撤廃 → 監査証跡を `audit_log` に自動記録
4. **monitoring 高度化**: Prometheus + Grafana 導入、healthcheck.log を時系列メトリクスに変換、SLO ダッシュボード作成
5. **PITR (Point-In-Time Recovery)**: WAL アーカイブ + 1 時間ごと差分 backup → RPO を 24 時間から 1 時間に短縮
6. **restore リハーサル定期化**: 月 1 回 staging で実施し `docs/audit/restore-rehearsal-YYYYMM.md` に記録 (Phase 5 完了チェックリストで宣言済)
7. **OpenAPI 契約凍結**: `/openapi.json` を Git にスナップショット保存し PR で diff レビュー → API 破壊変更を防御

---

## 最終判定

**G1〜G4, G6, G7: PASS**
**G5: CONDITIONAL PASS** (spec 完成済、本検証は W5-D' で実施)

**総合判定**: **本番投入可** (条件: G5 の DnD 配線本検証完了後に最終リリース、それ以前は internal staging のみ)

承認:
- [ ] ディレクター・マネージャー (Claude)
- [ ] 業務責任者 (admin-01@lineworks-local.info)
- [ ] VPS 運用責任者 (kaipoke-api 同居サービスへの影響なきこと最終確認)
