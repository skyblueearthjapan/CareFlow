\pset format unaligned
\echo --- schedule mutations 15:40-16:00 JST
select a.created_at::timestamp(0) t, a.method, a.path, a.status_code, a.latency_ms, u.username, left(a.request_body::text,160) body
from audit_logs a left join users u on u.id=a.actor_user_id
where a.created_at between '2026-09-03 15:40' and '2026-09-03 16:00' and a.method='POST' and a.path like '%schedule%' order by a.created_at;
\echo --- dup groups per week
with v as (select patient_id, visit_date, start_time, (visit_date - (extract(isodow from visit_date)::int - 1)) as wk from visits where status<>'cancelled' and deleted_at is null and visit_date between '2026-08-24' and '2026-10-18')
select wk, count(*) visits, (select count(*) from (select 1 from v x where x.wk=v.wk group by x.patient_id,x.visit_date,x.start_time having count(*)>1) d) dup_groups from v group by wk order by wk;
