-- 利用者の訪問条件（NG 職員・NG 曜日（その曜日には置けない・ハード）・女性/男性限定・2 名必須・希望の訪問パターン・訪問頻度（毎週／隔週＝起点の週から 2 週おき／月の第何週）・固定訪問の枠の要約（🔴＝赤ピン locked・A/B＝隔週の訪問する週）・同住所リンク）。patient は患者コードか氏名の一部・空なら条件のある稼働中の方すべて | params: patient
select p.code as patient_code, p.name as patient_name, p.status, o.short_label as office,
       p.sex_restriction, p.requires_multiple_staff,
       (select string_agg(s.name || coalesce('（' || n.note || '）', ''), '、' order by s.code)
          from patient_ng_staff n join staff s on s.id = n.staff_id where n.patient_id = p.id) as ng_staff,
       p.weekly_pattern->>'frequency_per_week' as pref_per_week,
       p.weekly_pattern->>'visit_frequency' as pref_frequency,
       p.weekly_pattern->>'biweekly_anchor' as biweekly_anchor,
       p.weekly_pattern->>'visit_weeks' as visit_weeks,
       (select string_agg((array['月','火','水','木','金','土','日'])[array_position(array['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], d)], '・'
                          order by array_position(array['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], d))
          from jsonb_array_elements_text(case when jsonb_typeof(p.weekly_pattern->'ng_weekdays') = 'array'
                                              then p.weekly_pattern->'ng_weekdays' else '[]'::jsonb end) d
          where d in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')) as ng_weekdays,
       p.weekly_pattern->>'time_type' as pref_time_type,
       p.weekly_pattern->>'preferred_start' as pref_start,
       p.weekly_pattern->>'preferred_end' as pref_end,
       p.weekly_pattern->>'service_minutes' as service_minutes,
       p.weekly_pattern->'preferred_weekdays' as pref_weekdays,
       (select string_agg((array['月','火','水','木','金','土','日'])[f.weekday + 1] || to_char(f.start_time, 'HH24:MI')
                          || '(' || f.duration_min || '分' || coalesce(' ' || t.label, '')
                          || case when f.movability = 'locked' then ' 🔴' else '' end
                          || case when f.visit_week in ('A', 'B') then ' ' || f.visit_week || 'の週' else '' end || ')',
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
            or exists (select 1 from patient_ng_staff n where n.patient_id = p.id)
            or jsonb_typeof(p.weekly_pattern->'ng_weekdays') = 'array' and jsonb_array_length(p.weekly_pattern->'ng_weekdays') > 0
            or p.weekly_pattern->>'visit_frequency' in ('biweekly', 'monthly')))
    or (:'patient' <> '' and (p.code = :'patient'
       or replace(replace(p.name, ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%'
       or replace(replace(coalesce(p.kana, ''), ' ', ''), '　', '') like '%' || replace(replace(:'patient', ' ', ''), '　', '') || '%'))
  )
order by p.code
