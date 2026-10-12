-- 毎週の型（固定訪問の枠 mode=normal）と、その週の予定（削除・取消を除く）の食い違い。曜日 × 開始時刻で数を比べる。only_master＝型にあるが週に無い／only_week＝週にあるが型に無い（今週だけの追加・時刻ずらし・特別訪問など）。稼働中の方のみ | params: monday
with m as (
  select f.patient_id, f.weekday, f.start_time, count(*) as n
  from patient_fixed_visits f join patients p on p.id = f.patient_id
  where f.mode = 'normal' and p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
  group by 1, 2, 3
), w as (
  select v.patient_id, (v.visit_date - :'monday'::date) as weekday, v.start_time, count(*) as n
  from visits v join patients p on p.id = v.patient_id
  where v.deleted_at is null and v.status <> 'cancelled'
    and v.visit_date between :'monday'::date and :'monday'::date + 6
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
       (:'monday'::date + d.weekday) as visit_date,
       to_char(d.start_time, 'HH24:MI') as start_jst, d.master_slots, d.week_visits,
       case when d.week_visits = 0 then 'only_master' when d.master_slots = 0 then 'only_week' else 'count_differs' end as diff_kind
from d join patients p on p.id = d.patient_id
order by p.code, d.weekday, d.start_time
