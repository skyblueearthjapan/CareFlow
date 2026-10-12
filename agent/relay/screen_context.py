"""画面の文脈（どの画面から話しかけられたか・AGENTS.md §11）。

画面が発話に添える小さな手がかり ``{screen, iso_year?, iso_week?, date?, patient_id?, staff_id?, draft_id?,
tab?, direction?, period?}`` を厳しく確かめ、エンジンへ送る文の頭に付ける 1 行の日本語にする。
名前は受け取らない（ID・数・短い決まった言葉だけ）。文として注ぎ込まれる余地を残さないため、
知らない鍵・長い文字列・決まった形でない値はまとめて断る（画面も同じ規則で整えてから送る）。
"""

from __future__ import annotations

import json
import re
from datetime import date as _date
from typing import Any

#: 画面の名前 → 文脈行での呼び名
SCREENS: dict[str, str] = {
    "schedule": "週の予定",
    "patients": "患者マスタ",
    "staff": "職員マスタ",
    "template_ws": "コースの作業場",
    "records": "訪問記録",
    "monitor": "訪問モニター",
    "integrations": "カイポケ連携",
    "dashboard": "分析",
    "special_week": "特別訪問週間",
    "leave": "休み申請",
    "settings": "設定",
    "other": "その他の画面",
}
DIRECTIONS = {"send": "送る", "receive": "取り込む"}
KEYS = {"screen", "iso_year", "iso_week", "date", "patient_id", "staff_id", "draft_id", "tab", "direction", "period"}
#: JSON にしたときの上限（字）。ふつうは 200 字ほど。
MAX_CONTEXT_CHARS = 400

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_PERIOD = re.compile(r"^(?:\d{4}-\d{2}(?:-\d{2})?(?:\.\.\d{4}-\d{2}(?:-\d{2})?)?|[a-z][a-z0-9_]{0,15})$")


class ContextError(ValueError):
    """画面の文脈の形が違う（画面へは 400 で返す）。"""


def _int(v: Any, lo: int, hi: int, key: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ContextError(f"画面の文脈の {key} は {lo}〜{hi} の数です")
    return v


def _id(v: Any, key: str) -> int | str:
    if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2 ** 53:
        return v
    if isinstance(v, str) and _UUID.match(v.lower()):
        return v.lower()
    raise ContextError(f"画面の文脈の {key} は ID（数か UUID）です")


def validate_context(raw: Any) -> dict | None:
    """画面の文脈を確かめて、整えた dict を返す（無ければ None）。形が違えば ContextError。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ContextError("画面の文脈の形が違います")
    if len(json.dumps(raw, ensure_ascii=False, default=str)) > MAX_CONTEXT_CHARS:
        raise ContextError("画面の文脈が長すぎます")
    unknown = set(raw) - KEYS
    if unknown:
        raise ContextError("画面の文脈に知らない項目があります")
    screen = raw.get("screen")
    if not isinstance(screen, str) or screen not in SCREENS:
        raise ContextError("画面の文脈の screen が違います")
    out: dict[str, Any] = {"screen": screen}
    for key, val in raw.items():
        if key == "screen" or val is None:
            continue
        if key == "iso_year":
            out[key] = _int(val, 2000, 2100, key)
        elif key == "iso_week":
            out[key] = _int(val, 1, 53, key)
        elif key == "date":
            if not isinstance(val, str) or not _DATE.match(val):
                raise ContextError("画面の文脈の date は YYYY-MM-DD です")
            try:
                _date.fromisoformat(val)
            except ValueError:
                raise ContextError("画面の文脈の date が日付ではありません") from None
            out[key] = val
        elif key in ("patient_id", "staff_id", "draft_id"):
            out[key] = _id(val, key)
        elif key == "tab":
            if not isinstance(val, str) or not _SLUG.match(val):
                raise ContextError("画面の文脈の tab が違います")
            out[key] = val
        elif key == "direction":
            if val not in DIRECTIONS:
                raise ContextError("画面の文脈の direction は send か receive です")
            out[key] = val
        elif key == "period":
            if not isinstance(val, str) or not _PERIOD.match(val):
                raise ContextError("画面の文脈の period が違います")
            out[key] = val
    return out


def context_line(ctx: dict | None) -> str:
    """確かめ済みの文脈 → エンジンへの文の頭に付ける 1 行（無ければ空）。"""
    if not ctx:
        return ""
    parts: list[str] = []
    if "iso_year" in ctx and "iso_week" in ctx:
        parts.append(f"{ctx['iso_year']} 年 第 {ctx['iso_week']} 週")
    if "date" in ctx:
        parts.append(f"日付 {ctx['date']}")
    for key in ("patient_id", "staff_id", "draft_id", "tab", "period"):
        if key in ctx:
            parts.append(f"{key}={ctx[key]}")
    if "direction" in ctx:
        parts.append(f"向き={DIRECTIONS[ctx['direction']]}")
    detail = f"（{'・'.join(parts)}）" if parts else ""
    return f"〔画面の文脈〕いまの画面: {SCREENS[ctx['screen']]}{detail}。手がかりであって縛りではありません。"
