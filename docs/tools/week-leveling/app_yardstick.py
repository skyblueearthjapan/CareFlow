"""アプリの「スケジュール診断」の計算をそのまま呼ぶ (backend の環境で動かす・DB には繋がない)。

diagnose.py が別のプロセスで呼ぶ。標準入力の JSON:
  {"settings": {"visit_buffer_min": .., "travel_speed_kmh": ..} | null,
   "routes": {"<key>": [{"patient_id", "start", "end", "lat", "lng"}, ...]}}
標準出力の JSON:
  {"config": {...}, "threshold_min": 10,
   "metrics": {"<key>": {"visit_count", "travel_minutes", "travel_km", "buffer_minutes", "gap_minutes"}}}

距離・移動時間・同じ住所・ゆとり・待ち時間は schedule_health._compute_course_metrics (純関数) が決める。
ここで式を書き直さない (アプリと道具の物差しを 1 つにするため)。
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, replace
from datetime import time

from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from app.services.scheduling.improvement_engine import IMPROVEMENT_THRESHOLD_MIN
from app.services.scheduling.schedule_health import (
    _compute_course_metrics,
    _HealthCourse,
    _HealthVisit,
)

_DUMMY = uuid.UUID(int=0)


def _t(m: int) -> time:
    if not 0 <= m < 24 * 60:
        raise ValueError(f"時刻が 0:00〜23:59 の外です（{m} 分）")
    return time(m // 60, m % 60)


def main() -> None:
    req = json.load(sys.stdin)
    cfg = DEFAULT_SCHEDULING_CONFIG
    s = req.get("settings") or {}
    if s.get("visit_buffer_min") is not None:
        cfg = replace(cfg, visit_buffer_min=int(s["visit_buffer_min"]))
    if s.get("travel_speed_kmh") is not None:
        cfg = replace(cfg, travel_speed_kmh=float(s["travel_speed_kmh"]))
    metrics = {}
    for key, visits in req["routes"].items():
        bucket = _HealthCourse(office_id=_DUMMY, weekday=0, course_code=key)
        bucket.visits = [
            _HealthVisit(
                patient_id=uuid.UUID(v["patient_id"]),
                start_time=_t(v["start"]),
                end_time=_t(v["end"]),
                lat=v["lat"],
                lng=v["lng"],
            )
            for v in visits
        ]
        course, raw_km = _compute_course_metrics(bucket, config=cfg)
        metrics[key] = {
            "visit_count": course.visit_count,
            "travel_minutes": course.travel_minutes,
            "travel_km": raw_km,
            "buffer_minutes": course.buffer_minutes,
            "gap_minutes": course.gap_minutes,
        }
    conf = {k: (v.isoformat() if isinstance(v, time) else v) for k, v in asdict(cfg).items()}
    json.dump(
        {"config": conf, "threshold_min": IMPROVEMENT_THRESHOLD_MIN, "metrics": metrics},
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
