-- 場所が要確認（review）・場所違い（mismatch）・GPS なし（no_gps）の打刻。読んだ時刻は日本時間。確認済み（reviewed_at）も印つきで出す | params: date_from, date_to
select (k.scanned_at at time zone 'Asia/Tokyo')::date as scan_date,
       to_char(coalesce(k.device_time, k.scanned_at) at time zone 'Asia/Tokyo', 'HH24:MI') as read_jst,
       k.kind, k.match_status, round(k.distance_m) as distance_m, k.is_override, k.checkin_source,
       s.name as scanned_by, p.code as patient_code, p.name as patient_name,
       (k.reviewed_at is not null) as reviewed, k.visit_id
from visit_checkins k
left join staff s on s.id = k.staff_id
left join patients p on p.id = k.patient_id
where k.match_status in ('review', 'mismatch', 'no_gps')
  and (k.scanned_at at time zone 'Asia/Tokyo')::date between :'date_from'::date and :'date_to'::date
  and coalesce(p.name, '') not like '【検証】%'
order by k.scanned_at
