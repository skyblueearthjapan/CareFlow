-- 稼働中の職員の一覧（コード・拠点・業務ロール・新人・資格・性別・曜日ごとの勤務時刻）。名前の同定にも使う | params: (なし)
select s.code as staff_code, s.name as staff_name, s.kana, s.sex, s.role, s.is_trainee, s.qualification,
       o.short_label as office,
       (select string_agg((array['月','火','水','木','金','土','日'])[sh.weekday + 1] || ' '
                          || to_char(sh.start_time, 'HH24:MI') || '-' || to_char(sh.end_time, 'HH24:MI'),
                          ' / ' order by sh.weekday)
        from staff_shifts sh where sh.staff_id = s.id and sh.is_on) as shifts,
       (select string_agg(so.short_label, ',') from staff_secondary_offices x
          join offices so on so.id = x.office_id where x.staff_id = s.id) as support_offices
from staff s
left join offices o on o.id = s.primary_office_id
where s.status = 'active' and s.deleted_at is null
order by s.code
