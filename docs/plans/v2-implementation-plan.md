# CareFlow v2 実装手順書（エージェント並行処理対応）

> **Status**: ドラフト v0.2（Codex レビュー反映済み）
> **対応設計仕様書**: `docs/plans/v2-allocation-redesign.md` v0.9
> **目的**: v2 設計を実装するための、サブエージェントに並行割当可能なチケット単位の手順書

---

## 0. 設計原則（並行処理を成立させるルール）

### 0.1 チケット粒度

各チケットは以下を満たす独立単位：

- **ファイル所有権が排他的**: 1 チケットが触る既存ファイルは他チケットと重ならない
- **入力契約が明確**: 依存する API / 型 / マイグレーションが事前に確定済み
- **出力契約が明確**: 完了時に何を生み出すかが Acceptance criteria で定義
- **テスト独立**: 他チケットの結果なしに最低限の検証ができる

### 0.2 並行実行のレイヤー

```
Wave 0: 基盤契約 (sequential)
   ├ 0-A: DB マイグレーション順序確定
   ├ 0-B: API contract / OpenAPI 雛形
   └ 0-C: 共有型 (zod / pydantic) ベース定義
        ↓
Wave 1: マスタ整理 (parallel × 6)
        ↓
Wave 2: 新エンティティ (parallel × 4)
        ↓
Wave 3: スケジュール UI v2 (parallel × 3)
        ↓
Wave 4: アルゴリズム L1〜L3 (semi-parallel)
        ↓
Wave 5: AI 統合 (parallel × 4)
        ↓
Wave 6: 移行・E2E・凍結 (sequential)
```

### 0.3 サブエージェント割当ポリシー

- 1 チケット = 1 サブエージェント（Claude Code `Agent` tool で起動）
- `isolation: "worktree"` でファイル衝突を物理的に排除
- 並行実行する場合は **同一 message 内で複数 `Agent` 呼び出し**
- 完了後、メイン側で順序付き merge（`merge order` 章参照）

### 0.4 ブランチ命名

```
feat/v2-{wave}-{ticket-id}-{short-desc}
例: feat/v2-w1-be1-patient-master-cleanup
   feat/v2-w3-fe4-schedule-grid-v2
```

### 0.5 受入基準の共通項目

各チケットは以下を満たすまで完了としない：
- [ ] 単体テスト追加（Backend は pytest、Frontend は vitest 既存パターン）
- [ ] 既存テスト全 pass
- [ ] 型チェック (`tsc --noEmit` / `mypy`) クリーン
- [ ] prettier / ruff format 適用
- [ ] チケット記載の Acceptance criteria を満たす

---

## 1. Wave 0: 基盤契約定義（Sequential）

> **これは並行処理しない**。後続の全 Wave が依存するため、ここを最初に確定する。

### 0-A: DB マイグレーション順序確定

| 項目 | 内容 |
|---|---|
| 担当 | メインエージェント or 単独サブエージェント |
| 出力 | `backend/alembic/versions/0007_v2_master_cleanup.py` 等の番号予約表 |
| 内容 | Wave 1〜2 で追加するマイグレーションの番号 / 順序 / 依存を表で管理 |
| Acceptance | マイグレーション衝突がないように番号を予約済み（コードは空） |

### 0-B: API 契約雛形

| 項目 | 内容 |
|---|---|
| 出力 | OpenAPI スキーマの差分案（`docs/plans/v2-api-contracts.md` 新規） |
| 内容 | 新規・変更 API 全てのリクエスト / レスポンス契約を文書化 |
| 対象 | patients, staff, offices, courses, pending_requests, ai/* |
| Acceptance | フロント・バック両エージェントが参照可能な状態 |

### 0-C: 共有型ベース定義（**完全確定が必須**）

| 項目 | 内容 |
|---|---|
| 出力 | `frontend/lib/schemas/v2/*.ts`（zod）と `backend/app/schemas/v2/*.py`（pydantic） |
| 必須型 | **`PatientV2`, `StaffV2`, `OfficeV2`, `CourseV2`, `PendingRequestV2`, `WeeklyPatternV2`, `VisitV2`, `CourseStatus` enum, `RequestType` enum, `RequestStatus` enum, `AiContextType` enum** |
| 既存 schema との関係 | 既存 `frontend/lib/schemas/{patient,staff,office}.ts` は v2 schema から re-export して移行期間互換を保つ。Wave 1 完了時に元ファイル削除 |
| context_type ⇔ request_type 対応表 | AI の `context_type` と `pending_requests.request_type` の 1:1 対応表を `docs/plans/v2-api-contracts.md` に明記 |
| Acceptance | (1) 11 種の型が全て pydantic / zod で定義済み (2) フロント・バックで型一致を CI で検証 (3) 対応表が両エージェント参照可能 |

---

## 2. Wave 1: マスタ整理（Parallel × 6）

**並行実行可能**: 6 チケットは互いに独立。同時起動可。

### W1-BE1: 患者マスタ整理

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w1-be1-patient-master` |
| 所有ファイル | `backend/app/models/patient.py`, `backend/app/schemas/patient.py`, `backend/app/api/v1/patients.py`, `backend/alembic/versions/0007_*.py`, `backend/tests/test_patients_v2.py` |
| 削除フィールド | 年齢 / NG時間 / 指定タイプ / NGスタッフ / 同行希望スタッフ / 継続要望 / 必要スタッフ数 / 曜日優先度 / NG曜日 / エリア（10 項目） |
| 追加フィールド | `weekly_pattern.staff_count`, `special_weekly_pattern`, `special_week_active` |
| 受入 | (1) migration up/down 動作 (2) 削除フィールドが API レスポンスから消えている (3) test_patients_v2 全 pass |

### W1-BE2: スタッフマスタ整理

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w1-be2-staff-master` |
| 所有ファイル | `backend/app/models/staff.py`, `backend/app/schemas/staff.py`, `backend/app/api/v1/staff.py`, `backend/alembic/versions/0008_*.py`, `backend/tests/test_staff_v2.py` |
| 削除フィールド | can_double_team / 自宅住所 + lat/lng / 得意エリア / 1日最大訪問数 / スキル / 割付ボリューム（6 項目） |
| 状態値 | 在籍 / 休職 / 退職 の 3 値に統一 |
| 受入 | 同上 + メンターフィールドは維持（UI 側で「詳細」セクション扱い） |

### W1-BE3: 拠点 (Office) 整備

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w1-be3-office-auto-assign` |
| 所有ファイル | `backend/app/api/v1/offices.py`, `backend/app/services/office_assigner.py`（新規）, `backend/scripts/seed_offices_v2.py`（新規）, `backend/tests/test_office_assigner.py` |
| 内容 | 稲毛 / 都賀をシード。患者住所→拠点自動紐付けロジック（`OfficeAssigner.resolve_for_address`）。手動上書きフラグ |
| 受入 | (1) 千葉市稲毛区の住所で「稲毛」自動取得 (2) 千葉市若葉区都賀の住所で「都賀」 (3) 範囲外住所で警告 |

### W1-FE1: 患者フォーム削減

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w1-fe1-patient-form` |
| 所有ファイル | `frontend/app/(app)/patients/_components/PatientForm.tsx`, `frontend/lib/schemas/patient.ts`, `frontend/app/(app)/patients/[id]/page.tsx` |
| 依存 | Wave 0-C（共有型）|
| 内容 | W1-BE1 の削除に対応するフォーム整理。週パターン UI に staff_count トグル追加 |
| 受入 | (1) 削除フィールドが UI に出ない (2) 既存患者編集が新スキーマで成立 (3) 必須フィールド検証 |

### W1-FE2: スタッフフォーム削減 + メンター移設

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w1-fe2-staff-form` |
| 所有ファイル | `frontend/app/(app)/staff/_components/StaffFormFields.tsx`, `frontend/lib/schemas/staff.ts`, `frontend/app/(app)/staff/[id]/page.tsx` |
| 依存 | Wave 0-C |
| 内容 | 削除 6 項目を反映。メンターフィールドを「詳細」セクションへ移動。状態を 3 値セレクトに |
| 受入 | (1) 「基本情報」にメンター欄が出ない (2) 「詳細」セクション末尾にメンターセレクト |

### W1-FE3: 拠点フォーム

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w1-fe3-office-form` |
| 所有ファイル | `frontend/app/(app)/offices/_components/OfficeForm.tsx` のみ（既存ファイル） |
| 内容 | 担当市区町村 UI の改善（任意。MVP では既存維持で OK） |
| 受入 | 拠点作成・編集が動作する |

---

## 3. Wave 2: 新エンティティ（Parallel × 4）

**並行実行可能**: 4 チケットは互いに独立。

### W2-BE4: Course / Visit 拡張テーブル

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w2-be4-course-table` |
| 所有ファイル | `backend/app/models/course.py`（新規）, `backend/app/models/visit.py`（既存改修）, `backend/app/models/visit_staff_assignment.py`（新規）, `backend/app/schemas/course.py`（新規）, `backend/app/schemas/visit.py`（既存改修）, `backend/app/api/v1/courses.py`（新規・CRUD のみ）, `backend/app/api/v1/visits.py`（既存改修）, `backend/alembic/versions/0009_*.py`, `backend/tests/test_courses.py`, `backend/tests/test_visit_v2.py` |
| 依存 | Wave 0-A, 0-C |
| 内容 | `courses` テーブル（status enum 単一管理）+ `visits` 拡張（`course_id`, `required_staff_count`, `visit_group_id`）+ `visit_staff_assignments` 多対多テーブル新設。CRUD API |
| 受入 | (1) コース CRUD 動作 (2) UNIQUE (year,week,weekday,code) (3) 2 名体制の visit が visit_group_id でリンク (4) visit_staff_assignments で 1 visit ↔ 2 staff 関係を表現 |

### W2-BE5: pending_requests + 適用処理

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w2-be5-pending-requests` |
| 所有ファイル | `backend/app/models/pending_request.py`（新規）, `backend/app/schemas/pending_request.py`（新規）, `backend/app/api/v1/pending_requests.py`（新規）, `backend/app/services/pending_request_applier.py`（新規）, `backend/alembic/versions/0010_*.py`, `backend/tests/test_pending_requests.py`, `backend/tests/test_pending_request_applier.py` |
| 依存 | Wave 0-A, 0-B（context_type ⇔ request_type 対応表）, 0-C |
| 内容 | (1) §4.4 のスキーマ実装 (2) 状態遷移 (pending → approved / rejected) (3) **`PendingRequestApplier`**: 承認時に request_type ごとに実業務テーブルへ反映するサービス層。冪等性（同一申請の二重適用防止）・失敗時 rollback を保証 |
| 反映対象（request_type ごとに副作用が異なる） | `staff_off`/`staff_event`/`staff_mentor`/`patient_create`/`staff_create`/`patient_cancel`/`patient_reschedule`/`patient_special_week_on`/`patient_special_week_off` |
| 受入 | (1) RBAC（admin/manager 承認可、staff は申請のみ） (2) 状態遷移テスト (3) 各 request_type の承認で正しいテーブルが更新される (4) 同一申請を 2 回 approve しても 1 回しか反映されない (5) 反映失敗時に DB トランザクションが rollback される |

### W2-BE6: AI scope 拡張（context_type 追加 + out_of_scope）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w2-be6-ai-scope` |
| 所有ファイル | `backend/app/services/gemini_client.py`, `backend/app/api/v1/ai.py`, `backend/app/schemas/ai.py`, `backend/tests/test_ai_interpret.py`（既存追記） |
| 依存 | Wave 0-B |
| 内容 | context_type に `staff_create`, `patient_cancel`, `patient_reschedule`, `patient_special_week` を追加。プロンプトに `out_of_scope` アクション選択を組込み |
| 受入 | (1) 各 context_type のハッピーパス (2) 範囲外発話で `out_of_scope` 返却 |

### W2-FE4: AI モバイル FAB（雛形）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w2-fe4-mobile-ai-fab` |
| 所有ファイル | `frontend/app/(mobile)/m/_components/MobileAiFab.tsx`（新規）, `frontend/components/MobileShell.tsx`（FAB 統合追加のみ）, `frontend/app/(mobile)/layout.tsx` |
| 依存 | なし（API は既存の /ai/interpret を使う） |
| 内容 | デスクトップ AiFab の薄いラッパとして実装。音声入力主体の UI |
| 受入 | (1) モバイルレイアウトで右下に表示 (2) タップで AiInputModal 開く |

---

## 4. Wave 3: スケジュール UI v2（Parallel × 3、ただし依存あり）

### W3-BE-FIX: schedule/fix エンドポイント

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w3-be-fix-endpoint` |
| 所有ファイル | `backend/app/api/v1/schedule.py`（新規）, `backend/app/services/schedule_fix_service.py`（新規）, `backend/tests/test_schedule_fix.py` |
| 依存 | W1-BE1, W2-BE4 |
| 内容 | `POST /api/v1/schedule/fix`: 当該週のレイアウトを各患者の `weekly_pattern` に保存する API。staff_count 含む |
| 受入 | (1) N 名分の weekly_pattern 一括更新 (2) 既存パターンとの差分のみ書込み (3) トランザクショナル |

### W3-FE5: スケジュールグリッド v2（Layer 1）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w3-fe5-schedule-grid` |
| 所有ファイル | `frontend/app/(app)/schedule/page.tsx`（既存リプレース）, `frontend/components/schedule/v2/ScheduleGridV2.tsx`（新規）, `frontend/components/schedule/v2/PoolPanel.tsx`（新規）, `frontend/components/schedule/v2/TimeSlotCell.tsx`（新規）, `frontend/components/schedule/v2/PatientCard.tsx`（新規）, `frontend/components/schedule/v2/FixButton.tsx`（新規）, `frontend/lib/queries/schedule_v2.ts`（新規） |
| 依存 | W1-BE1, W1-BE2, W2-BE4, W3-BE-FIX |
| 内容 | 縦軸時刻 (15 分) × 横軸曜日のグリッド。コース行（M/A/B/C/D）。下部保留プール。dnd-kit でドラッグ |
| 受入 | (1) 保留→セル→保留の双方向移動 (2) +1 人ボタンで 2 名体制化 (3) 「固定」ボタンで weekly_pattern 保存 |

### W3-FE6: 申請履歴ビュー（PC）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w3-fe6-pending-requests-ui` |
| 所有ファイル | `frontend/app/(app)/admin/pending-requests/page.tsx`（新規）, `frontend/lib/queries/pending_requests.ts`（新規）, `frontend/lib/schemas/pending_request.ts` |
| 依存 | W2-BE5 |
| 内容 | 申請一覧 + フィルタ（スタッフ / 患者）+ 承認 / 編集承認 / 却下 UI |
| 受入 | (1) pending 一覧表示 (2) 承認後に approved に変わる (3) 却下時に理由入力強制 |

### W3-FE7: スケジュール画面への申請パネル統合

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w3-fe7-schedule-pending-panel` |
| 所有ファイル | `frontend/components/schedule/v2/PendingRequestPanel.tsx`（新規）のみ。W3-FE5 が `ScheduleGridV2.tsx` 内に統合用スロットを事前用意することで衝突回避 |
| 依存 | W3-FE5（統合スロット先行）, W3-FE6 |
| 内容 | スケジュール画面の上端 or 下端に保留申請パネル。クリックで承認・編集モーダル |
| 受入 | スケジュール画面と申請履歴画面の双方から承認可能 |

---

## 5. Wave 4: アルゴリズム L1〜L3（Semi-parallel）

> **L1 → L2 → L3 の順序依存**。ただし内部で並行可。

### W4-BE7: Layer 1 アルゴリズム（プール展開）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w4-be7-layer1-expand` |
| 所有ファイル | `backend/app/services/scheduling/layer1_expander.py`（新規）, `backend/app/api/v1/schedule.py`（既存 or 新規）, `backend/tests/test_layer1.py` |
| 依存 | W1-BE1, W2-BE4 |
| 内容 | weekly_pattern → visits 生成。特別週判定。新規患者を保留プールへ |
| 受入 | (1) 既存患者の週展開 (2) 特別週切替が反映 (3) 新規患者は保留 |

### W4-BE8: Layer 2 アルゴリズム（コース分け）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w4-be8-layer2-courses` |
| 所有ファイル | `backend/app/services/scheduling/layer2_clustering.py`（新規）, `backend/app/api/v1/courses.py`（generate エンドポイント追加）, `backend/tests/test_layer2.py` |
| 依存 | W4-BE7, W1-BE3 |
| 内容 | K-means + 制約後処理（MVP）。`POST /api/v1/courses/generate` 実装 |
| 受入 | (1) 24 名 → 4 コース × 6 名以内（fixture: `tests/fixtures/layer2_24patients.json`） (2) `random_state=0` で総直線距離が **naive round-robin の 70% 以下**（評価指標: 全コース合算の総直線距離 km） (3) 時間衝突（移動時間込み）が発生しない (4) MVP 前提（Q1=サービス時間枠消費、Q4=直線距離）に準拠 |

### W4-BE9: Layer 3 アルゴリズム（スタッフ割付）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w4-be9-layer3-rotation` |
| 所有ファイル | `backend/app/services/scheduling/layer3_assignment.py`（新規）, `backend/app/api/v1/courses.py`（assign-staff エンドポイント追加）, `backend/tests/test_layer3.py` |
| 依存 | W4-BE8, W1-BE2 |
| 内容 | ハンガリアン法 + ローテーション分散。`POST /api/v1/courses/assign-staff` 実装 |
| 受入 | (1) 全ハード制約満たす（性別・勤務曜日・1 コース 1 スタッフ・マネージャー除外） (2) **MVP 前提 Q3=ハイブリッドに準拠**: 直近 1 週の担当者は強制除外、それ以前はソフトペナルティ (3) `tests/fixtures/layer3_4weeks.json` を入力に 4 週連続シミュレーションでローテ分散度（Gini 係数）が naive round-robin 以下 (4) 4 拠点 24 患者 × 4 スタッフ × 1 週で計算 30 秒以内 |

### W4-FE8: コース表示 + 微調整 UI

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w4-fe8-course-adjust` |
| 所有ファイル | `frontend/components/schedule/v2/CourseRow.tsx`（新規）, `frontend/components/schedule/v2/CourseProposal.tsx`（新規）, `frontend/lib/queries/courses.ts`（新規） |
| 依存 | W4-BE8, W3-FE5 |
| 内容 | 「コース案を生成」ボタン → 案を 4 行に表示 → ドラッグで微調整 → 「コース固定」 |
| 受入 | (1) 案生成 → 表示 → 確定の一連フロー (2) 患者をコース間で移動可能 |

### W4-FE9: スタッフ割付実行 UI

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w4-fe9-staff-assign` |
| 所有ファイル | `frontend/components/schedule/v2/StaffAssignButton.tsx`（新規） |
| 依存 | W4-BE9, W4-FE8 |
| 内容 | 「スタッフ割付を実行」ボタン → 結果を各セルの担当者欄に反映 |
| 受入 | 1 クリックで全曜日 × 全コースのスタッフが埋まる |

---

## 6. Wave 5: AI 統合（Sequential[1] → Parallel[3]）

> **重要**: `AiInputModal.tsx` の衝突を避けるため、W5-FE10 を **先行実装**（sequential）し、
> 拡張ポイント（hook / slot / context）を整備したうえで W5-FE11/FE12/FE13 を並行実行する。

### W5-FE10: AiInputModal の拡張ポイント整備（先行・sequential）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w5-fe10-ai-modal-extension-points` |
| 所有ファイル | `frontend/components/AiInputModal.tsx`（既存改修）, `frontend/lib/ai-capabilities.ts`（新規）, `frontend/components/ai/CapabilitiesPanel.tsx`（新規） |
| 依存 | W2-BE6 |
| 内容 | (1) 「できること / できないこと」恒常表示パネルを子コンポーネントに分離 (2) 範囲外フィードバック表示 (3) **以降のチケットが触れる拡張点（onSubmitInterceptor, MissingInfoSlot, SubmissionMode prop など）を Modal 側に提供** |
| 受入 | (1) 既存 AI 入力フローが回帰なし (2) 拡張ポイントが docs コメント付きで提供されている (3) 範囲外 prompt で適切なメッセージ |

### W5-FE11: AI 解釈→申請履歴フロー（FE10 完了後・並行可）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w5-fe11-ai-to-pending` |
| 所有ファイル | `frontend/components/ai/SubmitToPendingHandler.tsx`（新規・FE10 の onSubmitInterceptor を利用）, `frontend/lib/queries/pending_requests.ts`（既存追記、ファイル所有を W3-FE6 と分割管理 — このチケットは新関数の追加のみ） |
| 依存 | W5-FE10, W2-BE5, W2-BE6 |
| 内容 | AI 解釈結果を `pending_requests` に送信。デバイス・ロールに応じて pending / approved を自動判定 |
| 受入 | (1) モバイルは pending、PC admin/manager は approved (2) Staff は pending のみ |

### W5-FE12: 不足情報補完モーダル（FE10 完了後・並行可）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w5-fe12-missing-info-modal` |
| 所有ファイル | `frontend/components/ai/MissingInfoModal.tsx`（新規・FE10 の MissingInfoSlot にマウント） |
| 依存 | W5-FE10, W1-BE1, W1-BE2 |
| 内容 | patient_create / staff_create で必須未入力をハイライトして補完を促す |
| 受入 | 必須欠損時にモーダルで赤枠表示、補完後に通常 POST で作成 |

### W5-FE13: AI ヘルプ / FAQ ページ（FE10 と並行可）

| 項目 | 内容 |
|---|---|
| ブランチ | `feat/v2-w5-fe13-ai-help-page` |
| 所有ファイル | `frontend/app/(app)/help/ai/page.tsx`（新規）, `frontend/components/Sidebar.tsx`（追記のみ）or `frontend/components/Header.tsx`（ユーザーメニューへの追加 — どちらか 1 ファイルを選び所有） |
| 依存 | なし（`ai-capabilities.ts` は W5-FE10 で作成されるが、未着手なら定数だけ先行作成可） |
| 内容 | できる/できない一覧 + 例文集 + 制約事項のドキュメントページ + ナビゲーション追加 |
| 受入 | サイドバー or ユーザーメニューからアクセスできる |

---

## 7. Wave 6: 移行・E2E・凍結（Sequential）

### W6-MIG1: 既存データ移行

| 項目 | 内容 |
|---|---|
| 担当 | 単独（並列不可） |
| 内容 | (1) 患者・スタッフの廃止フィールド drop (2) special_weeks → 新形式統合 (3) 拠点シード適用 |
| 受入 | 本番 DB 相当のスナップショットで dry run 成功 |

### W6-MIG2: /special-weeks ページ廃止

| 項目 | 内容 |
|---|---|
| 内容 | フロントルート削除（既に `Sidebar.tsx` から削除済）+ バックエンド route を 410 Gone |
| 受入 | /special-weeks にアクセスで 410 |

### W6-E2E: Playwright E2E

| 項目 | 内容 |
|---|---|
| 所有ファイル | `frontend/e2e/v2-*.spec.ts` |
| 内容 | 主要フロー 4 件: <br>(1) 患者登録 → 保留 → 時間固定 → コース → スタッフ <br>(2) AI 音声入力 → 申請 → 承認 → 反映 <br>(3) 4 週分のスタッフローテーション検証 <br>(4) 不足情報補完モーダル |
| 受入 | 全 spec が CI で pass |

### W6-FREEZE: v1 凍結

| 項目 | 内容 |
|---|---|
| 内容 | careflow-scheduler GAS / VPS Python エンジンの保守凍結。kaipoke-api 経由のジョブのみ稼働継続 |
| 受入 | リリースノート発行、ユーザー周知 |

---

## 8. 並行実行マトリクス（一覧）

| Wave | チケット数 | 並行可 | 依存元 | 推奨同時実行数 |
|---|---|---|---|---|
| 0 | 3 | ❌ Sequential | — | 1 |
| 1 | 6 (BE×3, FE×3) | ✅ 全並行 | Wave 0 | 6 |
| 2 | 4 (BE×3, FE×1) | ✅ 全並行 | Wave 0 | 4 |
| 3 | 4 (BE×1, FE×3) | △ BE-FIX → FE5 → FE6/7 | Wave 1, 2 | 2-3 |
| 4 | 5 (BE×3, FE×2) | △ L1→L2→L3 順序 | Wave 1, 2, 3 | **L1 単独 → (L2+FE8 並行) → (L3+FE9 並行)** |
| 5 | 4 (FE×4) | △ FE10 sequential → FE11/12/13 並行 | Wave 2, 4 | 1 → 3 |
| 6 | 4 | ❌ Sequential | Wave 1〜5 | 1 |

**最大同時並行数: 6（Wave 1）**

**チケット総数**: Wave 0(3) + W1(6) + W2(4) + W3(4) + W4(5) + W5(4) + W6(4) = **30 チケット**

---

## 9. インターフェース契約

### 9.1 共有型（Wave 0-C で確定）

```ts
// frontend/lib/schemas/v2/patient.ts (zod)
export const PatientV2 = z.object({
  id: z.string().uuid(),
  code: z.string(),
  name: z.string(),
  kana: z.string().nullable(),
  sex: z.enum(['male', 'female', 'unknown']).nullable(),
  status: z.enum(['active', 'suspended', 'admitted', 'pending', 'cancelled']),
  insurance: z.enum(['medical', 'care']).nullable(),
  address: z.string().nullable(),
  lat: z.number().nullable(),
  lng: z.number().nullable(),
  primary_office_id: z.string().uuid().nullable(),
  sex_restriction: z.enum(['female_only', 'male_only']).nullable(),
  weekly_pattern: WeeklyPatternV2.nullable(),
  special_weekly_pattern: WeeklyPatternV2.nullable(),
  special_week_active: z.array(z.string()).default([]),
  note: z.string().nullable(),
});
```

（残りの型は Wave 0-C で確定後ここに追記）

### 9.2 主要 API エンドポイント

| エンドポイント | 担当チケット | 用途 |
|---|---|---|
| `POST /api/v1/patients` | W1-BE1 | 患者作成（新スキーマ） |
| `POST /api/v1/staff` | W1-BE2 | スタッフ作成（新スキーマ） |
| `POST /api/v1/offices/resolve` | W1-BE3 | 住所→拠点自動判定 |
| `POST /api/v1/schedule/generate-week` | W4-BE7 | Layer 1 実行 |
| `POST /api/v1/courses/generate` | W4-BE8 | Layer 2 実行 |
| `POST /api/v1/courses/assign-staff` | W4-BE9 | Layer 3 実行 |
| `POST /api/v1/schedule/fix` | W3-FE5 (BE 側追加) | 「固定」ボタン |
| `POST /api/v1/pending-requests` | W2-BE5 | 申請作成 |
| `PATCH /api/v1/pending-requests/:id/approve` | W2-BE5 | 承認 |
| `PATCH /api/v1/pending-requests/:id/reject` | W2-BE5 | 却下 |
| `POST /api/v1/ai/interpret` | W2-BE6 | AI 解釈（既存拡張） |

---

## 10. ブランチ戦略・マージ順序

### 10.1 ブランチモデル

- ベース: `develop`
- フィーチャーブランチ: `feat/v2-w{wave}-{ticket}-{desc}`
- 統合ブランチ: `feat/v2-integration` (Wave 終了ごとに作成)
- 各 Wave 終了時に `develop` にマージ

### 10.2 マージ順序（Wave 単位）

```
develop
  ↑ merge wave-0
  ↑ merge wave-1 (6 チケット統合)
  ↑ merge wave-2 (4 チケット統合)
  ↑ merge wave-3 (3 チケット統合)
  ↑ merge wave-4 (5 チケット統合)
  ↑ merge wave-5 (4 チケット統合)
  ↑ merge wave-6 (E2E + 凍結)
```

### 10.3 サブエージェント呼び出しテンプレート

```
Agent({
  description: "W1-BE1 患者マスタ整理",
  subagent_type: "general-purpose",
  isolation: "worktree",
  prompt: "..."
})
```

並列実行する場合は **同一メッセージで複数 Agent 呼び出し** を発行。

---

## 11. リスクと回避

| リスク | 影響 | 回避策 |
|---|---|---|
| マイグレーション番号衝突 | 並行ブランチ間で同一番号 | Wave 0-A で予約番号表を確定 |
| 共有型不一致 | フロント・バックの API 型がズレる | Wave 0-C を絶対先行 + 11 種の必須型を完全定義 |
| ファイル所有権重複 | 同じファイルを 2 チケットが触る | チケット定義時に **ファイル単位** で所有明示（ディレクトリグロブ禁止） |
| Layer アルゴリズムの精度不足 | 運用に耐えない | 各 Layer の人補正 UI を必須実装 |
| AI 解釈の信頼度低下 | 誤った申請が増える | 信頼度 < 0.7 で確認モーダル強制 |
| **DB マイグレーション rollback 時のデータ喪失** | drop column / JSON 変換後に元データを復元できない | (1) drop 前に **backup table** にコピー (2) **expand-contract** 方式（フィールド追加 → アプリ移行 → 旧フィールド削除を別 deploy に分割） (3) staging で `up → 業務操作 → down → up` を検証 |
| `pending_requests` の二重適用 | 同じ申請が 2 回反映される | applier に冪等性キー（applied_at + version）を持たせる |
| AiInputModal の同時改修 | Wave 5 内のチケット衝突 | W5-FE10 を sequential 先行で拡張ポイント整備 |

---

## 12. 残課題（実装前に決着すべき）

| 項目 | 関連 | 期限 | 状態 |
|---|---|---|---|
| Q1「6 人/日」の枠定義 | W4-BE8 着手前 | Wave 4 開始まで | ✅ MVP 確定（サービス時間枠消費） |
| Q3 ローテーション強度 | W4-BE9 着手前 | Wave 4 開始まで | ✅ MVP 確定（ハイブリッド） |
| Q4（方式）距離計算 | W4-BE8 | Wave 4 開始まで | ✅ MVP 確定（直線距離） |
| Q5 時間粒度 | W3-FE5 | Wave 3 開始まで | ✅ MVP 確定（15 分） |
| 拠点エッジケース（該当なし / 重複） | W1-BE3 | Wave 1 開始まで | ⚠️ 該当なし→警告のみ（フィールド null）/ 重複→マスタ側で防ぐ運用に確定 |
| `staff_weekly_overrides` / `staff_events` 維持判断 | W1-BE2 | Wave 1 開始まで | ⚠️ **要決定**（v1 維持と推定するが要確認） |
| 訪問頻度・訪問週の具体仕様 | 未定（保留） | 後追いで決定可 | ⚠️ 設計書に保留として記載 |

---

## 13. ロールバック戦略

### 13.1 各 Wave 単位

- 各 Wave 終了時に `release tag v2-wave{N}` を発行
- 問題発生時は前 Wave のタグへ revert

### 13.2 DB マイグレーション

- 全マイグレーションは `down` も実装必須
- 本番適用前に staging で `up → down → up` を検証

---

## 14. 改訂履歴

| 日付 | バージョン | 内容 |
|---|---|---|
| 2026-05-05 | v0.1 | ドラフト起稿。Wave 0〜6 構成、12 チケットの並行実行マトリクス、ブランチ戦略 |
| 2026-05-05 | v0.2 | Codex レビュー反映。(1) Wave 0-C を 11 種の必須型へ強化 (2) W2-BE4 に visit 関連を追加 (3) W2-BE5 に PendingRequestApplier を追加 (4) W3 に schedule/fix BE チケット新設 (5) Wave 5 を FE10 sequential 先行 → FE11/12/13 並行に変更し AiInputModal 衝突解消 (6) ファイル単位所有に統一（ディレクトリグロブ禁止） (7) Layer 2/3 acceptance を fixture 名・seed 付きで検証可能化 (8) 並行マトリクスを正しいチケット数（30 件合計）に訂正 (9) DB rollback リスクを expand-contract で補強 (10) MVP 前提（Q1/Q3/Q4/Q5）を残課題で確定済みとマーク |
| 2026-05-08 | v0.3 | Wave 15 実装完了。スケジュール大改修: course_templates / acceptance_calendar (Alembic 0019+0020) / place-and-fix endpoint / ScheduleUnifiedView / 取込スクリプト 2 本 |
| 2026-05-08 | v0.4 | Wave 16 実装完了。スタッフ別テーブル UI (StaffWeekTablePanel) / Layer 3 曜日別ローテーション + 固定制約 / generate-and-assign endpoint / M course_template seed (Alembic 0022) / manager_course_sync |
