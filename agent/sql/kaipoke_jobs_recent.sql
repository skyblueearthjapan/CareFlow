-- 最近のカイポケ連携ジョブ（送信 apply・差分 diff-local・取込 smart-apply・export など）と状態・件数の要約。時刻は日本時間 | params: days
select to_char(j.created_at at time zone 'Asia/Tokyo', 'YYYY-MM-DD HH24:MI') as created_jst,
       to_char(j.completed_at at time zone 'Asia/Tokyo', 'YYYY-MM-DD HH24:MI') as completed_jst,
       j.job_type, j.params->>'op' as op, j.status, j.week_start, j.params->>'month' as month,
       (select count(*) from kaipoke_job_items i where i.job_id = j.id) as items,
       (select count(*) from kaipoke_job_items i where i.job_id = j.id and i.status = 'success') as items_success,
       (select count(*) from kaipoke_job_items i where i.job_id = j.id and i.status = 'failed') as items_failed,
       (select count(*) from kaipoke_job_items i where i.job_id = j.id and i.status = 'unknown') as items_unknown,
       coalesce(u.username, u.email) as created_by, j.id as job_id
from kaipoke_jobs j
left join users u on u.id = j.created_by_user_id
where j.created_at >= now() - make_interval(days => :'days'::int)
order by j.created_at desc
