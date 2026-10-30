-- ある利用者の、今（日本時間）より後の予定を近い順に 5 件（削除・取消を除く）。週を作っていない先は出ない→固定訪問の枠で補う（隔週・月の第何週の方は枠をそのまま毎週と読まない。patient_fixed_visits の visit_week と頻度で数える） | params: patient
select p.code as patient_code, p.name as patient_name, v.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from v.visit_date)::int] as weekday_ja,
       to_char(v.start_time, 'HH24:MI') as start_jst, to_char(v.end_time, 'HH24:MI') as end_jst,
       s.name as staff_name, c.code as course_code, v.status, v.source
from visits v
join patients p on p.id = v.patient_id
left join courses c on c.id = v.course_id
left join staff s on s.id = coalesce(v.primary_staff_id,
          case when not v.manual_staff_override and c.deleted_at is null then c.assigned_staff_id end)
where v.deleted_at is null and v.status <> 'cancelled' and p.deleted_at is null
  and (v.visit_date, v.start_time) > ((now() at time zone 'Asia/Tokyo')::date, (now() at time zone 'Asia/Tokyo')::time)
  and (p.code = :'patient'
       or replace(replace(p.name, ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%')
order by v.visit_date, v.start_time
limit 5
