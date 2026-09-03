\pset format unaligned
\echo --- live W37 visits without primary staff
select v.visit_date, p.name, to_char(v.start_time,'HH24:MI') st, v.source, c.code, o.name office, s.name course_staff, v.created_at::timestamp(0)
from visits v join patients p on p.id=v.patient_id left join courses c on c.id=v.course_id left join staff s on s.id=c.assigned_staff_id left join offices o on o.id=p.primary_office_id
where v.deleted_at is null and v.status<>'cancelled' and v.visit_date between '2026-09-07' and '2026-09-13' and v.primary_staff_id is null order by 1,3;
\echo --- live totals
select count(*) live, sum(case when primary_staff_id is null then 1 else 0 end) no_primary from visits where deleted_at is null and status<>'cancelled' and visit_date between '2026-09-07' and '2026-09-13';
