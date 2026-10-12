-- 青ピン廃止: 本番の visits.week_pinned=true を false に戻す (未実行・director がリリース時に実行)
-- 設計: docs/plans/blue-pin/blue-pin-abolish-design-2026-10-12.md BP-1
-- 手順: pg_dump を取る -> 1) 件数確認 -> 2) トランザクションで UPDATE -> 件数が 1) と合えば COMMIT
-- 列 visits.week_pinned は 1 リリース残す (コードは読まない)。次のリリースの migration で DROP する。

-- 1) 件数 (読むだけ。B2 調査時点で 126 件)
SELECT count(*) AS week_pinned_true
FROM visits
WHERE week_pinned IS TRUE;

SELECT count(*) AS week_pinned_true_live
FROM visits
WHERE week_pinned IS TRUE AND deleted_at IS NULL;

-- 2) 戻す (トランザクション。1) の week_pinned_true と updated が一致することを確認してから COMMIT)
BEGIN;

-- 念のため戻し用に id を退避
CREATE TEMP TABLE _blue_pin_reset_ids AS
SELECT id FROM visits WHERE week_pinned IS TRUE;

UPDATE visits SET week_pinned = false WHERE week_pinned IS TRUE;

SELECT count(*) AS remaining_true FROM visits WHERE week_pinned IS TRUE;  -- 0 であること
SELECT count(*) AS updated FROM _blue_pin_reset_ids;                       -- 1) と同じ件数

-- 問題なければ:
COMMIT;
-- 問題があれば (COMMIT の前に):
-- ROLLBACK;

-- 3) COMMIT 後に元へ戻したくなったとき (同じセッションの一時表が消えているので id リストを残している場合のみ):
--    リスト: SELECT id FROM _blue_pin_reset_ids;  (COMMIT 前に psql で控えること)
--    UPDATE visits SET week_pinned = true WHERE id IN (<控えた id>);
