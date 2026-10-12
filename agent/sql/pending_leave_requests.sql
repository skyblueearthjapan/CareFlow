-- 未承認の申請（休み申請 staff_off ほか）。request_type が空なら全種類。1 日 1 件 | params: request_type
select r.request_type, r.target_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from r.target_date)::int] as weekday_ja,
       s.code as staff_code, s.name as staff_name, p.name as patient_name,
       to_char(r.created_at at time zone 'Asia/Tokyo', 'YYYY-MM-DD HH24:MI') as requested_jst,
       r.status, r.scope, r.id as request_id
from pending_requests r
left join staff s on s.id = r.target_staff_id
left join patients p on p.id = r.target_patient_id
where r.status = 'pending'
  and (:'request_type' = '' or r.request_type = :'request_type')
order by r.target_date nulls last, s.code
