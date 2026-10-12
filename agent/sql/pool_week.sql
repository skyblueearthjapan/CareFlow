-- 週の保留プールの近似。kind=special: 特別訪問週間の○で未配置（置いた訪問が消えていれば未配置）／kind=short: 希望の週回数（毎週の方のみ）よりその週の訪問（削除以外・取消も数える＝画面と同じ）が少ない稼働中の方。画面は保存せず数えて出すので最終確認は画面か API | params: monday
with wk as (
  select :'monday'::date as mon,
         extract(isoyear from :'monday'::date)::int as iy, extract(week from :'monday'::date)::int as iw
), special as (
  select 'special' as kind, p.code as patient_code, p.name as patient_name,
         (array['月','火','水','木','金','土','日'])[m.weekday + 1] as weekday_ja,
         null::int as wanted_per_week, null::bigint as visits_in_week, m.status as mark_status
  from special_visit_marks m
  join special_visit_periods per on per.id = m.period_id
  join patients p on p.id = m.patient_id, wk
  where m.iso_year = wk.iy and m.iso_week = wk.iw and m.kind = 'extra' and per.status = 'active'
    and p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
    and (m.status = 'pool' or (m.status = 'placed' and not exists (
          select 1 from visits w where w.id = m.placed_visit_id and w.deleted_at is null and w.status <> 'cancelled')))
), short as (
  select 'short' as kind, p.code, p.name, null::text,
         (p.weekly_pattern->>'frequency_per_week')::int,
         (select count(distinct coalesce(v.visit_group_id, v.id)) from visits v
            where v.patient_id = p.id and v.deleted_at is null
              and v.visit_date between wk.mon and wk.mon + 6),
         null::text
  from patients p, wk
  where p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
    and coalesce(p.weekly_pattern->>'visit_frequency', 'every') = 'every'
    and (p.weekly_pattern->>'frequency_per_week') ~ '^[0-9]+$'
    and (p.weekly_pattern->>'frequency_per_week')::int > (
          select count(distinct coalesce(v.visit_group_id, v.id)) from visits v
          where v.patient_id = p.id and v.deleted_at is null
            and v.visit_date between wk.mon and wk.mon + 6)
)
select * from special
union all
select * from short
order by kind, patient_code
