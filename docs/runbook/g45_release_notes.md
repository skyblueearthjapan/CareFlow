# Phase G-45 Release Notes (= 拠点稼働曜日 + 応援運用)

## 概要

Phase G-45 で導入された変更:

1. `offices.operating_weekdays` (JSONB int 配列) 追加
   (= 拠点ごとに営業曜日を明示. default は月-土 = `[0,1,2,3,4,5]`).
2. パイプライン (Stage 1 / `run_v2_pipeline` / `reset_visits_to_fixed`) で
   拠点休業日には visit を生成せず `office_closed` warning を emit.
3. `StaffInfo.effective_office_for_weekday` で primary 休業日に secondary
   office へ「応援転入」する staff_count 算入を実装 (= H6 制約専用).

## 既知の挙動 (= 一時的な warning 増加)

Phase G-45 適用後、 「cross-office で過去に発行された Visit
(= `v.office_id != patient.primary_office_id`)」 が存在する patient で、
patient の primary_office が当該曜日に休業日の場合、 `office_closed` warning が
一時的に発生します。

これは **pre-existing なデータ問題の表面化** であり、 G-45 の実装バグではない:

- これまでは「拠点に休業日」 という概念自体が無かったため、 cross-office で
  patient.primary_office が休業日でも visit がそのまま出ていた.
- G-45 で「拠点休業日」 を導入したことで、 patient.primary_office に紐付く
  曜日チェックが入り、 過去に別 office へ振られた visit が
  「primary_office 休業日に visit がある」 として warning 対象になる.

### 解消方法

個別 patient のデータ整理で解消します:

1. `patients.primary_office_id` の見直し
   (= 実際の主担当 office に揃える).
2. または `patient_fixed_visits.sub_office_id` を明示
   (= 「primary 休業日は secondary に振る」 ことを明示する).

WARN 一覧は WeeklyAllocationView の warning panel で
`type == "office_closed"` でフィルタ可能.

## scope 限定: distance への波及は別 Phase

`StaffInfo.effective_office_for_weekday` は **`count_active_staff_per_weekday`
の応援 staff_count 算入専用** として導入されました.

Hungarian assignment の距離計算 (`Layer3Assignment._distance_km`) は
**本 API を参照せず**、 引き続き `staff.primary_office_lat` /
`primary_office_lng` を使用します.

理由: distance ベースのコスト関数への波及 (= secondary 拠点座標で距離を再計算
する) は副作用範囲が大きく、 G-45 の応援カウント修正と切り離すべきと判断.
本変更は Phase G-46 以降で別途設計予定.

## Migration / Rollback

- Migration: `backend/alembic/versions/0038_office_operating_weekdays.py`.
- **注意**: downgrade は `operating_weekdays` カラムを drop するため、 本番で
  巻き戻すと operating_weekdays データは復元できません. 復元するには 0038 を
  再実行し UPDATE を手動で適用してください.
