-- 日 × 拠点の訪問件数（1 回 1 件と行数の両方・完了数。削除・取消・【検証】を除く） | params: date_from, date_to
with vv as (
  select v.id, coalesce(v.visit_group_id, v.id) as group_key, v.visit_date, v.status,
         coalesce(c.office_id, p.primary_office_id) as office_id
  from visits v
  join patients p on p.id = v.patient_id
  left join courses c on c.id = v.course_id
  where v.deleted_at is null and v.status <> 'cancelled'
    and p.name not like '【検証】%'
    and v.visit_date between :'date_from'::date and :'date_to'::date
)
select vv.visit_date,
       (array['月','火','水','木','金','土','日'])[extract(isodow from vv.visit_date)::int] as weekday_ja,
       coalesce(o.short_label, '(拠点なし)') as office,
       count(distinct vv.group_key) as visits_one_per_visit,
       count(*) as visit_rows,
       count(distinct vv.group_key) filter (where vv.status = 'completed') as completed
from vv left join offices o on o.id = vv.office_id
group by vv.visit_date, coalesce(o.short_label, '(拠点なし)')
order by vv.visit_date, office
