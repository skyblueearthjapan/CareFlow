\pset format unaligned
with cur as (select *, coalesce(nullif(before->>'date',''), after->>'date') as d, coalesce(nullif(before->>'user_name',''), after->>'user_name') as u from correction_sheet_items where sheet_id='3012f86a-d1c8-48eb-9311-2e44fea771cb'),
prev as (select *, coalesce(nullif(before->>'date',''), after->>'date') as d, coalesce(nullif(before->>'user_name',''), after->>'user_name') as u from correction_sheet_items where sheet_id='8534145e-6b76-44b5-abce-98ccbe188e57')
select cur.d, cur.u, cur.action, cur.include, cur.before->>'start_time' as t_before, cur.after->>'start_time' as t_after, cur.after->>'staff1' as staff_after, cur.visit_id is not null as has_visit,
 (select string_agg(prev.action||' '||coalesce(prev.before->>'start_time','')||'>'||coalesce(prev.after->>'start_time','')||' '||coalesce(prev.after->>'staff1',''), ' | ') from prev where prev.d=cur.d and prev.u=cur.u) as in_prev_sheet
from cur order by cur.d::int, cur.u, cur.action;
\echo --- prev sheet '-' items
select coalesce(nullif(before->>'date',''), after->>'date') d, coalesce(nullif(before->>'user_name',''), after->>'user_name') u, action, before->>'start_time', before->>'staff1', '=>', after->>'start_time', after->>'staff1' from correction_sheet_items where sheet_id='8534145e-6b76-44b5-abce-98ccbe188e57' and after->>'staff1'='-' order by 1::int, 2;
