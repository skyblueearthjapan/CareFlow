-- 希望の週回数と固定訪問の枠の数（曜日 × 時刻の異なる枠。2 名体制の 2 枠は 1 つに数える）が違う稼働中の方（例: 週 3 回希望・枠 2 つ＝毎週プールに残る）。毎週以外（隔週・月 1 など）は参考として出す | params: (なし)
with f as (
  select f.patient_id, count(distinct (f.weekday, f.start_time)) as slots
  from patient_fixed_visits f where f.mode = 'normal' group by 1
)
select p.code as patient_code, p.name as patient_name, o.short_label as office,
       p.weekly_pattern->>'visit_frequency' as pref_frequency,
       p.weekly_pattern->>'frequency_per_week' as pref_per_week,
       coalesce(f.slots, 0) as fixed_slots,
       p.weekly_pattern->'preferred_weekdays' as pref_weekdays,
       coalesce(p.weekly_pattern->>'visit_frequency', 'every') <> 'every' as not_weekly
from patients p
left join f on f.patient_id = p.id
left join offices o on o.id = p.primary_office_id
where p.deleted_at is null and p.status = 'active' and p.name not like '【検証】%'
  and coalesce(p.weekly_pattern->>'frequency_per_week', '') ~ '^[0-9]+$'
  and (p.weekly_pattern->>'frequency_per_week')::int <> coalesce(f.slots, 0)
order by not_weekly, p.code
