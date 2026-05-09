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

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_PROPOSED, Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff
from app.models.visit import Visit

logger = logging.getLogger(__name__)

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


async def _has_fixed_visits(db: AsyncSession, patient_id: UUID, mode: str) -> bool:
    """``patient_fixed_visits`` に当該 (patient_id, mode) の行が存在するか."""
    result = await db.scalar(
        select(PatientFixedVisit.id).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == mode,
        )
    )
    return result is not None


async def _expand_patient_fixed_visits(
    db: AsyncSession,
    *,
    patient: Patient,
    mode: str,
    week_monday: date,
) -> list[dict]:
    """``patient_fixed_visits`` から当該週の entries 相当リストを生成する.

    Returns:
        list of dicts compatible with ``_entries_from_pattern`` output,
        each having: weekday (int), start_time (time), service_minutes (int),
        course_template_id (UUID | None), slot_index (int 0/1).

    W37 Phase 2-B: ``slot_index`` を entry に含めて返す. 上位ロジック
    (``_expand_fixed_visits_to_visits``) が ``requires_multiple_staff`` に応じて
    slot 0/1 をどう扱うかを決める.
    """
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == mode,
            )
        )
    ).all()

    entries: list[dict] = []
    for fv in rows:
        entries.append(
            {
                "weekday": fv.weekday,
                "_start_time": fv.start_time,  # already a time object
                "service_minutes": fv.duration_min,
                # W22 Phase A: 各固定枠に紐付くコーステンプレート ID (NULL 可).
                # Layer 1 はこれが NULL でなければ優先採用、NULL なら office
                # フォールバックを使う。
                "course_template_id": fv.course_template_id,
                # W37 Phase 1: slot_index 0/1 (default 0).
                # Phase 2-B: 複数スタッフ対応患者 (requires_multiple_staff=True)
                # では slot 0 と slot 1 の両方を読んで 2 visit に展開する.
                "slot_index": getattr(fv, "slot_index", 0) or 0,
            }
        )
    return entries


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


async def _resolve_default_template_for_office(
    db: AsyncSession, *, office_id: UUID
) -> CourseTemplate | None:
    """``office_id`` の course_template の中から「最初の 1 件」を返す.

    W16 codex fix (重大 1): Layer 1 で patient → course_template を選ぶ簡易戦略.
    将来 wave で patient ↔ course_template の最適マッチング (capacity / 地理 / etc.)
    を実装するが、現段階では各 patient を「当該 office の最初のテンプレート」
    に集約する。"M" などのマネージャー枠は除外。

    NOTE (TODO): ラベル先頭文字 ('A' < 'B' < 'C' < ...) でソートして A 系列を
    優先する。E までは A〜E の 1 文字テンプレ、'M' / 'M2' / ... は管理職枠なので除外する。
    """
    rows = (
        await db.scalars(
            select(CourseTemplate)
            .where(
                CourseTemplate.office_id == office_id,
                CourseTemplate.deleted_at.is_(None),
            )
            .order_by(CourseTemplate.label, CourseTemplate.created_at)
        )
    ).all()
    for tpl in rows:
        label = (tpl.label or "").strip().upper()
        if not label or label.startswith("M"):
            continue
        return tpl
    # M しか無いケース (本店等で manager seed のみ) は最初の 1 件を返す
    return rows[0] if rows else None


def _normalize_course_code(label: str | None) -> str:
    """``course_template.label`` を ``courses.code`` (CHECK 制約) に正規化.

    W16 codex fix (中 2): CHECK ('A','B','C','D','E','M') に拡張済 (migration 0023).
    label 先頭 1 文字を大文字化し、許容集合外なら 'M' (オーバーフロー枠) に丸める。
    """
    first = (label or "").strip()[:1].upper()
    if first in ("A", "B", "C", "D", "E", "M"):
        return first
    return "M"


async def _get_or_create_course_for_template_week_l1(
    db: AsyncSession,
    *,
    template: CourseTemplate,
    iso_year: int,
    iso_week: int,
    weekday: int,
) -> Course:
    """``template`` × (iso_year, iso_week, weekday) に対応する Course を取得 / 作成.

    W16 codex fix (重大 1): Layer 1 で visit に course_id を確実に紐付けるため
    の helper. 既存の ``schedule.py::_get_or_create_course_for_template_week``
    と同じ意味だが、HTTP 層に依存せずサービス層で完結させる。
    """
    course = await db.scalar(
        select(Course).where(
            Course.template_id == template.id,
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.weekday == weekday,
            Course.deleted_at.is_(None),
        )
    )
    if course is not None:
        return course

    code = _normalize_course_code(template.label)
    try:
        async with db.begin_nested():  # savepoint — race-safe
            new_course = Course(
                iso_year=iso_year,
                iso_week=iso_week,
                weekday=weekday,
                code=code,
                course_status=COURSE_STATUS_PROPOSED,
                template_id=template.id,
                office_id=template.office_id,
            )
            db.add(new_course)
            await db.flush()
        return new_course
    except IntegrityError:
        course = await db.scalar(
            select(Course).where(
                Course.template_id == template.id,
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.weekday == weekday,
                Course.deleted_at.is_(None),
            )
        )
        if course is None:
            raise
        return course


def _get_capacity_for_weekday(ct: CourseTemplate, weekday: int) -> int:
    """``CourseTemplate`` の曜日別 capacity を返す.

    weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    """
    caps = (
        ct.capacity_mon,
        ct.capacity_tue,
        ct.capacity_wed,
        ct.capacity_thu,
        ct.capacity_fri,
        ct.capacity_sat,
        ct.capacity_sun,
    )
    if 0 <= weekday <= 6:
        return caps[weekday]
    return 0


async def _ensure_manager_courses_for_week(
    db: AsyncSession,
    iso_year: int,
    iso_week: int,
    office_id: UUID | None = None,
) -> int:
    """manager 在籍 office の M template について、毎週 course インスタンスを全曜日 (capacity > 0) で確保する.

    - office_id 指定時はその拠点のみ対象 (None なら全 office)
    - M / M2 / M3 ... すべての M 系ラベルを対象とする
    - capacity > 0 の曜日のみ生成 (capacity=0 はスキップ)
    - 既存 course あれば touch しない (冪等)
    - 生成 course の status は 'course_fixed' (Layer 3 で staff_assigned に昇格)

    Returns:
        新規生成した course の件数
    """
    # 1. 対象 office を取得
    offices_q = select(Office).where(Office.deleted_at.is_(None))
    if office_id is not None:
        offices_q = offices_q.where(Office.id == office_id)
    offices = list(await db.scalars(offices_q))

    created = 0
    for office in offices:
        # 2. manager 在籍チェック
        has_manager = await db.scalar(
            select(func.count(Staff.id)).where(
                Staff.primary_office_id == office.id,
                Staff.role == "manager",
                Staff.status == "active",
                Staff.deleted_at.is_(None),
            )
        )
        if not has_manager:
            continue

        # 3. 当該 office の M 系 template を取得
        m_templates = list(
            await db.scalars(
                select(CourseTemplate).where(
                    CourseTemplate.office_id == office.id,
                    CourseTemplate.label.startswith("M"),
                    CourseTemplate.deleted_at.is_(None),
                )
            )
        )
        if not m_templates:
            continue

        # 4. 各 M template × 各曜日 (capacity > 0) で course を find/create
        for ct in m_templates:
            code = _normalize_course_code(ct.label)
            for weekday in range(7):
                cap = _get_capacity_for_weekday(ct, weekday)
                if cap == 0:
                    continue

                # find (already exists?)
                existing = await db.scalar(
                    select(Course).where(
                        Course.template_id == ct.id,
                        Course.iso_year == iso_year,
                        Course.iso_week == iso_week,
                        Course.weekday == weekday,
                        Course.deleted_at.is_(None),
                    )
                )
                if existing is not None:
                    continue

                # create (savepoint で race-safe)
                try:
                    async with db.begin_nested():
                        new_course = Course(
                            iso_year=iso_year,
                            iso_week=iso_week,
                            weekday=weekday,
                            code=code,
                            course_status=COURSE_STATUS_COURSE_FIXED,
                            template_id=ct.id,
                            office_id=office.id,
                        )
                        db.add(new_course)
                        await db.flush()
                    created += 1
                    logger.info(
                        "Layer1: created M course office=%s template=%s weekday=%d year=%d week=%d",
                        office.id,
                        ct.id,
                        weekday,
                        iso_year,
                        iso_week,
                    )
                except IntegrityError:
                    # 競合: 既に別トランザクションが作成済み → 冪等で無視
                    logger.debug(
                        "Layer1: M course already exists (race) office=%s template=%s weekday=%d",
                        office.id,
                        ct.id,
                        weekday,
                    )

    return created


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
    course_id: UUID | None = None
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


class UnplacedMultiStaffEntry(BaseModel):
    """W37 hotfix C-2: multi-staff 患者で slot 0/1 のうち片方のみ設定 →
    visit を生成せず保留扱いにした 1 件のレコード.

    管理者が「どの患者・曜日が片側不備で生成されなかったか」を一目で把握し、
    UI から PFV 編集に誘導するための情報. レスポンスの warnings として返す.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    patient_name: str
    weekday: int  # 0=Mon..6=Sun
    reason: str  # "missing_slot_1" or "missing_slot_0"


@dataclass
class Layer1Result:
    """Layer 1 の総合出力. HTTP layer がレスポンス schema に詰め直す."""

    iso_year: int
    iso_week: int
    visits_created: list[VisitCreated] = field(default_factory=list)
    pool: list[PoolEntry] = field(default_factory=list)
    patients_processed: int = 0
    special_week_applied_count: int = 0
    # W37 hotfix C-2: multi-staff 片側欠けの保留リスト.
    unplaced_multi_staff_patients: list[UnplacedMultiStaffEntry] = field(default_factory=list)

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
        office_id: UUID | None = None,
    ) -> Layer1Result:
        """指定週の visits を全 active 患者から展開する.

        Args:
            db: 共有 SQLAlchemy セッション (commit/rollback は呼び出し側)。
            iso_year: ISO 年 (例 2026)。
            iso_week: ISO 週 (1〜53)。
            office_id: W16 codex fix (重大 2) — 指定時は patient.primary_office_id
                がこの拠点と一致する患者のみを展開し、削除も同患者の範囲に絞る。
                None の場合は全 active 患者を対象 (旧挙動).

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

        # ----- active 患者を取得 (W16 codex fix 重大 2: office_id でスコープ) -----
        patient_where = [
            Patient.status == "active",
            Patient.deleted_at.is_(None),
        ]
        if office_id is not None:
            patient_where.append(Patient.primary_office_id == office_id)
        patients_rows = await db.scalars(
            select(Patient).where(*patient_where).order_by(Patient.code)
        )
        patients = list(patients_rows.all())
        result.patients_processed = len(patients)

        if not patients:
            # 患者ゼロでも manager 用 M course は生成が必要なため早期 return しない
            await _ensure_manager_courses_for_week(db, iso_year, iso_week, office_id=office_id)
            await db.flush()
            return result

        # ----- 既存の自動生成 visit を当該週で削除 (冪等性) -----
        # status=completed / cancelled / source != auto は保護対象なので除外。
        # W16 codex fix 重大 2: office_id 指定時は patients も既に絞り込まれているため、
        # 削除スコープも自動的に当該拠点の patient だけになる (別拠点の visit は触らない)。
        await self._delete_existing_auto_visits(
            db, week_monday=week_monday, patient_ids=[p.id for p in patients]
        )

        # ----- 拠点ごとの default template を resolve するキャッシュ -----
        # patient ごとに primary_office_id を見て、その拠点の最初の course_template を選ぶ。
        # 同一拠点を何度も照会する無駄を避けるためメモ化する。
        template_cache: dict[UUID, CourseTemplate | None] = {}

        # ----- 各患者を展開 -----
        for patient in patients:
            created = await self._expand_patient_to_visits(
                db,
                patient=patient,
                iso_year=iso_year,
                iso_week=iso_week,
                week_monday=week_monday,
                result=result,
                template_cache=template_cache,
            )
            result.visits_created.extend(created)

        # ----- manager 用 M course を毎週全曜日 (capacity > 0) で確保 -----
        # patient_fixed_visit 展開後に呼ぶ (visit 紐付けに影響しない)。
        # manager 在籍 office の M template について冪等に course を find/create する。
        # Layer 3 がこれらの course に manager を割り当てる前提。
        await _ensure_manager_courses_for_week(db, iso_year, iso_week, office_id=office_id)

        # 確定 (commit は呼び出し側)
        await db.flush()
        return result

    async def _resolve_template_for_patient(
        self,
        db: AsyncSession,
        *,
        patient: Patient,
        template_cache: dict[UUID, CourseTemplate | None],
    ) -> CourseTemplate | None:
        """patient の primary_office_id から default template を解決 (memoized)."""
        office_id = patient.primary_office_id
        if office_id is None:
            return None
        if office_id in template_cache:
            return template_cache[office_id]
        template = await _resolve_default_template_for_office(db, office_id=office_id)
        template_cache[office_id] = template
        if template is None:
            logger.warning(
                "Layer1: no course_template found for office %s (patient %s) — visit.course_id=NULL",
                office_id,
                patient.id,
            )
        return template

    # ------------------------------------------------------------------ #
    # private helpers
    # ------------------------------------------------------------------ #

    async def _expand_patient_to_visits(
        self,
        db: AsyncSession,
        *,
        patient: Patient,
        iso_year: int,
        iso_week: int,
        week_monday: date,
        result: Layer1Result,
        template_cache: dict[UUID, CourseTemplate | None] | None = None,
    ) -> list[VisitCreated]:
        """1 患者を固定枠 / 希望パターンどちらかで展開する (hybrid).

        has_fixed_visits=True の場合は patient_fixed_visits から直接生成し、
        False の場合は従来の weekly_pattern (希望) ベースで生成する。
        """
        if template_cache is None:
            template_cache = {}

        # 当該 patient の default template を解決 (W16 codex fix 重大 1)。
        # primary_office_id 未設定 / 拠点に template 無しの場合は None。
        template = await self._resolve_template_for_patient(
            db, patient=patient, template_cache=template_cache
        )

        # mode 判定: special_week_active に当該週が含まれるかどうか
        is_special = _is_special_week_active(patient, iso_year, iso_week)
        mode = "special" if is_special else "normal"

        # 固定枠チェック
        if await _has_fixed_visits(db, patient.id, mode):
            # 固定枠ベースで展開
            fixed_entries = await _expand_patient_fixed_visits(
                db,
                patient=patient,
                mode=mode,
                week_monday=week_monday,
            )
            if is_special:
                result.special_week_applied_count += 1
            return await self._expand_fixed_visits_to_visits(
                db,
                patient=patient,
                fixed_entries=fixed_entries,
                week_monday=week_monday,
                special_applied=is_special,
                template=template,
                iso_year=iso_year,
                iso_week=iso_week,
                result=result,
            )

        # 従来: 希望パターン (weekly_pattern) ベース
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
            return []

        entries = _entries_from_pattern(pattern)
        return await self._expand_entries_to_visits(
            db,
            patient=patient,
            entries=entries,
            week_monday=week_monday,
            special_applied=special_applied,
            template=template,
            iso_year=iso_year,
            iso_week=iso_week,
        )

    async def _fetch_manual_conflict_keys(
        self,
        db: AsyncSession,
        *,
        candidate_keys: list[tuple[UUID, date, time]],
    ) -> set[tuple[UUID, date, time]]:
        """INSERT 候補の (patient_id, visit_date, start_time) のうち、
        既存の non-auto (manual 等) かつ active (deleted_at IS NULL) な visit と
        衝突するキーを返す。

        返却セットに含まれるキーは auto INSERT を skip する (manual を尊重)。
        """
        if not candidate_keys:
            return set()

        rows = await db.execute(
            select(Visit.patient_id, Visit.visit_date, Visit.start_time).where(
                Visit.deleted_at.is_(None),
                Visit.source != LAYER1_VISIT_SOURCE,  # manual / ai / import 等
                tuple_(Visit.patient_id, Visit.visit_date, Visit.start_time).in_(candidate_keys),
            )
        )
        return {(row.patient_id, row.visit_date, row.start_time) for row in rows}

    async def _expand_fixed_visits_to_visits(
        self,
        db: AsyncSession,
        *,
        patient: Patient,
        fixed_entries: list[dict],
        week_monday: date,
        special_applied: bool,
        template: CourseTemplate | None = None,
        iso_year: int | None = None,
        iso_week: int | None = None,
        result: Layer1Result | None = None,
    ) -> list[VisitCreated]:
        """固定枠エントリ (patient_fixed_visits 由来) から visits を INSERT する.

        W16 codex fix (重大 1): ``template`` が渡された場合は (template, year, week, weekday)
        の Course を find/create して visit.course_id を埋める。template=None の場合
        (=patient.primary_office_id 未設定) のみ NULL のまま (旧挙動).

        W22 Phase A: 各固定枠 ``fe['course_template_id']`` が NULL でなければ
        その template を最優先で採用する (= ドラッグドロップで明示指定された
        コースを翌週以降も継承する)。NULL なら patient.primary_office_id ベースの
        フォールバック ``template`` を使う。

        W37 Phase 2-B: ``patient.requires_multiple_staff`` の値で挙動を分岐する.

        - ``requires_multiple_staff = False`` (既存挙動):
            slot_index=0 の PFV のみ使用し、weekday ごとに 1 visit 生成.
            slot_index=1 行が誤って残っていても無視する.
            ``required_staff_count = 1``.

        - ``requires_multiple_staff = True``:
            slot_index=0 / slot_index=1 を **同 weekday** で組み合わせて 2 visit
            INSERT する. 各 visit は同一 (patient_id, visit_date, start_time,
            end_time) だが **異なる course_id** を持ち、同一 ``visit_group_id``
            で連結される (Layer 3 の 2 名体制判定用).

            slot 1 が欠けている場合は警告ログを出し、slot 0 のみで 1 visit
            生成 (案 X 違反だが Layer 1 は寛容. ユーザーが UI で補完するまで
            動作可能). slot 0 が欠けて slot 1 のみあるという異常ケースも
            警告して slot 1 のみ 1 visit 生成.

            manual visit との衝突 (W35): 同 (patient, date, start_time) の
            manual がある場合は 2 visit 共にスキップ (片方だけ残すと整合崩壊).
        """
        created: list[VisitCreated] = []

        requires_multi = bool(getattr(patient, "requires_multiple_staff", False))
        # 既存の required_staff_count フィールド (なければ 1) を 1/2 に正規化.
        # Phase 2-B では requires_multiple_staff が真なら 2, それ以外は 1 とする.
        staff_count = 2 if requires_multi else 1

        # entry-specific template の照会キャッシュ (同 template_id を何度も
        # SELECT しないようメモ化). Key=template_id, Value=CourseTemplate or None.
        per_entry_template_cache: dict[UUID, CourseTemplate | None] = {}

        # ----- (1) weekday ごとに slot_index 0/1 をグルーピング -----
        # Phase 1 で UNIQUE (patient_id, mode, weekday, slot_index) なので
        # 同一 (weekday, slot_index) は最大 1 行. 重複が混入していても
        # 最初の 1 行を採用する.
        slots_by_weekday: dict[int, dict[int, dict]] = {}
        for fe in fixed_entries:
            wd = fe.get("weekday")
            if not isinstance(wd, int) or not (0 <= wd <= 6):
                continue
            slot_idx = fe.get("slot_index", 0) or 0
            if slot_idx not in (0, 1):
                # 想定外 (CHECK で防がれる) → スキップ
                continue
            if requires_multi:
                # 複数スタッフ対応: slot 0/1 両方を採用候補にする.
                day_slots = slots_by_weekday.setdefault(wd, {})
                day_slots.setdefault(slot_idx, fe)
            else:
                # 単独スタッフ: slot_index=0 行のみ採用. slot=1 行は無視.
                if slot_idx != 0:
                    continue
                day_slots = slots_by_weekday.setdefault(wd, {})
                day_slots.setdefault(0, fe)

        # ----- (2) manual conflict 用の候補キー収集 -----
        # 各 weekday × その day の "primary slot" の (start_time) を 1 つ計算.
        # 2 visit 体制でも 2 visit は同一 start_time なのでキーは weekday に対して
        # 1 つで十分.
        candidate_keys_for_conflict: list[tuple[UUID, date, time]] = []
        for wd, day_slots in slots_by_weekday.items():
            # primary slot を選ぶ: slot 0 を優先, 無ければ slot 1.
            primary_fe = day_slots.get(0) or day_slots.get(1)
            if primary_fe is None:
                continue
            st: time = primary_fe["_start_time"]
            vd = date.fromordinal(week_monday.toordinal() + wd)
            candidate_keys_for_conflict.append((patient.id, vd, st))
        manual_conflict_keys = await self._fetch_manual_conflict_keys(
            db, candidate_keys=candidate_keys_for_conflict
        )

        # ----- (3) weekday ごとに展開 -----
        for weekday in sorted(slots_by_weekday.keys()):
            day_slots = slots_by_weekday[weekday]

            slot0 = day_slots.get(0)
            slot1 = day_slots.get(1)

            # 採用する slot のリストを決定する.
            # - 単独スタッフ: slot0 のみ
            # - 複数スタッフ: slot0 + slot1 (両方あれば 2 visit)
            #   片方欠けは警告 + 1 visit
            slots_to_use: list[dict] = []
            if requires_multi:
                if slot0 is not None and slot1 is not None:
                    slots_to_use = [slot0, slot1]
                elif slot0 is not None and slot1 is None:
                    # W37 hotfix C-2: silent 1-staff 化を廃止. visit を生成せず
                    # 「保留プール送り (unplaced_multi_staff)」として管理者に通知する.
                    logger.warning(
                        "Layer1: requires_multiple_staff=True patient %s has only slot 0 on"
                        " weekday=%d (slot 1 missing) — skipping visit generation; user must"
                        " complete the second slot via UI before 2-staff scheduling.",
                        patient.id,
                        weekday,
                    )
                    if result is not None:
                        result.unplaced_multi_staff_patients.append(
                            UnplacedMultiStaffEntry(
                                patient_id=patient.id,
                                patient_name=patient.name,
                                weekday=weekday,
                                reason="missing_slot_1",
                            )
                        )
                    continue
                elif slot1 is not None and slot0 is None:
                    # W37 hotfix C-2: 異常ケース (slot 0 が欠けて slot 1 のみ存在) も
                    # 同様に visit 生成をスキップして保留扱い.
                    logger.warning(
                        "Layer1: requires_multiple_staff=True patient %s has only slot 1 on"
                        " weekday=%d (slot 0 missing) — skipping visit generation; user should"
                        " reorganize slots via UI.",
                        patient.id,
                        weekday,
                    )
                    if result is not None:
                        result.unplaced_multi_staff_patients.append(
                            UnplacedMultiStaffEntry(
                                patient_id=patient.id,
                                patient_name=patient.name,
                                weekday=weekday,
                                reason="missing_slot_0",
                            )
                        )
                    continue
                else:
                    continue  # 両 slot とも無し → 何もしない
            else:
                if slot0 is None:
                    continue
                slots_to_use = [slot0]

            # ----- 時刻 / 期間 の計算 (primary slot をベースに).
            # Phase 2-B 仕様: 2 visit は同一 (start_time, end_time) を持つことが前提.
            # slot 0 と slot 1 で start_time / duration が異なる場合は slot 0 を
            # 優先採用する (運用上は両 slot 同時刻のはずだが、データ不整合への保険).
            primary = slots_to_use[0]
            start_t: time = primary["_start_time"]
            service_min: int = primary["service_minutes"]
            end_minutes = start_t.hour * 60 + start_t.minute + service_min
            if end_minutes >= 24 * 60:
                continue
            end_t = time(end_minutes // 60, end_minutes % 60)

            visit_date = date.fromordinal(week_monday.toordinal() + weekday)

            # W35: 既存 manual visit との衝突があれば auto INSERT を **2 visit 共に** スキップ.
            # 片方だけスキップは整合崩壊のため禁止 (W37 Phase 2-B 要件 A-(4)).
            if (patient.id, visit_date, start_t) in manual_conflict_keys:
                logger.warning(
                    "Layer1: skipped auto visit (fixed) due to conflict with existing manual"
                    " visit patient=%s visit_date=%s start_time=%s slots=%d",
                    patient.id,
                    visit_date,
                    start_t,
                    len(slots_to_use),
                )
                continue

            # ----- W37 Phase 2-B: manager template + requires_multiple_staff の妥当性チェック -----
            # manager (M-prefix) は通常 1 人で全コース回す想定のため、複数スタッフ対応とは
            # 概念的に競合する. データ整合性のため警告のみ出す (動作はそのまま続行).
            if requires_multi:
                for fe in slots_to_use:
                    fe_ct_id = fe.get("course_template_id")
                    if fe_ct_id is None:
                        continue
                    if fe_ct_id in per_entry_template_cache:
                        ct = per_entry_template_cache[fe_ct_id]
                    else:
                        ct = await db.scalar(
                            select(CourseTemplate).where(
                                CourseTemplate.id == fe_ct_id,
                                CourseTemplate.deleted_at.is_(None),
                            )
                        )
                        per_entry_template_cache[fe_ct_id] = ct
                    if ct is not None:
                        label = (ct.label or "").strip().upper()
                        if label.startswith("M"):
                            logger.warning(
                                "Layer1: requires_multiple_staff=True patient %s is mapped"
                                " to a manager-prefix course template (label=%s). manager は"
                                " 通常 1 人カバーのため、複数スタッフ運用と概念競合する可能性"
                                " あり. weekday=%d slot_index=%d",
                                patient.id,
                                ct.label,
                                weekday,
                                fe.get("slot_index", 0),
                            )

            # ----- 各 slot の course_id を解決 (slot ごとに独立) -----
            slot_course_ids: list[UUID | None] = []
            for fe in slots_to_use:
                # 1st: pfv.course_template_id (明示指定) があればそれを優先
                # 2nd: フォールバック (patient.primary_office_id ベース)
                entry_template: CourseTemplate | None = template
                entry_ct_id: UUID | None = fe.get("course_template_id")
                if entry_ct_id is not None:
                    if entry_ct_id in per_entry_template_cache:
                        cached = per_entry_template_cache[entry_ct_id]
                    else:
                        cached = await db.scalar(
                            select(CourseTemplate).where(
                                CourseTemplate.id == entry_ct_id,
                                CourseTemplate.deleted_at.is_(None),
                            )
                        )
                        per_entry_template_cache[entry_ct_id] = cached
                    if cached is not None:
                        entry_template = cached

                course_id: UUID | None = None
                if entry_template is not None and iso_year is not None and iso_week is not None:
                    course = await _get_or_create_course_for_template_week_l1(
                        db,
                        template=entry_template,
                        iso_year=iso_year,
                        iso_week=iso_week,
                        weekday=weekday,
                    )
                    course_id = course.id
                slot_course_ids.append(course_id)

            # ----- 2 visit 体制なら visit_group_id を共有 -----
            num_visits = len(slots_to_use)
            visit_group_id: UUID | None = None
            if requires_multi and num_visits >= 1:
                # 警告経路 (片方欠け = 1 visit) でも visit_group_id を割り当てるかは
                # 議論の余地があるが, 1 visit のみの場合は group_id 不要 (Layer 3 で
                # required_staff_count == 2 だけでも 2 名体制と判定されうるが
                # partner が存在しないので結局 1 名割当になる).
                # → Phase 2-B 設計指針: 2 visit 揃ったときのみ group_id 共有.
                if num_visits == 2:
                    visit_group_id = uuid.uuid4()

            for _fe, course_id in zip(slots_to_use, slot_course_ids, strict=True):
                visit = Visit(
                    patient_id=patient.id,
                    visit_date=visit_date,
                    start_time=start_t,
                    end_time=end_t,
                    type=LAYER1_VISIT_TYPE,
                    status=LAYER1_VISIT_STATUS,
                    source=LAYER1_VISIT_SOURCE,
                    required_staff_count=staff_count,
                    course_id=course_id,
                    visit_group_id=visit_group_id,
                    note=(
                        "Layer1: fixed pattern (special_week)"
                        if special_applied
                        else "Layer1: fixed pattern"
                    ),
                )
                db.add(visit)
                await db.flush()

                created.append(
                    VisitCreated(
                        visit_id=visit.id,
                        patient_id=patient.id,
                        course_id=course_id,
                        weekday=weekday,
                        visit_date=visit_date,
                        start_time=start_t.strftime("%H:%M"),
                        end_time=end_t.strftime("%H:%M"),
                        staff_count=staff_count,
                        special_week_applied=special_applied,
                    )
                )

        return created

    async def _delete_existing_auto_visits(
        self,
        db: AsyncSession,
        *,
        week_monday: date,
        patient_ids: list[UUID],
    ) -> None:
        """当該週の auto 生成 visit を **論理削除** (冪等性のため).

        W34: 物理 delete から **論理削除 (deleted_at = now())** に切替.
            - 監査ログ / visit_staff_assignments の整合性を保つ
            - 万一の誤判定でも ``deleted_at = NULL`` で復旧可能
            - migration 0026 (visits partial UNIQUE WHERE deleted_at IS NULL)
              が再生成行と衝突しないため、論理削除で UNIQUE は逃れる

        WHERE 句は **AND** 連鎖で確実に絞り込む:
            - patient_id IN (...) (拠点スコープ)
            - week_monday <= visit_date <= week_sunday (当該週)
            - source = 'auto' (人手入力 / kaipoke 等を保護)
            - status = 'planned' (completed / in_progress / cancelled を保護)
            - deleted_at IS NULL (既に論理削除された行は二重更新しない)

        bulk UPDATE を 1 文で発行し ORM ローダー経由のレース問題を避ける.
        ``synchronize_session=False`` を指定しないと SQLAlchemy が二段
        SELECT を発行するため明示する.
        """
        if not patient_ids:
            return
        week_sunday = date.fromordinal(week_monday.toordinal() + 6)
        now = datetime.now(UTC)

        # bulk UPDATE: 該当行の deleted_at を一括更新.
        # 削除対象 = 週内 + source=auto + status=planned + deleted_at IS NULL.
        # status in (completed/cancelled/in_progress) と source != auto は
        # 履歴・人手入力の保護対象なので絶対に触らない.
        await db.execute(
            update(Visit)
            .where(
                Visit.patient_id.in_(patient_ids),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
                Visit.source == LAYER1_VISIT_SOURCE,
                Visit.status == LAYER1_VISIT_STATUS,
                Visit.deleted_at.is_(None),
            )
            .values(deleted_at=now)
            .execution_options(synchronize_session=False),
        )
        # flush は呼び出し側 (expand_week 末尾) でまとめて

    async def _expand_entries_to_visits(
        self,
        db: AsyncSession,
        *,
        patient: Patient,
        entries: list[dict],
        week_monday: date,
        special_applied: bool,
        template: CourseTemplate | None = None,
        iso_year: int | None = None,
        iso_week: int | None = None,
    ) -> list[VisitCreated]:
        """1 患者の entries を visits に展開して INSERT する.

        W16 codex fix (重大 1): ``template`` が渡された場合は (template, year, week, weekday)
        の Course を find/create して visit.course_id を埋める。

        2 名体制 (staff_count=2) のときは ``visit_group_id`` を共有する 2 行を
        INSERT する (§3.3 / §4.5)。Layer 3 (W4-BE9) が後から
        ``visit_staff_assignments`` を埋める想定。
        """
        created: list[VisitCreated] = []

        # 同一週内の (weekday, start_time) 重複をフィルタ
        seen_slots: set[tuple[int, str]] = set()

        # W35: INSERT 前に既存 manual (active) との衝突キーを一括 SELECT して除外する。
        # entries の全候補キーを事前に計算してから 1 回の SELECT で衝突を確認する。
        candidate_keys_entries: list[tuple[UUID, date, time]] = []
        for entry in entries:
            wd = _resolve_weekday(entry.get("weekday"))
            if wd is None:
                continue
            st = _parse_hhmm(entry.get("preferred_start"))
            if st is None:
                continue
            vd = date.fromordinal(week_monday.toordinal() + wd)
            candidate_keys_entries.append((patient.id, vd, st))
        manual_conflict_keys_entries = await self._fetch_manual_conflict_keys(
            db, candidate_keys=candidate_keys_entries
        )

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

            # W35: 既存 manual visit との衝突があれば auto INSERT をスキップ
            if (patient.id, visit_date, start_t) in manual_conflict_keys_entries:
                logger.warning(
                    "Layer1: skipped auto visit (pattern) due to conflict with existing manual visit"
                    " patient=%s visit_date=%s start_time=%s",
                    patient.id,
                    visit_date,
                    start_t,
                )
                continue

            # W16 codex fix (重大 1): course_id を決定
            course_id: UUID | None = None
            if template is not None and iso_year is not None and iso_week is not None:
                course = await _get_or_create_course_for_template_week_l1(
                    db,
                    template=template,
                    iso_year=iso_year,
                    iso_week=iso_week,
                    weekday=weekday,
                )
                course_id = course.id

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
                    course_id=course_id,
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
                        course_id=course_id,
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
    "UnplacedMultiStaffEntry",
    "VisitCreated",
    "_ensure_manager_courses_for_week",
]
