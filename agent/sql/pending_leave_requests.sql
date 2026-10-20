-- 未承認の申請（休み申請 staff_off ほか）。request_type が空なら全種類。1 日 1 件。休み申請は種類（leave_type: off/am_off/pm_off/paid_off、変更のお願いだけ cancel＝休みを取り消してほしい）と変更のお願い（is_change＝締め切り＝前の月の 20 日 JST の後・確定後）と理由 | params: request_type
select r.request_type, r.target_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from r.target_date)::int] as weekday_ja,
       s.code as staff_code, s.name as staff_name, p.name as patient_name,
       to_char(r.created_at at time zone 'Asia/Tokyo', 'YYYY-MM-DD HH24:MI') as requested_jst,
       case when r.request_type = 'staff_off'
            then coalesce(r.payload->>'override_type', r.payload->>'type', 'off') end as leave_type,
       coalesce(r.payload->>'kind', '') = 'change' as is_change,
       r.payload->>'note' as note,
       r.status, r.scope, r.id as request_id
from pending_requests r
left join staff s on s.id = r.target_staff_id
left join patients p on p.id = r.target_patient_id
where r.status = 'pending'
  and (:'request_type' = '' or r.request_type = :'request_type')
order by r.target_date nulls last, s.code
