-- 希望の週回数（訪問する週の回数）と固定訪問の枠の数（曜日 × 時刻の異なる枠。2 名体制の 2 枠は 1 つに数える）が違う稼働中の方（例: 週 3 回希望・枠 2 つ＝毎週プールに残る）。隔週は A の週（毎回＋A の枠）と B の週（毎回＋B の枠）をそれぞれ比べる（B の枠が無ければ B の週・全部 B なら A の週はお休みの週で比べない）。1 名の方は slot 0 の枠だけ数える。月の第何週は訪問する週に全部の枠が当たるので毎週と同じに比べる。issue=count_differs／missing_anchor（隔週なのに起点の週が無い＝毎週作られる）／missing_weeks（月の第何週なのに第何週が空＝毎週作られる） | params: (なし)
with f as (
  select f.patient_id,
         count(distinct (f.weekday, f.start_time)) as slots,
         count(distinct (f.weekday, f.start_time)) filter (where coalesce(f.visit_week, 'every') <> 'B') as slots_a_week,
         count(distinct (f.weekday, f.start_time)) filter (where coalesce(f.visit_week, 'every') <> 'A') as slots_b_week,
         count(*) filter (where f.visit_week = 'B') as b_marks
  from patient_fixed_visits f join patients pp on pp.id = f.patient_id
  -- 1 名の方は slot 0 だけ数える（週の生成は 1 名の方の slot 1 を使わない。backend pfv_slots と同じ）
  where f.mode = 'normal' and (pp.requires_multiple_staff or f.slot_index = 0)
  group by 1
), p2 as (
  select p.*, coalesce(f.slots, 0) as fixed_slots, coalesce(f.slots_a_week, 0) as slots_a_week,
         coalesce(f.slots_b_week, 0) as slots_b_week, coalesce(f.b_marks, 0) as b_marks,
         case when p.weekly_pattern->>'visit_frequency' in ('biweekly', 'monthly')
              then p.weekly_pattern->>'visit_frequency' else 'every' end as freq,
         coalesce(extract(isodow from ad.anchor_d) = 1, false) as anchor_ok,
         coalesce(p.weekly_pattern->>'visit_weeks', '') ~ '[1-5]' as weeks_ok
  from patients p left join f on f.patient_id = p.id
  -- 起点の週: YYYY-MM-DD で、ありうる日付だけ date にする（2026-02-30 などは無いのと同じ。クエリ全体を止めない）
  cross join lateral (
    select case when a.s ~ '^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$' and a.s !~ '^0000'
                then case when substr(a.s, 9, 2)::int <= extract(day from make_date(substr(a.s, 1, 4)::int, substr(a.s, 6, 2)::int, 1)
                                                                       + interval '1 month - 1 day')::int
                          then a.s::date end end as anchor_d
    from (select p.weekly_pattern->>'biweekly_anchor' as s) a
  ) ad
  where p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
), judged as (
  select p2.*,
         case when p2.freq = 'biweekly' and not p2.anchor_ok then 'missing_anchor'
              when p2.freq = 'monthly' and not p2.weeks_ok then 'missing_weeks'
              when coalesce(p2.weekly_pattern->>'frequency_per_week', '') !~ '^[0-9]+$' then null
              when p2.freq = 'biweekly'
                -- 枠が全部 B の方は A の週がお休みの週なので、A の週は比べない
                then case when ((p2.slots_a_week > 0 or p2.fixed_slots = 0)
                                and (p2.weekly_pattern->>'frequency_per_week')::int <> p2.slots_a_week)
                            or (p2.b_marks > 0 and (p2.weekly_pattern->>'frequency_per_week')::int <> p2.slots_b_week)
                          then 'count_differs' end
              when (p2.weekly_pattern->>'frequency_per_week')::int <> p2.fixed_slots then 'count_differs'
          end as issue
  from p2
)
select j.code as patient_code, j.name as patient_name, o.short_label as office,
       j.weekly_pattern->>'visit_frequency' as pref_frequency,
       j.weekly_pattern->>'biweekly_anchor' as biweekly_anchor,
       j.weekly_pattern->>'visit_weeks' as visit_weeks,
       j.weekly_pattern->>'frequency_per_week' as pref_per_week,
       j.fixed_slots, j.slots_a_week, j.slots_b_week,
       j.weekly_pattern->'preferred_weekdays' as pref_weekdays,
       j.weekly_pattern->'ng_weekdays' as ng_weekdays,
       j.freq <> 'every' as not_weekly,
       j.issue
from judged j
left join offices o on o.id = j.primary_office_id
where j.issue is not null
order by j.issue, j.code
