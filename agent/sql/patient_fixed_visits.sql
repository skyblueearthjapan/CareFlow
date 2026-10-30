-- 固定訪問の枠（毎週の型）を 1 枠 1 行で。🔴赤ピン＝movability 'locked'。visit_week＝訪問する週（every 毎回／A=起点の週と同じ並び／B=その次の週・隔週の方だけ効く）。頻度（visit_frequency・biweekly_anchor・visit_weeks）も添える（隔週・月の第何週の方は枠を毎週と読まない）。patient は患者コードか氏名の一部・空なら稼働中の全員 | params: patient
select p.code as patient_code, p.name as patient_name, p.status as patient_status,
       f.weekday, (array['月','火','水','木','金','土','日'])[f.weekday + 1] as weekday_ja,
       to_char(f.start_time, 'HH24:MI') as start_jst, f.duration_min, f.slot_index,
       t.label as course_label, o.short_label as course_office,
       f.movability, (f.movability = 'locked') as red_pin, f.is_pinned, f.mode, f.visit_week,
       p.weekly_pattern->>'visit_frequency' as visit_frequency, p.weekly_pattern->>'biweekly_anchor' as biweekly_anchor,
       p.weekly_pattern->>'visit_weeks' as visit_weeks, f.id as fixed_visit_id
from patient_fixed_visits f
join patients p on p.id = f.patient_id
left join course_templates t on t.id = f.course_template_id
left join offices o on o.id = t.office_id
where p.deleted_at is null and p.name not like '【検証】%' and f.mode = 'normal'
  and ((:'patient' = '' and p.status = 'active')
       or (:'patient' <> '' and (p.code = :'patient'
           or replace(replace(p.name, ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%'
           or replace(replace(coalesce(p.kana, ''), ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%')))
order by p.code, f.weekday, f.start_time, f.slot_index
