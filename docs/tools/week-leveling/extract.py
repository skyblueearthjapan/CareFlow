"""本番から 1 週分の材料を読み取り専用で取り出す (ssh → psql)。書き込みは一切しない。

出力: <out>/week.json (訪問・利用者・職員・勤務・休み・NG・予定・事業所・移動の設定。固定枠は読まない) と
      <out>/history.json (直前 4 週の担当歴 = ローテーション用)。利用者名を含むので git に入れない。
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path


def _week_sql(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    iso_year, iso_week, _ = monday.isocalendar()
    return f"""
select json_build_object(
 'extracted_at', now(),
 'week_start', '{monday}',
 'visits', (select json_agg(row_to_json(x)) from (
   select v.id, v.patient_id, v.visit_date, v.start_time, v.end_time, v.required_staff_count,
          v.visit_group_id, v.source, v.week_pinned, v.status, v.primary_staff_id, v.secondary_staff_id,
          c.code course_code, co.short_label course_office
   from visits v left join courses c on c.id=v.course_id left join offices co on co.id=c.office_id
   where v.visit_date between '{monday}' and '{sunday}' and v.deleted_at is null and v.status<>'cancelled') x),
 'patients', (select json_agg(row_to_json(x)) from (
   select p.id, p.name, p.sex, p.sex_restriction, p.lat, p.lng, p.primary_office_id, p.weekly_pattern, p.status
   from patients p where p.id in (select patient_id from visits
     where visit_date between '{monday}' and '{sunday}' and deleted_at is null)) x),
 'ng', (select json_agg(row_to_json(x)) from (select patient_id, staff_id from patient_ng_staff) x),
 -- 移動の速さ・ゆとり (アプリの診断と同じ設定。0 行なら既定値)
 'scheduling_settings', (select row_to_json(x) from (
   select visit_buffer_min, travel_speed_kmh from scheduling_settings where is_singleton limit 1) x),
 'offices', (select json_agg(row_to_json(x)) from (
   select id, name, short_label, lat, lng, operating_weekdays from offices where deleted_at is null) x),
 'staff', (select json_agg(row_to_json(x)) from (
   select s.id, s.code, s.name, s.role, s.sex, s.is_trainee, s.primary_office_id
   from staff s where s.status='active' and s.deleted_at is null) x),
 'shifts', (select json_agg(row_to_json(x)) from (select staff_id, weekday, is_on, start_time, end_time from staff_shifts) x),
 'weekly_overrides', (select json_agg(row_to_json(x)) from (
   select staff_id, weekday, override_type, start_time, end_time from staff_weekly_overrides
   where iso_year={iso_year} and iso_week={iso_week}) x),
 -- 予定の時刻は「壁時計を UTC として保存」の決まり (backend/Dockerfile) なので UTC で読む
 'events', (select json_agg(row_to_json(x)) from (
   select staff_id,
          to_char(starts_at at time zone 'UTC', 'YYYY-MM-DD') as date,
          to_char(starts_at at time zone 'UTC', 'HH24:MI') as start,
          to_char(ends_at at time zone 'UTC', 'HH24:MI') as "end",
          to_char(ends_at at time zone 'UTC', 'YYYY-MM-DD') as end_date
   from staff_events
   where cancelled_at is null
     and (starts_at at time zone 'UTC') < '{sunday + timedelta(days=1)}'
     and (ends_at at time zone 'UTC') > '{monday}') x)
);"""


def _history_sql(monday: date) -> str:
    return f"""
select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
 select v.patient_id, v.visit_date, v.start_time, v.primary_staff_id
 from visits v
 where v.visit_date between '{monday - timedelta(days=28)}' and '{monday - timedelta(days=1)}'
   and v.deleted_at is null and v.status<>'cancelled' and v.primary_staff_id is not null
 order by v.visit_date, v.start_time) x;"""


def _psql(server: str, container: str, sql: str) -> str:
    # 読み取り専用のトランザクションで流す (誤って書く文が混ざっても DB が拒否する)
    body = "begin transaction read only;\n" + sql + "\ncommit;\n"
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        server,
        f"docker exec -i {container} psql -U carelink -d carelink -v ON_ERROR_STOP=1 -At",
    ]
    res = subprocess.run(cmd, input=body.encode("utf-8"), capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(
            "本番からの読み取りに失敗しました: " + res.stderr.decode("utf-8", "replace")[:500]
        )
    lines = [ln for ln in res.stdout.decode("utf-8").splitlines() if ln.startswith(("{", "["))]
    if not lines:
        raise RuntimeError(
            "psql の出力に JSON がありません: " + res.stderr.decode("utf-8", "replace")[:300]
        )
    return lines[0]


def extract(monday: date, out: Path, server: str, container: str) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    week_path, hist_path = out / "week.json", out / "history.json"
    week = json.loads(_psql(server, container, _week_sql(monday)))
    if not week.get("visits"):
        raise RuntimeError(f"{monday} の週に訪問がありません")
    week_path.write_text(json.dumps(week, ensure_ascii=False), encoding="utf-8")
    hist_path.write_text(_psql(server, container, _history_sql(monday)), encoding="utf-8")
    return week_path, hist_path
