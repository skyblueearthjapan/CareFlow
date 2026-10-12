-- 担当が決まっていない訪問（主担当もコース担当も無い＝本当の担当なし。削除・取消・【検証】を除く） | params: date_from, date_to
select v.id as visit_id, v.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from v.visit_date)::int] as weekday_ja,
       to_char(v.start_time, 'HH24:MI') as start_jst, to_char(v.end_time, 'HH24:MI') as end_jst,
       p.code as patient_code, p.name as patient_name, c.code as course_code,
       o.short_label as office, v.source, v.manual_staff_override, v.status
from visits v
join patients p on p.id = v.patient_id
left join courses c on c.id = v.course_id
left join offices o on o.id = coalesce(c.office_id, p.primary_office_id)
where v.deleted_at is null and v.status <> 'cancelled' and p.name not like '【検証】%'
  and v.visit_date between :'date_from'::date and :'date_to'::date
  and coalesce(v.primary_staff_id,
               case when not v.manual_staff_override and c.deleted_at is null
                    then c.assigned_staff_id end) is null
order by v.visit_date, v.start_time
