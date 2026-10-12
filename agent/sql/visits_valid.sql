-- 有効な訪問の一覧（削除・取消・【検証】を除く。担当はコース担当で補う。collapse=1 で 2 名体制を 1 回 1 行に畳む。office は拠点名か略称・空なら全拠点） | params: date_from, date_to, office, collapse
with vv as (
  select v.id as visit_id, v.visit_date, v.start_time, v.end_time,
         coalesce(v.visit_group_id, v.id) as group_key,
         v.required_staff_count, v.status, v.source, v.week_pinned, v.is_unplanned,
         p.code as patient_code, p.name as patient_name,
         coalesce(v.primary_staff_id,
                  case when not v.manual_staff_override and c.deleted_at is null
                       then c.assigned_staff_id end) as owner_staff_id,
         v.secondary_staff_id,
         c.code as course_code,
         coalesce(c.office_id, p.primary_office_id) as office_id
  from visits v
  join patients p on p.id = v.patient_id
  left join courses c on c.id = v.course_id
  where v.deleted_at is null and v.status <> 'cancelled'
    and p.name not like '【検証】%'
    and v.visit_date between :'date_from'::date and :'date_to'::date
), picked as (
  select distinct on (case when :'collapse' = '1' then vv.group_key else vv.visit_id end) vv.*
  from vv
  order by (case when :'collapse' = '1' then vv.group_key else vv.visit_id end), vv.visit_id
)
select x.visit_id, x.group_key, x.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from x.visit_date)::int] as weekday_ja,
       to_char(x.start_time, 'HH24:MI') as start_jst, to_char(x.end_time, 'HH24:MI') as end_jst,
       x.patient_code, x.patient_name,
       s.code as staff_code, s.name as staff_name, s2.name as secondary_staff_name,
       x.course_code, o.short_label as office, x.status, x.source,
       x.week_pinned, x.is_unplanned, x.required_staff_count
from picked x
left join staff s on s.id = x.owner_staff_id
left join staff s2 on s2.id = x.secondary_staff_id
left join offices o on o.id = x.office_id
where (:'office' = '' or o.name = :'office' or o.short_label = :'office')
order by x.visit_date, x.start_time, s.code
