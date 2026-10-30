-- 毎週の型（固定訪問の枠 mode=normal・その週に訪問する枠だけ＝隔週・月の第何週のお休みの週の枠は除く）と、その週の予定（削除・取消を除く）の食い違い。曜日 × 開始時刻で 1 回 1 件に数えて比べる（2 名体制は枠 2 つ＝予定 1 行 secondary_staff_id なので、どちらも 1 回）。only_master＝型にあるが週に無い／only_week＝週にあるが型に無い（今週だけの追加・時刻ずらし・特別訪問・お休みの週の追加など）。frequency はその方の訪問頻度。稼働中の方のみ | params: monday
with wk as (
  select date_trunc('week', :'monday'::date)::date as mon,
         extract(isoyear from :'monday'::date)::int as iy, extract(week from :'monday'::date)::int as iw
), fq as (
  -- 訪問頻度の判定（backend app/services/scheduling/visit_frequency.py と同じ決まり）
  select p.id as patient_id,
         case when p.weekly_pattern->>'visit_frequency' in ('biweekly', 'monthly')
              then p.weekly_pattern->>'visit_frequency' else 'every' end as freq,
         case when p.weekly_pattern->>'biweekly_anchor' ~ '^\d{4}-\d{2}-\d{2}$'
               and extract(isodow from (p.weekly_pattern->>'biweekly_anchor')::date) = 1
              then (p.weekly_pattern->>'biweekly_anchor')::date end as anchor,
         array(select x::int from regexp_split_to_table(
                 translate(coalesce(p.weekly_pattern->>'visit_weeks', ''), '１２３４５，、　', '12345,,,'), '[,;\s]+') x
               where x ~ '^[1-5]$') as nth_weeks,
         exists (select 1 from jsonb_array_elements(case when jsonb_typeof(p.special_week_active::jsonb) = 'array'
                                                         then p.special_week_active::jsonb else '[]'::jsonb end) e
                 where e->>'iso_year' = wk.iy::text and e->>'iso_week' = wk.iw::text) as special_on
  from patients p, wk
  where p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
), fa as (
  select fq.*,
         (fq.freq = 'biweekly' and fq.anchor is not null
          or fq.freq = 'monthly' and cardinality(fq.nth_weeks) > 0) and not fq.special_on as thinned
  from fq
), m as (
  select f.patient_id, f.weekday, f.start_time, 1 as n
  from patient_fixed_visits f join fa on fa.patient_id = f.patient_id, wk
  where f.mode = 'normal'
    and (not fa.thinned
         or (fa.freq = 'biweekly'
             and ((((wk.mon - fa.anchor) / 7) % 2) = 0) = (coalesce(f.visit_week, 'every') <> 'B'))
         or (fa.freq = 'monthly'
             and ((extract(day from wk.mon + f.weekday)::int - 1) / 7 + 1) = any(fa.nth_weeks)))
  group by 1, 2, 3
), w as (
  select v.patient_id, (v.visit_date - wk.mon) as weekday, v.start_time,
         count(distinct coalesce(v.visit_group_id, v.id)) as n
  from visits v join patients p on p.id = v.patient_id, wk
  where v.deleted_at is null and v.status <> 'cancelled'
    and v.visit_date between wk.mon and wk.mon + 6
    and p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
  group by 1, 2, 3
), d as (
  select coalesce(m.patient_id, w.patient_id) as patient_id, coalesce(m.weekday, w.weekday) as weekday,
         coalesce(m.start_time, w.start_time) as start_time,
         coalesce(m.n, 0) as master_slots, coalesce(w.n, 0) as week_visits
  from m full join w on w.patient_id = m.patient_id and w.weekday = m.weekday and w.start_time = m.start_time
  where coalesce(m.n, 0) <> coalesce(w.n, 0)
)
select p.code as patient_code, p.name as patient_name,
       (array['月','火','水','木','金','土','日'])[d.weekday + 1] as weekday_ja,
       (wk.mon + d.weekday) as visit_date,
       to_char(d.start_time, 'HH24:MI') as start_jst, d.master_slots, d.week_visits,
       case when d.week_visits = 0 then 'only_master' when d.master_slots = 0 then 'only_week' else 'count_differs' end as diff_kind,
       coalesce(fa.freq, 'every') as frequency
from d join patients p on p.id = d.patient_id
left join fa on fa.patient_id = d.patient_id, wk
order by p.code, d.weekday, d.start_time
