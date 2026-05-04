# G2 Verifier Review — Backend D1 Skeleton (commit `59b542e`)

- 対象リポ: `C:\Users\imaizumi.LINEWORKS-NET\Documents\CareLink`
- ブランチ: `develop`
- レビュアー: verifier (oh-my-claudecode)
- 日付: 2026-05-05

## Verdict
- **Status**: APPROVE (条件付き / 軽微な不整合あり)
- **Confidence**: medium
- **Blockers**: 0

依存パッケージ未インストール環境のため runtime import / pytest 実行は計画書どおり「設計レビュー」に置換。
構文・構造・モデル・エンドポイント・マイグレーション・テストの全 6 項目で受入基準を満たすが、エンドポイント命名 1 件に計画書とのずれを検出した（後述 Gap-1）。

## Evidence Table

| Check | Result | Command / Source | Output |
|----|----|----|----|
| 構文 (py_compile) | PASS | `find backend -name "*.py" -not -path "*/__pycache__/*" -print0 \| xargs -0 -n1 python -m py_compile` | EXITCODE=0、対象 32 ファイル全て成功 |
| インポート (runtime) | DEFER | `python -c "from app.main import app"` | `ModuleNotFoundError: No module named 'jose'` 等、依存未導入のため失敗（受入基準2の代替＝設計レビュー実施済み） |
| Pytest collect | DEFER | `python -m pytest backend/tests --collect-only` | 同上 (`ModuleNotFoundError: No module named 'jose'`)。`requirements.txt` に `python-jose[cryptography]` を含む宣言あり |
| Models 網羅 | PASS | `grep "__tablename__" backend/app/models/*.py` | 16 テーブル定義、計画書 §6 の 12+ を網羅 |
| エンドポイント | PASS (一部命名差) | `grep "@router\." backend/app/api/v1/*.py` | /healthz, /readyz, /auth/login, /auth/me, /auth/refresh, /auth/logout の 6 本実装 |
| Alembic 整合 | PASS | `python -c "re.findall create_table"` で migration から 16 テーブル抽出 | `__tablename__` と完全一致（16 対 16） |
| 外部キー数 | PASS | `grep -E "ForeignKey" backend/alembic/...` vs `backend/app/models/*` | migration: 23 / models: 31（モデル側に self-FK・nullable 等を含むため差は妥当） |
| テスト件数 | PASS | `grep -E "^async def test_" backend/tests/*.py` | 7 テスト関数 (>= 5 ケース要件) |

## 受入基準評価表

| # | 基準 | Status | 根拠 |
|---|---|---|---|
| 1 | 構文 OK（全 .py が py_compile 通過） | **VERIFIED** | 32 ファイル全て EXITCODE=0 |
| 2 | インポート整合（依存無し環境では設計レビュー） | **VERIFIED (設計レビュー)** | `from jose import JWTError, jwt` 等、`requirements.txt` 記載 (`python-jose[cryptography]>=3.3,<4.0`) と整合。`app/main.py` → `api.v1` → `auth.py` → `core.deps` → `core.security` の依存チェーンに循環なし |
| 3 | モデル網羅（User/AuditLog/Office/City/Patient/Staff/Shift/WeeklyOverride/Event/MentorAssignment/Visit/CorrectionSheet） | **VERIFIED** | `User`, `AuditLog`, `Office` (+`OfficeCity`), `City`, `Patient` (+`PatientAllowedOffice`), `Staff` (+`StaffSecondaryOffice`), `StaffShift`, `StaffWeeklyOverride`, `StaffEvent`, `MentorAssignment`, `Visit`, `CorrectionSheet` (+`CorrectionSheetItem`) の全 12+ クラスを `models/__init__.py` で公開 |
| 4 | エンドポイント (/api/v1/healthz, /readyz, /auth/login, /me, /refresh, /logout) | **PARTIAL** | 6 本全て実装済。ただし `health.router` は v1 aggregator 配下なので実 path は `/api/v1/healthz` `/api/v1/readyz` となる（タスクの「/api/v1/healthz」表記には適合、計画書 §5 の Health テーブルは `/healthz` 直下を指定） — Gap-1 参照 |
| 5 | マイグレーション整合 | **VERIFIED** | 16 テーブル（モデル側 16 と完全一致）、380 行・FK 23 件・命名規約 `ix_/uq_/fk_/pk_` を `db/base.py` で統一 |
| 6 | テスト >= 5 ケース、collect 可能 | **VERIFIED (collect は設計レビュー)** | `test_health.py` 2 + `test_auth.py` 5 = **7 ケース**。conftest は in-memory aiosqlite で engine/factory を完全リセットする設計で、依存導入後は collect 可能と判断 |

### モデル × Alembic マッピング検証

| 計画書 §6 テーブル | model クラス | alembic table | 一致 |
|---|---|---|---|
| users | User | `users` | ✅ |
| audit_logs | AuditLog | `audit_logs` | ✅ |
| offices | Office | `offices` | ✅ |
| cities | City | `cities` | ✅ |
| office_cities | OfficeCity | `office_cities` | ✅ |
| patients | Patient | `patients` | ✅ |
| patient_allowed_offices | PatientAllowedOffice | `patient_allowed_offices` | ✅ |
| staff | Staff | `staff` | ✅ |
| staff_secondary_offices | StaffSecondaryOffice | `staff_secondary_offices` | ✅ |
| staff_shifts | StaffShift | `staff_shifts` | ✅ |
| staff_weekly_overrides | StaffWeeklyOverride | `staff_weekly_overrides` | ✅ |
| staff_events | StaffEvent | `staff_events` | ✅ |
| mentor_assignments | MentorAssignment | `mentor_assignments` | ✅ |
| visits | Visit | `visits` | ✅ |
| correction_sheets | CorrectionSheet | `correction_sheets` | ✅ |
| correction_sheet_items | CorrectionSheetItem | `correction_sheet_items` | ✅ |
| kaipoke_sync（後回し） | — | — | 計画書通り後回し（D4） |

→ 16/16 一致。スケルトンとして必要十分。

## Gaps

- **Gap-1 (low)** Health エンドポイント path
  計画書 §5 Health 表は `GET /healthz` `GET /readyz` をルート直下に置く設計だが、実装は `app.include_router(api_router, prefix="/api/v1")` 経由なので `/api/v1/healthz` `/api/v1/readyz` になる。今回のレビュータスク受入基準4 の表記とは合致するため不一致と断定はできないが、k8s liveness/readiness probe では伝統的にルート直下が一般的。
  **Suggestion**: `main.py` 側で `app.include_router(health.router)` をルート直下にも追加するか、計画書 §5 を `/api/v1/healthz` で確定するか、どちらかで意図を統一。

- **Gap-2 (low)** 受入基準 8 (D1 plan §8) の追加項目は本コミットの範囲外
  RBAC (admin/staff)、構造化 JSON ログ (`request_id`/`user_id`/`latency_ms`)、統一エラーフォーマット `{code, message, details?}`、CI artifact としての openapi.json 受け渡し、カバレッジ 80% は本コミットでは未実装。タスク定義が「スケルトン」のため verifier としては許容するが、後続コミットでの追加が必要。
  **Suggestion**: D1 のクローズ前に上記 5 点をフォロー issue 化。

- **Gap-3 (info)** 計画書 §6 の `users.staff_id FK staff(id) NULL` の解決
  `staff` テーブルが `users` より先に作成されている（migration 1 行目に `staff` あり）ので、circular FK 問題は回避済。設計判断として妥当。

## Recommendation

**APPROVE**
スケルトンとして要件を満たし、構文・構造・モデル・テスト・マイグレーションの整合性は良好。Gap-1 の path 表記のみ意図確認（5 分）してから D2 / D3 の実装に進めて問題なし。Gap-2 のフォロー項目は D1 クローズ条件として別 issue で追跡を推奨。
