-- 期間の日ごとの件数（rk period scope が読む）: 訪問（1 回 1 件・削除・取消・【検証】を除く）・担当なし（明示の担当なしを含む）・打刻／サイン／実績のある訪問と予定外の訪問（週を空にしても残る・week_clear.plan_clear と同じ）・取消の件数・終日の休み（公休・有給）の職員数。日付は全日を返す（訪問の無い日も 0） | params: date_from, date_to
with days as (
  select d::date as d from generate_series(:'date_from'::date, :'date_to'::date, interval '1 day') d
), vv as (
  select v.id, coalesce(v.visit_group_id, v.id) as group_key, v.visit_date, v.status,
         coalesce(v.primary_staff_id,
                  case when not v.manual_staff_override and c.deleted_at is null
                       then c.assigned_staff_id end) as staff_id,
         (v.status in ('in_progress', 'completed') or v.is_unplanned
          or exists (select 1 from visit_checkins k where k.visit_id = v.id)
          or exists (select 1 from visit_signatures g where g.visit_id = v.id)) as checked_in
  from visits v
  join patients p on p.id = v.patient_id
  left join courses c on c.id = v.course_id
  where v.deleted_at is null and p.name not like '【検証】%'
    and v.visit_date between :'date_from'::date and :'date_to'::date
), vis as (
  select visit_date,
         count(distinct group_key) filter (where status <> 'cancelled') as visits,
         count(distinct group_key) filter (where status <> 'cancelled' and staff_id is null) as unassigned,
         count(distinct group_key) filter (where status <> 'cancelled' and checked_in) as checked_in,
         count(distinct group_key) filter (where status = 'cancelled') as cancelled
  from vv group by visit_date
), off_ as (
  select days.d, count(distinct o.staff_id) as staff_off
  from days
  join staff_weekly_overrides o
    on o.iso_year = extract(isoyear from days.d)::int and o.iso_week = extract(week from days.d)::int
   and o.weekday = extract(isodow from days.d)::int - 1 and o.override_type in ('off', 'paid_off')
  join staff s on s.id = o.staff_id and s.deleted_at is null and s.status = 'active'
  group by days.d
)
select to_char(days.d, 'YYYY-MM-DD') as visit_date,
       coalesce(vis.visits, 0) as visits,
       coalesce(vis.unassigned, 0) as unassigned,
       coalesce(vis.checked_in, 0) as checked_in,
       coalesce(vis.cancelled, 0) as cancelled,
       coalesce(off_.staff_off, 0) as staff_off
from days
left join vis on vis.visit_date = days.d
left join off_ on off_.d = days.d
order by days.d
