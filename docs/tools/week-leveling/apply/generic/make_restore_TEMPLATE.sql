-- 週42 (2026-10-12〜10-18) の「反映前」を、そのまま戻す SQL を作る (この問い合わせ自体は読み取りだけ)。
-- 出力 = 戻し用スクリプト。対象: visits / visit_staff_assignments / courses / special_visit_marks / accompaniments。
-- 1 の UPDATE は deleted_at も元 (NULL) に戻すので、プールへ移した 5 件も戻る。
begin transaction read only;
\pset format unaligned
\pset tuples_only on
select 'BEGIN;';
select '-- 週42 を反映前 (' || now() || ') に戻す';
-- 1. 反映前の訪問を、行ごと元の値へ
with cols as (
  select string_agg(quote_ident(column_name), ',' order by ordinal_position) c
  from information_schema.columns where table_name='visits' and column_name not in ('id','created_at'))
select format('UPDATE visits SET (%s) = (SELECT %s FROM jsonb_populate_record(NULL::visits, %L::jsonb)) WHERE id=%L;',
              cols.c, cols.c, to_jsonb(vv), vv.id)
from visits vv, cols where vv.visit_date between '2026-10-12' and '2026-10-18' and vv.deleted_at is null;
-- 2. 反映で増えた訪問 (反映前に無かった行) は論理削除
select format('UPDATE visits SET deleted_at=now() WHERE visit_date BETWEEN %L AND %L AND deleted_at IS NULL AND id NOT IN (%s);',
              '2026-10-12', '2026-10-18', string_agg(quote_literal(v.id::text) || '::uuid', ','))
from visits v where v.visit_date between '2026-10-12' and '2026-10-18' and v.deleted_at is null;
-- 3. 担当の紐付け: 週42 の訪問の分を消して、反映前の行を戻す
select 'DELETE FROM visit_staff_assignments WHERE visit_id IN (SELECT id FROM visits WHERE visit_date BETWEEN ''2026-10-12'' AND ''2026-10-18'');';
select format('INSERT INTO visit_staff_assignments SELECT * FROM jsonb_populate_record(NULL::visit_staff_assignments, %L::jsonb) ON CONFLICT DO NOTHING;', to_jsonb(a))
from visit_staff_assignments a join visits v on v.id=a.visit_id where v.visit_date between '2026-10-12' and '2026-10-18';
-- 4. コース: 反映前の行を元の値へ・反映で増えたコースは論理削除
with cols as (
  select string_agg(quote_ident(column_name), ',' order by ordinal_position) c
  from information_schema.columns where table_name='courses' and column_name not in ('id','created_at'))
select format('UPDATE courses SET (%s) = (SELECT %s FROM jsonb_populate_record(NULL::courses, %L::jsonb)) WHERE id=%L;',
              cols.c, cols.c, to_jsonb(co), co.id)
from courses co, cols where co.iso_year=2026 and co.iso_week=42;
select format('UPDATE courses SET deleted_at=now() WHERE iso_year=2026 AND iso_week=42 AND deleted_at IS NULL AND id NOT IN (%s);',
              string_agg(quote_literal(co.id::text) || '::uuid', ','))
from courses co where co.iso_year=2026 and co.iso_week=42;
-- 5. 特別訪問の○
with cols as (
  select string_agg(quote_ident(column_name), ',' order by ordinal_position) c
  from information_schema.columns where table_name='special_visit_marks' and column_name not in ('id','created_at'))
select format('UPDATE special_visit_marks SET (%s) = (SELECT %s FROM jsonb_populate_record(NULL::special_visit_marks, %L::jsonb)) WHERE id=%L;',
              cols.c, cols.c, to_jsonb(mm), mm.id)
from special_visit_marks mm, cols where mm.iso_year=2026 and mm.iso_week=42;
-- 6. 同行: 週42 のコース・訪問に付いた同行を消して、反映前の行を戻す
select 'DELETE FROM accompaniments WHERE course_id IN (SELECT id FROM courses WHERE iso_year=2026 AND iso_week=42) OR visit_id IN (SELECT id FROM visits WHERE visit_date BETWEEN ''2026-10-12'' AND ''2026-10-18'');';
select format('INSERT INTO accompaniments SELECT * FROM jsonb_populate_record(NULL::accompaniments, %L::jsonb) ON CONFLICT DO NOTHING;', to_jsonb(a))
from accompaniments a left join courses c on c.id=a.course_id left join visits v on v.id=a.visit_id
where (c.iso_year=2026 and c.iso_week=42) or v.visit_date between '2026-10-12' and '2026-10-18';
-- 7. 「戻る」の記録: この SQL を作った後の週42 の操作は、戻した後に「戻る」で逆の操作が走らないよう undone にする
select format('UPDATE schedule_op_log SET undone=true WHERE iso_year=2026 AND iso_week=42 AND undone=false AND created_at >= %L;', now());
select 'COMMIT;';
rollback;
