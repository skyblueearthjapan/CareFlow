-- 週の休み（公休 off・午前休 am_off＝13:00 から勤務・午後休 pm_off＝12:30 から休み・有給 paid_off）と時間変更 custom_time（staff_weekly_overrides＝休みの正典。承認前の申請は入らない→pending_leave_requests）。source＝request 申請の承認／admin 管理者／kaipoke 照合で取込。monday は週の月曜 | params: monday
select s.code as staff_code, s.name as staff_name,
       (:'monday'::date + o.weekday) as off_date,
       (array['月','火','水','木','金','土','日'])[o.weekday + 1] as weekday_ja,
       o.override_type,
       case o.override_type when 'off' then '公休' when 'am_off' then '午前休' when 'pm_off' then '午後休'
                            when 'paid_off' then '有給' when 'custom_time' then '時間変更' end as type_ja,
       o.override_type in ('off', 'paid_off') as full_day_off,
       to_char(o.start_time, 'HH24:MI') as start_time, to_char(o.end_time, 'HH24:MI') as end_time,
       o.source, o.reason
from staff_weekly_overrides o
join staff s on s.id = o.staff_id
where o.iso_year = extract(isoyear from :'monday'::date)::int
  and o.iso_week = extract(week from :'monday'::date)::int
order by o.weekday, s.code
