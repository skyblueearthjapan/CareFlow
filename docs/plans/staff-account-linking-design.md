# スタッフ×ログインアカウント紐付け & スタッフID方式ログイン 設計書

作成: 2026-07-01 / 対象ブランチ: develop / ステータス: レビュー前ドラフト

## 0. 要約

訪問スタッフがモバイルアプリで「自分の今日/今週の訪問」を閲覧・QRチェックインできるようにする。
そのために欠けている **(A) アカウントとスタッフの紐付けUI** と **(B) スタッフ用ログインアカウントの供給**、
および **(C) スタッフコードでのログイン** を実装する。

- 仕組み（DB `users.staff_id` FK / JWT / self-scope / API）は既に実装済み。**紐付けを設定する導線だけが存在しない。**
- 本番実データ: スタッフ6名（S001〜S006・全員コード有・全員active）、`staff` ロールのアカウントは 0 件、
  既存4アカウント（admin3・manager1）は全員 staff 未紐付け。
- 運用マニュアル `docs/manual/初回ログイン／スタッフ反映マニュアル.html` 6.3 は本機能が「存在する」前提で記述済み
  （ドキュメントと実装の乖離）。本設計はマニュアルの記述に実装を合わせる。

## 1. 背景・現状（調査結果）

### 1.1 紐付けの仕組み（実装済み）
- `users.staff_id`（FK → `staff.id`, nullable, `ondelete=SET NULL`）が唯一の紐付けキー。`backend/app/models/user.py:33-37,48`
- ログイン時 JWT に `staff_id` を格納 → セッション `session.user.staffId`。`backend/app/api/v1/auth.py:42-43` / `frontend/lib/auth.ts:67,87,152`
- モバイルは `GET /api/v1/visits?staff_id={staffId}` で self-scope。`staff` ロールは backend が本人の `staff_id` に強制。`frontend/lib/queries/me.ts:167,172` / `backend/app/api/v1/visits.py:227-230`
- チェックインの打刻者も `user.staff_id`。未紐付けは 403。`backend/app/api/v1/visits.py:620-626`

### 1.2 欠落
- **紐付けUIが無い**: `/admin/users` 作成ダイアログは `{email, role}` のみ送信、編集ダイアログは「Staff linkage is deliberately read-only here」。`frontend/app/(app)/admin/users/_components/UserCreateDialog.tsx:62` / `UserEditDialog.tsx:7,57-64`
- **紐付けを設定する取込スクリプトが無い**: `import_users.py` は「管理者」シート専用で `staff_id` を設定しない。他スクリプトは users を触らない。
- **スタッフ用アカウントが無い**: 本番に `staff` ロールのユーザーは 0 件。6名のスタッフはログイン不可。
- **突合キーが `staff.id` のみ**: `staff` に email/連絡先列なし、`users` に code 列なし。email/コード一致での自動突合は不可能。

### 1.3 本番実データ（2026-07-01 読み取り確認）
```
users(role別, 有効):  admin=3(紐付け0),  manager=1(紐付け0)
staff(有効):          6名  S001〜S006  全員コード有・重複なし・全員active
users カラム:         email=NOT NULL,  username 列なし,  staff_id=nullable
既存アカウント:        ayaka.adachi@ / matsuoka@ / yuji.imaizumi@ (admin),
                      chie.kawana@thousands.jp (manager)
```

スタッフマスタの role: S001 川名千恵=manager, S002〜S006=staff。
`chie.kawana@thousands.jp`（既存manager）は S001 川名千恵本人。

## 2. ゴール / 非ゴール

### ゴール
- 訪問スタッフがモバイルで自分の訪問（今日/今週）・QRチェックイン・シフト希望を利用できる。
- 管理者が `/admin/users` からスタッフ紐付けを設定・変更・解除できる（マニュアル6.3準拠）。
- スタッフが **スタッフコード（S001…）+ パスワード** でログインできる。
- 6名のアカウント供給を UI（1人ずつ）と一括スクリプトの両方で行える。

### 非ゴール
- スタッフの自己サインアップ（管理者発行のみ）。
- 外部IdP/SSO 連携。
- スタッフマスタへの email 列追加（コードログインを採るため不要）。
- モバイルからの予定編集（現状通り閲覧＋チェックイン＋シフト希望のみ）。

## 3. 確定した設計判断（ユーザー合意済み）

1. **ログインID = `staff.code`（S001〜S006）を直接採用**。別途の簡易ID体系は設けない。
2. **供給はUI＋一括スクリプトの両輪**。
3. **投入マッピング**:
   - S001 川名千恵 → 既存 `chie.kawana@thousands.jp`（manager）に **staff_id を紐付けるのみ**（新規作成しない）。
   - S002〜S006 → **新規 staff ロールアカウント**を作成（username=コード）＋紐付け。
4. admin 3名はスタッフマスタ非存在のため紐付け対象外。

> **⚠️ レビュー反映（architect=条件付きGO / critic=REVISE, 2026-07-01）**
> `email` nullable 化は **DB行レベルでは非破壊だがスキーマ層では破壊的変更**。`EmailStr` / `z.string().email()` で
> email を厳格型付けしている全箇所を**同一デプロイで一斉に**更新しないと、スタッフログイン・/me・管理ユーザー一覧が
> 実行時エラーになる。§4.5「影響範囲マトリクス」と §5.5「ログイン変更の全7層」を必ず参照。
> また `identifier` ログインは**バックエンドが移行期間中 `email` と `identifier` の両方を受理**して後方互換を保つ
> （P1a/P1b の非原子デプロイでも既存ログインを壊さないため）。

## 4. データモデル変更（migration 0046）

`users` テーブルへの変更（**行レベルは非破壊／スキーマ層は破壊的** — §4.5 参照）:

1. **`username` 列を追加**（`String`, nullable）。
   - 正規化: 小文字・trim して保存（ログイン時も同様に正規化して比較）。
   - **部分ユニークインデックス** `ix_users_username_unique_alive`:
     `WHERE username IS NOT NULL AND deleted_at IS NULL`（PostgreSQL / SQLite 両対応、既存の staff.code パターンに倣う）。
2. **`email` を nullable 化**（`NOT NULL` → `NULL` 許可）。
   - スタッフはメール無し。既存admin/managerは値を保持。
   - 既存のユニーク制約は「値がある場合のみ」を担保するため、必要なら email も部分ユニークへ移行を検討（現状は unique index。email に NULL 複数を許すため部分ユニーク化が安全）。
3. **`users.staff_id` に部分ユニークインデックス** `ix_users_staff_id_unique_alive`:
   `WHERE staff_id IS NOT NULL AND deleted_at IS NULL`。**1スタッフ＝1アカウントを DB で保証**。

制約整合性:
- `email` と `username` は **少なくとも一方が非NULL** であること（アプリ層バリデーション。DB CHECK も可）。
- ロール別要件: `staff` ロールは `username` 必須・`email` 任意、`admin/manager` は従来通り `email` 必須（アプリ層で分岐）。

### 4.5 影響範囲マトリクス（email nullable 化で一斉更新が必要な全箇所）

`email` を nullable にすると、以下すべてを**同一デプロイ**で修正する。1つでも欠けるとスタッフユーザーで実行時エラー。

| # | 層 | file:line | 現状 | 必要な変更 |
|---|---|---|---|---|
| 1 | BE schema | `backend/app/schemas/auth.py:12` | `LoginRequest.email: EmailStr` | `identifier: str`（＋移行期は `email` も任意受理） |
| 2 | BE schema | `backend/app/schemas/auth.py:28` | `UserOut.email: EmailStr` | `email: EmailStr \| None = None`（＋`username` 追加） |
| 3 | BE schema | `backend/app/schemas/admin_user.py:20` | `AdminUserRead.email: EmailStr` | `EmailStr \| None = None`（＋`username`/`staff_name` 追加） |
| 4 | BE schema | `backend/app/schemas/admin_user.py:33-36` | `AdminUserCreate.email: EmailStr`（必須） | `EmailStr \| None = None`（＋`username`） |
| 5 | BE schema | `backend/app/schemas/admin_user.py:43-49` | `AdminUserUpdate` に username 無し | `username: str \| None = None` 追加 |
| 6 | BE api | `backend/app/api/v1/admin.py:129` | `email=str(payload.email)` | `email=payload.email`（None をそのまま／`str(None)="None"` 事故回避） |
| 7 | BE api | `backend/app/api/v1/admin.py:94-95` | 検索が email のみ | `or_(lower(email) like, lower(username) like)` |
| 8 | BE api | `backend/app/api/v1/admin.py:60` | 409 メッセージ「email already in use?」 | 制約種別を判別 or 汎用「email/username/staff 紐付けのいずれかが重複」 |
| 9 | BE api | `backend/app/api/v1/visit_review.py:43` | `return actor.email`（null 化） | `actor.staff.name or actor.username or "unknown"` にフォールバック |
| 10 | FE auth | `frontend/lib/auth.ts:8` | `credentialsSchema email: z.string().email()` | `identifier: z.string().min(1)` |
| 11 | FE auth | `frontend/lib/auth.ts:18` | `loginResponseSchema ... email: z.string().email()` | `.email().nullable()`（＋`username`） |
| 12 | FE auth | `frontend/lib/auth.ts:65` | `user.email.split('@')[0]`（**null で TypeError**） | `user.name ?? user.email?.split('@')[0] ?? user.username ?? 'user'` |
| 13 | FE schema | `frontend/lib/schemas/admin-user.ts:20,33` | `email: z.string().email()` | `.email().nullable()` / create は optional＋`username` |
| 14 | FE page | `frontend/app/(app)/admin/users/page.tsx:195,229` | `{u.email}` / `${u.email} を削除します` | `u.email ?? u.username ?? '--'` で null 安全化 |
| 15 | FE types | `frontend/types/next-auth.d.ts` | `username` フィールド無し | Session/User/JWT に `username?: string \| null` 追加 |
| 16 | BE monitor | `backend/app/services/checkin/monitor.py:257` | `user.email` フォールバック | staff.name 優先で低リスクだが null 安全化 |

※ 監査ログ・通知の宛先に email を使う経路が無いことを実装時に grep で最終確認（現状メール送信機能は無い見込み）。

## 5. バックエンド変更

### 5.1 モデル `app/models/user.py`
- `username: Mapped[str | None]` 追加。`email` を nullable に。`__table_args__` に上記2つの部分ユニークインデックスを追加。

### 5.2 認証 `app/api/v1/auth.py` + `app/schemas/auth.py`
- `LoginRequest`: `email: EmailStr` → **`identifier: str`**。**移行期の後方互換**として `email: str | None = None` も残し、
  `identifier or email` を実効識別子とする（P1a のみ先行デプロイしても既存メールログインが壊れないため）。
- ログイン解決 `auth.py:54`: `User.email == payload.email` を **`or_(User.username == norm, User.email == norm)`** に変更。
  - `norm = identifier.strip().lower()`。email も username も小文字正規化で比較（DB も小文字保存）。
- ロックアウト（5回/15分・`auth.py:64-68`）・IP レート制限（`auth.py:48`）・must_change_password フローは不変。
- エラーメッセージ `auth.py:57-62` の「Invalid email or password」→「Invalid credentials」（username も対象のため）。列挙対策の汎用文は維持。
- `UserOut`（`schemas/auth.py:28`）に `username: str | None` を追加、`email` を optional 化。

### 5.5 ログイン変更の全7層（原子性チェックリスト — critic C1）
「S001」でログインするには次の7層すべてを更新する。移行期の後方互換（§5.2）を守れば、BE→FE の順で非原子デプロイ可。

| 層 | file:line | 変更 |
|---|---|---|
| 1 入力欄 type | `frontend/app/(auth)/login/page.tsx:52` | `type="email"` → `type="text"`、ラベル「メール または スタッフID」 |
| 2 autocomplete | `frontend/app/(auth)/login/page.tsx:55` | `autoComplete="email"` → `"username"` |
| 3 NextAuth field | `frontend/lib/auth.ts:42` | credentials field を `identifier`（type text）に |
| 4 authorize 検証 | `frontend/lib/auth.ts:8` | Zod `email()` → `identifier: z.string().min(1)` |
| 5 リクエスト body | `frontend/lib/auth.ts:54` | `{ email }` → `{ identifier }`（BE が両受理なので既存も可） |
| 6 BE schema | `backend/app/schemas/auth.py:12` | `identifier: str`（＋`email` 任意） |
| 7 BE query | `backend/app/api/v1/auth.py:54` | `or_(username, email)` 解決 |

### 5.3 admin users CRUD `app/api/v1/admin.py` + `app/schemas/admin_user.py`
- `AdminUserCreate` / `AdminUserUpdate` に `username` を追加（`staff_id` は既に受理済み）。
- `AdminUserRead` に `username` と **`staff_name`** を追加。
  - `staff_name` は `User` に `@property def staff_name(self) -> str | None: return self.staff.name if self.staff else None` を足し、
    `from_attributes=True` で拾わせるのが最短。
  - `User.staff` は既に `lazy="selectin"`（`user.py:48`）なので**明示 `selectinload` は不要**（自動で selectin される）。
    ※ architect 指摘: 設計初版の明示 selectinload は冗長。
- バリデーション:
  - `staff_id` 指定時、対象 staff が存在し **`deleted_at IS NULL`** か**明示クエリで確認**（FK は soft-delete を検知しないため）。不正は 422。
  - 二重紐付けは DB 部分ユニークで 409（`_commit_or_409` を制約種別判別できるよう改修 — §4.5#8）。
  - `staff` ロール作成時は `username` 必須、`email` 任意。admin/manager は従来通り `email` 必須。
  - **紐付け解除（staff_id=null）の副作用**: email も username も無いユーザーを解除するとログイン不能になる。
    解除時は「email か username の少なくとも一方が残ること」を検査し、満たさない場合は 422 で警告（UI 側でも注意表示）。
  - `AdminUserCreate.email` に None を渡したとき `str(None)="None"` を保存しないこと（§4.5#6）。
- 既存の temp password 自動発行・`must_change_password=true` は流用。
- **`extra="forbid"` ハザード**（critic）: admin schema は `extra="forbid"`。FE が BE より先にデプロイして `username`/`staff_name`
  を送ると 422。§5.5 の通り **BE 先行デプロイ**を厳守。

### 5.4 スタッフ一覧（picker 用）`app/api/v1/staff.py`
- 現状 `GET /api/v1/staff`（admin/manager, limit/offset のみ）を picker ソースに流用。
- 追加が望ましい（任意・後続でも可）: `active` フィルタ、`q`（name/code 部分一致）。6名規模では無くても可。
- **紐付け済みスタッフの判別**: picker で「既にアカウントあり」を灰色表示するため、
  `GET /admin/users` の一覧（staff_id 集合）とクライアントで突合するか、staff 一覧に `has_account` を付与する拡張を検討。

## 6. フロントエンド変更

### 6.1 紐付けUI（マニュアル6.3準拠）
- **作成ダイアログ** `UserCreateDialog.tsx`:
  - `username`（ログインID）入力欄を追加。ロール=staff 選択時は必須。
  - **スタッフ選択ドロップダウン**（`GET /api/v1/staff`）。選択で `staff_id` をペイロードに含める。
  - 既に紐付け済みのスタッフは選択肢から除外/無効化。
  - 補助: スタッフ選択時に username を `code` で自動補完（編集可）。
- **編集ダイアログ** `UserEditDialog.tsx`:
  - 「read-only」制限を解除。`username` 編集＋スタッフ紐付けの設定/変更/解除を追加。
  - payload に `staff_id`（null で解除）と `username` を含める。
- **一覧** `users/page.tsx`:
  - 「スタッフ紐付け」列を UUID断片 → **`staff_name`** 表示に変更。未紐付けは「--」。
  - ログインID（username）列の追加も検討。
- スキーマ `frontend/lib/schemas/admin-user.ts`: `username` / `staff_name` を追加（zod は既に `staff_id` 対応済み）。

### 6.2 ログイン画面
- ラベル「メールアドレス」→「**メール または スタッフID**」。
- `frontend/lib/auth.ts` の authorize が `identifier` を backend に渡すよう変更。
- 入力バリデーションを email 形式固定から緩和（identifier は email or code）。

## 7. スタッフ用アカウント供給（WS-3）

### 7.1 一括スクリプト `backend/scripts/provision_staff_accounts.py`（新規）
- `staff`（`deleted_at IS NULL` かつ `code IS NOT NULL`）を走査。
- 各スタッフについて:
  - 既に `users.staff_id = staff.id` のアカウントがあればスキップ。
  - 無ければ `User(username=staff.code, email=NULL, role='staff', staff_id=staff.id, must_change_password=True, password_hash=hash(temp))` を作成。
- `--only-role staff` 等で対象を絞れる。`--dry-run` 対応。
- 生成した仮パスワードは `import_users.py` と同じ方式（`--out CSV`（0600） or stdout一回表示）で受け渡し。
- **email を一切要求しない**（コードログインの利点）。
- S001 のような「既存アカウントに紐付けるだけ」のケースは本スクリプトの対象外（UI で実施、または `--link-existing code=email` の明示マップを別途受ける拡張も可）。

### 7.2 UI での個別供給
- 6.1 の作成ダイアログで1名ずつ作成＋紐付け。少人数運用・増員時に使用。

### 7.3 投入手順（本番）
1. **川名千恵（S001）**: UI で既存 `chie.kawana@thousands.jp` を編集し staff_id=S001 を紐付け。
   **username=S001 も付与**（メール/コード両方でログイン可・他スタッフと運用一貫）。既存メールログインは維持。
2. **S002〜S006**: 一括スクリプト（または UI）で staff アカウント作成（username=S002…）＋紐付け。仮パスワードを本人へ伝達。
3. スモーク: 1名で実機ログイン→モバイル「今日/今週」に自分の訪問が表示されることを確認。

### 7.4 紐付け後のセッション反映（critic M1 — 運用直結・重要）
JWT に `staff_id` が焼き込まれるため、**既にログイン中のユーザー**を後から紐付けても、現在の access token は
`staff_id=null` のままで、モバイルは空・チェックインは 403 が続く。反映は次のいずれか:
- access token 失効後の**自動リフレッシュ**（`frontend/lib/auth.ts:92` 約55分）で `/auth/refresh` が DB から最新 `staff_id` を再取得。
- 本人が**再ログイン**（即時反映）。

運用ルール:
- **新規5名（S002〜S006）は「作成＋紐付け」後に初回ログインするため、滞留ウィンドウは発生しない**（初回トークンに staff_id が入る）。
- **川名様のみ**、既にログイン中なら紐付け後に**一度ログアウト→再ログイン**を案内（最大55分待てば自動反映）。
- 改善案（任意・後続）: 紐付け変更時に「対象者は再ログインが必要」バナー表示／対象セッション強制リフレッシュ。P4 はまず運用案内で対応。

## 8. テスト計画

- backend `tests/test_admin_users.py`:
  - staff_id 紐付き作成/更新/解除、二重紐付け 409、存在しない staff_id で 422、staff ロールで username 必須。
  - `AdminUserRead.staff_name` が返ること。
- backend 認証テスト（`tests/test_*login*` 相当を新設）:
  - username ログイン成功、email ログイン後方互換、大文字小文字/前後空白の正規化、ロックアウト不変。
- backend `provision_staff_accounts` のユニット（dry-run 集計、既存スキップ、username=code）。
- frontend vitest: 作成/編集ダイアログのスタッフ選択・解除、一覧の staff_name 表示、ログイン画面の identifier 送信。
- migration: `alembic upgrade` → `alembic heads` 単一（0046）、SQLite/PG 両方でユニーク制約が効くこと。

## 9. デプロイ（migration 込み・標準手順）

1. `git push origin develop`（pre-commit 通過必須）
2. `pg_dump` バックアップ（`/opt/carelink/backups/pre-0046-*.sql.gz`）
3. `git pull --ff-only` → `docker compose ... build backend frontend`
4. `alembic upgrade head`（0046）→ `alembic heads` 単一確認
5. `up -d --force-recreate backend frontend` → スモーク（healthz / login / /admin/users）
6. 現場端末は Ctrl+Shift+R（SW cache）
7. 投入手順（§7.3）を実施

## 10. リスク・未確認・論点

- **email nullable 化の影響**: §4.5 で列挙済み。加えて email 前提のコード（通知・パスワードリセットの宛先等）が無いか実装時に grep 最終確認。現状メール送信機能は無い見込みだが未確定。
- **email ユニーク制約**: PostgreSQL は UNIQUE 列に複数 NULL を許すため、現行 unique index のままでも NULL 複数は通る。ただし可読性のため email も `WHERE email IS NOT NULL AND deleted_at IS NULL` の部分ユニークへ移行推奨。既存 unique 制約名（`users_email_key` 等）を migration 内で特定して張り替え。SQLite は `batch_alter_table`（0043 パターン）。
- **川名様の username**: 付与する（決定）。メール/コード両ログイン可・運用一貫のため（§7.3）。
- **NextAuth/authorize の identifier 変更**が既存ログインを壊さないこと（後方互換テスト必須）。
- **username と staff.code の同期**: code をログインIDにすると、後で code を変更した際に username が追随しない（現状は独立コピー）。運用ルール or 将来同期を検討。
- **S001 の username 付与要否**: 既存メールログインのままでも紐付けだけで自分の訪問は見える。username 付与は任意。
- **1:1 の後追い担保**: 既存データに二重紐付けは無い（現状 linked=0）ため migration での競合は起きない。

## 11. フェーズ分割・実装順（レビュー反映で P1 を分割）

- **P1a（土台・紐付け先行）**: migration 0046（username/email nullable/staff_id 部分ユニーク）＋ モデル ＋
  admin CRUD（username/staff_id/staff_name・soft-delete 検査・解除ガード・409 メッセージ）。§4.5 の BE 分をすべて含む。
  → これだけで**手動紐付けが可能**になり、川名様（S001）の先行紐付けができる。
- **P1b（コードログイン）**: `identifier` 化（§5.5 の全7層）。BE は移行期 `email`/`identifier` 両受理で後方互換 → **BE 先行 → FE の順**でデプロイ可。
- **P2（紐付けUI仕上げ）**: 作成/編集ダイアログのスタッフ選択・解除・username 欄、一覧の staff_name 表示、ログイン画面の identifier 対応。§4.5 の FE 分。
- **P3（供給）**: `provision_staff_accounts.py`。
- **P4（検証・投入）**: 実機1名検証 → 川名紐付け（再ログイン案内）→ 残り5名投入 → マニュアル改訂（`docs/manual/`）。

**デプロイ原子性（critic）**: `identifier` 化は BE を先に（両受理で後方互換）→ FE を後に、で既存メールログインを一切壊さない。
逆順・片側のみデプロイは全ログイン不能を招くため禁止。

各フェーズ実装（executor opus）→ 別パスでコードレビュー（code-reviewer＋security-reviewer）→ 修正 → 検証、最後に最終クロスレビュー。自己approve禁止。
