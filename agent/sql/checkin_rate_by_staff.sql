-- 担当職員ごとの打刻率（終わった予定だけ＝今日の終了予定時刻より前。到着あり・QR で読んだ到着・到着と退出の両方。no_show は数えない。削除・取消・【検証】を除く） | params: date_from, date_to
with vv as (
  select v.id, v.visit_date, v.end_time,
         coalesce(v.primary_staff_id,
                  case when not v.manual_staff_override and c.deleted_at is null
                       then c.assigned_staff_id end) as staff_id
  from visits v join patients p on p.id = v.patient_id left join courses c on c.id = v.course_id
  where v.deleted_at is null and v.status <> 'cancelled' and p.name not like '【検証】%'
    and v.visit_date between :'date_from'::date and :'date_to'::date
    and (v.visit_date < (now() at time zone 'Asia/Tokyo')::date
         or (v.visit_date = (now() at time zone 'Asia/Tokyo')::date
             and v.end_time <= (now() at time zone 'Asia/Tokyo')::time))
), flags as (
  select vv.*,
         exists (select 1 from visit_checkins k where k.visit_id = vv.id and k.kind = 'arrival') as has_arrival,
         exists (select 1 from visit_checkins k where k.visit_id = vv.id and k.kind = 'arrival'
                 and k.checkin_source = 'qr') as has_qr_arrival,
         exists (select 1 from visit_checkins k where k.visit_id = vv.id and k.kind = 'departure') as has_departure
  from vv
)
select coalesce(s.code, '-') as staff_code, coalesce(s.name, '(担当なし)') as staff_name,
       count(*) as finished_visits,
       count(*) filter (where has_arrival) as with_arrival,
       count(*) filter (where has_qr_arrival) as with_qr_arrival,
       count(*) filter (where has_arrival and has_departure) as with_both,
       round(100.0 * count(*) filter (where has_arrival) / nullif(count(*), 0), 1) as arrival_rate_pct,
       round(100.0 * count(*) filter (where has_arrival and has_departure) / nullif(count(*), 0), 1) as both_rate_pct
from flags left join staff s on s.id = flags.staff_id
group by s.code, s.name
order by staff_code
