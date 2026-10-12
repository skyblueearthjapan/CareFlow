-- 利用者の訪問条件（NG 職員・女性限定・2 名必須・希望の訪問パターン・固定訪問の枠の要約（🔴＝赤ピン locked）・同住所リンク）。patient は患者コードか氏名の一部・空なら条件のある稼働中の方すべて | params: patient
select p.code as patient_code, p.name as patient_name, p.status, o.short_label as office,
       p.sex_restriction, p.requires_multiple_staff,
       (select string_agg(s.name || coalesce('（' || n.note || '）', ''), '、' order by s.code)
          from patient_ng_staff n join staff s on s.id = n.staff_id where n.patient_id = p.id) as ng_staff,
       p.weekly_pattern->>'frequency_per_week' as pref_per_week,
       p.weekly_pattern->>'visit_frequency' as pref_frequency,
       p.weekly_pattern->>'time_type' as pref_time_type,
       p.weekly_pattern->>'preferred_start' as pref_start,
       p.weekly_pattern->>'preferred_end' as pref_end,
       p.weekly_pattern->>'service_minutes' as service_minutes,
       p.weekly_pattern->'preferred_weekdays' as pref_weekdays,
       (select string_agg((array['月','火','水','木','金','土','日'])[f.weekday + 1] || to_char(f.start_time, 'HH24:MI')
                          || '(' || f.duration_min || '分' || coalesce(' ' || t.label, '')
                          || case when f.movability = 'locked' then ' 🔴' else '' end || ')',
                          ' / ' order by f.weekday, f.start_time, f.slot_index)
          from patient_fixed_visits f left join course_templates t on t.id = f.course_template_id
          where f.patient_id = p.id and f.mode = 'normal') as fixed_slots,
       (select count(*) from patient_same_address_links l
          where l.patient_a_id = p.id or l.patient_b_id = p.id) as same_address_links
from patients p
left join offices o on o.id = p.primary_office_id
where p.deleted_at is null and p.name not like '【検証】%'
  and (
    (:'patient' = '' and p.status = 'active'
       and (p.sex_restriction is not null or p.requires_multiple_staff
            or exists (select 1 from patient_ng_staff n where n.patient_id = p.id)))
    or (:'patient' <> '' and (p.code = :'patient'
       or replace(replace(p.name, ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%'
       or replace(replace(coalesce(p.kana, ''), ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%'))
  )
order by p.code
