"""患者ステータスと予定の連動 (docs/plans/patient-status-schedule-design-2026-09-09.md §7).

「入院中にしたのに予定が残っている」(2026-08-31 の残置事故) を構造的に無くすための
単一の入口。ステータスを変える経路 (ダイアログ / PATCH /patients / 申請適用) は
**すべてここを通す**。

* 非稼働化 (active → active 以外。``pending`` も非稼働):
  ``from_date`` 以降の planned を **取消** (``status='cancelled'`` /
  ``source='status_cancel'``)。行は消さない (履歴が追える・カイポケ突合は delete 差分)。
* 復帰 (active 以外 → active): ``status_cancel`` を soft-delete して、生成済みの週へ
  **型 (patient_fixed_visits) から作り直す** (``reset_visits_to_fixed``)。
* どちらでもない (非稼働 → 別の非稼働 / 同一) は status だけ書く。

設計上の要点:
  - 影響件数 (dry-run) と実行は **同じ selector 関数**を通る。表示と実行がズレると
    「9 件と出たのに 7 件しか消えない」という信用を失う不具合になる。
  - 取消は op-log ``cancel_visit`` (既存 op) で週ごとに 1 グループ記録する。
    ツールバーの「戻る」がそのまま効く。
  - session は ``autoflush=False``。段階ごとに ``await db.flush()`` を呼び、
    **commit は呼び出し側** (endpoint / applier) が行う。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.patient import Patient
from app.models.pending_request import PendingRequest
from app.models.special_visit import (
    MARK_STATUS_CANCELLED,
    MARK_STATUS_PLACED,
    MARK_STATUS_POOL,
    PERIOD_STATUS_ACTIVE,
    PERIOD_STATUS_ENDED,
    SpecialVisitMark,
    SpecialVisitPeriod,
)
from app.models.user import User
from app.models.visit import (
    VISIT_SOURCE_STATUS_CANCEL,
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.patient_status import (
    ImpactRegenerate,
    ImpactSpecialPeriod,
    ImpactVisits,
    OpGroupRef,
    RegeneratedSummary,
    SpecialPeriodResult,
    StatusChangeResult,
    StatusImpact,
    WeekCount,
)
from app.schemas.v2.enums import RequestStatus
from app.schemas.v2.patient import PatientV2Read
from app.services.op_log_service import record_op

logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")

#: 通知の種別 (FE のアイコン分岐キー)。
NOTIFY_TYPE_PATIENT_STATUS_SYNC: str = "patient_status_sync"

#: 申請の自動却下理由 (設計 §7-3 (c))。
AUTO_REJECT_REASON: str = "患者が非稼働のため自動却下"

#: 復帰で型から作り直す週の上限 (from_date の週 + 以降 7 週 = 8 週)。
#: 生成済み週は理屈の上では無限に伸びうるため、1 回の操作が重くなりすぎない
#: ところで打ち切る (足りない分は次の週生成が埋める)。
MAX_REGENERATE_WEEKS: int = 8

#: 画面表示・通知本文で使う日本語ラベル。
STATUS_LABELS: dict[str, str] = {
    "active": "稼働中",
    "suspended": "一時休止",
    "admitted": "入院中",
    "pending": "開始前",
    "cancelled": "解約済み",
    # 旧データ (v1 の残骸)。ラベルだけ引けるようにしておく。
    "inactive": "一時休止",
}


def status_label(s: str | None) -> str:
    """ステータスの日本語ラベル (未知の値はそのまま返す)."""
    if not s:
        return "不明"
    return STATUS_LABELS.get(s, s)


def is_schedulable_status(s: str | None) -> bool:
    """予定を持てるステータスか (= 稼働中).

    非稼働の列挙 (suspended / admitted / pending / cancelled / 旧 inactive) は
    増減しうるので **active かどうか**の 1 点だけで判定する (設計 §7-1)。
    """
    return s == "active"


def direction_for(current: str | None, to: str | None) -> str:
    """ステータス変更の向き: ``deactivate`` / ``reactivate`` / ``none``.

    現在と変更後がともに稼働中、またはともに非稼働なら ``none``
    (= 予定への影響なし・確認ダイアログ不要)。
    """
    cur = is_schedulable_status(current)
    nxt = is_schedulable_status(to)
    if cur and not nxt:
        return "deactivate"
    if not cur and nxt:
        return "reactivate"
    return "none"


def today_jst() -> date:
    """今日 (JST)。サーバは UTC なので日付境界を跨がないよう必ずこれを使う."""
    return datetime.now(UTC).astimezone(_JST).date()


def _week_label(iso_year: int, iso_week: int) -> str:
    """'9/14週' (その週の月曜日)."""
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    return f"{monday.month}/{monday.day}週"


def _week_counts(counter: dict[tuple[int, int], int]) -> list[WeekCount]:
    return [
        WeekCount(iso_year=y, iso_week=w, count=c, label=_week_label(y, w))
        for (y, w), c in sorted(counter.items())
    ]


class _PatientReadShim:
    """``PatientV2Read`` へ渡す読み取り用ラッパ (DB は一切変えない).

    ``patients.special_week_active`` は NULL 可なのに DTO は ``list`` を要求する
    (旧データの残骸)。ここで空リストへ倒す。属性が無いもの (``ng_staff_count``)
    は AttributeError のまま = pydantic 側の default に落ちる。
    """

    __slots__ = ("_patient",)

    def __init__(self, patient: Patient) -> None:
        self._patient = patient

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._patient, name)
        if name == "special_week_active" and value is None:
            return []
        return value


async def build_patient_read(db: AsyncSession, patient: Patient) -> PatientV2Read:
    """患者 ORM → 応答 DTO.

    ``model_validate`` は同期文脈で属性を読むため、遅延ロードが残っていると
    ``MissingGreenlet`` になる。明示的に flush → refresh してから詰める。
    """
    await db.flush()
    await db.refresh(patient)
    return PatientV2Read.model_validate(_PatientReadShim(patient), from_attributes=True)


def _validate_from_date(from_date: date | None) -> date:
    """``from_date`` の既定 (今日) と過去日ガード (設計 §7-3 422)."""
    today = today_jst()
    resolved = from_date or today
    if resolved < today:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="過去の日付は指定できません（今日以降を指定してください）",
        )
    return resolved


# ---------------------------------------------------------------------------
# selector — 影響件数 (dry-run) と実行が共有する単一ソース
# ---------------------------------------------------------------------------


@dataclass
class _DeactivationPlan:
    """非稼働化で「何をどうするか」。dry-run も実行もこの 1 つを見る."""

    targets: list[Visit] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    #: 特別訪問週間の ● (配置済み) 由来で、``end`` を選んだ場合のみ取消される訪問
    special_placed_targets: list[Visit] = field(default_factory=list)
    period: SpecialVisitPeriod | None = None
    pool_marks: int = 0
    placed_marks: int = 0


async def _active_special_period(db: AsyncSession, patient_id: UUID) -> SpecialVisitPeriod | None:
    """患者の有効な特別訪問週間 (同時に 1 本のみ = API 層で担保)."""
    return await db.scalar(
        select(SpecialVisitPeriod)
        .where(
            SpecialVisitPeriod.patient_id == patient_id,
            SpecialVisitPeriod.status == PERIOD_STATUS_ACTIVE,
        )
        .order_by(SpecialVisitPeriod.start_date.desc())
        .limit(1)
    )


async def _select_deactivation_targets(
    db: AsyncSession,
    patient_id: UUID,
    from_date: date,
    *,
    special_period_action: str,
) -> _DeactivationPlan:
    """非稼働化の取消対象を選ぶ (設計 §7-3 (c)).

    対象: ``status='planned'`` / ``deleted_at IS NULL`` / ``visit_date >= from_date``。
    除外 (``excluded`` に理由別で計上):
      * ``checked_in``  … 打刻がある (実績が付いている)
      * ``in_progress`` … 訪問中
      * ``week_pinned`` … 青ピン (蓋)。解除してから
    2 名体制 (``visit_group_id``) は **グループ単位**で扱い、1 人でも除外理由が
    あればグループごと除外する (片肺の取消を作らない)。

    特別訪問週間の ● (placed) 由来の訪問は ``special_period_action='keep'`` なら
    **一切触らない** (PO 決定: ⭐ は管理者の判断に従う)。``end`` のとき対象に含める。
    """
    plan = _DeactivationPlan()

    base_rows = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == patient_id,
                    Visit.deleted_at.is_(None),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.visit_date >= from_date,
                )
            )
        ).all()
    )

    # 2 名体制の相方を引き込む (visit_cancel_week と同じ作法)。
    group_ids = {v.visit_group_id for v in base_rows if v.visit_group_id is not None}
    candidates: dict[UUID, Visit] = {v.id: v for v in base_rows}
    if group_ids:
        partners = await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient_id,
                Visit.visit_group_id.in_(group_ids),
                Visit.deleted_at.is_(None),
            )
        )
        for v in partners.all():
            # 過去日・別ステータスの相方は取消対象にしない (下の除外判定で落ちる)。
            candidates.setdefault(v.id, v)

    if not candidates:
        plan.period = await _active_special_period(db, patient_id)
        if plan.period is not None:
            await _count_period_marks(db, plan)
        return plan

    checked_in_ids = set(
        (
            await db.scalars(
                select(VisitCheckin.visit_id).where(
                    VisitCheckin.visit_id.in_(list(candidates.keys()))
                )
            )
        ).all()
    )

    excluded: dict[str, int] = {"checked_in": 0, "in_progress": 0, "week_pinned": 0}

    def _reason(v: Visit) -> str | None:
        if v.id in checked_in_ids:
            return "checked_in"
        if v.status == VISIT_STATUS_IN_PROGRESS:
            return "in_progress"
        if bool(v.week_pinned):
            return "week_pinned"
        return None

    # グループ単位の除外: 1 件でも理由があればグループ全員を同じ理由で除外する。
    group_reason: dict[UUID, str] = {}
    for v in candidates.values():
        r = _reason(v)
        if r is not None and v.visit_group_id is not None:
            group_reason.setdefault(v.visit_group_id, r)

    in_scope: list[Visit] = []
    for v in candidates.values():
        if v.status != VISIT_STATUS_PLANNED or v.visit_date < from_date:
            # 相方が過去日 / 訪問中 / 完了。**from_date 以降の**訪問中だけ理由として
            # 見せる (過去日は元々対象外なので「除外した」とは言わない)。
            if v.status == VISIT_STATUS_IN_PROGRESS and v.visit_date >= from_date:
                excluded["in_progress"] += 1
            continue
        reason = _reason(v)
        if reason is None and v.visit_group_id is not None:
            reason = group_reason.get(v.visit_group_id)
        if reason is not None:
            excluded[reason] += 1
            continue
        in_scope.append(v)

    plan.excluded = excluded

    # 特別訪問週間の ● (placed mark) が指している訪問。
    plan.period = await _active_special_period(db, patient_id)
    placed_visit_ids: set[UUID] = set()
    if plan.period is not None:
        await _count_period_marks(db, plan)
        rows = await db.scalars(
            select(SpecialVisitMark.placed_visit_id).where(
                SpecialVisitMark.period_id == plan.period.id,
                SpecialVisitMark.status == MARK_STATUS_PLACED,
                SpecialVisitMark.placed_visit_id.is_not(None),
            )
        )
        placed_visit_ids = {vid for vid in rows.all() if vid is not None}

    for v in sorted(in_scope, key=lambda x: (x.visit_date, x.start_time)):
        if v.id in placed_visit_ids:
            plan.special_placed_targets.append(v)
            if special_period_action == "end":
                plan.targets.append(v)
        else:
            plan.targets.append(v)

    return plan


async def _count_period_marks(db: AsyncSession, plan: _DeactivationPlan) -> None:
    """期間の ○ (pool) / ● (placed) 枚数を数える."""
    if plan.period is None:  # pragma: no cover (呼び出し側でガード済み)
        return
    marks = list(
        (
            await db.scalars(
                select(SpecialVisitMark).where(SpecialVisitMark.period_id == plan.period.id)
            )
        ).all()
    )
    plan.pool_marks = sum(1 for m in marks if m.status == MARK_STATUS_POOL)
    plan.placed_marks = sum(1 for m in marks if m.status == MARK_STATUS_PLACED)


async def _pending_request_rows(
    db: AsyncSession, patient_id: UUID, *, exclude_request_id: UUID | None = None
) -> list[PendingRequest]:
    """その患者を対象にした未処理の申請 (自動却下の対象).

    ``exclude_request_id``: 申請適用 (applier) から呼ばれたときの **その申請自身**。
    適用中は行がまだ ``pending`` なので、除外しないと自分を却下してしまう。
    """
    stmt = select(PendingRequest).where(
        PendingRequest.target_patient_id == patient_id,
        PendingRequest.status == RequestStatus.PENDING.value,
    )
    if exclude_request_id is not None:
        stmt = stmt.where(PendingRequest.id != exclude_request_id)
    return list((await db.scalars(stmt)).all())


async def _fixed_visit_row_count(db: AsyncSession, patient_id: UUID) -> int:
    """型 (patient_fixed_visits) の行数。非稼働化では **消さない** (Q13)."""
    from sqlalchemy import func

    from app.models.patient_fixed_visit import PatientFixedVisit

    return int(
        await db.scalar(
            select(func.count())
            .select_from(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == patient_id)
        )
        or 0
    )


async def _generated_weeks(
    db: AsyncSession, *, office_id: UUID, from_date: date
) -> list[tuple[int, int]]:
    """「生成済みの週」= その拠点の生存 visit が 1 件でもある ISO 週 (from_date の週以降).

    復帰で型から作り直す対象週。まだ週生成していない先の週まで作ってしまわないための
    目安として、拠点の実績 (= 週生成が回った証拠) を使う (設計 §7-3 (c))。
    先頭から ``MAX_REGENERATE_WEEKS`` 週で打ち切る (dry-run と実行で同じ helper)。
    """
    iso = from_date.isocalendar()
    week_monday = date.fromisocalendar(iso.year, iso.week, 1)
    rows = await db.scalars(
        select(Visit.visit_date)
        .join(Patient, Patient.id == Visit.patient_id)
        .where(
            Patient.primary_office_id == office_id,
            Patient.deleted_at.is_(None),
            Visit.deleted_at.is_(None),
            Visit.visit_date >= week_monday,
        )
        .distinct()
    )
    weeks: set[tuple[int, int]] = set()
    for d in rows.all():
        c = d.isocalendar()
        weeks.add((c.year, c.week))
    return sorted(weeks)[:MAX_REGENERATE_WEEKS]


# ---------------------------------------------------------------------------
# 影響件数 (dry-run)
# ---------------------------------------------------------------------------


async def compute_impact(
    db: AsyncSession,
    patient: Patient,
    *,
    to_status: str,
    from_date: date | None = None,
    special_period_action: str = "keep",
) -> StatusImpact:
    """確認ダイアログ用の影響件数を返す (DB は変更しない).

    **実行と同じ selector / 同じ再生成関数**を通すので、表示と結果がズレない。
    復帰 (reactivate) の見込み件数だけは「実際に作ってみて数え、
    ``db.rollback()`` で捨てる」試走で求める (型の衝突スキップ・移動時間補正まで
    含めて実行と一致させるため)。呼び出し側 (GET endpoint) は commit しないこと。

    注意 (reactivate のみ): 試走の ``rollback`` で **同じ session の ORM
    オブジェクトは一律 expire する**。``patient`` はここで読み直すが、他の
    オブジェクト (操作者 ``User`` など) を呼び出し後に使うなら、呼び出し前に
    ``id`` 等を控えておくこと。
    """
    resolved_from = _validate_from_date(from_date)
    patient_id = patient.id
    current_status = patient.status
    office_id = patient.primary_office_id
    direction = direction_for(current_status, to_status)

    special: ImpactSpecialPeriod | None = None
    visits = ImpactVisits(total=0)
    regenerate: ImpactRegenerate | None = None
    pending_count = 0
    fixed_rows = await _fixed_visit_row_count(db, patient_id)

    if direction == "deactivate":
        # 件数は選択された ⭐ の扱い (既定 keep) で数える。keep のとき end で増える
        # 分は special_period.placed_future_visits に出す (FE はラジオ変更で再取得)。
        plan = await _select_deactivation_targets(
            db, patient_id, resolved_from, special_period_action=special_period_action
        )
        by_week: dict[tuple[int, int], int] = {}
        by_source: dict[str, int] = {}
        for v in plan.targets:
            c = v.visit_date.isocalendar()
            by_week[(c.year, c.week)] = by_week.get((c.year, c.week), 0) + 1
            by_source[v.source] = by_source.get(v.source, 0) + 1
        visits = ImpactVisits(
            total=len(plan.targets),
            by_week=_week_counts(by_week),
            by_source=by_source,
            pair_groups=len({v.visit_group_id for v in plan.targets if v.visit_group_id}),
            excluded=plan.excluded or {"checked_in": 0, "in_progress": 0, "week_pinned": 0},
        )
        if plan.period is not None:
            special = ImpactSpecialPeriod(
                id=plan.period.id,
                start_date=plan.period.start_date,
                end_date=plan.period.end_date,
                pool_marks=plan.pool_marks,
                placed_marks=plan.placed_marks,
                placed_future_visits=len(plan.special_placed_targets),
            )
        pending_count = len(await _pending_request_rows(db, patient_id))
        kaipoke_weeks = len(visits.by_week)

    elif direction == "reactivate":
        weeks: list[WeekCount] = []
        total = 0
        if office_id is not None:
            # 試走 (DB へは書くが最後に rollback する)。実行と同じ関数を通す。
            patient.status = "active"
            # ``_load_active_patients`` は DB を読むので、flush しないと試走が
            # 「まだ非稼働」を見て 0 件になる (autoflush=False)。
            await db.flush()
            trial_weeks = await _generated_weeks(db, office_id=office_id, from_date=resolved_from)
            await _soft_delete_status_cancelled(db, patient_id, resolved_from)
            created_by_week = await _regenerate_from_fixed(
                db,
                patient_id=patient_id,
                office_id=office_id,
                from_date=resolved_from,
                weeks=trial_weeks,
            )
            weeks = _week_counts(created_by_week)
            total = sum(created_by_week.values())
            await db.rollback()
            # rollback で patient は expire する。呼び出し側が続けて使えるよう
            # 明示的に読み直す (同期文脈の遅延ロード = MissingGreenlet を防ぐ)。
            await db.refresh(patient)
        regenerate = ImpactRegenerate(weeks=weeks, total=total)
        kaipoke_weeks = len(weeks)

    else:
        kaipoke_weeks = 0

    return StatusImpact(
        patient_id=patient_id,
        current_status=current_status,
        to_status=to_status,  # type: ignore[arg-type]
        from_date=resolved_from,
        direction=direction,  # type: ignore[arg-type]
        visits=visits,
        special_period=special,
        fixed_visit_rows=fixed_rows,
        pending_requests=pending_count,
        kaipoke_weeks=kaipoke_weeks,
        regenerate=regenerate,
    )


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


async def apply_status_change(
    db: AsyncSession,
    patient: Patient,
    *,
    to_status: str,
    from_date: date | None = None,
    special_period_action: str = "keep",
    regenerate: bool = True,
    actor_user_id: UUID | None = None,
    note: str | None = None,
    exclude_pending_request_id: UUID | None = None,
) -> StatusChangeResult:
    """ステータスを変更し、予定を連動させる (単一の入口).

    commit はしない (呼び出し側のトランザクション境界に乗せる)。
    """
    resolved_from = _validate_from_date(from_date)
    direction = direction_for(patient.status, to_status)

    try:
        if direction == "deactivate":
            return await _apply_deactivation(
                db,
                patient,
                to_status=to_status,
                from_date=resolved_from,
                special_period_action=special_period_action,
                actor_user_id=actor_user_id,
                note=note,
                exclude_pending_request_id=exclude_pending_request_id,
            )
        if direction == "reactivate":
            return await _apply_reactivation(
                db,
                patient,
                to_status=to_status,
                from_date=resolved_from,
                regenerate=regenerate,
                actor_user_id=actor_user_id,
                note=note,
            )
        return await _apply_status_only(
            db, patient, to_status=to_status, actor_user_id=actor_user_id
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="他のユーザーが同じ予定を処理中です。もう一度実行してください。",
        ) from exc


def _stamp_status(patient: Patient, to_status: str, actor_user_id: UUID | None) -> None:
    patient.status = to_status
    patient.status_changed_at = datetime.now(UTC)
    patient.status_changed_by = actor_user_id


async def _apply_status_only(
    db: AsyncSession,
    patient: Patient,
    *,
    to_status: str,
    actor_user_id: UUID | None,
) -> StatusChangeResult:
    """向きが ``none`` (非稼働 → 別の非稼働 / 同一): status だけ書く no-op."""
    if patient.status != to_status:
        _stamp_status(patient, to_status, actor_user_id)
        await db.flush()
    return StatusChangeResult(
        patient=await build_patient_read(db, patient),
        direction="none",
    )


async def _apply_deactivation(
    db: AsyncSession,
    patient: Patient,
    *,
    to_status: str,
    from_date: date,
    special_period_action: str,
    actor_user_id: UUID | None,
    note: str | None,
    exclude_pending_request_id: UUID | None = None,
) -> StatusChangeResult:
    plan = await _select_deactivation_targets(
        db, patient.id, from_date, special_period_action=special_period_action
    )

    # 1) 取消 (行は残す)。元の出所は op-log に控えて undo で戻す。
    prev_sources = {str(v.id): v.source for v in plan.targets}
    for v in plan.targets:
        v.status = VISIT_STATUS_CANCELLED
        v.source = VISIT_SOURCE_STATUS_CANCEL
    await db.flush()

    cancelled_ids = [v.id for v in plan.targets]

    # 2) 特別訪問週間 (PO 決定 Q12: 既定は「残す」・選択に従う)。
    special_result: SpecialPeriodResult | None = None
    if plan.period is not None:
        cancelled_pool = 0
        if special_period_action == "end":
            plan.period.status = PERIOD_STATUS_ENDED
            plan.period.end_date = max(plan.period.start_date, from_date - timedelta(days=1))
            marks = list(
                (
                    await db.scalars(
                        select(SpecialVisitMark).where(SpecialVisitMark.period_id == plan.period.id)
                    )
                ).all()
            )
            cancelled_visit_id_set = set(cancelled_ids)
            for m in marks:
                if m.status == MARK_STATUS_POOL:
                    m.status = MARK_STATUS_CANCELLED
                    cancelled_pool += 1
                elif (
                    m.status == MARK_STATUS_PLACED
                    and m.placed_visit_id is not None
                    and m.placed_visit_id in cancelled_visit_id_set
                ):
                    # 訪問側を取消したので ● も倒す (自己回復ロジックの対象外にする)。
                    m.status = MARK_STATUS_CANCELLED
            await db.flush()
        special_result = SpecialPeriodResult(
            id=plan.period.id,
            action="end" if special_period_action == "end" else "keep",  # type: ignore[arg-type]
            cancelled_pool_marks=cancelled_pool,
        )

    # 3) 未処理の申請を自動却下 (Q14) + 申請者へ通知。
    rejected = await _reject_pending_requests(
        db,
        patient,
        to_status=to_status,
        actor_user_id=actor_user_id,
        exclude_request_id=exclude_pending_request_id,
    )

    # 4) ステータスを刻む。
    _stamp_status(patient, to_status, actor_user_id)
    await db.flush()

    # 5) op-log (週ごとに 1 グループ・既存 cancel_visit を再利用 = 「戻る」が効く)。
    op_groups = await _record_cancel_ops(
        db,
        patient=patient,
        to_status=to_status,
        visits=plan.targets,
        prev_sources=prev_sources,
        actor_user_id=actor_user_id,
        note=note,
    )

    # 6) 管理者へお知らせ。
    week_labels = sorted({_week_label(*_iso_week_of(v.visit_date)) for v in plan.targets})
    body_lines = [
        f"{len(cancelled_ids)} 件の予定を取消しました"
        + (f"（{week_labels[0]}〜{week_labels[-1]}）" if week_labels else "")
        + f"。起点: {from_date.isoformat()}"
    ]
    if special_result is not None:
        body_lines.append(
            "特別訪問週間は終了しました。"
            if special_result.action == "end"
            else "特別訪問週間は残しています。"
        )
    if rejected:
        body_lines.append(f"未処理の申請 {rejected} 件を自動却下しました。")
    if note:
        body_lines.append(f"メモ: {note}")
    body_lines.append("カイポケの週間パターンを停止してください。")
    body_lines.append(f"患者ID: {patient.id}")
    notified = await _notify_admins(
        db,
        patient=patient,
        title=f"{patient.name}様を{status_label(to_status)}にしました",
        body="\n".join(body_lines),
    )

    return StatusChangeResult(
        patient=await build_patient_read(db, patient),
        direction="deactivate",
        cancelled_visit_ids=cancelled_ids,
        cancelled_count=len(cancelled_ids),
        special_period=special_result,
        rejected_requests=rejected,
        op_groups=op_groups,
        notification_count=notified,
    )


async def _apply_reactivation(
    db: AsyncSession,
    patient: Patient,
    *,
    to_status: str,
    from_date: date,
    regenerate: bool,
    actor_user_id: UUID | None,
    note: str | None,
) -> StatusChangeResult:
    if regenerate and patient.primary_office_id is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "主担当拠点が未設定のため予定を作り直せません。"
                "拠点を設定してから稼働中に戻してください（作り直さない場合は"
                "「型から予定を作る」のチェックを外してください）"
            ),
        )

    _stamp_status(patient, to_status, actor_user_id)
    await db.flush()

    summary: RegeneratedSummary | None = None
    if regenerate:
        office_id = patient.primary_office_id
        assert office_id is not None  # 上でガード済み
        # 「生成済みの週」は **取消分を消す前に**数える (取消済みの週も対象に含める)。
        weeks = await _generated_weeks(db, office_id=office_id, from_date=from_date)
        await _soft_delete_status_cancelled(db, patient.id, from_date)
        created_by_week = await _regenerate_from_fixed(
            db,
            patient_id=patient.id,
            office_id=office_id,
            from_date=from_date,
            weeks=weeks,
        )
        summary = RegeneratedSummary(
            created=sum(created_by_week.values()),
            weeks=_week_counts(created_by_week),
        )

    created = summary.created if summary is not None else 0
    body_lines = [
        f"稼働中に戻しました。型から {created} 件を作りました。（起点: {from_date.isoformat()}）"
    ]
    if summary is not None and summary.weeks:
        body_lines.append("対象週: " + "・".join(w.label for w in summary.weeks))
    body_lines.append("特別訪問週間は自動では再開しません（必要なら新しく設定してください）。")
    if note:
        body_lines.append(f"メモ: {note}")
    body_lines.append("カイポケの週間パターンを再開してください。")
    body_lines.append(f"患者ID: {patient.id}")
    notified = await _notify_admins(
        db,
        patient=patient,
        title=f"{patient.name}様を{status_label(to_status)}に戻しました",
        body="\n".join(body_lines),
    )

    return StatusChangeResult(
        patient=await build_patient_read(db, patient),
        direction="reactivate",
        regenerated=summary,
        notification_count=notified,
    )


def _iso_week_of(d: date) -> tuple[int, int]:
    c = d.isocalendar()
    return (c.year, c.week)


async def _record_cancel_ops(
    db: AsyncSession,
    *,
    patient: Patient,
    to_status: str,
    visits: list[Visit],
    prev_sources: dict[str, str],
    actor_user_id: UUID | None,
    note: str | None,
) -> list[OpGroupRef]:
    """取消を週ごとに 1 op_group で記録する (既存 ``cancel_visit`` を再利用).

    記録は **監査のため**であって「戻る」で戻すためではない: ステータス連動の取消
    (``cancel_source='status_cancel'``) は undo / redo とも ``op_log_service`` 側で
    ブロックする (戻すと出所が auto に戻って次の週生成で再び消える / manual_week に
    戻ると復帰時に二重になる)。戻し方は「ステータスを稼働中に戻す」1 本に絞る。

    ``user_id`` は NOT NULL なので、操作者不明 (バッチ / 申請適用) の場合は記録しない。
    """
    if not visits or actor_user_id is None:
        return []
    by_week: dict[tuple[int, int], list[Visit]] = {}
    for v in visits:
        by_week.setdefault(_iso_week_of(v.visit_date), []).append(v)

    refs: list[OpGroupRef] = []
    for (iso_year, iso_week), rows in sorted(by_week.items()):
        op_group_id = uuid.uuid4()
        visit_ids = [str(v.id) for v in rows]
        sources = {vid: prev_sources.get(vid, "") for vid in visit_ids}
        label = f"{patient.name}様 {status_label(to_status)}により取消（ステータス連動）"
        if note:
            label = f"{label}（{note}）"
        forward: dict[str, Any] = {
            "op": "cancel_visit",
            "visit_ids": visit_ids,
            "cancel": True,
            "sources": sources,
            # 取消の出所 (undo/redo でも status_cancel を保つ)。
            "cancel_source": VISIT_SOURCE_STATUS_CANCEL,
        }
        inverse: dict[str, Any] = {
            "op": "cancel_visit",
            "visit_ids": visit_ids,
            "cancel": False,
            "sources": sources,
            # 逆向き (戻す) 側にも刻む: op_log 側が「ステータス連動の取消」だと
            # 判定して undo / redo をどちらもブロックするため (下記 label 参照)。
            "cancel_source": VISIT_SOURCE_STATUS_CANCEL,
        }
        await record_op(
            db,
            user_id=actor_user_id,
            iso_year=iso_year,
            iso_week=iso_week,
            op_group_id=op_group_id,
            op_kind="cancel_visit",
            label=label,
            forward_payload=forward,
            inverse_payload=inverse,
            strict=True,
        )
        refs.append(OpGroupRef(iso_year=iso_year, iso_week=iso_week, op_group_id=op_group_id))
    return refs


async def _soft_delete_status_cancelled(db: AsyncSession, patient_id: UUID, from_date: date) -> int:
    """復帰時: ステータス連動で取消した訪問 (``status_cancel``) を soft-delete する.

    ``reset_visits_to_fixed`` の削除対象は planned/proposed のみ (cancelled は保護)
    なので、明示的に消しておかないと同じ枠に再生成できない (unique key 衝突で skip)。
    """
    rows = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == patient_id,
                    Visit.deleted_at.is_(None),
                    Visit.source == VISIT_SOURCE_STATUS_CANCEL,
                    Visit.status == VISIT_STATUS_CANCELLED,
                    Visit.visit_date >= from_date,
                )
            )
        ).all()
    )
    if not rows:
        return 0
    now = datetime.now(UTC)
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(VisitStaffAssignment).where(
            VisitStaffAssignment.visit_id.in_([v.id for v in rows])
        )
    )
    for v in rows:
        v.deleted_at = now
    await db.flush()
    return len(rows)


async def _regenerate_from_fixed(
    db: AsyncSession,
    *,
    patient_id: UUID,
    office_id: UUID,
    from_date: date,
    weeks: list[tuple[int, int]],
) -> dict[tuple[int, int], int]:
    """生成済みの各週で型から作り直し、週ごとの作成件数を返す.

    ``from_date`` より前の日は **触らない**: その週の再生成で作られた過去日の訪問は
    soft-delete し、再生成が消した既存の過去日訪問は元に戻す (盤面の過去は不可侵)。
    ``reset_visits_to_fixed`` は削除の際に ``visit_staff_assignments`` を物理削除
    するので、戻す訪問の担当行は事前に控えて貼り直す (担当が消えた過去日を作らない)。
    """
    from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

    created: dict[tuple[int, int], int] = {}
    for iso_year, iso_week in weeks:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        before_ids = await _live_visit_ids_before(
            db, patient_id, week_monday=week_monday, from_date=from_date
        )
        before_assignments = await _assignments_of(db, before_ids)
        result = await reset_visits_to_fixed(
            db,
            iso_year=iso_year,
            iso_week=iso_week,
            office_ids=[office_id],
            mode="auto",
            patient_id=patient_id,
        )
        made = int(result.get("visits_regenerated", 0) or 0)
        made -= await _revert_before_from_date(
            db, patient_id, week_monday=week_monday, from_date=from_date, before_ids=before_ids
        )
        await _restore_assignments(db, before_assignments)
        created[(iso_year, iso_week)] = max(made, 0)
    return created


async def _live_visit_ids_before(
    db: AsyncSession, patient_id: UUID, *, week_monday: date, from_date: date
) -> set[UUID]:
    if from_date <= week_monday:
        return set()
    rows = await db.scalars(
        select(Visit.id).where(
            Visit.patient_id == patient_id,
            Visit.deleted_at.is_(None),
            Visit.visit_date >= week_monday,
            Visit.visit_date < from_date,
        )
    )
    return set(rows.all())


async def _assignments_of(db: AsyncSession, visit_ids: set[UUID]) -> set[tuple[UUID, UUID]]:
    """``visit_staff_assignments`` の (visit_id, staff_id) を控える (復元用)."""
    if not visit_ids:
        return set()
    rows = await db.execute(
        select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
            VisitStaffAssignment.visit_id.in_(list(visit_ids))
        )
    )
    return {(vid, sid) for vid, sid in rows.all()}


async def _restore_assignments(db: AsyncSession, pairs: set[tuple[UUID, UUID]]) -> None:
    """控えた担当行を貼り直す (生存している訪問のみ・既にある行は触らない)."""
    if not pairs:
        return
    visit_ids = {vid for vid, _ in pairs}
    alive = set(
        (
            await db.scalars(
                select(Visit.id).where(Visit.id.in_(list(visit_ids)), Visit.deleted_at.is_(None))
            )
        ).all()
    )
    existing = await _assignments_of(db, alive)
    added = False
    for visit_id, staff_id in sorted(pairs):
        if visit_id not in alive or (visit_id, staff_id) in existing:
            continue
        db.add(VisitStaffAssignment(visit_id=visit_id, staff_id=staff_id))
        added = True
    if added:
        await db.flush()


async def _revert_before_from_date(
    db: AsyncSession,
    patient_id: UUID,
    *,
    week_monday: date,
    from_date: date,
    before_ids: set[UUID],
) -> int:
    """``from_date`` より前の日を再生成前の状態へ戻し、消した新規件数を返す."""
    if from_date <= week_monday:
        return 0
    rows = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == patient_id,
                    Visit.visit_date >= week_monday,
                    Visit.visit_date < from_date,
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    from sqlalchemy import delete as sa_delete

    created_before = [v for v in rows if v.id not in before_ids and v.deleted_at is None]
    if created_before:
        await db.execute(
            sa_delete(VisitStaffAssignment).where(
                VisitStaffAssignment.visit_id.in_([v.id for v in created_before])
            )
        )
        for v in created_before:
            v.deleted_at = now
        await db.flush()
    # 再生成が消した既存の過去日訪問を戻す (穴を空けない)。
    restored = [v for v in rows if v.id in before_ids and v.deleted_at is not None]
    for v in restored:
        v.deleted_at = None
    if restored:
        await db.flush()
    return len(created_before)


# ---------------------------------------------------------------------------
# 申請の自動却下 / 通知
# ---------------------------------------------------------------------------


async def _reject_pending_requests(
    db: AsyncSession,
    patient: Patient,
    *,
    to_status: str,
    actor_user_id: UUID | None,
    exclude_request_id: UUID | None = None,
) -> int:
    """その患者宛の未処理申請を自動却下し、申請者へ通知する (Q14)."""
    rows = await _pending_request_rows(db, patient.id, exclude_request_id=exclude_request_id)
    if not rows:
        return 0
    now = datetime.now(UTC)
    for r in rows:
        r.status = RequestStatus.REJECTED.value
        r.rejected_by = actor_user_id
        r.rejected_at = now
        r.rejection_reason = AUTO_REJECT_REASON
        await _add_notification(
            db,
            user_id=r.requester_user_id,
            title="申請が自動却下されました",
            body="\n".join(
                [
                    f"{patient.name}様が{status_label(to_status)}のため、"
                    "申請は自動的に却下されました。",
                    f"理由: {AUTO_REJECT_REASON}",
                    f"患者ID: {patient.id}",
                ]
            ),
            reference_type="pending_request",
        )
    await db.flush()
    return len(rows)


async def _notify_admins(db: AsyncSession, *, patient: Patient, title: str, body: str) -> int:
    """active な admin 全員へお知らせを作る (監査を兼ねるので操作者も除外しない)."""
    from app.services.constraint_override_notify import _active_admin_users

    users = await _active_admin_users(db)
    for u in users:
        await _add_notification(db, user_id=u.id, title=title, body=body)
    await db.flush()
    return len(users)


async def _add_notification(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    title: str,
    body: str,
    reference_type: str = "patient",
) -> None:
    """通知を 1 行足す (常に新規)。

    ``notifications`` の冪等キー ``(user_id, reference_type, reference_id)`` は
    **部分 UNIQUE (reference_id IS NOT NULL)** なので、``reference_id=None`` で
    入れると同じ患者のステータスを何度変えても衝突しない = 変更のたびに 1 通
    残る (履歴として読める)。対象の患者 / 申請は本文に書く
    (``leave_notify`` の「毎回通知は reference を通さない」流儀と同じ)。
    """
    if user_id is None:
        return
    alive = await db.scalar(select(User.id).where(User.id == user_id, User.deleted_at.is_(None)))
    if alive is None:
        return
    db.add(
        Notification(
            user_id=user_id,
            type=NOTIFY_TYPE_PATIENT_STATUS_SYNC,
            title=title,
            body=body,
            reference_type=reference_type,
            reference_id=None,
        )
    )


__all__ = [
    "AUTO_REJECT_REASON",
    "build_patient_read",
    "NOTIFY_TYPE_PATIENT_STATUS_SYNC",
    "STATUS_LABELS",
    "apply_status_change",
    "compute_impact",
    "direction_for",
    "is_schedulable_status",
    "status_label",
    "today_jst",
]
