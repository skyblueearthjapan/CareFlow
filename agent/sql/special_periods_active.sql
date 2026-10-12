-- その日に特別訪問週間の期間中の利用者（status='active' かつ start_date〜end_date に on_date を含む）と、その週の○（追加枠）の数・未配置の数 | params: on_date
select p.code as patient_code, p.name as patient_name, p.status as patient_status,
       sp.start_date, sp.end_date, sp.weekly_target, sp.note,
       (select count(*) from special_visit_marks m where m.period_id = sp.id and m.kind = 'extra'
          and m.status <> 'cancelled'
          and m.iso_year = extract(isoyear from :'on_date'::date)::int
          and m.iso_week = extract(week from :'on_date'::date)::int) as extra_marks_this_week,
       (select count(*) from special_visit_marks m where m.period_id = sp.id and m.kind = 'extra'
          and m.iso_year = extract(isoyear from :'on_date'::date)::int
          and m.iso_week = extract(week from :'on_date'::date)::int
          and (m.status = 'pool' or (m.status = 'placed' and not exists (
                select 1 from visits w where w.id = m.placed_visit_id and w.deleted_at is null
                  and w.status <> 'cancelled')))) as unplaced_this_week,
       (select count(distinct coalesce(v.visit_group_id, v.id)) from visits v
          where v.patient_id = p.id and v.deleted_at is null and v.status <> 'cancelled'
            and v.visit_date between date_trunc('week', :'on_date'::date)::date
                                 and date_trunc('week', :'on_date'::date)::date + 6) as visits_this_week
from special_visit_periods sp
join patients p on p.id = sp.patient_id
where sp.status = 'active' and p.deleted_at is null and p.name not like '【検証】%'
  and :'on_date'::date between sp.start_date and sp.end_date
order by sp.start_date, p.code
