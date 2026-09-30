-- QR 打刻履歴の月次抽出 (読み取り専用)。期間は下の 2 箇所の日付を書き換える。
-- 出力は JSON 配列 1 本。build_report.py の入力。
--
-- 行の範囲: 有効な訪問 (未削除・未取消) ＋ 打刻のある訪問 (取消・削除されていても載せる。
-- 取込で取り消された予定に打刻が残る例があるため、訪問の事実を落とさない)。
-- 検証用の架空患者 (【検証】…) は除く。時刻は JST。
SET default_transaction_read_only = on;
WITH arr AS (
  SELECT DISTINCT ON (visit_id) visit_id, staff_id, scanned_at, device_time, match_status, checkin_source, distance_m
  FROM visit_checkins WHERE kind='arrival' ORDER BY visit_id, scanned_at DESC, id DESC),
dep AS (
  SELECT DISTINCT ON (visit_id) visit_id, staff_id, scanned_at, device_time, match_status, checkin_source
  FROM visit_checkins WHERE kind='departure' ORDER BY visit_id, scanned_at DESC, id DESC),
nos AS (SELECT DISTINCT visit_id FROM visit_checkins WHERE kind='no_show')
SELECT coalesce(json_agg(r ORDER BY r.visit_date, r.start_time, r.patient), '[]'::json) FROM (
  SELECT v.id, v.visit_date, to_char(v.start_time,'HH24:MI') AS start_time, to_char(v.end_time,'HH24:MI') AS end_time,
    v.status, v.is_unplanned, (v.deleted_at IS NOT NULL) AS deleted,
    p.name AS patient, p.code AS patient_code, o.name AS office,
    ps.name AS planned_staff, ss.name AS secondary_staff,
    sa.name AS arr_staff, sd.name AS dep_staff,
    to_char(arr.scanned_at AT TIME ZONE 'Asia/Tokyo','YYYY-MM-DD HH24:MI:SS') AS arr_at,
    to_char(dep.scanned_at AT TIME ZONE 'Asia/Tokyo','YYYY-MM-DD HH24:MI:SS') AS dep_at,
    to_char(arr.device_time AT TIME ZONE 'Asia/Tokyo','YYYY-MM-DD HH24:MI:SS') AS arr_device,
    to_char(dep.device_time AT TIME ZONE 'Asia/Tokyo','YYYY-MM-DD HH24:MI:SS') AS dep_device,
    arr.match_status AS arr_match, arr.checkin_source AS arr_source, dep.checkin_source AS dep_source, arr.distance_m,
    (nos.visit_id IS NOT NULL) AS no_show,
    (arr.staff_id IS NOT NULL AND arr.staff_id IS DISTINCT FROM v.primary_staff_id
       AND arr.staff_id IS DISTINCT FROM v.secondary_staff_id AND arr.staff_id IS DISTINCT FROM v.mentor_staff_id
       AND NOT EXISTS (SELECT 1 FROM visit_staff_assignments a WHERE a.visit_id=v.id AND a.staff_id=arr.staff_id)
       AND NOT EXISTS (SELECT 1 FROM accompaniments a WHERE a.visit_id=v.id AND a.accompanying_staff_id=arr.staff_id)) AS substitute
  FROM visits v
  JOIN patients p ON p.id=v.patient_id
  LEFT JOIN offices o ON o.id=p.primary_office_id
  LEFT JOIN staff ps ON ps.id=v.primary_staff_id
  LEFT JOIN staff ss ON ss.id=v.secondary_staff_id
  LEFT JOIN arr ON arr.visit_id=v.id LEFT JOIN staff sa ON sa.id=arr.staff_id
  LEFT JOIN dep ON dep.visit_id=v.id LEFT JOIN staff sd ON sd.id=dep.staff_id
  LEFT JOIN nos ON nos.visit_id=v.id
  WHERE v.visit_date BETWEEN '2026-09-01' AND '2026-09-30'
    AND p.name NOT LIKE '【検証】%'
    AND ( (v.deleted_at IS NULL AND v.status <> 'cancelled') OR arr.visit_id IS NOT NULL OR dep.visit_id IS NOT NULL )
) r;
