-- 職員 × 日の担当件数（主担当→コース担当で補う。カイポケ取込の副担当も 1 件ずつ。同行は含めない。staff は職員コードか氏名の一部・空なら全員） | params: date_from, date_to, staff
with vv as (
  select v.id, v.visit_date, v.start_time, v.end_time,
         coalesce(v.primary_staff_id,
                  case when not v.manual_staff_override and c.deleted_at is null
                       then c.assigned_staff_id end) as owner_staff_id,
         v.secondary_staff_id
  from visits v
  join patients p on p.id = v.patient_id
  left join courses c on c.id = v.course_id
  where v.deleted_at is null and v.status <> 'cancelled'
    and p.name not like '【検証】%'
    and v.visit_date between :'date_from'::date and :'date_to'::date
), per as (
  select id, visit_date, start_time, end_time, owner_staff_id as staff_id, 'main' as role
  from vv where owner_staff_id is not null
  union all
  select id, visit_date, start_time, end_time, secondary_staff_id, 'secondary'
  from vv where secondary_staff_id is not null and secondary_staff_id is distinct from owner_staff_id
)
select s.code as staff_code, s.name as staff_name, per.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from per.visit_date)::int] as weekday_ja,
       count(*) as visits,
       count(*) filter (where per.role = 'secondary') as as_secondary,
       to_char(min(per.start_time), 'HH24:MI') as first_start,
       to_char(max(per.end_time), 'HH24:MI') as last_end,
       sum(extract(epoch from (per.end_time - per.start_time)) / 60)::int as visit_minutes
from per join staff s on s.id = per.staff_id
where (:'staff' = '' or s.code = :'staff'
       or replace(replace(s.name, ' ', ''), '　', '') like '%' || replace(replace(:'staff', ' ', ''), '　', '') || '%')
group by s.code, s.name, per.visit_date
order by s.code, per.visit_date
