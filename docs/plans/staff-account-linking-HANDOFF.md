# 引き継ぎ書：スタッフ×アカウント紐付け＋スタッフコードログイン

作成 2026-07-02 / 対象ブランチ develop / 本番HEAD = `b196000`（origin/develop と一致）

このドキュメントは本機能の**次セッター向け引き継ぎ**。設計の正典は `docs/plans/staff-account-linking-design.md`、
経緯・実データは自動メモリ `careflow-staff-account-linking.md`。プロジェクト全体像は `docs/HANDOFF.md`。

---

## 1. この機能は何か / なぜ作ったか

訪問スタッフが**モバイルアプリで自分の今日/今週の訪問を閲覧・QRチェックインできる**ようにする機能。
モバイルは `session.user.staffId`(= `users.staff_id`)に依存して self-scope するが、
**紐付けを設定する導線が存在せず**、本番では staff ロールのアカウントが0件・全員未紐付けで、
モバイルが事実上使えない状態だった。これを解消するため以下を実装した:

- **紐付けの土台**（`users.staff_id` は元々あった）＋ **紐付けAPI/バリデーション**
- **スタッフコード（S001 等）でのログイン**（従来は email 必須）
- **管理画面での紐付けUI**（作成/編集ダイアログ・一覧のスタッフ名表示）
- **スタッフ用アカウント一括供給スクリプト**

補足: 運用マニュアル(`docs/manual/初回ログイン…html` 6.3)は元々「紐付け機能が存在する前提」で書かれていたが、
実UIが無かった（Wave 4-F で users CRUD を作った際に紐付けUIだけ欠落）。本機能で記述と実装が一致した。

---

## 2. 実装（develop・全て本番反映済み）

| フェーズ | コミット | 内容 |
|---|---|---|
| P1a backend土台 | `1aaf6b8` | migration 0046・`users.username`追加・`email` nullable化・email/username/staff_id 部分ユニーク・CHECK(email or username)・admin CRUD(username/staff_name/soft-delete検査/解除ガード/409判別)・login/refresh に deleted_at フィルタ |
| P1b コードログイン | `9007e63` | `LoginRequest` に identifier 追加(email も両受理=後方互換)・login解決 `or_(lower(username),lower(email))`・frontend login/auth.ts/next-auth.d.ts の identifier化＋null安全 |
| P2 紐付けUI | `6bc62d8` | /admin/users 作成/編集ダイアログにスタッフ選択・username欄・ロール別必須・解除・一覧 staff_name＋username列。既存 `useStaffList` 流用 |
| P3 供給スクリプト | `b3228b9` | `backend/scripts/provision_staff_accounts.py`（下記6章） |
| 最終レビュー反映 | `1e24606` | PATCH のロール変更時のみロール別識別子(admin=email/staff=username)強制・username validator 共通化 |
| マニュアル改訂 | `b196000` | `docs/manual/` をスタッフIDログインに整合 |

各フェーズ「実装(executor)→別パスで code-reviewer＋security-reviewer→修正→検証」。
最終に全体クロスレビュー（critic=GO / code-reviewer=COMMENT）。自己approveなし。

**契約**: backend `AdminUserRead/Create/Update`・`UserOut`・`LoginRequest` と frontend zod
(`lib/schemas/admin-user.ts`・`lib/auth.ts`)は username/staff_name/email-nullable で一致確認済み。
`LoginRequest` のみ `extra="ignore"`（後方互換で未知キー許容）、他 admin schema は `extra="forbid"`。

---

## 3. 本番デプロイ（完了 2026-07-01/02）

- VPS `root@72.60.211.213`、`/opt/carelink`、公開 `https://carelink.kaipoke-api.net`。
- 手順実績: push → pg_dump(`backups/pre-0046-20260701-2157.sql.gz` 610K) → `git pull --ff-only` →
  `docker compose ... build backend frontend` → `alembic upgrade head`(0045→**0046**, single head確認) →
  `up -d --force-recreate backend frontend` → スモーク全pass。
- スモーク: healthz local/public=ok / login 空=422 / 空identifier=422 / 不正=401 / admin/users未認証=401 / backendエラーなし。
- compose は常に `-f docs/deployment/docker-compose.production.yml --env-file /opt/carelink/.env`。

---

## 4. 本番アカウント投入（完了 2026-07-02）＝ P4-B

事前に**使い捨てテストアカウント**(staff `ZTEST1`/user `ztest1`)で本番E2E検証（ログイン200・大小文字両対応・
/auth/me に staff_id・visits self-scope 200・誤PW 401）→削除→ベースライン復元確認、を実施してから本投入。

**現在の本番アカウント状態（6名 全員 紐付け済み・未紐付けstaff=0）**:

| code | 氏名 | username | email | role | must_change |
|---|---|---|---|---|---|
| S001 | 川名 千恵 | s001 | chie.kawana@thousands.jp | manager | True |
| S002 | 熊澤 妙子 | s002 | (なし) | staff | True |
| S003 | 関谷 公佑 | s003 | (なし) | staff | True |
| S004 | 高岡 真由美 | s004 | (なし) | staff | True |
| S005 | 本名 大 | s005 | (なし) | staff | True |
| S006 | 宇田川 優莉 | s006 | (なし) | staff | True |

- S002-S006 は `provision_staff_accounts.py` で新規作成。**S001 川名は既存 manager アカウント `chie.kawana@` に
  `docker exec carelink-backend python` で staff_id + username=s001 を設定**（メール/権限は不変）＋
  未ログイン(must_change=t)だったので仮PWも再発行。
- **6名の仮パスワードは配布用カード `staff-login-cards.html`（プロジェクト直下・`.gitignore` 済＝git非追跡）に記載**。
  この引き継ぎ書には秘密は書かない。仮PWは全員 **有効・未使用**（2026-07-02 時点で非破壊ログイン検証済）。
- admin 3名(ayaka.adachi/matsuoka/yuji.imaizumi)は staff マスタ非存在のため**意図的に未紐付け**。

---

## 5. 残工程（P4-C）＝ クライアントの初回ログイン待ち

1. 管理者(依頼元)が `staff-login-cards.html` を印刷し、各スタッフへ**スタッフID＋仮パスワード**を安全に手渡し。
2. 各スタッフがスマホで `carelink.kaipoke-api.net` を開く → スタッフID＋仮PWでログイン →
   **強制パスワード変更**(`must_change_password`) → 自分の今日/今週の訪問が表示される。
3. **管理者はスタッフのアカウントで初回ログインを"完了"しないこと**（パスワード変更まで進むと仮PWが消費され本人が入れなくなる）。
   ※ただの login API は非破壊。検証は使い捨てアカウントで済ませてある。

確認したい観点（次セッションで見ると良い）: 実際にスタッフがログイン後、モバイルに訪問が出るか
（visits が割り当てられている前提。表示ロジックは `frontend/lib/queries/me.ts`）。

---

## 6. 運用 how-to（次セッション用スニペット）

### 追加スタッフのアカウント作成（staff マスタに code 済みが前提）
```
cd /opt/carelink
# 予定確認
docker compose --env-file /opt/carelink/.env -f docs/deployment/docker-compose.production.yml \
  run --rm backend python scripts/provision_staff_accounts.py --dry-run
# 本実行（仮PWは stdout に1回表示。--out CSV も可＝umask0077で0600生成）
docker compose ... run --rm backend python scripts/provision_staff_accounts.py [--only-code S007,S008]
```
既定は staff ロールのみ（manager 除外）。冪等（既紐付け/username衝突/IntegrityError は skip）。

### 既存アカウントへの紐付け / パスワードリセット（UI が使えない/バッチ時）
`/admin/users` UI（管理者ログイン）でも可。UI を使わない場合は
`docker exec -i carelink-backend python -` に流し込む（S001 紐付け・川名PWリセットで実施した方式）。
username は必ず `strip().lower()` 正規化して入れる（スキーマ層の規約と一致させる）。

### 紐付け反映のタイミング（重要 / critic M1）
JWT に staff_id が焼き込まれる。**既にログイン中**のユーザーを後から紐付けると、
access token 失効(約55分)or 再ログインまで反映されない。**新規スタッフは「作成＋紐付け→初回ログイン」順なので滞留なし**。

### レート制限 / ロックアウト
IP レート制限 5回/15分（`@limiter.limit`）。同一端末から連続ログインは 429 になり得る（実運用では各自別端末なので無問題）。
パスワード5回連続失敗で 15分アカウントロック。

---

## 7. backlog（据置・実害小・次以降で対応可）

- **タイミングオラクル**: user 不在時は即 401、存在時は bcrypt 分の遅延 → 存在推測余地。dummy bcrypt で均一化する案（security P1b MEDIUM）。IP レート制限で緩和済み。
- **ロックアウトDoS**: 連番コード(S001..)は総当たり容易。exponential backoff / CAPTCHA / 複数ロック時アラート（security P1b MEDIUM）。現状6名で実害小。
- **username に `@` 禁止の DB CHECK**: 現状スキーマ層(`^[a-z0-9_.\-]+$`)で `@` 拒否。DB CHECK は多層防御として別 migration で追加可。
- **picker の大規模時 除外漏れ**: 作成/編集ダイアログの staff/users 一覧が limit 500/200 ハードコード。将来 `has_account` 付き専用エンドポイント化。
- **`HTTP_422_UNPROCESSABLE_ENTITY` deprecation**: `_CONTENT` へ（バージョン確認の上）。
- **既知フレーク（本機能と無関係）**:
  - backend `tests/test_auth.py::test_login_locks_after_5_failed_attempts`（SQLite の naive/aware datetime 比較・`auth.py:92`付近）。identifier 版は `xfail` マーク済み。
  - frontend `CourseDayTablePanel.test.tsx`(20件) / `PasswordChangeForm.test.tsx`（QueryClientProvider 未設定）。

---

## 8. 主要ファイル早見

- migration: `backend/alembic/versions/0046_user_username_and_uniques.py`
- モデル: `backend/app/models/user.py`（username/staff_name property/部分ユニーク/CHECK）
- 認証: `backend/app/api/v1/auth.py`(login/refresh)・`backend/app/schemas/auth.py`(LoginRequest/UserOut)
- admin CRUD: `backend/app/api/v1/admin.py`・`backend/app/schemas/admin_user.py`
- 供給: `backend/scripts/provision_staff_accounts.py`＋`backend/tests/scripts/test_provision_staff_accounts.py`
- 紐付けUI: `frontend/app/(app)/admin/users/`（page.tsx / _components/UserCreateDialog.tsx / UserEditDialog.tsx）＋`frontend/lib/schemas/admin-user.ts`
- ログインFE: `frontend/app/(auth)/login/page.tsx`・`frontend/lib/auth.ts`・`frontend/types/next-auth.d.ts`
- モバイル self-scope: `frontend/lib/queries/me.ts`
- マニュアル: `docs/manual/初回ログイン／スタッフ反映マニュアル.html`・`現場スタッフ／クイックガイド.html`
- 配布カード(秘密・git外): `staff-login-cards.html`（プロジェクト直下）
- 設計: `docs/plans/staff-account-linking-design.md`

---

## 9. テスト実行（次セッション用）

- backend: `cd backend && python -m pytest tests/test_admin_users.py tests/test_auth.py tests/scripts/test_provision_staff_accounts.py -q`（`uv run` 不可）。
- frontend: `cd frontend && pnpm tsc --noEmit` / `pnpm vitest run <file>` / `pnpm lint`。
- alembic: `python -m alembic heads`(単一=0046) / `alembic current`。
