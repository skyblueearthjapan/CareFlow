# W3-C. 患者マスタ UI 拡張

**実装 commit**: `f7e43f6` (2026-05-05)
**ドメイン**: D3 (Screens) / Phase 2

## 概要

Wave 3-A で追加した患者 schema 列を frontend マスタ UI に露出させる。
さらに、Wave 1〜2 で textarea ベースだった weekly_pattern を構造化エディタ
(7 曜日チェックボックス + 時刻 picker + service_minutes + time_type) に
置き換え、入力品質と読みやすさを大幅に改善する。

## 実装範囲

- **patient zod schema 拡張** (`frontend/lib/schemas/patient.ts`):
  `area`, `ng_staff_ids`, `preferred_staff_ids`, `specified_type`,
  `continuous_request` の追加 + 構造化 `WeeklyPattern` 型 + `coerceWeeklyPattern`
- **PatientForm.tsx**: 「訪問条件」セクションを拡張
  - area / specified_type の select
  - NG スタッフ / 優先スタッフの Combobox (W4-E で本格化)
  - continuous_request switch
- **`WeeklyPatternEditor.tsx` (新規 313 行)**:
  - 7 曜日チェックボックス + 時間帯 picker (start / end)
  - service_minutes 数値入力
  - time_type (固定 / 午前 / 午後 / 終日) ラジオ
- **詳細ページ `patients/[id]/page.tsx`**: WeeklyPatternView で JSONB を
  整形表示

## 関連 commit

- `f7e43f6` feat(W3-C): 本体
- `1414e03` fix(W1-A-revise2): PatientFormValues の null clearing 整理
  (W3-C の前提)
- `efa204f` 患者マスタ未送信フィールド修正 + 週間訪問パターン time_type
  対応 (post-W3-C 微修正、別リポ `careflow-scheduler` 側のミラー修正)

## テスト被覆

- frontend は Playwright E2E (W5-C `e2e-patient-master.spec.ts`) でフォーム
  CRUD 通り抜けを確認
- backend 側 contract test は `test_patients.py` で実施 (W3-A で追加した
  列が往復することを確認)

## 残課題 / 次 Wave 移譲

- WeeklyPatternEditor のキーボード操作 (Tab 順 / Enter 確定) は W4-E
  zodResolver 有効化と合わせて a11y チェック済
- AI 入力からの patient_create 流入 (Gemini → 構造化 → Form pre-fill) は
  W4-B で実装済
