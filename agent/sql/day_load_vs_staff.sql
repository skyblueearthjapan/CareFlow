-- 日ごとの訪問件数（1 回 1 件）と出勤人数（勤務表の出勤日−終日休み＝公休 off・有給 paid_off。半休は出勤に数えて別列にも。新人・マネージャーは別列）と 1 人あたり件数 | params: date_from, date_to
with days as (
  select d::date as d from generate_series(:'date_from'::date, :'date_to'::date, interval '1 day') d
), vis as (
  select v.visit_date, count(distinct coalesce(v.visit_group_id, v.id)) as visits,
         count(*) filter (where coalesce(v.primary_staff_id,
                  case when not v.manual_staff_override and c.deleted_at is null
                       then c.assigned_staff_id end) is null) as unassigned_rows
  from visits v join patients p on p.id = v.patient_id left join courses c on c.id = v.course_id
  where v.deleted_at is null and v.status <> 'cancelled' and p.name not like '【検証】%'
    and v.visit_date between :'date_from'::date and :'date_to'::date
  group by v.visit_date
), duty as (
  select days.d, s.id, s.role, s.is_trainee, o.override_type
  from days
  join staff s on s.status = 'active' and s.deleted_at is null
  join staff_shifts sh on sh.staff_id = s.id and sh.weekday = extract(isodow from days.d)::int - 1 and sh.is_on
  left join staff_weekly_overrides o on o.staff_id = s.id
       and o.iso_year = extract(isoyear from days.d)::int and o.iso_week = extract(week from days.d)::int
       and o.weekday = extract(isodow from days.d)::int - 1
)
select days.d as visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from days.d)::int] as weekday_ja,
       coalesce(vis.visits, 0) as visits_one_per_visit,
       coalesce(vis.unassigned_rows, 0) as unassigned_rows,
       count(duty.id) filter (where not duty.is_trainee and duty.role <> 'manager'
                              and coalesce(duty.override_type, '') not in ('off', 'paid_off')) as staff_on_duty,
       count(duty.id) filter (where not duty.is_trainee and duty.role <> 'manager'
                              and duty.override_type in ('am_off', 'pm_off')) as staff_half_day,
       count(duty.id) filter (where duty.role = 'manager' and not duty.is_trainee
                              and coalesce(duty.override_type, '') not in ('off', 'paid_off')) as managers_on_duty,
       count(duty.id) filter (where duty.is_trainee and coalesce(duty.override_type, '') not in ('off', 'paid_off')) as trainees_on_duty,
       count(duty.id) filter (where duty.override_type in ('off', 'paid_off')) as staff_off,
       count(duty.id) filter (where duty.override_type = 'paid_off') as staff_paid_off,
       round(coalesce(vis.visits, 0)::numeric / nullif(count(duty.id) filter (
             where not duty.is_trainee and duty.role <> 'manager'
               and coalesce(duty.override_type, '') not in ('off', 'paid_off')), 0), 1) as visits_per_staff
from days
left join vis on vis.visit_date = days.d
left join duty on duty.d = days.d
group by days.d, vis.visits, vis.unassigned_rows
order by days.d
