"""カルテの下書きを、らく助エージェントの中継経由で「決めた管理者の ChatGPT の席」に作らせる.

設計: ``docs/plans/karte-via-seat-2026-10-13.md`` (PO 2026-10-13)。

* 文字起こし + 聞き間違いの補正は今までどおり Vertex AI 東京の Gemini (``jobs.py``)。
  その結果の **文字起こしの文** だけを中継のサーバーどうしの口 (``POST /svc/karte/draft``) に渡し、
  要約 (``summary``) を作り直してもらう。
* 拠点の機能の旗 ``karte_via_seat`` が ON の拠点だけ。OFF なら呼ばない (今までと同じ)。
* どこかで失敗したら (席がサインアウト・上限・打ち切り・中継が落ちている・形が違う) **Gemini の要約をそのまま使う**。
  カルテは止めない。理由は ``SeatDraftError.kind`` → ``visit_recordings.error_kind`` に ``seat_<kind>``。
* 中継へ送るのは文字起こしと **名前の無い文脈** (性別・訪問日時・職種) だけ。患者名・職員名は送らない
  (文字起こしの中で呼ばれた呼称はそのまま残る)。ログに本文は書かない。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import Visit
from app.models.visit_recording import VisitRecording
from app.services.voice import prompts
from app.services.voice.vertex_client import render_summary_text

logger = logging.getLogger(__name__)

FEATURE_KEY = "karte_via_seat"
#: ``visit_recordings.provider`` に残す名前 (席で作った下書き)。
SEAT_PROVIDER = "codex-seat"
#: 中継の合言葉のヘッダー (agent/relay/service_port.py と同じ)。
TOKEN_HEADER = "X-Rakusuke-Service-Token"
DRAFT_PATH = "/svc/karte/draft"

_SEX_JA = {"male": "男性", "female": "女性"}


class SeatDraftError(RuntimeError):
    """席での下書きができなかった (Gemini の要約に戻る)。``kind`` は短い英語の印。"""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind

    @property
    def error_kind(self) -> str:
        return f"seat_{self.kind}"[:32]


@dataclass
class SeatDraft:
    summary: dict[str, Any]
    summary_text: str
    model: str | None
    prompt_version: str | None


# --- テスト用の差し込み口 (中継へ実際に通信しない) ---
_TRANSPORT: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _TRANSPORT
    _TRANSPORT = transport


async def flag_enabled(db: AsyncSession, office_id: uuid.UUID | None) -> bool:
    """その拠点で ``karte_via_seat`` が有効か (拠点が無い録音は対象外)。"""
    if office_id is None:
        return False
    row = await db.scalar(
        select(OfficeFeatureFlag).where(
            OfficeFeatureFlag.office_id == office_id,
            OfficeFeatureFlag.feature_key == FEATURE_KEY,
        )
    )
    return bool(row and row.enabled_at is not None)


async def build_context(db: AsyncSession, row: VisitRecording) -> dict[str, str]:
    """名前の無い文脈 (性別・訪問日・時刻・職種)。年代は患者に生年月日が無いので送らない。"""
    ctx: dict[str, str] = {}
    if row.patient_id is not None:
        sex = await db.scalar(select(Patient.sex).where(Patient.id == row.patient_id))
        if sex in _SEX_JA:
            ctx["sex"] = _SEX_JA[sex]
    role = await db.scalar(select(Staff.qualification).where(Staff.id == row.staff_id))
    if role:
        ctx["staff_role"] = str(role)[:20]
    if row.visit_id is not None:
        visit = await db.get(Visit, row.visit_id)
        if visit is not None:
            ctx["visit_date"] = visit.visit_date.isoformat()
            ctx["visit_time"] = visit.start_time.strftime("%H:%M")
    return ctx


def _normalize_summary(raw: Any) -> dict[str, Any]:
    """中継の答えを Gemini の要約と同じ形にそろえる (足りない項目は空・余計な項目は捨てる)。"""
    if not isinstance(raw, dict) or not any(k in raw for k in prompts.SUMMARY_SECTIONS):
        raise SeatDraftError("parse")
    out: dict[str, Any] = {}
    for key in ("主訴・様子", "処置・ケア", "申し送り"):
        v = raw.get(key)
        out[key] = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    vitals = raw.get("バイタル") if isinstance(raw.get("バイタル"), dict) else {}
    out["バイタル"] = {k: str(vitals.get(k) or "").strip() for k in prompts.VITAL_KEYS}
    for key in ("次回", "free"):
        out[key] = str(raw.get(key) or "").strip()
    return out


async def request_draft(
    settings: Any,
    *,
    recording_id: uuid.UUID,
    transcript: str,
    context: dict[str, str],
    presser_user_id: uuid.UUID | None,
) -> SeatDraft:
    """中継に 1 回だけ頼む。失敗はすべて ``SeatDraftError`` (再試行しない = 待たせずに Gemini へ戻る)。"""
    base = str(getattr(settings, "agent_relay_service_url", "") or "").rstrip("/")
    token = str(getattr(settings, "agent_relay_service_token", "") or "")
    if not base or not token:
        raise SeatDraftError("config")
    if not transcript.strip():
        raise SeatDraftError("empty")
    timeout = float(getattr(settings, "karte_seat_timeout_seconds", 100) or 100)
    body = {
        "recording_id": str(recording_id),
        "transcript": transcript,
        "context": context,
        "presser_user_id": str(presser_user_id) if presser_user_id else None,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=_TRANSPORT) as client:
            resp = await client.post(base + DRAFT_PATH, json=body, headers={TOKEN_HEADER: token})
    except httpx.TimeoutException:
        raise SeatDraftError("timeout") from None
    except httpx.RequestError:
        raise SeatDraftError("unreachable") from None
    if resp.status_code == 401:
        raise SeatDraftError("auth")
    if resp.status_code == 404:
        raise SeatDraftError("off")
    if resp.status_code != 200:
        raise SeatDraftError("http")
    try:
        data = resp.json()
    except ValueError:
        raise SeatDraftError("parse") from None
    if not isinstance(data, dict):
        raise SeatDraftError("parse")
    if not data.get("ok"):
        kind = str(data.get("kind") or "failed")
        raise SeatDraftError("".join(c for c in kind if c.isalnum() or c == "_")[:24] or "failed")
    summary = _normalize_summary(data.get("summary"))
    model = data.get("model")
    version = data.get("prompt_version")
    return SeatDraft(
        summary=summary,
        summary_text=render_summary_text(summary),
        model=str(model)[:64] if model else None,
        prompt_version=str(version)[:16] if version else None,
    )
