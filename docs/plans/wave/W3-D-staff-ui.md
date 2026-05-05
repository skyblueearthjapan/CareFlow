# W3-D. スタッフマスタ UI 拡張

**実装 commit**: `9af9e9e` (2026-05-05)
**ドメイン**: D3 (Screens) + D1 (special_weeks API) / Phase 2

## 概要

Wave 3-A で追加したスタッフ schema 列 (home_address / areas / max_per_day
/ skill_level / assignment_volume / note) を frontend マスタ UI に露出さ
せる。あわせて、本コミットで special_weeks 用 backend API + frontend
画面 (一覧 / 詳細 / 新規 / 編集) を一括投入する。

## 実装範囲

- **staff zod schema 拡張** (`frontend/lib/schemas/staff.ts`): 新フィールド +
  enum 定数 + label helpers
- **StaffFormFields.tsx**: 「拠点・能力」セクション新設 (home address /
  areas / skill_level / max_per_day / assignment_volume / note)
- **staff/new + staff/[id]/edit**: EMPTY_FORM / fromStaff / toPayload を
  新フィールドに合わせて更新
- **staff/[id]/page.tsx**: 基本情報セクションに新フィールド表示
- **staff/page.tsx**: 一覧テーブルに「得意エリア / スキル / 最大件数」列追加
- **special_weeks 一式** (本コミットで追加):
  - backend `api/v1/special_weeks.py` (CRUD + items 操作)
  - frontend 4 画面 (一覧 / 詳細 / 新規 / 編集) + `SpecialWeekForm.tsx`

## 関連 commit

- `9af9e9e` feat(W3-D): 本体
- `076946d` feat(staff): スタッフマスタ詳細編集 UI 4 セクション (F5)
- `ad64674` feat(staff): スタッフマスタ詳細編集の 4 リソース API (F5-Backend)
- `b693eb9` fix(staff): OverrideRead/EventRead schema 揃え (F5 hotfix)
- `2a9a9e8`, `8612a02` fix(staff): F5 build エラー修正 + RHF v5 onError 4 引数

## テスト被覆

- backend: `test_staff.py`, `test_staff_shifts.py`, `test_staff_events.py`,
  `test_staff_overrides.py`, `test_special_weeks_api.py`
- frontend: Playwright E2E は staff master 専用 spec を W5-C で追加予定
  (現状は patient_master + week_view + dashboard の 3 spec)

## 残課題 / 次 Wave 移譲

- mentor_id (新人 → 指導者) の Combobox 化は W4-E で `StaffCombobox` 共通
  コンポを介して実装済
- スタッフごとのエリア許容範囲を割当エンジンに反映する仕組みは Wave 6
  以降で検討
