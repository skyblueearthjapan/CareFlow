"""操作ジャーナルサービス (Wave U-3) — schedule_op_log の書込・undo/redo 実行.

## v1 記録対象
    a. place-and-fix (fix_pattern=False): forward={restore_visits}, inverse={soft_delete_visits}
    b. delete_visit:                      forward={soft_delete_visits}, inverse={restore_visits}
    c. patch_course_staff:                forward={set_course_staff, new}, inverse={set_course_staff, old}
    d. move_visit_week_only:              forward={move_visit_week_only, to}, inverse={move_visit_week_only, from}

## undo/redo の動作
    - undo: 最新の undone=False グループを inverse 実行（グループ内は created_at DESC 順）
    - redo: 最古の undone=True グループを forward 実行（グループ内は created_at ASC 順）
    - 新操作記録時: その週の自分の undone=True 行（redo 枝）を削除（Excel 同等）
    - 楽観ロック: 実行前に対象の現在状態を検証し、不一致 → OpLogConflictError (409)
    - ベストエフォート: 記録失敗は logger.warning のみ（本体を失敗させない）

## ペイロード op フィールド
    "restore_visits"       — visit_ids を復元（deleted_at = NULL）
    "soft_delete_visits"   — visit_ids を soft-delete（deleted_at = now）
    "set_course_staff"     — course の assigned_staff_id を staff_id に設定
    "move_visit_week_only" — (patient_id, iso_year, iso_week, from_weekday, from_start,
                              to_weekday, to_start) で visit 位置を変更
    "set_visit_staff"      — visit 1 件の primary_staff_id / manual_staff_override /
                              visit_staff_assignments を (staff_id, manual) に設定
                              (週空間 A2: 患者個別の担当貼り替え)
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, time
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.schedule_op_log import ScheduleOpLog
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

logger = logging.getLogger(__name__)

_WEEKDAY_JP = ("月", "火", "水", "木", "金", "土", "日")


class OpLogConflictError(Exception):
    """undo/redo 前の現在状態チェックで他者変更を検出したとき."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Public helpers — エンドポイントから呼び出す
# ---------------------------------------------------------------------------


async def record_op(
    db: AsyncSession,
    *,
    user_id: UUID,
    iso_year: int,
    iso_week: int,
    op_group_id: UUID | None,
    op_kind: str,
    label: str,
    forward_payload: dict[str, Any],
    inverse_payload: dict[str, Any],
) -> None:
    """操作を記録する（ベストエフォート）.

    新操作記録時: その週の自分の undone=True 行（redo 枝）を削除する（Excel 同等）。
    記録失敗は logger.warning のみ — 本体処理を失敗させない。
    """
    try:
        effective_group_id = op_group_id or uuid.uuid4()

        # Excel 同等: 新操作記録で redo 枝をクリア
        await db.execute(
            delete(ScheduleOpLog).where(
                ScheduleOpLog.user_id == user_id,
                ScheduleOpLog.iso_year == iso_year,
                ScheduleOpLog.iso_week == iso_week,
                ScheduleOpLog.undone.is_(True),
            )
        )

        row = ScheduleOpLog(
            user_id=user_id,
            iso_year=iso_year,
            iso_week=iso_week,
            op_group_id=effective_group_id,
            op_kind=op_kind,
            label=label,
            forward_payload=forward_payload,
            inverse_payload=inverse_payload,
            undone=False,
        )
        db.add(row)
        await db.flush()
    except Exception as exc:
        logger.warning(
            "op_log record_op failed (best-effort, ignoring): user_id=%s op_kind=%s error=%r",
            user_id,
            op_kind,
            exc,
        )


async def get_state(
    db: AsyncSession,
    *,
    user_id: UUID,
    iso_year: int,
    iso_week: int,
) -> dict[str, Any]:
    """自分のその週のスタック状態を返す（D-4: 自分の操作のみ）."""
    # 最新の undone=False グループ (undo 対象)
    undo_row = await db.scalar(
        select(ScheduleOpLog)
        .where(
            ScheduleOpLog.user_id == user_id,
            ScheduleOpLog.iso_year == iso_year,
            ScheduleOpLog.iso_week == iso_week,
            ScheduleOpLog.undone.is_(False),
        )
        .order_by(ScheduleOpLog.created_at.desc())
        .limit(1)
    )

    # 最古の undone=True グループ (redo 対象 = 最も最近 undo した操作の "先頭")
    redo_row = await db.scalar(
        select(ScheduleOpLog)
        .where(
            ScheduleOpLog.user_id == user_id,
            ScheduleOpLog.iso_year == iso_year,
            ScheduleOpLog.iso_week == iso_week,
            ScheduleOpLog.undone.is_(True),
        )
        .order_by(ScheduleOpLog.created_at.asc())
        .limit(1)
    )

    can_undo = undo_row is not None
    can_redo = redo_row is not None

    undo_label: str | None = None
    if undo_row is not None:
        # グループの最後の行 (created_at DESC) のラベルを使う
        latest_in_group = await db.scalar(
            select(ScheduleOpLog)
            .where(
                ScheduleOpLog.op_group_id == undo_row.op_group_id,
                ScheduleOpLog.undone.is_(False),
            )
            .order_by(ScheduleOpLog.created_at.desc())
            .limit(1)
        )
        undo_label = latest_in_group.label if latest_in_group else undo_row.label

    redo_label: str | None = None
    if redo_row is not None:
        # グループの最初の行 (created_at ASC) のラベルを使う
        first_in_group = await db.scalar(
            select(ScheduleOpLog)
            .where(
                ScheduleOpLog.op_group_id == redo_row.op_group_id,
                ScheduleOpLog.undone.is_(True),
            )
            .order_by(ScheduleOpLog.created_at.asc())
            .limit(1)
        )
        redo_label = first_in_group.label if first_in_group else redo_row.label

    return {
        "can_undo": can_undo,
        "can_redo": can_redo,
        "undo_label": undo_label,
        "redo_label": redo_label,
    }


async def execute_undo(
    db: AsyncSession,
    *,
    user_id: UUID,
    iso_year: int,
    iso_week: int,
) -> None:
    """最新の undone=False グループを inverse 実行する（グループ内は created_at DESC 順）.

    Raises:
        OpLogConflictError: 現在状態と forward 結果が不一致（他者変更）
        ValueError: undo 対象が存在しない
    """
    # 最新の undone=False 行を取得してグループ特定
    latest_row = await db.scalar(
        select(ScheduleOpLog)
        .where(
            ScheduleOpLog.user_id == user_id,
            ScheduleOpLog.iso_year == iso_year,
            ScheduleOpLog.iso_week == iso_week,
            ScheduleOpLog.undone.is_(False),
        )
        .order_by(ScheduleOpLog.created_at.desc())
        .limit(1)
    )
    if latest_row is None:
        raise ValueError("undo 対象がありません")

    group_id = latest_row.op_group_id

    # グループ全行を created_at DESC 順（逆順）で取得
    # (レビュー指摘: 防御として user_id でも絞る — 他人のグループを巻き込まない)
    rows_result = await db.scalars(
        select(ScheduleOpLog)
        .where(
            ScheduleOpLog.op_group_id == group_id,
            ScheduleOpLog.user_id == user_id,
            ScheduleOpLog.undone.is_(False),
        )
        .order_by(ScheduleOpLog.created_at.desc())
    )
    rows = list(rows_result.all())

    for row in rows:
        await _verify_forward_state(db, row)
        await _execute_payload(db, row.inverse_payload)

    for row in rows:
        row.undone = True
    await db.flush()


async def execute_redo(
    db: AsyncSession,
    *,
    user_id: UUID,
    iso_year: int,
    iso_week: int,
) -> None:
    """最古の undone=True グループを forward 実行する（グループ内は created_at ASC 順）.

    Raises:
        OpLogConflictError: 現在状態と inverse 結果が不一致（他者変更）
        ValueError: redo 対象が存在しない
    """
    # 最古の undone=True 行を取得してグループ特定
    oldest_row = await db.scalar(
        select(ScheduleOpLog)
        .where(
            ScheduleOpLog.user_id == user_id,
            ScheduleOpLog.iso_year == iso_year,
            ScheduleOpLog.iso_week == iso_week,
            ScheduleOpLog.undone.is_(True),
        )
        .order_by(ScheduleOpLog.created_at.asc())
        .limit(1)
    )
    if oldest_row is None:
        raise ValueError("redo 対象がありません")

    group_id = oldest_row.op_group_id

    # グループ全行を created_at ASC 順（正順）で取得
    # (レビュー指摘: 防御として user_id でも絞る)
    rows_result = await db.scalars(
        select(ScheduleOpLog)
        .where(
            ScheduleOpLog.op_group_id == group_id,
            ScheduleOpLog.user_id == user_id,
            ScheduleOpLog.undone.is_(True),
        )
        .order_by(ScheduleOpLog.created_at.asc())
    )
    rows = list(rows_result.all())

    for row in rows:
        await _verify_inverse_state(db, row)
        await _execute_payload(db, row.forward_payload)

    for row in rows:
        row.undone = False
    await db.flush()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _verify_forward_state(db: AsyncSession, row: ScheduleOpLog) -> None:
    """undo 前検証: forward_payload が生み出した状態が現在も保たれているか確認.

    不一致（他者変更）なら OpLogConflictError を raise する。
    """
    fp = row.forward_payload
    op_name = fp.get("op", "")

    if op_name == "restore_visits":
        # forward result = visits are active
        visit_ids = [UUID(v) for v in fp.get("visit_ids", [])]
        await _assert_visits_active(db, visit_ids)

    elif op_name == "soft_delete_visits":
        # forward result = visits are soft-deleted
        visit_ids = [UUID(v) for v in fp.get("visit_ids", [])]
        await _assert_visits_deleted(db, visit_ids)

    elif op_name == "set_course_staff":
        # forward result = course.assigned_staff_id == fp["staff_id"]
        course_id = UUID(fp["course_id"])
        expected_staff_id_str: str | None = fp.get("staff_id")
        expected: UUID | None = UUID(expected_staff_id_str) if expected_staff_id_str else None
        await _assert_course_staff(db, course_id, expected)

    elif op_name == "move_visit_week_only":
        # forward result = visit is at to_weekday/to_start
        patient_id = UUID(fp["patient_id"])
        iso_year = int(fp["iso_year"])
        iso_week = int(fp["iso_week"])
        to_weekday = int(fp["to_weekday"])
        to_start = _parse_time(fp["to_start"])
        await _assert_visit_at_position(db, patient_id, iso_year, iso_week, to_weekday, to_start)

    elif op_name == "set_visit_staff":
        # forward result = visit.primary_staff_id == fp["staff_id"]
        await _assert_visit_staff(
            db,
            UUID(fp["visit_id"]),
            UUID(fp["staff_id"]) if fp.get("staff_id") else None,
        )

    # 他 op_name は検証なしで通過（将来拡張用）


async def _verify_inverse_state(db: AsyncSession, row: ScheduleOpLog) -> None:
    """redo 前検証: inverse_payload が生み出した状態が現在も保たれているか確認.

    不一致（他者変更）なら OpLogConflictError を raise する。
    """
    ip = row.inverse_payload
    op_name = ip.get("op", "")

    if op_name == "restore_visits":
        # inverse result = visits are active
        visit_ids = [UUID(v) for v in ip.get("visit_ids", [])]
        await _assert_visits_active(db, visit_ids)

    elif op_name == "soft_delete_visits":
        # inverse result = visits are soft-deleted
        visit_ids = [UUID(v) for v in ip.get("visit_ids", [])]
        await _assert_visits_deleted(db, visit_ids)

    elif op_name == "set_course_staff":
        # inverse result = course.assigned_staff_id == ip["staff_id"]
        course_id = UUID(ip["course_id"])
        expected_staff_id_str: str | None = ip.get("staff_id")
        expected: UUID | None = UUID(expected_staff_id_str) if expected_staff_id_str else None
        await _assert_course_staff(db, course_id, expected)

    elif op_name == "move_visit_week_only":
        # inverse result = visit is at to_weekday/to_start (of the inverse = original position)
        patient_id = UUID(ip["patient_id"])
        iso_year = int(ip["iso_year"])
        iso_week = int(ip["iso_week"])
        to_weekday = int(ip["to_weekday"])
        to_start = _parse_time(ip["to_start"])
        await _assert_visit_at_position(db, patient_id, iso_year, iso_week, to_weekday, to_start)

    elif op_name == "set_visit_staff":
        # inverse result = visit.primary_staff_id == ip["staff_id"]
        await _assert_visit_staff(
            db,
            UUID(ip["visit_id"]),
            UUID(ip["staff_id"]) if ip.get("staff_id") else None,
        )


async def _execute_payload(db: AsyncSession, payload: dict[str, Any]) -> None:
    """payload の op に応じて DB 更新を実行する."""
    op_name = payload.get("op", "")

    if op_name == "restore_visits":
        visit_ids = [UUID(v) for v in payload.get("visit_ids", [])]
        await _restore_visits(db, visit_ids)

    elif op_name == "soft_delete_visits":
        visit_ids = [UUID(v) for v in payload.get("visit_ids", [])]
        await _soft_delete_visits(db, visit_ids)

    elif op_name == "set_course_staff":
        course_id = UUID(payload["course_id"])
        staff_id_str: str | None = payload.get("staff_id")
        staff_id: UUID | None = UUID(staff_id_str) if staff_id_str else None
        await _set_course_staff(db, course_id, staff_id)

    elif op_name == "move_visit_week_only":
        patient_id = UUID(payload["patient_id"])
        iso_year = int(payload["iso_year"])
        iso_week = int(payload["iso_week"])
        from_weekday = int(payload["from_weekday"])
        from_start = _parse_time(payload["from_start"])
        to_weekday = int(payload["to_weekday"])
        to_start = _parse_time(payload["to_start"])
        await _move_visits(
            db, patient_id, iso_year, iso_week, from_weekday, from_start, to_weekday, to_start
        )

    elif op_name == "set_visit_staff":
        await _set_visit_staff(
            db,
            UUID(payload["visit_id"]),
            UUID(payload["staff_id"]) if payload.get("staff_id") else None,
            bool(payload.get("manual", False)),
        )

    else:
        logger.warning("op_log_service: 未知の op_name=%r — スキップ", op_name)


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------


async def _assert_visits_active(db: AsyncSession, visit_ids: list[UUID]) -> None:
    if not visit_ids:
        return
    deleted = await db.scalars(
        select(Visit.id).where(
            Visit.id.in_(visit_ids),
            Visit.deleted_at.is_not(None),
        )
    )
    gone = list(deleted.all())
    if gone:
        raise OpLogConflictError(
            f"他の変更があったため戻せません (visit {gone[0]} が既に削除されています)"
        )


async def _assert_visits_deleted(db: AsyncSession, visit_ids: list[UUID]) -> None:
    if not visit_ids:
        return
    active = await db.scalars(
        select(Visit.id).where(
            Visit.id.in_(visit_ids),
            Visit.deleted_at.is_(None),
        )
    )
    still_active = list(active.all())
    if still_active:
        raise OpLogConflictError(
            f"他の変更があったため戻せません (visit {still_active[0]} が既にアクティブです)"
        )


async def _assert_visit_staff(db: AsyncSession, visit_id: UUID, expected: UUID | None) -> None:
    """set_visit_staff の undo/redo 前検証: 訪問の現担当が期待値のままか."""
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象の訪問が見つかりません)")
    if visit.primary_staff_id != expected:
        raise OpLogConflictError(
            "他の変更があったため戻せません (訪問の担当が既に変更されています)"
        )


async def _set_visit_staff(
    db: AsyncSession, visit_id: UUID, staff_id: UUID | None, manual: bool
) -> None:
    """visit 1 件の担当を (staff_id, manual) に設定する (週空間 A2).

    正典の書込 3 点セット (endpoint 側と同一):
      primary_staff_id + manual_staff_override + visit_staff_assignments 置換。
    """
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象の訪問が見つかりません)")
    visit.primary_staff_id = staff_id
    visit.manual_staff_override = manual
    # VSA 置換は ORM 経由 + 先 flush: bulk delete は同一セッションに残る旧行と
    # 同一 PK の再 INSERT が flush 順序 (INSERT→DELETE) で相殺される罠がある。
    existing = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_id)
        )
    ).all()
    for row in existing:
        await db.delete(row)
    await db.flush()
    if staff_id is not None:
        db.add(VisitStaffAssignment(visit_id=visit_id, staff_id=staff_id))
    await db.flush()


async def _assert_course_staff(db: AsyncSession, course_id: UUID, expected: UUID | None) -> None:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise OpLogConflictError(
            f"他の変更があったため戻せません (course {course_id} が見つかりません)"
        )
    if course.assigned_staff_id != expected:
        raise OpLogConflictError(
            "他の変更があったため戻せません (course の担当者が変更されています)"
        )


async def _assert_visit_at_position(
    db: AsyncSession,
    patient_id: UUID,
    iso_year: int,
    iso_week: int,
    weekday: int,
    start_time: time,
) -> None:
    visit_date = date.fromisocalendar(iso_year, iso_week, weekday + 1)
    v = await db.scalar(
        select(Visit.id).where(
            Visit.patient_id == patient_id,
            Visit.visit_date == visit_date,
            Visit.start_time == start_time,
            Visit.deleted_at.is_(None),
            Visit.status == "planned",
        )
    )
    if v is None:
        raise OpLogConflictError("他の変更があったため戻せません (移動先の visit が見つかりません)")


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------


async def _restore_visits(db: AsyncSession, visit_ids: list[UUID]) -> None:
    """visit を復元（soft-delete 解除）."""
    if not visit_ids:
        return
    rows = await db.scalars(select(Visit).where(Visit.id.in_(visit_ids)))
    for v in rows.all():
        v.deleted_at = None
    await db.flush()


async def _soft_delete_visits(db: AsyncSession, visit_ids: list[UUID]) -> None:
    """visit を soft-delete."""
    if not visit_ids:
        return
    rows = await db.scalars(select(Visit).where(Visit.id.in_(visit_ids)))
    now = func.now()
    for v in rows.all():
        v.deleted_at = now
    await db.flush()


async def _set_course_staff(db: AsyncSession, course_id: UUID, staff_id: UUID | None) -> None:
    """course.assigned_staff_id を更新."""
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        logger.warning("op_log_service: _set_course_staff: course %s が見つかりません", course_id)
        return
    course.assigned_staff_id = staff_id
    await db.flush()


async def _move_visits(
    db: AsyncSession,
    patient_id: UUID,
    iso_year: int,
    iso_week: int,
    from_weekday: int,
    from_start: time,
    to_weekday: int,
    to_start: time,
) -> None:
    """visit の位置を (from_weekday, from_start) → (to_weekday, to_start) へ移動.

    source='manual_week' を刻む（週生成・固定枠戻で保護される値）。
    """
    from_date = date.fromisocalendar(iso_year, iso_week, from_weekday + 1)
    to_date = date.fromisocalendar(iso_year, iso_week, to_weekday + 1)

    visits = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == patient_id,
                    Visit.visit_date == from_date,
                    Visit.start_time == from_start,
                    Visit.deleted_at.is_(None),
                    Visit.status == "planned",
                )
            )
        ).all()
    )

    for v in visits:
        dur_min = (v.end_time.hour * 60 + v.end_time.minute) - (
            v.start_time.hour * 60 + v.start_time.minute
        )
        if dur_min <= 0:
            dur_min = 30
        end_minutes = to_start.hour * 60 + to_start.minute + dur_min
        v.visit_date = to_date
        v.start_time = to_start
        v.end_time = time(min(end_minutes // 60, 23), end_minutes % 60)
        v.source = VISIT_SOURCE_MANUAL_WEEK
    await db.flush()


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------


def fmt_weekday(wd: int) -> str:
    """曜日番号（0=Mon..6=Sun）→ 日本語短縮名。"""
    return _WEEKDAY_JP[wd] if 0 <= wd <= 6 else str(wd)


def fmt_time(t: time) -> str:
    """time → HH:MM 文字列。"""
    return t.strftime("%H:%M")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _parse_time(s: str) -> time:
    """HH:MM 文字列 → time。"""
    h, m = s.split(":")
    return time(int(h), int(m))
