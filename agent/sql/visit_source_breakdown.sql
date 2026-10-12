-- 期間の訪問を 出所（source）× 状態 で数える（削除済みは別列）。取消の内訳（manual_cancel 今週だけ取消 / status_cancel 患者の非稼働化 / その他）を見るのに使う | params: date_from, date_to
select v.source, v.status,
       count(*) filter (where v.deleted_at is null) as rows_alive,
       count(*) filter (where v.deleted_at is not null) as rows_deleted
from visits v
join patients p on p.id = v.patient_id
where v.visit_date between :'date_from'::date and :'date_to'::date
  and p.name not like '【検証】%'
group by v.source, v.status
order by v.source, v.status
