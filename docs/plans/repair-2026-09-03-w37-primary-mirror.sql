-- =====================================================================
-- 一度きりのデータ修復: W37 (2026-09-07〜2026-09-13) の
-- visits.primary_staff_id が NULL のまま残った訪問を、コース担当
-- (courses.assigned_staff_id = 表示の正典) へ揃える。
--
-- ★このファイルは「そのまま流す」ものではない。
--   1) 下の 1) プレビュー SELECT だけを実行し、対象行を目視確認する。
--   2) 納得できたら 2) の UPDATE ブロック (コメントアウト済み) を
--      **手で貼り付けて** 実行する。BEGIN 〜 COMMIT 推奨。
--   3) 3) の事後確認で 0 行になったことを確かめる。
--
-- 背景 (2026-09-03):
--   プール一括投入 (POST /schedule/v2/pool-bulk-apply) が
--   reset_visits_to_fixed 経由で courses.assigned_staff_id を書いた際、
--   そのコースに既にあった他患者の visits (source='auto') の
--   primary_staff_id を NULL のまま放置した。
--   盤面はコース担当を表示するのに、カイポケCSV
--   (services/kaipoke/csv_builder.resolve_month_rows) は
--   visits.primary_staff_id しか見ていなかったため 職員名1='-' (担当なし)
--   で週次差分が作られ、カイポケ側の担当が消えた
--   (稲毛A 9/9 の 5 件: 山岡 11:00 / 清水 13:00 / 井川 14:00 /
--    安永 15:30 / 菅原 16:15)。
--
-- コード側は根治済み (このSQLは既存データの後始末のみ):
--   * services/scheduling/course_staff_mirror.mirror_course_staff_to_visits
--     … コース担当を書き換えたら既存 visits へ伝播する
--   * services/kaipoke/csv_builder.resolve_month_rows
--     … primary が NULL なら在籍中のコース担当へフォールバック (安全網)
--
-- 対象条件 (コード側のミラーと同じ絞り込み):
--   visit_date が W37 / primary_staff_id IS NULL /
--   manual_staff_override = false (手動で担当を外した訪問は触らない) /
--   deleted_at IS NULL / status <> 'cancelled' /
--   コースに担当が居て、その担当が **在籍中** (staff.status='active' かつ
--   deleted_at IS NULL — 退職者を書き戻さない)。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1) プレビュー (read-only): 影響を受ける行
-- ---------------------------------------------------------------------
SELECT
    p.name        AS patient_name,
    v.visit_date  AS visit_date,
    v.start_time  AS start_time,
    c.code        AS course_code,
    s.name        AS course_staff_name,
    s.status      AS course_staff_status,
    s.deleted_at  AS course_staff_deleted_at,
    v.status      AS visit_status,
    v.source      AS visit_source,
    v.id          AS visit_id
FROM visits v
JOIN courses c ON c.id = v.course_id
JOIN staff   s ON s.id = c.assigned_staff_id
JOIN patients p ON p.id = v.patient_id
WHERE v.visit_date BETWEEN DATE '2026-09-07' AND DATE '2026-09-13'
  AND v.primary_staff_id IS NULL
  AND v.manual_staff_override = false
  AND v.deleted_at IS NULL
  AND v.status <> 'cancelled'
  AND c.assigned_staff_id IS NOT NULL
  AND s.deleted_at IS NULL
  AND s.status = 'active'
ORDER BY v.visit_date, v.start_time, c.code;

-- ---------------------------------------------------------------------
-- 2) 修復 (書込み): プレビューの結果に納得してから **手で貼り付けて** 実行する
--    (誤爆防止のため、このファイル上では全行コメントアウトしてある)
-- ---------------------------------------------------------------------
-- BEGIN;
--
-- UPDATE visits v
-- SET primary_staff_id = c.assigned_staff_id,
--     updated_at = now()
-- FROM courses c
-- JOIN staff s ON s.id = c.assigned_staff_id
-- WHERE c.id = v.course_id
--   AND v.visit_date BETWEEN DATE '2026-09-07' AND DATE '2026-09-13'
--   AND v.primary_staff_id IS NULL
--   AND v.manual_staff_override = false
--   AND v.deleted_at IS NULL
--   AND v.status <> 'cancelled'
--   AND c.assigned_staff_id IS NOT NULL
--   AND s.deleted_at IS NULL
--   AND s.status = 'active';
--
-- COMMIT;

-- ---------------------------------------------------------------------
-- 3) 事後確認: 0 行になっていること (上のプレビューを再実行しても可)
-- ---------------------------------------------------------------------
-- SELECT count(*)
-- FROM visits v
-- JOIN courses c ON c.id = v.course_id
-- JOIN staff s ON s.id = c.assigned_staff_id
-- WHERE v.visit_date BETWEEN DATE '2026-09-07' AND DATE '2026-09-13'
--   AND v.primary_staff_id IS NULL
--   AND v.manual_staff_override = false
--   AND v.deleted_at IS NULL
--   AND v.status <> 'cancelled'
--   AND s.deleted_at IS NULL
--   AND s.status = 'active';
