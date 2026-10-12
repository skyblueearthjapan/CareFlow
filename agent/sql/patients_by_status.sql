-- 状態ごとの利用者（status: active 稼働中 / admitted 入院中 / suspended 一時休止 / pending 開始前 / cancelled 終了。空なら稼働中以外すべて）。いつから＝status_changed_at（日本時間・NULL は不明）。今日以降に予定（planned）が残っている件数つき | params: status
select p.code as patient_code, p.name as patient_name, p.status,
       case p.status when 'active' then '稼働中' when 'admitted' then '入院中' when 'suspended' then '一時休止'
                     when 'inactive' then '一時休止(旧)' when 'pending' then '開始前' when 'cancelled' then '終了'
                     else p.status end as status_ja,
       (p.status_changed_at at time zone 'Asia/Tokyo')::date as since_jst,
       o.short_label as office,
       (select count(*) from visits v where v.patient_id = p.id and v.deleted_at is null
          and v.status = 'planned' and v.visit_date >= (now() at time zone 'Asia/Tokyo')::date) as planned_from_today,
       (select count(*) from patient_fixed_visits f where f.patient_id = p.id and f.mode = 'normal') as fixed_slots
from patients p
left join offices o on o.id = p.primary_office_id
where p.deleted_at is null and p.name not like '【検証】%'
  and ((:'status' = '' and p.status <> 'active') or p.status = :'status')
order by p.status, since_jst nulls first, p.code
