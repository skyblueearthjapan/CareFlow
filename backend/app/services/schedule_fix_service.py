"""ScheduleFixService — W3-BE-FIX.

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 (Layer 1
時間配置) と API 契約 ``docs/plans/v2-api-contracts.md`` §6.1
(`POST /api/v1/schedule/fix`) に対応する週レイアウト固定ロジック。

責務:
    当該週のレイアウト (FixedVisit のリスト) を受け取り、各患者の
    ``patients.weekly_pattern.entries`` に保存する。staff_count を含む。

差分書込み (受入基準 2):
    既存の ``weekly_pattern.entries`` と ``new_entries`` を曜日 +
    時刻ベースで比較し、**差分が無い患者は touch しない**。これにより
    更新カウンタや audit ノイズを抑え、N 名分の一括更新で必要分のみ
    DB write が走る。

トランザクション境界 (受入基準 3):
    本サービスは ``db.commit()`` / ``db.rollback()`` を **呼ばない**。
    呼び出し側 (``app/api/v1/schedule.py``) が単一の HTTP リクエストを
    1 トランザクションとして包み、例外時に rollback する契約。本サービス
    内部では ``await db.flush()`` のみで状態を確定させる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.schemas.v2.patient import (
    WeekdayV2,
    WeeklyPatternEntryV2,
)

# Pydantic 側 (``app.schemas.v2.patient._HHMM_RE``) と同じ正規表現を再宣言する.
# 別モジュールの ``_``-prefixed シンボルを直接 import すると ruff(N) で
# warning が出るため、ここではコピーして局所化する。
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# 0=Mon..6=Sun。WeeklyPatternEntryV2.weekday と zip させる順序。
_WEEKDAY_INT_TO_CODE: tuple[WeekdayV2, ...] = (
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
)
_WEEKDAY_CODE_TO_INT: dict[str, int] = {code: idx for idx, code in enumerate(_WEEKDAY_INT_TO_CODE)}


class FixedVisit(BaseModel):
    """週レイアウト 1 行 (= 患者×曜日×時刻スロット) を表すリクエスト要素.

    API 契約 §6.1 に準拠しつつ、UI 側からは ``end_time`` を送る運用とし、
    永続化時に ``service_minutes`` を導出する。
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    weekday: int = Field(ge=0, le=6, description="0=Mon..6=Sun")
    start_time: str = Field(description="HH:MM (24h)")
    end_time: str = Field(description="HH:MM (24h)")
    staff_count: int = Field(default=1, ge=1, le=2)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str) -> str:
        if not isinstance(v, str) or not _HHMM_RE.match(v):
            raise ValueError(f"invalid HH:MM time string: {v!r}")
        return v


class WeeklyPatternChange(BaseModel):
    """1 患者分の更新サマリ (受入基準: 差分のみ書込み が成立したことの証跡)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    entries_before: list[dict[str, Any]] = Field(default_factory=list)
    entries_after: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class ScheduleFixResult:
    """サービス層の出力 (HTTP layer が ScheduleFixResponse に詰め直す)."""

    patients_updated: int = 0
    weekly_pattern_changes: list[WeeklyPatternChange] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _hhmm_to_minutes(value: str) -> int:
    """``HH:MM`` を 0:00 起点の分に変換."""
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def _service_minutes(start: str, end: str) -> int:
    """``end - start`` を分単位で返す。end <= start の場合は ValueError."""
    delta = _hhmm_to_minutes(end) - _hhmm_to_minutes(start)
    if delta <= 0:
        raise ValueError(f"end_time must be > start_time (start={start}, end={end})")
    return delta


def _entry_sort_key(entry: dict[str, Any]) -> tuple[int, str, int]:
    """曜日 → 開始時刻 → staff_count で安定ソート."""
    weekday = entry.get("weekday")
    if isinstance(weekday, int):
        wd = weekday
    elif isinstance(weekday, str):
        wd = _WEEKDAY_CODE_TO_INT.get(weekday, 99)
    else:
        wd = 99
    start = str(entry.get("preferred_start") or "99:99")
    sc = int(entry.get("staff_count") or 1)
    return (wd, start, sc)


def _normalize_entry(entry: Any) -> dict[str, Any] | None:
    """既存 entries の 1 件を比較用に正規化する.

    既存 JSONB は dict / WeeklyPatternEntryV2 / その他いずれの形でも
    来うるので、比較用に dict に揃える。
    """
    if entry is None:
        return None
    if isinstance(entry, BaseModel):
        data = entry.model_dump(mode="json")
    elif isinstance(entry, dict):
        data = dict(entry)
    else:
        return None

    weekday = data.get("weekday")
    if isinstance(weekday, int):
        if 0 <= weekday <= 6:
            data["weekday"] = _WEEKDAY_INT_TO_CODE[weekday]
        else:
            return None
    return {
        "weekday": data.get("weekday"),
        "time_type": data.get("time_type") or "固定",
        "preferred_start": data.get("preferred_start"),
        "preferred_end": data.get("preferred_end"),
        "service_minutes": data.get("service_minutes"),
        "staff_count": int(data.get("staff_count") or 1),
    }


def _build_entry(visit: FixedVisit) -> dict[str, Any]:
    """FixedVisit を WeeklyPatternEntryV2 互換 dict に変換する."""
    weekday_code: WeekdayV2 = _WEEKDAY_INT_TO_CODE[visit.weekday]
    minutes = _service_minutes(visit.start_time, visit.end_time)
    # WeeklyPatternEntryV2 で validate (HHMM 形式 / staff_count 範囲を二重検査)
    entry = WeeklyPatternEntryV2(
        weekday=weekday_code,
        time_type="固定",
        preferred_start=visit.start_time,
        preferred_end=visit.end_time,
        service_minutes=minutes,
        staff_count=visit.staff_count,
    )
    return entry.model_dump(mode="json", exclude_none=False)


def _existing_entries_list(weekly_pattern: dict | None) -> list[dict[str, Any]]:
    """``patient.weekly_pattern.entries`` を list[dict] として取り出す.

    ``weekly_pattern`` 自体が None / entries キーが無い / 空リスト の
    いずれの場合も `[]` を返す。
    """
    if not weekly_pattern:
        return []
    entries = weekly_pattern.get("entries") if isinstance(weekly_pattern, dict) else None
    if not entries:
        return []
    out: list[dict[str, Any]] = []
    for raw in entries:
        if isinstance(raw, dict):
            out.append(dict(raw))
    return out


def _entries_equal(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    """曜日+開始時刻+staff_count で正規化したうえで集合比較する."""

    def _key(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = [_normalize_entry(it) for it in items]
        cleaned: list[dict[str, Any]] = [n for n in normalized if n is not None]
        cleaned.sort(key=_entry_sort_key)
        return cleaned

    return _key(left) == _key(right)


# ----------------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------------


class ScheduleFixError(Exception):
    """サービス層の業務エラー (HTTP 層で 4xx に翻訳される)."""

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


class ScheduleFixService:
    """週レイアウトを各患者の ``weekly_pattern`` に保存する.

    呼び出し側は本サービスの ``fix_week_layout`` を 1 度だけ呼び、戻り値の
    ``ScheduleFixResult`` を HTTP レスポンスに詰め替える。
    """

    async def fix_week_layout(
        self,
        db: AsyncSession,
        iso_year: int,
        iso_week: int,
        layout: list[FixedVisit],
    ) -> ScheduleFixResult:
        """``layout`` を patient_id でグループ化し、各患者の weekly_pattern を更新する.

        Args:
            db: 共有 SQLAlchemy セッション (commit/rollback は呼び出し側)。
            iso_year: ログ / トレース用のメタ情報。本実装では永続化しない。
            iso_week: 同上。
            layout: 週内の (患者×曜日×時刻スロット) リスト。

        Returns:
            ScheduleFixResult: 更新人数 + 患者ごとの差分 (entries_before/after)。

        Raises:
            ScheduleFixError: 不整合データ (例: 同患者×同曜日×同時刻で
                staff_count 矛盾) や患者未存在。
        """
        # 1 トランザクション内で扱うため引数 iso_year/iso_week は監査用のみ。
        del iso_year, iso_week

        if not layout:
            return ScheduleFixResult()

        # ----- patient_id でグループ化 -----
        grouped: dict[UUID, list[FixedVisit]] = {}
        for visit in layout:
            grouped.setdefault(visit.patient_id, []).append(visit)

        # ----- 患者を 1 度の SELECT でまとめて取得 -----
        patient_ids = list(grouped.keys())
        rows = await db.scalars(
            select(Patient).where(
                Patient.id.in_(patient_ids),
                Patient.deleted_at.is_(None),
            )
        )
        patients_by_id = {p.id: p for p in rows.all()}

        missing = [str(pid) for pid in patient_ids if pid not in patients_by_id]
        if missing:
            raise ScheduleFixError(
                f"unknown or deleted patients: {','.join(sorted(missing))}",
                http_status=404,
            )

        result = ScheduleFixResult()

        for pid, visits in grouped.items():
            patient = patients_by_id[pid]

            # ----- 新 entries (FixedVisit -> WeeklyPatternEntryV2 dict) -----
            new_entries: list[dict[str, Any]] = []
            seen_slots: set[tuple[int, str]] = set()
            for v in visits:
                slot = (v.weekday, v.start_time)
                if slot in seen_slots:
                    raise ScheduleFixError(
                        (
                            f"duplicate slot for patient {pid}: "
                            f"weekday={v.weekday}, start_time={v.start_time}"
                        ),
                        http_status=422,
                    )
                seen_slots.add(slot)
                try:
                    new_entries.append(_build_entry(v))
                except ValueError as exc:
                    raise ScheduleFixError(str(exc), http_status=422) from exc

            existing_entries = _existing_entries_list(patient.weekly_pattern)

            if _entries_equal(existing_entries, new_entries):
                # 差分なし → 触らない (受入基準 2)
                continue

            # 既存トップレベルキー (frequency_per_week 等) は維持し、
            # entries だけ置換する。
            base_pattern: dict[str, Any] = (
                dict(patient.weekly_pattern) if isinstance(patient.weekly_pattern, dict) else {}
            )
            base_pattern["entries"] = new_entries

            patient.weekly_pattern = base_pattern

            result.patients_updated += 1
            result.weekly_pattern_changes.append(
                WeeklyPatternChange(
                    patient_id=pid,
                    entries_before=existing_entries,
                    entries_after=new_entries,
                )
            )

        # ステートを確定 (commit は呼び出し側)
        await db.flush()
        return result


__all__ = [
    "FixedVisit",
    "ScheduleFixError",
    "ScheduleFixResult",
    "ScheduleFixService",
    "WeeklyPatternChange",
]
