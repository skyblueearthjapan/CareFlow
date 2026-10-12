-- 週の同行（新人 trainee・支援 support）。course リンクはそのコースの全訪問に同行（件数は有効訪問）、visit リンクはその 1 件。同行は担当件数に入れない | params: monday
with links as (
  select a.id, a.accompanying_staff_id, a.kind, a.source, a.target_type,
         c.weekday, c.code as course_code, c.office_id, c.assigned_staff_id as lead_staff_id,
         (select count(*) from visits v where v.course_id = c.id and v.deleted_at is null and v.status <> 'cancelled') as visits
  from accompaniments a join courses c on c.id = a.course_id
  where a.target_type = 'course' and c.deleted_at is null
    and c.iso_year = extract(isoyear from :'monday'::date)::int
    and c.iso_week = extract(week from :'monday'::date)::int
  union all
  select a.id, a.accompanying_staff_id, a.kind, a.source, a.target_type,
         (v.visit_date - :'monday'::date), cc.code, coalesce(cc.office_id, p.primary_office_id),
         coalesce(v.primary_staff_id, cc.assigned_staff_id), 1
  from accompaniments a join visits v on v.id = a.visit_id
  join patients p on p.id = v.patient_id
  left join courses cc on cc.id = v.course_id
  where a.target_type = 'visit' and v.deleted_at is null and v.status <> 'cancelled'
    and v.visit_date between :'monday'::date and :'monday'::date + 6
)
select s.code as staff_code, s.name as staff_name, s.is_trainee, l.kind, l.source, l.target_type,
       (:'monday'::date + l.weekday) as visit_date,
       (array['月','火','水','木','金','土','日'])[l.weekday + 1] as weekday_ja,
       o.short_label as office, l.course_code, ls.name as lead_staff_name, l.visits
from links l
join staff s on s.id = l.accompanying_staff_id
left join staff ls on ls.id = l.lead_staff_id
left join offices o on o.id = l.office_id
order by s.code, l.weekday, l.course_code
