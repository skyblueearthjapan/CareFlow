-- 職員の名前の同定（氏名・かなの一部・空白を無視。退職者も残るので稼働中を先に出す）。staff_id は API に渡す ID | params: name
select s.code as staff_code, s.name as staff_name, s.kana, s.status, s.role, s.is_trainee,
       o.short_label as office, s.id as staff_id
from staff s
left join offices o on o.id = s.primary_office_id
where s.deleted_at is null
  and (s.code = :'name'
       or replace(replace(s.name, ' ', ''), '　', '') like '%' || replace(replace(:'name', ' ', ''), '　', '') || '%'
       or replace(replace(coalesce(s.kana, ''), ' ', ''), '　', '') like '%' || replace(replace(:'name', ' ', ''), '　', '') || '%')
order by (s.status = 'active') desc, s.code
limit 30
