# W3-A. Backend スキーマ拡張

**実装 commit**: `fc999d6` (2026-05-05)
**ドメイン**: D1 (Backend) / Phase 2

## 概要

Wave 1 で導入された 10 テーブル基盤に対し、患者・スタッフのマスタ列を
業務要件に合わせて拡張する。あわせて「特別週パターン」(通常週とは独立に
特定 ISO 週で適用するパターン) を保持するための新規 2 テーブルを追加する。
Wave 3-B (import scripts) / Wave 3-C/D (UI) の前提となる schema 凍結。

## 実装範囲

- **patients テーブル拡張**:
  `area`, `ng_staff_ids` (UUID 配列), `preferred_staff_ids`,
  `specified_type` (enum: 通常 / 指定 / 不可), `continuous_request` (bool)
- **staff テーブル拡張**:
  `home_address`, `home_lat`, `home_lng`, `areas` (text 配列),
  `max_per_day`, `skill_level`, `assignment_volume`, `note`
- **新規テーブル**:
  - `special_weeks` (header: id, patient_id, iso_week, applied_at, note)
  - `special_week_items` (detail: id, special_week_id, weekday, time_start,
    time_end, time_type, service_minutes)
- **alembic 0004_w3_master_extensions**: 上記列追加 + 新 2 テーブル + 適切な
  downgrade

## 関連 commit

- `fc999d6` feat(W3-A): backend schema extension (本体)
- `e873da7` feat(schema): WeeklyPatternSchema 構造化 + special_weeks UNIQUE
  制約 + alembic 0005 (UNIQUE 補強)
- `33005b4` fix(schema): PatientRead を asymmetric 化 (read 時 500 解消)

## テスト被覆

- `test_patients.py`: 新列の往復 (POST/GET) と空入力
- `test_special_weeks_api.py`: header + items の CRUD、UNIQUE 違反 409
- `test_weekly_pattern_validation.py`: WeeklyPatternSchema 構造検証
- alembic up/down はローカル PostgreSQL で手動確認

## 残課題 / 次 Wave 移譲

- ng_staff_ids / preferred_staff_ids の整合性チェック (削除済み staff の
  ID が残るケース) は W4-E で Combobox 化と合わせて整理
- specified_type の業務ルール (割当エンジンへの反映) は W2-A の allocate
  に渡す `_build_inputs` 拡張で実装 (commit `15886a7`)
- 監査列 (created_by / updated_by) は W4-F で audit_logs middleware に
  集約済 (各テーブル個別列は不要と判断)
