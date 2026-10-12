-- ある利用者の訪問（取消も状態つきで出す・削除は除く。patient は患者コードか氏名/かなの一部） | params: patient, date_from, date_to
select p.code as patient_code, p.name as patient_name, p.status as patient_status, v.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from v.visit_date)::int] as weekday_ja,
       to_char(v.start_time, 'HH24:MI') as start_jst, to_char(v.end_time, 'HH24:MI') as end_jst,
       v.status, v.source, v.week_pinned,
       coalesce(v.visit_group_id, v.id) as group_key,
       s.name as staff_name, s2.name as secondary_staff_name, c.code as course_code,
       o.short_label as office, v.id as visit_id
from visits v
join patients p on p.id = v.patient_id
left join courses c on c.id = v.course_id
left join staff s on s.id = coalesce(v.primary_staff_id,
          case when not v.manual_staff_override and c.deleted_at is null then c.assigned_staff_id end)
left join staff s2 on s2.id = v.secondary_staff_id
left join offices o on o.id = coalesce(c.office_id, p.primary_office_id)
where v.deleted_at is null and p.deleted_at is null
  and v.visit_date between :'date_from'::date and :'date_to'::date
  and (p.code = :'patient'
       or replace(replace(p.name, ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%'
       or replace(replace(coalesce(p.kana, ''), ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%')
order by p.code, v.visit_date, v.start_time
