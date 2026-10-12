-- 打刻の実績（誰が・何時に・どの家）。到着/退出は kind ごとの最新 1 行、時刻を合わせた記録があればそちらを採る（近似。正確な実績は GET /visit-history）。時刻は日本時間。取消・削除の訪問も打刻があれば出す。patient/staff は空なら全員 | params: date_from, date_to, patient, staff
with arr as (
  select distinct on (visit_id) visit_id, staff_id, coalesce(device_time, scanned_at) as read_at,
         match_status, checkin_source, distance_m
  from visit_checkins where kind = 'arrival'
  order by visit_id, coalesce(device_time, scanned_at) desc, scanned_at desc, id desc
), dep as (
  select distinct on (visit_id) visit_id, staff_id, coalesce(device_time, scanned_at) as read_at, checkin_source
  from visit_checkins where kind = 'departure'
  order by visit_id, coalesce(device_time, scanned_at) desc, scanned_at desc, id desc
), adj_a as (
  select distinct on (visit_id) visit_id, adjusted_at, created_at, reason_code
  from visit_time_adjustments where kind = 'arrival'
  order by visit_id, created_at desc, id desc
), adj_d as (
  select distinct on (visit_id) visit_id, adjusted_at, created_at, reason_code
  from visit_time_adjustments where kind = 'departure'
  order by visit_id, created_at desc, id desc
)
select v.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from v.visit_date)::int] as weekday_ja,
       to_char(v.start_time, 'HH24:MI') as planned_start, to_char(v.end_time, 'HH24:MI') as planned_end,
       p.code as patient_code, p.name as patient_name,
       sa.name as arrival_by, sd.name as departure_by, so.name as owner_staff_name,
       to_char((case when adj_a.adjusted_at is not null and adj_a.created_at > coalesce(arr.read_at, '-infinity')
                     then adj_a.adjusted_at else arr.read_at end) at time zone 'Asia/Tokyo', 'HH24:MI') as arrival_jst,
       to_char((case when adj_d.adjusted_at is not null and adj_d.created_at > coalesce(dep.read_at, '-infinity')
                     then adj_d.adjusted_at else dep.read_at end) at time zone 'Asia/Tokyo', 'HH24:MI') as departure_jst,
       to_char(arr.read_at at time zone 'Asia/Tokyo', 'HH24:MI') as arrival_read_jst,
       adj_a.reason_code as arrival_adjust_reason, adj_d.reason_code as departure_adjust_reason,
       arr.match_status, arr.distance_m, arr.checkin_source as arrival_source, dep.checkin_source as departure_source,
       v.status as visit_status, (v.deleted_at is not null) as visit_deleted, v.is_unplanned, v.id as visit_id
from visits v
join patients p on p.id = v.patient_id
left join courses c on c.id = v.course_id
left join arr on arr.visit_id = v.id
left join dep on dep.visit_id = v.id
left join adj_a on adj_a.visit_id = v.id
left join adj_d on adj_d.visit_id = v.id
left join staff sa on sa.id = arr.staff_id
left join staff sd on sd.id = dep.staff_id
left join staff so on so.id = coalesce(v.primary_staff_id,
          case when not v.manual_staff_override and c.deleted_at is null then c.assigned_staff_id end)
where v.visit_date between :'date_from'::date and :'date_to'::date
  and (arr.visit_id is not null or dep.visit_id is not null or adj_a.visit_id is not null or adj_d.visit_id is not null)
  and p.name not like '【検証】%'
  and (:'patient' = '' or p.code = :'patient'
       or replace(replace(p.name, ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%')
  and (:'staff' = '' or exists (
         select 1 from staff s where s.id in (arr.staff_id, dep.staff_id)
           and (s.code = :'staff'
                or replace(replace(s.name, ' ', ''), '　', '') like '%' || replace(replace(:'staff', ' ', ''), '　', '') || '%')))
order by v.visit_date, arrival_jst nulls last, v.start_time
