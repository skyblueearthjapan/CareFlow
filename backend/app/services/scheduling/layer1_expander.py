"""Layer1Expander — W4-BE7.

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.4 / §3.6.2 / §5.2
と API 契約 ``docs/plans/v2-api-contracts.md`` §6.2
(`POST /api/v1/schedule/generate-week`) に対応する Layer 1 アルゴリズム。

責務 (§5.2):
    - 全 active 患者の ``weekly_pattern`` を読み、当該週の ``visits`` を生成
    - 特別週 (``special_week_active`` 該当週) は ``special_weekly_pattern`` を優先
    - 確定パターン未設定の患者は **保留プール** に積む

冪等性 (受入基準):
    同一 (iso_year, iso_week) で再実行された場合、当該週の自動生成された
    既存 visit (status=planned, source=auto) は削除してから再生成する。
    ただし以下の visit は **絶対に触らない**:
      - status=completed (実施済み): 履歴保護
      - status=cancelled (キャンセル済み): 申請履歴の整合性
      - source != auto (manual / ai / import): 人手入力の保護

トランザクション境界:
    本サービスは ``db.commit()`` / ``db.rollback()`` を **呼ばない**。
    呼び出し側 (``app/api/v1/schedule.py``) が 1 HTTP リクエストを 1
    トランザクションとして包み、例外時に rollback する契約。本サービス
    内部では ``await db.flush()`` のみ。

出力 schema (W4-BE8 / W4-BE9 が依存):
    - ``visits_created``: 生成された visit の概要 (visit_id / patient_id /
      weekday / start_time / end_time / staff_count)
    - ``pool``: 保留プールに積まれた患者 (patient_id / patient_name /
      preferred_weekdays / frequency_per_week)
    - ``summary``: 集計値 (patients_processed / visits_created /
      pool_count / special_week_applied_count)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.visit import Visit

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

# 0=Mon..6=Sun → JSONB の英語短縮表記。``WeeklyPatternEntryV2.weekday`` と整合。
_WEEKDAY_INT_TO_CODE: tuple[str, ...] = (
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
)
_WEEKDAY_CODE_TO_INT: dict[str, int] = {code: idx for idx, code in enumerate(_WEEKDAY_INT_TO_CODE)}

# 自動生成された visit の source 値。冪等再生成時の削除対象を識別するために使う。
LAYER1_VISIT_SOURCE = "auto"
# 自動生成された visit の type 値。v1 / v2 共に運用する "regular" を採用。
LAYER1_VISIT_TYPE = "regular"
# 自動生成時の status。状態遷移は将来 Layer 3 (W4-BE9) で更新される。
LAYER1_VISIT_STATUS = "planned"

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _parse_hhmm(value: Any, default: time | None = None) -> time | None:
    """``HH:MM`` 文字列を ``time`` に変換。形式違反時は default を返す."""
    if isinstance(value, time):
        return value
    if not isinstance(value, str) or not _HHMM_RE.match(value):
        return default
    h, m = value.split(":")
    return time(int(h), int(m))


def _resolve_weekday(value: Any) -> int | None:
    """JSONB 上の weekday 値を 0-6 の int に正規化."""
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        return None
    if isinstance(value, str):
        return _WEEKDAY_CODE_TO_INT.get(value)
    return None


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        # bool は int の subclass なので明示弾く
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_special_week_active(patient: Patient, iso_year: int, iso_week: int) -> bool:
    """``patient.special_week_active`` に当該週が含まれるか判定 (§3.4)."""
    refs = patient.special_week_active or []
    if not isinstance(refs, list):
        return False
    for item in refs:
        if not isinstance(item, dict):
            continue
        try:
            iy = int(item.get("iso_year"))  # type: ignore[arg-type]
            iw = int(item.get("iso_week"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if iy == iso_year and iw == iso_week:
            return True
    return False


def _select_pattern(patient: Patient, iso_year: int, iso_week: int) -> tuple[dict | None, bool]:
    """当該週で適用すべき weekly_pattern を返す (§3.4 Option A).

    Returns:
        (pattern_dict, special_applied)
        - special_applied=True: special_weekly_pattern を採用
        - special_applied=False: 通常の weekly_pattern を採用
        - 採用候補が None / dict 以外 / entries 空 の場合は ``(None, False)``
    """
    if _is_special_week_active(patient, iso_year, iso_week):
        sp = patient.special_weekly_pattern
        if isinstance(sp, dict):
            return sp, True
        # 特別週フラグ ON だが special_weekly_pattern 未定義 → 通常へフォールバック
    wp = patient.weekly_pattern
    if isinstance(wp, dict):
        return wp, False
    return None, False


def _entries_from_pattern(pattern: dict | None) -> list[dict]:
    """pattern 辞書から entries リストを取り出す。entries キーが無い場合は []."""
    if not isinstance(pattern, dict):
        return []
    entries = pattern.get("entries")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _preferred_weekdays(pattern: dict | None) -> list[str]:
    """patten 辞書から preferred_weekdays を取り出す (保留プール用)."""
    if not isinstance(pattern, dict):
        return []
    raw = pattern.get("preferred_weekdays")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in _WEEKDAY_CODE_TO_INT:
            out.append(item)
    return out


def _frequency_per_week(pattern: dict | None) -> int | None:
    """patten 辞書から frequency_per_week を取り出す (保留プール用)."""
    if not isinstance(pattern, dict):
        return None
    raw = pattern.get("frequency_per_week")
    if isinstance(raw, int) and 1 <= raw <= 7:
        return raw
    return None


def _has_confirmed_entries(pattern: dict | None) -> bool:
    """確定パターン (entries に少なくとも 1 件の有効な weekday) を持つか.

    保留プール判定に使用。確定済みパターンがあれば visits を生成、
    無ければ保留プールへ。
    """
    entries = _entries_from_pattern(pattern)
    if not entries:
        return False
    for entry in entries:
        weekday = _resolve_weekday(entry.get("weekday"))
        if weekday is None:
            continue
        # weekday が定まれば最低限「いつ訪問するか」決まっている → 確定とみなす
        return True
    return False


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class VisitCreated(BaseModel):
    """生成 / 保持された 1 件の visit の概要 (W4-BE8 が消費する出力 schema)."""

    model_config = ConfigDict(extra="forbid")

    visit_id: UUID
    patient_id: UUID
    weekday: int
    visit_date: date
    start_time: str  # "HH:MM"
    end_time: str  # "HH:MM"
    staff_count: int
    special_week_applied: bool = False


class PoolEntry(BaseModel):
    """保留プールの 1 患者 (Layer 2 で人が手動配置するための情報)."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    patient_name: str
    preferred_weekdays: list[str] = []
    frequency_per_week: int | None = None


@dataclass
class Layer1Result:
    """Layer 1 の総合出力. HTTP layer がレスポンス schema に詰め直す."""

    iso_year: int
    iso_week: int
    visits_created: list[VisitCreated] = field(default_factory=list)
    pool: list[PoolEntry] = field(default_factory=list)
    patients_processed: int = 0
    special_week_applied_count: int = 0

    @property
    def visits_created_count(self) -> int:
        return len(self.visits_created)

    @property
    def pool_count(self) -> int:
        return len(self.pool)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Layer1ExpandError(Exception):
    """サービス層から HTTP 層へ伝播する業務エラー (4xx に翻訳される)."""

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Layer1Expander:
    """Layer 1: 患者マスタの ``weekly_pattern`` を当該週の visits に展開する.

    冪等性: 同じ (iso_year, iso_week) で 2 回呼んでも結果が一致する。
    再生成時は当該週の既存 auto-visit を削除して INSERT し直すが、
    completed / cancelled / source != auto の visit は保護する。
    """

    async def expand_week(
        self,
        db: AsyncSession,
        iso_year: int,
        iso_week: int,
    ) -> Layer1Result:
        """指定週の visits を全 active 患者から展開する.

        Args:
            db: 共有 SQLAlchemy セッション (commit/rollback は呼び出し側)。
            iso_year: ISO 年 (例 2026)。
            iso_week: ISO 週 (1〜53)。

        Returns:
            Layer1Result: 生成された visits + 保留プール + サマリ。

        Raises:
            Layer1ExpandError: ISO 週指定が不正など、業務上の入力エラー。
        """
        if iso_year < 2000 or iso_year > 2100:
            raise Layer1ExpandError(f"iso_year out of range: {iso_year}", http_status=422)
        if iso_week < 1 or iso_week > 53:
            raise Layer1ExpandError(f"iso_week out of range: {iso_week}", http_status=422)

        # 当該週の月曜 (weekday=0) を起点として 7 日分の date を導出。
        try:
            week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        except ValueError as exc:
            raise Layer1ExpandError(
                f"invalid ISO week: year={iso_year} week={iso_week}",
                http_status=422,
            ) from exc

        result = Layer1Result(iso_year=iso_year, iso_week=iso_week)

        # ----- 全 active 患者を取得 -----
        patients_rows = await db.scalars(
            select(Patient)
            .where(
                Patient.status == "active",
                Patient.deleted_at.is_(None),
            )
            .order_by(Patient.code)
        )
        patients = list(patients_rows.all())
        result.patients_processed = len(patients)

        if not patients:
            return result

        # ----- 既存の自動生成 visit を当該週で削除 (冪等性) -----
        # status=completed / cancelled / source != auto は保護対象なので除外。
        await self._delete_existing_auto_visits(
            db, week_monday=week_monday, patient_ids=[p.id for p in patients]
        )

        # ----- 各患者を展開 -----
        for patient in patients:
            pattern, special_applied = _select_pattern(patient, iso_year, iso_week)
            if special_applied:
                result.special_week_applied_count += 1

            if not _has_confirmed_entries(pattern):
                # 新規患者 / 確定パターン未設定 → 保留プールへ
                result.pool.append(
                    PoolEntry(
                        patient_id=patient.id,
                        patient_name=patient.name,
                        preferred_weekdays=_preferred_weekdays(patient.weekly_pattern),
                        frequency_per_week=_frequency_per_week(patient.weekly_pattern),
                    )
                )
                continue

            # entries → visits 展開
            entries = _entries_from_pattern(pattern)
            created = await self._expand_entries_to_visits(
                db,
                patient=patient,
                entries=entries,
                week_monday=week_monday,
                special_applied=special_applied,
            )
            result.visits_created.extend(created)

        # 確定 (commit は呼び出し側)
        await db.flush()
        return result

    # ------------------------------------------------------------------ #
    # private helpers
    # ------------------------------------------------------------------ #

    async def _delete_existing_auto_visits(
        self,
        db: AsyncSession,
        *,
        week_monday: date,
        patient_ids: list[UUID],
    ) -> None:
        """当該週の auto 生成 visit を削除 (冪等性のため)。

        completed / cancelled / source != auto は保護対象なので残す。
        soft-delete (deleted_at) は行わず物理削除する: Layer 1 が再生成
        するたびに古い auto 行が積み上がるのを防ぐため。
        """
        if not patient_ids:
            return
        week_sunday = date.fromordinal(week_monday.toordinal() + 6)

        # 削除対象 = 週内 + source=auto + status=planned のみ。
        # status in (completed/cancelled/in_progress) と source != auto は
        # 履歴・人手入力の保護対象。
        rows = await db.scalars(
            select(Visit).where(
                Visit.patient_id.in_(patient_ids),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
                Visit.source == LAYER1_VISIT_SOURCE,
                Visit.status == LAYER1_VISIT_STATUS,
                Visit.deleted_at.is_(None),
            )
        )
        for visit in rows.all():
            await db.delete(visit)
        # flush は呼び出し側 (expand_week 末尾) でまとめて

    async def _expand_entries_to_visits(
        self,
        db: AsyncSession,
        *,
        patient: Patient,
        entries: list[dict],
        week_monday: date,
        special_applied: bool,
    ) -> list[VisitCreated]:
        """1 患者の entries を visits に展開して INSERT する.

        2 名体制 (staff_count=2) のときは ``visit_group_id`` を共有する 2 行を
        INSERT する (§3.3 / §4.5)。Layer 3 (W4-BE9) が後から
        ``visit_staff_assignments`` を埋める想定。
        """
        created: list[VisitCreated] = []

        # 同一週内の (weekday, start_time) 重複をフィルタ
        seen_slots: set[tuple[int, str]] = set()

        for entry in entries:
            weekday = _resolve_weekday(entry.get("weekday"))
            if weekday is None:
                continue

            start_time_str = entry.get("preferred_start")
            preferred_end_str = entry.get("preferred_end")
            time_type = entry.get("time_type") or "固定"

            start_t = _parse_hhmm(start_time_str)
            end_t = _parse_hhmm(preferred_end_str)

            # 時刻が無い entry は Layer 1 では確定 visit にできない。
            # 現仕様 (§5.2) は「単純展開」なので、時刻指定が無い entry は
            # 該当 entry のみスキップ (患者全体は保留プールに送らない)。
            # スケジュール表側で個別に時刻を埋める運用を想定。
            if start_t is None:
                continue
            if end_t is None:
                # service_minutes から end_t を算出
                service_min = _coerce_int(entry.get("service_minutes"), 0)
                if service_min <= 0:
                    continue
                end_minutes = start_t.hour * 60 + start_t.minute + service_min
                if end_minutes >= 24 * 60:
                    # 翌日へまたぐパターンは Layer 1 では扱わない
                    continue
                end_t = time(end_minutes // 60, end_minutes % 60)

            if end_t <= start_t:
                # 不整合 entry はスキップ (Layer 1 では soft-skip し、
                # 監査ログ等で気付ける運用を想定。例外にすると 1 件で全
                # 患者の展開が止まる)。
                continue

            slot_key = (weekday, start_t.strftime("%H:%M"))
            if slot_key in seen_slots:
                continue
            seen_slots.add(slot_key)

            staff_count = _coerce_int(entry.get("staff_count"), 1)
            if staff_count not in (1, 2):
                staff_count = 1

            visit_date = date.fromordinal(week_monday.toordinal() + weekday)

            # 2 名体制は同じ visit_group_id を持つ visit を 2 行 (§3.3)
            visit_group_id: UUID | None = None
            if staff_count == 2:
                visit_group_id = uuid.uuid4()

            for _slot_idx in range(staff_count):
                visit = Visit(
                    patient_id=patient.id,
                    visit_date=visit_date,
                    start_time=start_t,
                    end_time=end_t,
                    type=LAYER1_VISIT_TYPE,
                    status=LAYER1_VISIT_STATUS,
                    source=LAYER1_VISIT_SOURCE,
                    required_staff_count=staff_count,
                    visit_group_id=visit_group_id,
                    note=(
                        f"Layer1: {time_type} pattern (special_week)"
                        if special_applied
                        else f"Layer1: {time_type} pattern"
                    ),
                )
                db.add(visit)
                # 個別 ID を確定させ、レスポンスに渡す
                await db.flush()

                created.append(
                    VisitCreated(
                        visit_id=visit.id,
                        patient_id=patient.id,
                        weekday=weekday,
                        visit_date=visit_date,
                        start_time=start_t.strftime("%H:%M"),
                        end_time=end_t.strftime("%H:%M"),
                        staff_count=staff_count,
                        special_week_applied=special_applied,
                    )
                )

        return created


# ---------------------------------------------------------------------------
# Re-exports for app.services.scheduling.__init__
# ---------------------------------------------------------------------------

__all__ = [
    "LAYER1_VISIT_SOURCE",
    "LAYER1_VISIT_STATUS",
    "LAYER1_VISIT_TYPE",
    "Layer1ExpandError",
    "Layer1Expander",
    "Layer1Result",
    "PoolEntry",
    "VisitCreated",
]
