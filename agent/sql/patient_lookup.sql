-- 利用者の名前の同定（氏名・かなの一部、空白の有無を無視。齋藤/齊藤 などの揺れは候補を見て確認）。状態・拠点・コードを返す | params: name
select p.code as patient_code, p.name as patient_name, p.kana, p.status, o.short_label as office,
       p.id as patient_id
from patients p
left join offices o on o.id = p.primary_office_id
where p.deleted_at is null
  and (p.code = :'name'
       or replace(replace(p.name, ' ', ''), '　', '') like '%' || replace(replace(:'name', ' ', ''), '　', '') || '%'
       or replace(replace(coalesce(p.kana, ''), ' ', ''), '　', '') like '%' || replace(replace(:'name', ' ', ''), '　', '') || '%')
order by (p.status = 'active') desc, p.code
limit 30
