"""本番から 1 週分の材料を読み取り専用で取り出す（rk の SQL の道を使う）。書き込みは一切しない。

extract.py（docs/tools/week-leveling）の SQL をそのまま使い、走らせ方だけを差し替える:
  run_sql(sql: str) -> Any   … 1 文の SELECT を読み取り専用で流し、結果を返す関数（rk.sqlrun.run_sql を包んだもの）。
                               戻り値は「JSON の配列（rk の形）」か「psql の出力の文字列（extract._psql の形）」のどちらでもよい。

出力（out_dir）:
  week.json     … extract.py と同じ形（ならしの計算の入力）
  history.json  … 直前 4 週の担当歴（同じ）
  agent_extra.json … 案（plan.json）を作るために足したもの: その週のコース・コースのテンプレート・手の上書きの訪問
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from tools.leveling import LEVELING_DIR

RunSql = Callable[[str], Any]


def load_extract_module():
    """docs/tools/week-leveling/extract.py を別名で読み込む（sys.path を汚さない・中身は変えない）。"""
    spec = importlib.util.spec_from_file_location("wl_extract", LEVELING_DIR / "extract.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def unwrap(result: Any) -> Any:
    """run_sql の戻り値から、SELECT の 1 行 1 列の JSON を取り出す。

    - 文字列（psql の -At の出力）: { か [ で始まる最初の行を読む
    - rk の形 [ {"列名": 値} ]（json_agg(t) で包まれる）: 1 行 1 列なら その値
    """
    if isinstance(result, (bytes, bytearray)):
        result = result.decode("utf-8")
    if isinstance(result, str):
        lines = [ln for ln in result.splitlines() if ln.startswith(("{", "["))]
        if not lines:
            raise RuntimeError("SQL の出力に JSON がありません: " + result[:300])
        result = json.loads(lines[0])
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict) and len(result[0]) == 1:
        (value,) = result[0].values()
        if isinstance(value, str) and value[:1] in "{[":
            value = json.loads(value)
        return value
    return result


def extra_sql(monday: date) -> str:
    """案を作るのに足すもの（1 文）。コースの担当を付ける先・テンプレート ID・手の上書きの訪問。"""
    sunday = monday + timedelta(days=6)
    iso_year, iso_week, _ = monday.isocalendar()
    return f"""
select json_build_object(
 'week_start', '{monday}',
 'courses', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select c.id, c.weekday, c.code, c.template_id, c.assigned_staff_id, c.office_id, o.short_label as office
   from courses c join offices o on o.id = c.office_id
   where c.iso_year = {iso_year} and c.iso_week = {iso_week} and c.deleted_at is null
   order by c.weekday, o.short_label, c.code) x),
 'templates', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select t.id, t.label, t.office_id, o.short_label as office
   from course_templates t join offices o on o.id = t.office_id
   where t.deleted_at is null and o.deleted_at is null
   order by o.short_label, t.label) x),
 'manual_overrides', (select coalesce(json_agg(row_to_json(x)), '[]'::json) from (
   select v.id, v.primary_staff_id, v.visit_date
   from visits v
   where v.visit_date between '{monday}' and '{sunday}' and v.deleted_at is null
     and v.status = 'planned' and v.manual_staff_override) x)
)"""


STAFF_CONFIG_SQL = """
select coalesce(json_agg(row_to_json(x)), '[]'::json) as staff from (
  select s.code, s.role, s.is_trainee, o.short_label as office,
         (select coalesce(json_agg(so.short_label order by so.short_label), '[]'::json)
            from staff_secondary_offices sx join offices so on so.id = sx.office_id
           where sx.staff_id = s.id and so.deleted_at is null) as support_offices
  from staff s
  left join offices o on o.id = s.primary_office_id and o.deleted_at is null
  where s.status = 'active' and s.deleted_at is null and s.code is not null
  order by s.code) x"""


def config_from_staff(rows: list[dict], cap_regular: int = 6) -> dict:
    """職員の記録（稼働中・職員コード・業務ロール・主担当拠点・応援拠点）から、ならしの config を作る。
    config.local.json と同じ形: 正規（role が manager 以外）= {コード: 主担当拠点}、
    マネージャー = {コード: [主担当拠点 + 応援拠点]}。新人も正規に入れる（trainee_off が週全部を休みにして外す）。
    拠点の無い人は入れない（道具が見ない）。事業所ごとの名前はコードに書かない（記録から読む）。"""
    regular: dict[str, str] = {}
    manager: dict[str, list[str]] = {}
    for r in rows or []:
        code = r.get("code")
        support = r.get("support_offices") or []
        if isinstance(support, str):
            support = json.loads(support) if support[:1] == "[" else [x for x in support.split(",") if x]
        if not code:
            continue
        if r.get("role") == "manager":
            offices = list(dict.fromkeys([o for o in [r.get("office"), *support] if o]))
            if offices:
                manager[code] = offices
        elif r.get("office"):
            regular[code] = r["office"]
    return {"cap_regular": int(cap_regular), "regular": regular, "manager": manager}


def config_from_db(run_sql: RunSql, cap_regular: int = 6) -> dict:
    """本番の職員の記録から config を作る（読み取り専用・1 文）。"""
    rows = unwrap(run_sql(STAFF_CONFIG_SQL))
    if not isinstance(rows, list):
        raise RuntimeError("職員の一覧を読めませんでした")
    return config_from_staff(rows, cap_regular)


def extract_week(run_sql: RunSql, monday: date, out_dir: Path) -> tuple[Path, Path, Path]:
    """本番から読み、week.json / history.json / agent_extra.json を書く。"""
    ex = load_extract_module()
    out_dir.mkdir(parents=True, exist_ok=True)
    week = unwrap(run_sql(ex._week_sql(monday)))
    if not isinstance(week, dict) or not week.get("visits"):
        raise RuntimeError(f"{monday} の週に訪問がありません")
    hist = unwrap(run_sql(ex._history_sql(monday)))
    if hist is None:
        hist = []
    extra = unwrap(run_sql(extra_sql(monday)))
    week_path = out_dir / "week.json"
    hist_path = out_dir / "history.json"
    extra_path = out_dir / "agent_extra.json"
    week_path.write_text(json.dumps(week, ensure_ascii=False), encoding="utf-8")
    hist_path.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    extra_path.write_text(json.dumps(extra, ensure_ascii=False), encoding="utf-8")
    return week_path, hist_path, extra_path


def rk_runner(cfg) -> RunSql:
    """rk の読み取り専用 SQL（rk.sqlrun.run_sql）を run_sql(sql) の形に包む。"""
    from rk.sqlrun import run_sql as rk_run_sql

    return lambda sql: rk_run_sql(cfg, sql)


def legacy_runner(server: str, container: str) -> RunSql:
    """rk が使えないときの予備: extract.py と同じ ssh → psql（読み取り専用トランザクション）。"""
    ex = load_extract_module()
    return lambda sql: ex._psql(server, container, sql.strip().rstrip(";") + ";")
