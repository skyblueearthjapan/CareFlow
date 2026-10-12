-- 拠点 × 状態ごとの利用者数（主担当拠点＝拠点の正典。【検証】を除く。開始前 pending は稼働中に数えない） | params: (なし)
select coalesce(o.short_label, '(拠点なし)') as office, o.name as office_name, p.status,
       count(*) as patients
from patients p
left join offices o on o.id = p.primary_office_id
where p.deleted_at is null and p.name not like '【検証】%'
group by o.short_label, o.name, p.status
order by office, p.status
