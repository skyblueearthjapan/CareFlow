-- 週の職員の予定（会議・研修・朝会・カイポケ個別業務）。starts_at は「日本時間の壁時計を UTC として保存」なので at time zone 'UTC' で戻す。今週だけ外したもの（cancelled）も印つきで出す | params: monday
select s.code as staff_code, s.name as staff_name,
       to_char(e.starts_at at time zone 'UTC', 'YYYY-MM-DD') as event_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from (e.starts_at at time zone 'UTC'))::int] as weekday_ja,
       to_char(e.starts_at at time zone 'UTC', 'HH24:MI') as start_wall,
       to_char(e.ends_at at time zone 'UTC', 'HH24:MI') as end_wall,
       e.title, e.event_type, e.source, e.blocking, (e.cancelled_at is not null) as cancelled_this_week
from staff_events e
join staff s on s.id = e.staff_id
where (e.starts_at at time zone 'UTC') < (:'monday'::date + 7)
  and (e.ends_at at time zone 'UTC') > :'monday'::date
order by event_date, start_wall, s.code
