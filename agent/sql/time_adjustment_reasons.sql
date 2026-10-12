-- 実績の時刻を「合わせた」件数を理由別に（訪問 × 到着/退出ごとの最新 1 行だけが有効。adjusted_at が NULL＝読取時刻に戻したもの） | params: date_from, date_to
with latest as (
  select distinct on (a.visit_id, a.kind) a.visit_id, a.kind, a.adjusted_at, a.reason_code, a.source
  from visit_time_adjustments a
  order by a.visit_id, a.kind, a.created_at desc, a.id desc
)
select l.kind, coalesce(l.reason_code, '(なし)') as reason_code,
       case l.reason_code when 'intercom_wait' then 'インターホン待ち' when 'read_later' then '読み取りが後'
                          when 'no_read' then '読み取りなし' when 'other' then 'その他' else l.reason_code end as reason_ja,
       count(*) filter (where l.adjusted_at is not null) as adjusted,
       count(*) filter (where l.adjusted_at is null) as reverted_to_read,
       count(*) filter (where l.source = 'pc') as by_pc,
       count(*) filter (where l.source = 'mobile') as by_mobile
from latest l
join visits v on v.id = l.visit_id
join patients p on p.id = v.patient_id
where v.visit_date between :'date_from'::date and :'date_to'::date
  and p.name not like '【検証】%'
group by l.kind, l.reason_code
order by l.kind, adjusted desc
