-- 最近の書き込み API（誰が・いつ・どのパス・結果）。path_like はパスの一部（例 visits・courses）・空なら全部。request_body は伏せ字ありで個人情報を含みうるので出さない | params: days, path_like
select to_char(a.created_at at time zone 'Asia/Tokyo', 'YYYY-MM-DD HH24:MI:SS') as at_jst,
       coalesce(u.username, u.email, '(不明)') as actor, s.name as actor_staff_name, a.role,
       a.method, a.path, a.status_code, a.action, a.target_table, a.target_id
from audit_logs a
left join users u on u.id = a.actor_user_id
left join staff s on s.id = u.staff_id
where a.created_at >= now() - make_interval(days => :'days'::int)
  and coalesce(a.method, '') not in ('GET', 'HEAD', 'OPTIONS')
  and (:'path_like' = '' or a.path like '%' || :'path_like' || '%')
order by a.created_at desc
limit 500
