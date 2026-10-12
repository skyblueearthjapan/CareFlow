-- 同じ職員の訪問どうしで時間が重なっている組（同じ 2 名体制の組は除く。移動時間は見ない＝重なりだけ。移動込みは実現性チェックの API） | params: date_from, date_to
with vv as (
  select v.id, v.visit_date, v.start_time, v.end_time, v.patient_id, v.visit_group_id,
         coalesce(v.primary_staff_id,
                  case when not v.manual_staff_override and c.deleted_at is null
                       then c.assigned_staff_id end) as staff_id
  from visits v join patients p on p.id = v.patient_id left join courses c on c.id = v.course_id
  where v.deleted_at is null and v.status <> 'cancelled' and p.name not like '【検証】%'
    and v.visit_date between :'date_from'::date and :'date_to'::date
)
select s.code as staff_code, s.name as staff_name, a.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from a.visit_date)::int] as weekday_ja,
       pa.name as patient_a, to_char(a.start_time, 'HH24:MI') || '-' || to_char(a.end_time, 'HH24:MI') as time_a,
       pb.name as patient_b, to_char(b.start_time, 'HH24:MI') || '-' || to_char(b.end_time, 'HH24:MI') as time_b,
       (extract(epoch from (least(a.end_time, b.end_time) - greatest(a.start_time, b.start_time))) / 60)::int as overlap_min,
       a.id as visit_id_a, b.id as visit_id_b
from vv a
join vv b on b.staff_id = a.staff_id and b.visit_date = a.visit_date and a.id < b.id
         and a.start_time < b.end_time and b.start_time < a.end_time
         and (a.visit_group_id is null or a.visit_group_id is distinct from b.visit_group_id)
join staff s on s.id = a.staff_id
join patients pa on pa.id = a.patient_id
join patients pb on pb.id = b.patient_id
order by a.visit_date, s.code, a.start_time
