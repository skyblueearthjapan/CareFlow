-- 保存してあるカイポケ現況 CSV の鮮度（区分 plan/actual × 月 × 週ごとの最新 fetched_at・日本時間・行数）。●未送信・突合はこの時点の写しと比べる。中身（csv_text）は出さない | params: (なし)
select distinct on (s.division, s.month, s.week_start)
       s.division, s.month, s.week_start,
       to_char(s.fetched_at at time zone 'Asia/Tokyo', 'YYYY-MM-DD HH24:MI') as fetched_at_jst,
       s.row_count, s.source_op, o.short_label as office
from kaipoke_csv_snapshots s
left join offices o on o.id = s.office_id
order by s.division, s.month, s.week_start, s.fetched_at desc
