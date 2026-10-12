"""本番から「コースのならし」の材料を読み取り専用で取り出す（rk.sqlrun.run_sql・1 文の SELECT）。書き込みはしない。

読むもの（拠点・曜日で絞らず全部。絞るのは model.build_problem）:
  pfv        … 固定訪問の枠（mode=normal）。稼働中・削除なし・検証用（【検証】）を除く患者だけ
  patients   … 上の患者の名前・座標・主担当拠点・性別制限・希望（weekly_pattern）
  candidates … まだ固定訪問の枠が 1 つも無い方（稼働中・開始前）。コースの作業場（template_ws）が希望の中で新しい枠を置く
  templates  … コース（course_templates・拠点ごと・削除なし）
  usual      … ふだんの担当: 直近 4 週＋今週の週のコース（courses）の担当を (テンプレート, 曜日, 職員) ごとに数えたもの
  staff / shifts / event_defaults … 稼働中の職員・勤務の型・固定イベント（毎週の予定・壁時計）
  ng / offices / scheduling_settings
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

RunSql = Callable[[str], Any]

TEST_PREFIX = "【検証】"


def problem_sql(today: date) -> str:
    monday = today - timedelta(days=today.weekday())
    since = monday - timedelta(days=28)
    until = monday + timedelta(days=6)
    return f"""
select json_build_object(
 'extracted_at', now(),
 'reference_monday', '{monday}',
 'usual_from', '{since}', 'usual_to', '{until}',
 'offices', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select id, short_label, lat, lng, operating_weekdays from offices where deleted_at is null) x),
 'templates', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select t.id, t.office_id, t.label,
          json_build_array(t.capacity_mon, t.capacity_tue, t.capacity_wed, t.capacity_thu,
                           t.capacity_fri, t.capacity_sat, t.capacity_sun) capacity
   from course_templates t join offices o on o.id = t.office_id
   where t.deleted_at is null and o.deleted_at is null) x),
 'pfv', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select f.id, f.patient_id, f.weekday, to_char(f.start_time, 'HH24:MI') start_time, f.duration_min,
          f.course_template_id, f.sub_office_id, f.slot_index, f.movability, f.is_pinned
   from patient_fixed_visits f join patients p on p.id = f.patient_id
   where f.mode = 'normal' and p.deleted_at is null and p.status = 'active'
     and p.name not like '{TEST_PREFIX}%'
   order by f.patient_id, f.weekday, f.slot_index) x),
 'patients', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select p.id, p.code, p.name, p.sex_restriction, p.lat, p.lng, p.primary_office_id, p.weekly_pattern,
          p.requires_multiple_staff
   from patients p
   where p.deleted_at is null and p.status = 'active' and p.name not like '{TEST_PREFIX}%'
     and exists (select 1 from patient_fixed_visits f where f.patient_id = p.id and f.mode = 'normal')) x),
 'candidates', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select p.id, p.code, p.name, p.status, p.sex_restriction, p.lat, p.lng, p.primary_office_id, p.weekly_pattern,
          p.requires_multiple_staff
   from patients p
   where p.deleted_at is null and p.status in ('active', 'pending') and p.name not like '{TEST_PREFIX}%'
     and not exists (select 1 from patient_fixed_visits f where f.patient_id = p.id and f.mode = 'normal')) x),
 'usual', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select c.template_id, c.weekday, c.assigned_staff_id staff_id, count(*) n,
          max(make_date(c.iso_year, 1, 4) + (c.iso_week - 1) * 7) last_week
   from courses c
   where c.deleted_at is null and c.course_status <> 'proposed' and c.template_id is not null
     and c.assigned_staff_id is not null
     and (make_date(c.iso_year, 1, 4) + (c.iso_week - 1) * 7) between '{since - timedelta(days=7)}' and '{until + timedelta(days=7)}'
   group by 1, 2, 3) x),
 'staff', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select s.id, s.code, s.name, s.sex, s.role, s.is_trainee, s.primary_office_id
   from staff s where s.status = 'active' and s.deleted_at is null) x),
 'shifts', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select staff_id, weekday, is_on, to_char(start_time, 'HH24:MI') start_time, to_char(end_time, 'HH24:MI') end_time
   from staff_shifts) x),
 'event_defaults', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select staff_id, weekday, to_char(start_time, 'HH24:MI') start_time, to_char(end_time, 'HH24:MI') end_time
   from staff_event_defaults) x),
 'ng', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select patient_id, staff_id from patient_ng_staff) x),
 'scheduling_settings', (select row_to_json(x) from (
   select visit_buffer_min, travel_speed_kmh from scheduling_settings where is_singleton limit 1) x)
)"""


def unwrap(result: Any) -> Any:
    """rk.sqlrun.run_sql の戻り値 [ {"json_build_object": {...}} ] から中身を取り出す。"""
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict) and len(result[0]) == 1:
        (value,) = result[0].values()
        if isinstance(value, str) and value[:1] in "{[":
            value = json.loads(value)
        return value
    return result


def extract(run_sql: RunSql, today: date, out_dir: Path) -> Path:
    """本番から読み、raw.json（利用者名入り・docs/reports の下だけに置く）を書く。"""
    raw = unwrap(run_sql(problem_sql(today)))
    if not isinstance(raw, dict) or not raw.get("pfv"):
        raise RuntimeError("固定訪問の枠が読めませんでした")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "raw.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def rk_runner(cfg) -> RunSql:
    from rk.sqlrun import run_sql as rk_run_sql

    return lambda sql: rk_run_sql(cfg, sql)
