-- 週の休み・半休・時間変更（staff_weekly_overrides＝休みの正典。承認前の申請は入らない→pending_leave_requests）。monday は週の月曜 | params: monday
select s.code as staff_code, s.name as staff_name,
       (:'monday'::date + o.weekday) as off_date,
       (array['月','火','水','木','金','土','日'])[o.weekday + 1] as weekday_ja,
       o.override_type, to_char(o.start_time, 'HH24:MI') as start_time, to_char(o.end_time, 'HH24:MI') as end_time,
       o.reason
from staff_weekly_overrides o
join staff s on s.id = o.staff_id
where o.iso_year = extract(isoyear from :'monday'::date)::int
  and o.iso_week = extract(week from :'monday'::date)::int
order by o.weekday, s.code
