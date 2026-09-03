\pset format unaligned
select v.visit_date, p.name, to_char(v.start_time,'HH24:MI') st, left(v.id::text,8) vid, v.source, v.created_at::timestamp(0) created, left(v.course_id::text,8) course, c.code, c.iso_week, s2.name as course_staff,
 (select string_agg(s.name, '+') from visit_staff_assignments a join staff s on s.id=a.staff_id where a.visit_id=v.id) as vsa_staff,
 v.manual_staff_override
from visits v join patients p on p.id=v.patient_id left join courses c on c.id=v.course_id left join staff s2 on s2.id=c.assigned_staff_id
where v.visit_date between '2026-09-07' and '2026-09-13' and v.status<>'cancelled'
 and (regexp_replace(p.name,'[[:space:]　]','','g'), v.visit_date) in (('山岡由美子','2026-09-09'),('清水洋之','2026-09-09'),('井川裕太','2026-09-09'),('安永愛菜','2026-09-09'),('菅原華純','2026-09-09'),('林修','2026-09-10'),('森田美穂子','2026-09-10'),('石川えみ','2026-09-10'),('松戸きよ','2026-09-10'),('並木啓悦','2026-09-09'),('木村駿','2026-09-07'))
order by v.visit_date, p.name, v.start_time, v.created_at;
\echo --- W37 totals
select count(*) total, sum(case when v.primary_staff_id is null then 1 else 0 end) no_primary, sum(case when not exists(select 1 from visit_staff_assignments a where a.visit_id=v.id) then 1 else 0 end) no_vsa from visits v where v.visit_date between '2026-09-07' and '2026-09-13' and v.status<>'cancelled';
\echo --- duplicate (patient,date,start) groups in W37
select p.name, v.visit_date, to_char(v.start_time,'HH24:MI') st, count(*) from visits v join patients p on p.id=v.patient_id where v.visit_date between '2026-09-07' and '2026-09-13' and v.status<>'cancelled' group by 1,2,3 having count(*)>1 order by 2,1;
