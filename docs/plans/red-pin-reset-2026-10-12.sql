-- 赤ピン（完全固定）を一度ゼロに戻す 1 回きりの作業（K-4 / Q-3）
-- 設計: docs/plans/red-pin-simplify-design.md §4
-- 実行: リリースと同時に、ディレクターが VPS の psql で実施する。リポジトリ側では実行しない。
-- 手順: (a) 読むだけ → 結果を docs/reports/ に保存 → pg_dump → (b) → 必要なら (c)
-- 注意: 通常モード (mode='normal') の固定訪問の枠だけが対象。特別週 (special) は触らない。

-- ============================================================
-- (a) 読むだけ: 今の赤（locked / is_pinned）の一覧。戻す材料としてファイルに保存する。
--     psql なら: \copy ( ...この SELECT... ) TO 'red_pins_export.csv' CSV HEADER
-- ============================================================
SELECT
    pfv.id                 AS pfv_id,
    p.id                   AS patient_id,
    p.code                 AS patient_code,
    p.name                 AS patient_name,
    pfv.weekday,
    pfv.slot_index,
    pfv.start_time,
    pfv.duration_min,
    ct.label               AS course_label,
    pfv.course_template_id,
    pfv.sub_office_id,
    pfv.movability,
    pfv.is_pinned,
    pfv.created_at,
    pfv.updated_at
FROM patient_fixed_visits pfv
JOIN patients p ON p.id = pfv.patient_id
LEFT JOIN course_templates ct ON ct.id = pfv.course_template_id
WHERE pfv.mode = 'normal'
  AND (pfv.movability <> 'unknown' OR pfv.is_pinned)
ORDER BY p.code, pfv.weekday, pfv.slot_index;

-- 件数の内訳（参考）
SELECT movability, is_pinned, COUNT(*) AS n
FROM patient_fixed_visits
WHERE mode = 'normal'
GROUP BY movability, is_pinned
ORDER BY movability, is_pinned;

-- ============================================================
-- (b) 実行: すべての通常モードの枠を「赤でない」にする。
--     トランザクション内で件数を確認してから COMMIT する（想定外なら ROLLBACK）。
-- ============================================================
BEGIN;

-- 事前件数（赤の枠の数）
SELECT COUNT(*) AS red_before
FROM patient_fixed_visits
WHERE mode = 'normal' AND (movability <> 'unknown' OR is_pinned);

-- 戻せるように、(a) の結果とは別に DB 内にも控えを作る（pg_dump 済みでも念のため）
CREATE TABLE IF NOT EXISTS _bak_pfv_red_pin_20261012 AS
SELECT id, patient_id, mode, weekday, slot_index, start_time, movability, is_pinned, now() AS backed_up_at
FROM patient_fixed_visits
WHERE mode = 'normal' AND (movability <> 'unknown' OR is_pinned);

UPDATE patient_fixed_visits
SET movability = 'unknown',
    is_pinned  = false
WHERE mode = 'normal'
  AND (movability <> 'unknown' OR is_pinned);

-- 事後件数（0 になるはず）／控えの件数（事前件数と一致するはず）
SELECT COUNT(*) AS red_after
FROM patient_fixed_visits
WHERE mode = 'normal' AND (movability <> 'unknown' OR is_pinned);
SELECT COUNT(*) AS backed_up FROM _bak_pfv_red_pin_20261012;

-- 想定どおりなら COMMIT、違えば ROLLBACK。
COMMIT;
-- ROLLBACK;

-- ============================================================
-- (c) 戻す（ロールバック）: (b) の控え _bak_pfv_red_pin_20261012 から赤を復元する。
--     (a) の CSV しか無い場合は、CSV の pfv_id 一覧で同じ UPDATE を行う
--     （movability は CSV の値、is_pinned は movability='locked' かどうかに合わせる）。
-- ============================================================
-- BEGIN;
-- UPDATE patient_fixed_visits pfv
-- SET movability = b.movability,
--     is_pinned  = (b.movability = 'locked')
-- FROM _bak_pfv_red_pin_20261012 b
-- WHERE pfv.id = b.id;
-- SELECT COUNT(*) AS restored FROM patient_fixed_visits WHERE mode = 'normal' AND movability <> 'unknown';
-- COMMIT;
-- 後片付け（復元が不要と確定してから）: DROP TABLE _bak_pfv_red_pin_20261012;
