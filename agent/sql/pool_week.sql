-- 週の保留プールの近似。kind=special: 特別訪問週間の○で未配置（置いた訪問が消えていれば未配置）／kind=short: その週の希望回数（隔週・月の第何週はお休みの週 0・訪問する週は min(週の回数, その週に当たる枠の曜日数)）よりその週の訪問（削除以外・取消も数える＝画面と同じ）が少ない稼働中の方／kind=off_week: 隔週・月の第何週でこの週はお休みの方（足りないに数えない）。note=frequency_status（missing_anchor/missing_weeks は毎週として数える）・NG 曜日。画面は保存せず数えて出すので最終確認は画面か API | params: monday
with wk as (
  select date_trunc('week', :'monday'::date)::date as mon,
         extract(isoyear from :'monday'::date)::int as iy, extract(week from :'monday'::date)::int as iw
), fq as (
  -- 訪問頻度の判定（backend app/services/scheduling/visit_frequency.py と同じ決まり）
  select p.id as patient_id,
         case when p.weekly_pattern->>'visit_frequency' in ('biweekly', 'monthly')
              then p.weekly_pattern->>'visit_frequency' else 'every' end as freq,
         case when extract(isodow from ad.anchor_d) = 1 then ad.anchor_d end as anchor,
         -- 枠の無い月の第何週の方の曜日: entries の曜日、無ければ preferred_weekdays、無ければ月曜（backend pattern_weekdays と同じ）
         coalesce(
           nullif(array(select distinct x.d from (
                    select case when jsonb_typeof(e->'weekday') = 'number' and e->>'weekday' ~ '^[0-6]$' then (e->>'weekday')::int
                                when jsonb_typeof(e->'weekday') = 'string'
                                  then array_position(array['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], e->>'weekday') - 1 end as d
                    from jsonb_array_elements(case when jsonb_typeof(p.weekly_pattern->'entries') = 'array'
                                                   then p.weekly_pattern->'entries' else '[]'::jsonb end) e
                    where jsonb_typeof(e) = 'object') x where x.d is not null), '{}'),
           nullif(array(select distinct x.d from (
                    select case when jsonb_typeof(e) = 'number' and e #>> '{}' ~ '^[0-6]$' then (e #>> '{}')::int
                                when jsonb_typeof(e) = 'string'
                                  then array_position(array['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], e #>> '{}') - 1 end as d
                    from jsonb_array_elements(case when jsonb_typeof(p.weekly_pattern->'preferred_weekdays') = 'array'
                                                   then p.weekly_pattern->'preferred_weekdays' else '[]'::jsonb end) e) x
                  where x.d is not null), '{}'),
           array[0]) as pattern_days,
         array(select x::int from regexp_split_to_table(
                 translate(coalesce(p.weekly_pattern->>'visit_weeks', ''), '１２３４５，、　', '12345,,,'), '[,;\s]+') x
               where x ~ '^[1-5]$') as nth_weeks,
         exists (select 1 from jsonb_array_elements(case when jsonb_typeof(p.special_week_active::jsonb) = 'array'
                                                         then p.special_week_active::jsonb else '[]'::jsonb end) e
                 where e->>'iso_year' = wk.iy::text and e->>'iso_week' = wk.iw::text) as special_on,
         array(select (array['月','火','水','木','金','土','日'])[array_position(array['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], d) ]
               from jsonb_array_elements_text(case when jsonb_typeof(p.weekly_pattern->'ng_weekdays') = 'array'
                                                   then p.weekly_pattern->'ng_weekdays' else '[]'::jsonb end) d
               where d in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')) as ng_ja
  from patients p
  -- 起点の週: YYYY-MM-DD で、ありうる日付だけ date にする（2026-02-30 などは無いのと同じ。クエリ全体を止めない）
  cross join lateral (
    select case when a.s ~ '^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$' and a.s !~ '^0000'
                then case when substr(a.s, 9, 2)::int <= extract(day from make_date(substr(a.s, 1, 4)::int, substr(a.s, 6, 2)::int, 1)
                                                                       + interval '1 month - 1 day')::int
                          then a.s::date end end as anchor_d
    from (select p.weekly_pattern->>'biweekly_anchor' as s) a
  ) ad, wk
), fs as (
  select fq.*,
         case when fq.freq = 'biweekly' and fq.anchor is null then 'missing_anchor'
              when fq.freq = 'monthly' and cardinality(fq.nth_weeks) = 0 then 'missing_weeks'
              else 'ok' end as status
  from fq
), fa as (
  select fs.*, (fs.freq <> 'every' and fs.status = 'ok' and not fs.special_on) as thinned from fs
), slot as (
  -- 通常の枠の曜日ごとに、この週に訪問するか（1 名の方は slot 0 だけ数える）
  select f.patient_id, f.weekday,
         bool_or(case when not fa.thinned then true
                      when fa.freq = 'biweekly'
                        then ((((wk.mon - fa.anchor) / 7) % 2) = 0) = (coalesce(f.visit_week, 'every') <> 'B')
                      else ((extract(day from wk.mon + f.weekday)::int - 1) / 7 + 1) = any(fa.nth_weeks) end) as visits
  from patient_fixed_visits f
  join patients p on p.id = f.patient_id
  join fa on fa.patient_id = f.patient_id, wk
  where f.mode = 'normal' and (p.requires_multiple_staff or f.slot_index = 0)
  group by 1, 2
), want as (
  select p.id as patient_id, fa.thinned, fa.status, fa.ng_ja,
         (p.weekly_pattern->>'frequency_per_week')::int as base,
         (select count(*) from slot s where s.patient_id = p.id) as slot_days,
         (select count(*) from slot s where s.patient_id = p.id and s.visits) as visit_days,
         -- 枠の無い方: 隔週は A の週だけ・月の第何週は希望の曜日のどれかの日付が第 N 週に当たるか（backend is_patient_visit_week と同じ）
         case when fa.freq = 'biweekly' then (((wk.mon - fa.anchor) / 7) % 2) = 0
              else exists (select 1 from unnest(fa.pattern_days) d
                           where ((extract(day from wk.mon + d)::int - 1) / 7 + 1) = any(fa.nth_weeks)) end as visit_week_no_slot
  from patients p join fa on fa.patient_id = p.id, wk
  where p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
    and (p.weekly_pattern->>'frequency_per_week') ~ '^[0-9]+$'
), wanted as (
  select w.patient_id, w.thinned, w.status, w.ng_ja,
         case when not w.thinned then w.base
              when w.slot_days > 0 then least(w.base, w.visit_days)
              when w.visit_week_no_slot then w.base else 0 end as wanted
  from want w
), special as (
  select 'special' as kind, p.code as patient_code, p.name as patient_name,
         (array['月','火','水','木','金','土','日'])[m.weekday + 1] as weekday_ja,
         null::int as wanted_per_week, null::bigint as visits_in_week, m.status as mark_status, null::text as note
  from special_visit_marks m
  join special_visit_periods per on per.id = m.period_id
  join patients p on p.id = m.patient_id, wk
  where m.iso_year = wk.iy and m.iso_week = wk.iw and m.kind = 'extra' and per.status = 'active'
    and p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
    and (m.status = 'pool' or (m.status = 'placed' and not exists (
          select 1 from visits w where w.id = m.placed_visit_id and w.deleted_at is null and w.status <> 'cancelled')))
), counted as (
  select p.code, p.name, wd.wanted, wd.thinned, wd.status, wd.ng_ja,
         (select count(distinct coalesce(v.visit_group_id, v.id)) from visits v
            where v.patient_id = p.id and v.deleted_at is null
              and v.visit_date between wk.mon and wk.mon + 6) as n
  from wanted wd join patients p on p.id = wd.patient_id, wk
), short as (
  select 'short', c.code, c.name, null::text, c.wanted, c.n, null::text,
         nullif(concat_ws(' / ', case when c.status <> 'ok' then c.status end,
                          case when cardinality(c.ng_ja) > 0 then 'NG 曜日 ' || array_to_string(c.ng_ja, '・') end), '')
  from counted c where c.wanted > c.n
), off_week as (
  select 'off_week', c.code, c.name, null::text, c.wanted, c.n, null::text,
         '隔週・月の第何週のお休みの週'
  from counted c where c.thinned and c.wanted = 0
)
select * from special
union all
select * from short
union all
select * from off_week
order by kind, patient_code
