"""QR 訪問チェックイン Phase 5-2: アプリ内通知 producer (場所違い / 未訪問).

通知チャネルは **アプリ内 notifications のみ** (LINE WORKS 連携・連絡先は対象外、
設計 §5-2 ユーザー判断 2026-06-30)。トリガは 2 系統:

* **場所違い (mismatch) = イベント駆動**: checkin 記録時 (judge 後) に
  ``match_status == "mismatch"`` なら ``notify_checkin_mismatch`` を呼び、即時生成。
  呼び出し側 (visits.py checkin ハンドラ) の transaction 内で行を add する (commit は
  呼び出し側)。

* **未訪問 (missing) = 時間ベース**: イベントが無いため VPS cron (5 分毎想定) が叩く
  管理 API ``POST /api/v1/admin/checkin/check-missing`` から ``run_check_missing`` を
  実行する。advisory lock で多重実行を排他し自前で commit する
  (``services/geocoding/audit.py`` の実績パターンに倣う)。

冪等化: ``notifications.reference_type`` + ``reference_id (= visit_id)`` で
「同一 visit の同種通知は 1 ユーザー 1 行」を担保する (DB 側は migration 0045 の
部分 UNIQUE が安全網)。同一 visit へ複数回 checkin しても通知は増えない。

通知ターゲットの簡略化 (設計明記): 通知は **全 active admin/manager** に送る
(``role in (admin, manager)`` かつ ``deleted_at IS NULL``)。拠点別の絞り込みは
visit → staff → office の解決が必要で当面オーバースペックのため見送る (設計 §5-2
「対象が拠点別に絞れない場合は全 admin/manager に通知」)。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notification import Notification
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_checkin import VisitCheckin
from app.models.visit_review import VisitReview
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.accompaniment import resolve_accompaniment_by_visit
from app.services.checkin.judge import load_thresholds
from app.services.checkin.monitor import compute_pair_effective_starts, visit_staff_id_set
from app.services.constraint_override_notify import collect_constraint_warnings_for_staff
from app.utils.db import try_advisory_xact_lock

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

# notifications.type / reference_type に使う種別 (frontend のアイコン選択キーと共用)。
NOTIFY_MISMATCH: Final = "checkin_mismatch"
NOTIFY_MISSING: Final = "checkin_missing"
# QR 打刻の開放 (設計 ``docs/plans/qr-open-checkin-design.md`` §4-4)。
# いずれも reference_id = visit.id (**非 NULL**) で冪等。reference_id に None を
# 使うと ``IS NULL`` 側へ展開されて以後の通知が永久に沈黙するため使わない。
NOTIFY_SUBSTITUTE: Final = "checkin_substitute"
NOTIFY_UNPLANNED: Final = "checkin_unplanned"
NOTIFY_NG_STAFF: Final = "checkin_ng_staff"

# pg_try_advisory_xact_lock 用キー (bigint 範囲内)。check-missing 専用。
# "CHKMISSG" 相当の 64-bit 整数 (audit.py の ADVISORY_LOCK_KEY と同方式)。
CHECK_MISSING_LOCK_KEY: Final = 0x43484B4D_49535347


def _as_jst(dt: datetime) -> datetime:
    """timestamptz (or SQLite の naive=UTC) を JST aware に正規化する."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST)


async def _active_admin_manager_users(db: AsyncSession) -> list[User]:
    """通知ターゲット: active な admin / manager (deleted_at IS NULL)."""
    return list(
        (
            await db.scalars(
                select(User).where(
                    # 管理者アカウントへ通知 (旧 'manager' は 0069 で admin へ移行済み。
                    # 万一の残存行も受け取れるよう別名込みで維持)。
                    User.role.in_(("admin", "manager")),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def _resolve_staff_name(db: AsyncSession, staff_id: UUID | None) -> str:
    if staff_id is None:
        return "担当者"
    name = await db.scalar(select(Staff.name).where(Staff.id == staff_id))
    return name or "担当者"


async def _create_idempotent(
    db: AsyncSession,
    *,
    users: list[User],
    type_: str,
    reference_type: str,
    reference_id: UUID,
    title: str,
    body: str,
) -> int:
    """(reference_type, reference_id) で未通知のユーザーにのみ通知行を add する.

    commit はしない (呼び出し側が制御する)。返却 = 新規に add した件数。
    """
    if not users:
        return 0
    already = set(
        (
            await db.scalars(
                select(Notification.user_id).where(
                    Notification.reference_type == reference_type,
                    Notification.reference_id == reference_id,
                )
            )
        ).all()
    )
    created = 0
    for u in users:
        if u.id in already:
            continue
        db.add(
            Notification(
                user_id=u.id,
                type=type_,
                title=title,
                body=body,
                reference_type=reference_type,
                reference_id=reference_id,
            )
        )
        created += 1
    return created


async def notify_checkin_mismatch(
    db: AsyncSession,
    *,
    visit: Visit,
    checkin: VisitCheckin,
) -> int:
    """場所違い checkin を admin/manager へ冪等通知する (イベント駆動・commit しない).

    ``visit.patient`` は呼び出し側で eager-load 済みを前提とする。``match_status`` が
    mismatch 以外なら no-op。返却 = 新規に add した件数。
    """
    if checkin.match_status != "mismatch":
        return 0
    users = await _active_admin_manager_users(db)
    patient_name = getattr(visit.patient, "name", None) or "利用者"
    staff_name = await _resolve_staff_name(db, checkin.staff_id)
    hhmm = _as_jst(checkin.scanned_at).strftime("%H:%M")
    dist = f"{int(checkin.distance_m)}m" if checkin.distance_m is not None else "距離不明"
    title = "場所違いのチェックイン"
    body = f"{patient_name}（{staff_name}）{hhmm} 拠点から離れた場所で打刻（{dist}）"
    return await _create_idempotent(
        db,
        users=users,
        type_=NOTIFY_MISMATCH,
        reference_type=NOTIFY_MISMATCH,
        reference_id=visit.id,
        title=title,
        body=body,
    )


# ---------------------------------------------------------------------------
# QR 打刻の開放 (代行 / 予定外 / NG 交差) — 設計 §4-4
#
# 「記録は止めない」方針は維持 (決定#5): 打刻フロー内では警告もブロックもせず、
# 事実を記録したうえで管理者ベルへ冪等通知するだけ。宛先・文面は
# ``notify_checkin_mismatch`` に準拠する (admin 向け・患者名 + スタッフ名 + 種別)。
# 文面は仮 (PO 確認事項 §9-3)。
# ---------------------------------------------------------------------------


async def _resolve_patient_name(db: AsyncSession, visit: Visit) -> str:
    """visit の患者名。eager-load 済みならそれを使い、無ければ 1 行引く.

    予定外訪問 (adhoc-checkin) は visit をその場で生成するため ``visit.patient``
    が未ロードのことがある (relationship は ``lazy="noload"``)。
    """
    name = getattr(visit.patient, "name", None) if visit.patient is not None else None
    if name:
        return name
    name = await db.scalar(select(Patient.name).where(Patient.id == visit.patient_id))
    return name or "利用者"


async def _resolve_visit_staff_ids(db: AsyncSession, visit: Visit) -> set[UUID]:
    """visit 1 件の担当集合を解決する (assignments + 新人同行を DB から引く)."""
    assignment_ids = list(
        (
            await db.scalars(
                select(VisitStaffAssignment.staff_id).where(
                    VisitStaffAssignment.visit_id == visit.id
                )
            )
        ).all()
    )
    # 同行は複数名あり得る (一般化 決定#5)。代表 1 名だけ渡すと 2 人目の同行者の
    # 打刻が「代行」と誤判定され、管理者へ誤通知が飛ぶため**全件**渡す。
    accompaniment = (await resolve_accompaniment_by_visit(db, [visit])).get(visit.id, [])
    return visit_staff_id_set(
        visit,
        assignment_staff_ids=assignment_ids,
        accompaniment_staff_ids=[e.staff_id for e in accompaniment],
    )


async def notify_checkin_substitute(
    db: AsyncSession,
    *,
    visit: Visit,
    checkin: VisitCheckin,
) -> int:
    """代行打刻 (担当外スタッフの到着) を admin へ冪等通知する (**commit しない**).

    代行 = 打刻スタッフが visit の担当集合 (primary/secondary/mentor/assignments/
    新人同行) の外。予定側の担当は書き換えない (決定#4) ため、乖離に気づく唯一の
    経路がこの通知とモニターの代行バッジになる。担当内の打刻なら no-op。
    """
    if checkin.staff_id in await _resolve_visit_staff_ids(db, visit):
        return 0
    users = await _active_admin_manager_users(db)
    patient_name = await _resolve_patient_name(db, visit)
    staff_name = await _resolve_staff_name(db, checkin.staff_id)
    planned_name = await _resolve_staff_name(db, visit.primary_staff_id)
    hhmm = _as_jst(checkin.scanned_at).strftime("%H:%M")
    return await _create_idempotent(
        db,
        users=users,
        type_=NOTIFY_SUBSTITUTE,
        reference_type=NOTIFY_SUBSTITUTE,
        reference_id=visit.id,
        title="代行での訪問記録",
        body=f"{patient_name}（予定: {planned_name} / 実績: {staff_name}）{hhmm} 担当外のスタッフが打刻",
    )


async def notify_checkin_unplanned(
    db: AsyncSession,
    *,
    visit: Visit,
    checkin: VisitCheckin,
) -> int:
    """予定外訪問の打刻を admin へ冪等通知する (**commit しない**).

    ``visit.is_unplanned`` が false なら no-op。予定外訪問は予定に載っていない
    実績のため、管理者が後から突合 (カイポケ手入力等 §7-1) できるよう必ず通知する。
    """
    if not visit.is_unplanned:
        return 0
    users = await _active_admin_manager_users(db)
    patient_name = await _resolve_patient_name(db, visit)
    staff_name = await _resolve_staff_name(db, checkin.staff_id)
    hhmm = _as_jst(checkin.scanned_at).strftime("%H:%M")
    return await _create_idempotent(
        db,
        users=users,
        type_=NOTIFY_UNPLANNED,
        reference_type=NOTIFY_UNPLANNED,
        reference_id=visit.id,
        title="予定外の訪問記録",
        body=f"{patient_name}（{staff_name}）{hhmm} 予定に無い訪問を記録",
    )


async def notify_checkin_ng_staff(
    db: AsyncSession,
    *,
    visit: Visit,
    checkin: VisitCheckin,
) -> int:
    """NG スタッフ / 性別制限に抵触する打刻を admin へ冪等通知する (**commit しない**).

    判定は手動経路の 422 確認フローと同じ芯
    (``constraint_override_notify.collect_constraint_warnings_for_staff``) を使う
    = NG 行あり、または ``patient.sex_restriction`` とスタッフ性別の不一致。
    性別未登録スタッフを違反にしないのも同じ (誤検知回避側)。

    **打刻は止めない** (決定#5)。抵触が無ければ no-op。
    """
    warnings = await collect_constraint_warnings_for_staff(
        db, staff_id=checkin.staff_id, patient_ids=[visit.patient_id]
    )
    if not warnings:
        return 0
    users = await _active_admin_manager_users(db)
    patient_name = await _resolve_patient_name(db, visit)
    staff_name = await _resolve_staff_name(db, checkin.staff_id)
    hhmm = _as_jst(checkin.scanned_at).strftime("%H:%M")
    kinds = {w.kind for w in warnings}
    if kinds == {"ng_staff"}:
        label = "NGスタッフ"
    elif kinds == {"gender"}:
        label = "性別制限外"
    else:
        label = "NGスタッフ・性別制限外"
    return await _create_idempotent(
        db,
        users=users,
        type_=NOTIFY_NG_STAFF,
        reference_type=NOTIFY_NG_STAFF,
        reference_id=visit.id,
        title=f"{label}のスタッフが訪問",
        body=f"{patient_name}（{staff_name}）{hhmm} {label}のスタッフが打刻（記録は保存済み）",
    )


async def notify_checkin_anomalies(
    db: AsyncSession,
    *,
    visit: Visit,
    checkin: VisitCheckin,
) -> dict[str, int]:
    """到着打刻の 3 種通知 (代行 / 予定外 / NG 交差) をまとめて発火する.

    到着 (arrival) 打刻の経路 (通常 checkin / adhoc-checkin) から 1 回呼ぶための
    入口。各 producer が自分で該当しないケースを no-op 判定するため、呼び出し側は
    条件分岐を持たない。**commit しない** (呼び出し側が TX 境界を握る)。
    """
    return {
        NOTIFY_SUBSTITUTE: await notify_checkin_substitute(db, visit=visit, checkin=checkin),
        NOTIFY_UNPLANNED: await notify_checkin_unplanned(db, visit=visit, checkin=checkin),
        NOTIFY_NG_STAFF: await notify_checkin_ng_staff(db, visit=visit, checkin=checkin),
    }


async def resolve_checkin_missing(db: AsyncSession, visit_id: UUID) -> int:
    """当該 visit の「未訪問」(missing) 通知を全ユーザー分削除する (commit しない).

    遅刻して後から到着した場合、cron が先に生成した missing 通知が残り続けると
    誤報になる。到着 checkin ハンドラから到着記録と同一 transaction で呼び、
    ``reference_type='checkin_missing' AND reference_id=visit_id`` の行を解消する。
    返却 = 削除した行数。
    """
    result = await db.execute(
        delete(Notification).where(
            Notification.reference_type == NOTIFY_MISSING,
            Notification.reference_id == visit_id,
        )
    )
    return int(result.rowcount or 0)


async def run_check_missing(
    db: AsyncSession,
    *,
    target_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, int | bool]:
    """当日 visit を走査し未訪問を admin/manager へ冪等通知する (VPS cron 5 分毎想定).

    未訪問 = 到着記録なし かつ (未訪問記録あり または 予定開始 + grace 超過)。
    取消 / 削除 visit、reviewed 済み (visit_reviews 行あり)、既通知は除外する。
    advisory lock で多重実行を排他し、自前で commit する (lock は commit で解放)。

    返却 = ``{"locked", "scanned", "missing", "created"}``。``created`` が生成件数。
    """
    if not await try_advisory_xact_lock(db, CHECK_MISSING_LOCK_KEY):
        logger.warning("check_missing: another job holds advisory lock, skipping")
        return {"locked": True, "scanned": 0, "missing": 0, "created": 0}

    if now is None:
        now = datetime.now(UTC)
    now_jst = _as_jst(now)
    if target_date is None:
        target_date = now_jst.date()

    thresholds = await load_thresholds(db)
    grace = thresholds["no_show_grace_min"]

    visits = list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.visit_date == target_date,
                    Visit.deleted_at.is_(None),
                    Visit.status != VISIT_STATUS_CANCELLED,
                )
                .options(selectinload(Visit.patient), selectinload(Visit.primary_staff))
                # 同一 session の identity map に既存 visit が居ても patient の eager load を
                # 確実に反映する (直接呼び出しテスト対策; 本番は request-scoped session)。
                # ペア補正が patient 座標を必要とするため必須。
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    scanned = len(visits)
    visit_ids = [v.id for v in visits]

    arrived: set[UUID] = set()
    no_showed: set[UUID] = set()
    reviewed: set[UUID] = set()
    # 同住所・同時刻ペア補正用: 到着/退出の最新 scanned_at マップ (JST)。
    arrival_at: dict[UUID, datetime] = {}
    departure_at: dict[UUID, datetime] = {}
    if visit_ids:
        arr_dep_rows = (
            await db.scalars(
                select(VisitCheckin)
                .where(
                    VisitCheckin.visit_id.in_(visit_ids),
                    VisitCheckin.kind.in_(("arrival", "departure")),
                )
                .order_by(VisitCheckin.scanned_at.desc())
            )
        ).all()
        for r in arr_dep_rows:
            target = arrival_at if r.kind == "arrival" else departure_at
            if r.visit_id not in target:  # DESC 並びなので最初に出た = 最新。
                target[r.visit_id] = _as_jst(r.scanned_at)
        arrived = set(arrival_at.keys())
        no_showed = set(
            (
                await db.scalars(
                    select(VisitCheckin.visit_id).where(
                        VisitCheckin.visit_id.in_(visit_ids),
                        VisitCheckin.kind == "no_show",
                    )
                )
            ).all()
        )
        reviewed = set(
            (
                await db.scalars(
                    select(VisitReview.visit_id).where(VisitReview.visit_id.in_(visit_ids))
                )
            ).all()
        )

    # 同住所・同時刻ペア補正後起点 (相方に到着があれば B の missing 起点を後ろへ)。
    pair_eff = compute_pair_effective_starts(visits, arrival_at, departure_at)

    users = await _active_admin_manager_users(db)
    missing = 0
    created = 0
    for v in visits:
        # 到着済み / 確認済みは要対応ではない。
        if v.id in arrived or v.id in reviewed:
            continue
        start_dt = datetime.combine(v.visit_date, v.start_time, tzinfo=JST)
        # ペア補正が効いていれば補正後起点を、無ければ予定開始を missing 起点にする。
        eff_start = pair_eff.get(v.id, start_dt)
        is_missing = v.id in no_showed or now_jst >= eff_start + timedelta(minutes=grace)
        if not is_missing:
            continue
        missing += 1
        patient_name = getattr(v.patient, "name", None) or "利用者"
        staff_name = getattr(v.primary_staff, "name", None) or "担当者"
        hhmm = start_dt.strftime("%H:%M")
        title = "未訪問の可能性"
        body = f"{patient_name}（{staff_name}）{hhmm} 予定 未訪問（到着記録なし）"
        created += await _create_idempotent(
            db,
            users=users,
            type_=NOTIFY_MISSING,
            reference_type=NOTIFY_MISSING,
            reference_id=v.id,
            title=title,
            body=body,
        )

    # commit で advisory xact lock も解放される。生成 0 件でも transaction を閉じる。
    await db.commit()
    return {"locked": False, "scanned": scanned, "missing": missing, "created": created}
