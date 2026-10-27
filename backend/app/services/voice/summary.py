"""③ まとめ — 看護記録の下書き (Markdown の本文) と事実の JSON を寛容に読み分ける.

正典: ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3 P3。
読み方は PC-Assistant の ``MinutesReply.Split`` を写した:

* 事実のブロック = 事実のキー (``prompts.FACT_KEYS``) を含む ``{`` で始まるコードブロックで、
  答えの **最後** (後ろに何も無い) か **最初** (前に何も無い) にあるもの。閉じ忘れは最後まで。
  それ以外のコードブロックは本文の一部として残す。
* フェンスが無くても、最後の行から始まる ``{…}`` が事実のキーを含めば事実のブロック。
* JSON が壊れていても本文は活かす (ブロックは本文から除き、事実は空)。
* 答え全体が ```markdown で包まれていたら外す。
* 本文が空なら失敗 (``VoiceAiError('parse')``)。事実だけの答えも失敗。

保存の形: ``summary = {"format": "v2", "body": 本文, "facts": {...}}``・``summary_text = 本文``。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.voice.prompts import FACT_KEYS, VITAL_KEYS
from app.services.voice.vertex_client import SUMMARY_FORMAT_V2, VoiceAiError

_FENCE_OPEN = re.compile(r"^[ \t]*```[ \t]*[\w+-]*[ \t]*$", re.M)
_FENCE_CLOSE = re.compile(r"^[ \t]*```[ \t]*$", re.M)
_MARKDOWN_FENCE = re.compile(r"^```[ \t]*(markdown|md)?[ \t]*\n", re.I)
_PLACEHOLDERS = {"…", "...", "……", "m:ss"}


def _is_facts(inner: str) -> bool:
    return inner.lstrip().startswith("{") and any(f'"{key}"' in inner for key in FACT_KEYS)


def _object(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _fences(text: str) -> list[tuple[int, int, str]]:
    """コードブロック (開きの行の頭, 閉じの行の終わり, 中身)。閉じ忘れは最後まで。"""
    found: list[tuple[int, int, str]] = []
    at = 0
    while at < len(text):
        opened = _FENCE_OPEN.search(text, at)
        if not opened:
            break
        inner_from = min(len(text), opened.end() + 1)
        closed = _FENCE_CLOSE.search(text, inner_from)
        end = closed.end() if closed else len(text)
        found.append((opened.start(), end, text[inner_from : closed.start() if closed else end]))
        at = max(end, opened.end() + 1)
    return found


def _unwrapped(text: str) -> str:
    opened = _MARKDOWN_FENCE.match(text)
    if not opened:
        return text
    inner = text[opened.end() :]
    close = inner.rfind("```")
    if close >= 0 and inner[close:].strip() == "```":
        inner = inner[:close]
    return inner.strip()


def split_reply(text: str) -> tuple[str, dict[str, Any] | None]:
    """答え → (本文, 事実の dict or None)。事実のブロックは本文から除く。"""
    t = (text or "").replace("\r\n", "\n").strip()
    if t.startswith("{") and t.endswith("}") and (whole := _object(t)) is not None:
        return "", whole
    facts: dict[str, Any] | None = None
    found = False
    fences = _fences(t)
    for start, end, inner in (fences[-1], fences[0]) if fences else ():
        if not _is_facts(inner):
            continue
        last, first = not t[end:].strip(), not t[:start].strip()
        if not last and not first:
            continue
        found = True
        facts = _object(inner)
        t = (t[:start] + "\n" + t[end:]).strip()
        break
    if not found:
        at = t.rfind("\n{")
        if at >= 0 and t.endswith("}") and _is_facts(t[at + 1 :]):
            tail = _object(t[at + 1 :])
            if tail is not None:
                facts = tail
                t = t[:at].rstrip()
    return _unwrapped(t).strip(), facts


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    s = str(value).strip()
    return "" if s in _PLACEHOLDERS else s


def normalize_facts(raw: Any) -> dict[str, Any]:
    """事実の JSON を決まった形にそろえる (足りない項目は空・余計な項目と「…」は捨てる)。"""
    src = raw if isinstance(raw, dict) else {}
    vitals_raw = src.get("vitals") if isinstance(src.get("vitals"), dict) else {}
    handover_raw = src.get("handover")
    if isinstance(handover_raw, str):
        handover_raw = [handover_raw]
    handover = [_text(v) for v in handover_raw] if isinstance(handover_raw, list) else []
    next_raw = src.get("next")
    if isinstance(next_raw, dict):
        next_raw = next_raw.get("text")
    checks: list[dict[str, str]] = []
    checks_raw = src.get("needs_check")
    for item in checks_raw if isinstance(checks_raw, list) else []:
        if isinstance(item, dict):
            text, at = _text(item.get("text")), _text(item.get("time"))
        else:
            text, at = _text(item), ""
        if text:
            checks.append({"text": text, "time": at})
    return {
        "vitals": {key: _text(vitals_raw.get(key)) for key in VITAL_KEYS},
        "handover": [h for h in handover if h],
        "next": _text(next_raw),
        "needs_check": checks,
    }


def build_summary(body: str, facts: Any) -> dict[str, Any]:
    """保存する ``summary`` (v2)。"""
    return {"format": SUMMARY_FORMAT_V2, "body": body, "facts": normalize_facts(facts)}


def read_reply(text: str) -> dict[str, Any]:
    """モデルの答え → v2 の ``summary``。本文が空なら ``VoiceAiError('parse')``。"""
    body, facts = split_reply(text)
    if not body:
        raise VoiceAiError("parse", "summary answer had no body", body=text)
    return build_summary(body, facts)
