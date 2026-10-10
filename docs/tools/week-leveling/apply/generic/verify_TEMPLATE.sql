-- 反映後の照合 (読み取りのみ)。expect.txt (visit_id|date|start|staff_id|course) と突き合わせる
begin transaction read only;
\pset format unaligned
\pset tuples_only on
\pset fieldsep '|'
select 'V', v.id, v.patient_id, v.visit_date, to_char(v.start_time,'HH24:MI'), v.primary_staff_id, o.short_label||c.code
from visits v left join courses c on c.id=v.course_id left join offices o on o.id=c.office_id
where v.visit_date between '2026-10-12' and '2026-10-18' and v.deleted_at is null and v.status='planned';
select 'S008_course', count(*) from courses where iso_year=2026 and iso_week=42 and deleted_at is null and assigned_staff_id='cdc014b5-c84a-48ea-9332-ab0ada023269';
select 'S008_duty', count(*) from visits v where v.visit_date between '2026-10-12' and '2026-10-18' and v.deleted_at is null and v.status<>'cancelled'
  and ('cdc014b5-c84a-48ea-9332-ab0ada023269' in (v.primary_staff_id, v.secondary_staff_id, v.mentor_staff_id)
       or exists (select 1 from visit_staff_assignments a where a.visit_id=v.id and a.staff_id='cdc014b5-c84a-48ea-9332-ab0ada023269'));
select 'override', count(*) from visits where visit_date between '2026-10-12' and '2026-10-18' and deleted_at is null and manual_staff_override;
select 'null_course_with_planned', count(distinct c.id) from courses c join visits v on v.course_id=c.id
  where c.iso_year=2026 and c.iso_week=42 and c.deleted_at is null and c.assigned_staff_id is null and v.deleted_at is null and v.status='planned';
select 'planned_no_staff', count(*) from visits where visit_date between '2026-10-12' and '2026-10-18' and deleted_at is null and status='planned' and primary_staff_id is null;
select 'acc', a.target_type, c.weekday, o.short_label||c.code from accompaniments a join courses c on c.id=a.course_id join offices o on o.id=c.office_id
  where a.accompanying_staff_id='cdc014b5-c84a-48ea-9332-ab0ada023269' and c.iso_year=2026 and c.iso_week=42;
rollback;
