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
    "move_course_weekday"  — course の weekday と配下 planned visits の visit_date を
                              to_weekday へ移動 (週空間 A2後段: コース丸ごと曜日移動)
    "cancel_visit"         — visit_ids の status を cancelled / planned へ切り替える
                              (週空間 Phase E: 今週だけ取消。inverse は逆フラグ)
    "set_visit_service_override"
                           — visit 1 件の kaipoke_service_override を設定 / 解除する
                              (カイポケのサービス内容に合わせる。inverse は旧値)
    "set_visit_staff_slot" — visit 1 件の **担当枠 1 つ分**を差し替える。
                              primary を書くかどうか (set_primary) と、
                              visit_staff_assignments の add / remove を持つので、
                              2 名体制の **相方を巻き込まずに** 1 人だけ入れ替えられる
                              (「🛌 休みにする」の付替。inverse は add/remove を反転)
    "set_staff_off"        — staff_weekly_overrides を (staff, iso週, weekday) 単位で
                              設定 / 削除する (``type`` が null = 行を削除)。
                              「🛌 休みにする」の休み本体。訪問 / コースの付替
                              (set_visit_staff / set_course_staff) と同一 op_group で
                              記録するので「戻る」1 回で休みごと元へ戻る
    "cancel_staff_event"   — staff_events の ``cancelled_at`` を掛ける / 外す
                              (``cancel``: true=取消印, false=解除)。**行は消さない**
                              (消すと固定イベントの冪等キーが空いて再展開される)。
                              「🛌 休みにする」が休みの日の固定イベント
                              (source='fixed') に自動で取消印を付けるのに使う
                              (staff-event-history-design.md §2 Phase 3)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.schedule_op_log import ScheduleOpLog
from app.models.staff import StaffEvent, StaffWeeklyOverride
from app.models.visit import (
    VISIT_SOURCE_MANUAL_CANCEL,
    VISIT_SOURCE_MANUAL_WEEK,
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment

logger = logging.getLogger(__name__)

_WEEKDAY_JP = ("月", "火", "水", "木", "金", "土", "日")

# 「当日以前」の判定は JST 基準 (サーバは UTC・日付境界がずれないように)。
_JST = ZoneInfo("Asia/Tokyo")


async def check_cancel_visit_allowed(
    db: AsyncSession, visits: list[Visit], *, cancel: bool
) -> str | None:
    """「今週だけ取消 / 取消をやめる」の共通ガード (week-cockpit-design.md §2-2).

    エンドポイント (``schedule_v2.visit_cancel_week``) と undo/redo
    (``_set_visits_cancelled``) で **同一の判定**を使うための単一ソース。
    片方にしか無いと、undo で過去日の訪問が取り消せる等の抜け道ができる。

    Returns:
        違反理由 (利用者向け日本語)。問題なければ ``None``。
        エンドポイントは 422、undo/redo は 409 (OpLogConflictError) に変換する。
    """
    if not visits:
        return None
    # 青ピン (蓋) はどちら向きでもブロックする — 「今週この位置のまま」の宣言は
    # 取消にも及ぶ (解除してから操作する)。
    if any(bool(getattr(v, "week_pinned", False)) for v in visits):
        return "今週固定（青ピン）されています。解除してから取消してください"

    if not cancel:
        if any(v.status != VISIT_STATUS_CANCELLED for v in visits):
            return "取消済み (cancelled) の訪問のみ戻せます"
        return None

    today_jst = datetime.now(UTC).astimezone(_JST).date()
    if any(v.visit_date <= today_jst for v in visits):
        return "当日以前の訪問は取消できません（明日以降の予定のみ）"
    if any(v.status != VISIT_STATUS_PLANNED for v in visits):
        return "予定 (planned) の訪問のみ取消できます（訪問中・完了・取消済は不可）"
    checked_in = await db.scalar(
        select(VisitCheckin.id).where(VisitCheckin.visit_id.in_([v.id for v in visits]))
    )
    if checked_in is not None:
        return "打刻済みの訪問は取消できません"
    return None


class OpLogConflictError(Exception):
    """undo/redo 前の現在状態チェックで他者変更を検出したとき."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Public helpers — エンドポイントから呼び出す
# ---------------------------------------------------------------------------


async def clear_redo_branch(
    db: AsyncSession, *, user_id: UUID, iso_year: int, iso_week: int
) -> None:
    """その週の自分の redo 枝 (undone=True 行) を消す (Excel 同等).

    通常は ``record_op`` が毎回呼ぶ。1 操作で複数行を記録する API
    (「🛌 休みにする」など) は **ループ前に 1 回**これを呼び、各 record_op へ
    ``clear_redo=False`` を渡す (同じ DELETE を件数分流さない)。
    """
    await db.execute(
        delete(ScheduleOpLog).where(
            ScheduleOpLog.user_id == user_id,
            ScheduleOpLog.iso_year == iso_year,
            ScheduleOpLog.iso_week == iso_week,
            ScheduleOpLog.undone.is_(True),
        )
    )


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
    strict: bool = False,
    clear_redo: bool = True,
) -> None:
    """操作を記録する（既定はベストエフォート）.

    新操作記録時: その週の自分の undone=True 行（redo 枝）を削除する（Excel 同等）。
    記録失敗は logger.warning のみ — 本体処理を失敗させない。

    Args:
        strict: True なら記録失敗を **握り潰さず** 送出する。「戻る」で元に
            戻せることが機能の前提になっている操作 (休み + 付替をまとめて書く
            ``staff-off-week`` 等) で使う: ジャーナルだけ欠けると「戻せない
            変更」が残ってしまうため、本体ごとロールバックさせる。
        clear_redo: False なら redo 枝の削除を行わない (呼び出し側が
            ``clear_redo_branch`` で 1 回だけ済ませている場合)。
    """
    try:
        effective_group_id = op_group_id or uuid.uuid4()

        if clear_redo:
            await clear_redo_branch(db, user_id=user_id, iso_year=iso_year, iso_week=iso_week)

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
        if strict:
            raise
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
        .order_by(ScheduleOpLog.created_at.desc(), ScheduleOpLog.id.desc())
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
        .order_by(ScheduleOpLog.created_at.asc(), ScheduleOpLog.id.asc())
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
            .order_by(ScheduleOpLog.created_at.desc(), ScheduleOpLog.id.desc())
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
            .order_by(ScheduleOpLog.created_at.asc(), ScheduleOpLog.id.asc())
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
        .order_by(ScheduleOpLog.created_at.desc(), ScheduleOpLog.id.desc())
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
        .order_by(ScheduleOpLog.created_at.desc(), ScheduleOpLog.id.desc())
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
        .order_by(ScheduleOpLog.created_at.asc(), ScheduleOpLog.id.asc())
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
        .order_by(ScheduleOpLog.created_at.asc(), ScheduleOpLog.id.asc())
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

    elif op_name == "move_course_weekday":
        # forward result = course.weekday == fp["to_weekday"]
        await _assert_course_weekday(db, UUID(fp["course_id"]), int(fp["to_weekday"]))

    elif op_name == "cancel_visit":
        # forward result = 対象 visit の status が forward の結果値のまま
        await _assert_visits_status(
            db,
            [UUID(v) for v in fp.get("visit_ids", [])],
            VISIT_STATUS_CANCELLED if fp.get("cancel") else VISIT_STATUS_PLANNED,
        )

    elif op_name == "set_visit_service_override":
        # forward result = visit.kaipoke_service_override == fp["service_content"]
        await _assert_visit_service_override(db, UUID(fp["visit_id"]), fp.get("service_content"))

    elif op_name == "set_visit_staff_slot":
        # forward result = primary (書いたなら) と VSA の add/remove が反映済み
        await _assert_visit_staff_slot(db, fp)

    elif op_name == "set_staff_off":
        # forward result = 休みの行が fp["type"] のまま存在する (null なら不在)
        await _assert_staff_override(db, fp, fp.get("type"))

    elif op_name == "cancel_staff_event":
        # forward result = 対象イベントの取消印が forward の結果値のまま
        await _assert_staff_events_cancelled(
            db,
            [UUID(v) for v in fp.get("event_ids", [])],
            bool(fp.get("cancel", False)),
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

    elif op_name == "move_course_weekday":
        # inverse result = course.weekday == ip["to_weekday"] (= 元の曜日)
        await _assert_course_weekday(db, UUID(ip["course_id"]), int(ip["to_weekday"]))

    elif op_name == "cancel_visit":
        # inverse result = 対象 visit の status が inverse の結果値のまま
        await _assert_visits_status(
            db,
            [UUID(v) for v in ip.get("visit_ids", [])],
            VISIT_STATUS_CANCELLED if ip.get("cancel") else VISIT_STATUS_PLANNED,
        )

    elif op_name == "set_visit_service_override":
        # inverse result = visit.kaipoke_service_override == ip["service_content"]
        await _assert_visit_service_override(db, UUID(ip["visit_id"]), ip.get("service_content"))

    elif op_name == "set_visit_staff_slot":
        # inverse result = primary (書いたなら) と VSA の add/remove が反映済み
        await _assert_visit_staff_slot(db, ip)

    elif op_name == "set_staff_off":
        # inverse result = 休みの行が ip["type"] のまま (null なら不在)
        await _assert_staff_override(db, ip, ip.get("type"))

    elif op_name == "cancel_staff_event":
        # inverse result = 対象イベントの取消印が inverse の結果値のまま
        await _assert_staff_events_cancelled(
            db,
            [UUID(v) for v in ip.get("event_ids", [])],
            bool(ip.get("cancel", False)),
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

    elif op_name == "move_course_weekday":
        await _move_course_weekday(
            db,
            UUID(payload["course_id"]),
            int(payload["iso_year"]),
            int(payload["iso_week"]),
            int(payload["to_weekday"]),
        )

    elif op_name == "cancel_visit":
        await _set_visits_cancelled(
            db,
            [UUID(v) for v in payload.get("visit_ids", [])],
            bool(payload.get("cancel", False)),
            sources=payload.get("sources") or {},
        )

    elif op_name == "set_visit_service_override":
        await _set_visit_service_override(
            db, UUID(payload["visit_id"]), payload.get("service_content")
        )

    elif op_name == "set_visit_staff_slot":
        await _set_visit_staff_slot(db, payload)

    elif op_name == "set_staff_off":
        await _set_staff_override(db, payload)

    elif op_name == "cancel_staff_event":
        await set_staff_events_cancelled(
            db,
            [UUID(v) for v in payload.get("event_ids", [])],
            bool(payload.get("cancel", False)),
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


async def _assert_visits_status(db: AsyncSession, visit_ids: list[UUID], expected: str) -> None:
    """cancel_visit の undo/redo 前検証: 対象 visit の status が期待値のままか."""
    if not visit_ids:
        return
    rows = await db.scalars(
        select(Visit).where(Visit.id.in_(visit_ids), Visit.deleted_at.is_(None))
    )
    found = list(rows.all())
    if len(found) != len(set(visit_ids)):
        raise OpLogConflictError("他の変更があったため戻せません (対象の訪問が見つかりません)")
    for v in found:
        if v.status != expected:
            raise OpLogConflictError(
                "他の変更があったため戻せません (訪問の状態が既に変更されています)"
            )


async def _set_visits_cancelled(
    db: AsyncSession,
    visit_ids: list[UUID],
    cancel: bool,
    *,
    sources: dict[str, str] | None = None,
) -> None:
    """visit_ids の status を cancelled / planned へ切り替える (週空間 Phase E).

    endpoint 側 (schedule_v2.visit_cancel_week) と同一の書込。取消の表現は
    取込の delete と同じ ``status='cancelled'`` (履歴は残り csv_builder が除外する)
    で、加えて出所に ``manual_cancel`` を刻む (取込 delete 由来の cancelled と
    区別するため — 取込の add はそちらだけ復活させてよい)。

    Args:
        sources: 取消前の出所 ``{visit_id: source}``。戻す (cancel=False) ときに
            使う。無ければ ``manual_week`` (週生成・固定枠戻しから保護される値)。
    """
    if not visit_ids:
        return
    rows = list(
        (
            await db.scalars(
                select(Visit).where(Visit.id.in_(visit_ids), Visit.deleted_at.is_(None))
            )
        ).all()
    )
    # エンドポイントと同一のガード (単一ソース)。undo/redo の時点で条件が
    # 変わっている (日が過ぎた・打刻が付いた・青ピンが刺さった) なら 409。
    reason = await check_cancel_visit_allowed(db, rows, cancel=cancel)
    if reason is not None:
        raise OpLogConflictError(f"戻せません: {reason}")
    target = VISIT_STATUS_CANCELLED if cancel else VISIT_STATUS_PLANNED
    _sources = sources or {}
    for v in rows:
        v.status = target
        v.source = (
            VISIT_SOURCE_MANUAL_CANCEL
            if cancel
            else (_sources.get(str(v.id)) or VISIT_SOURCE_MANUAL_WEEK)
        )
    await db.flush()


async def _assert_visit_service_override(
    db: AsyncSession, visit_id: UUID, expected: str | None
) -> None:
    """set_visit_service_override の undo/redo 前検証: 上書き値が期待値のままか."""
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象の訪問が見つかりません)")
    if (visit.kaipoke_service_override or None) != (expected or None):
        raise OpLogConflictError(
            "他の変更があったため戻せません (訪問のサービス内容が既に変更されています)"
        )


async def _set_visit_service_override(
    db: AsyncSession, visit_id: UUID, service_content: str | None
) -> None:
    """visit 1 件の ``kaipoke_service_override`` を設定 / 解除する。

    endpoint 側 (``schedule_v2.visit_service_override``) と同一の書込。位置
    (日付/時刻/担当) は触らないため、取消のような状態ガードは持たない
    (完了済み訪問のサービス内容を直せることが機能の目的そのもの)。
    """
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象の訪問が見つかりません)")
    visit.kaipoke_service_override = (service_content or "").strip() or None
    await db.flush()


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


async def visit_staff_ids(db: AsyncSession, visit_id: UUID) -> set[UUID]:
    """その訪問に紐づくスタッフ id 集合 (visit_staff_assignments)."""
    rows = await db.scalars(
        select(VisitStaffAssignment.staff_id).where(VisitStaffAssignment.visit_id == visit_id)
    )
    return set(rows.all())


async def _assert_visit_staff_slot(db: AsyncSession, payload: dict[str, Any]) -> None:
    """set_visit_staff_slot の undo/redo 前検証.

    「この payload を実行した直後の状態」がいま保たれているかを見る:
    primary を書く payload なら primary が一致し、``vsa_add`` が全員居て
    ``vsa_remove`` が誰も居ないこと。相方 (この操作が触っていない VSA 行) は
    検証対象にしない = 相方だけ他の人が入れ替えても衝突にしない。
    """
    visit_id = UUID(payload["visit_id"])
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象の訪問が見つかりません)")
    if payload.get("set_primary"):
        raw = payload.get("primary_staff_id")
        expected: UUID | None = UUID(raw) if raw else None
        if visit.primary_staff_id != expected:
            raise OpLogConflictError(
                "他の変更があったため戻せません (訪問の担当が既に変更されています)"
            )
    current = await visit_staff_ids(db, visit_id)
    for raw_id in payload.get("vsa_add") or []:
        if UUID(raw_id) not in current:
            raise OpLogConflictError(
                "他の変更があったため戻せません (訪問の担当が既に変更されています)"
            )
    for raw_id in payload.get("vsa_remove") or []:
        if UUID(raw_id) in current:
            raise OpLogConflictError(
                "他の変更があったため戻せません (訪問の担当が既に変更されています)"
            )


async def set_visit_staff_slot(
    db: AsyncSession,
    *,
    visit_id: UUID,
    set_primary: bool,
    primary_staff_id: UUID | None,
    manual: bool,
    vsa_add: list[UUID],
    vsa_remove: list[UUID],
) -> None:
    """訪問 1 件の **担当枠 1 つ分**を差し替える (エンドポイントと undo の単一ソース).

    ``visit-assign-staff-week`` の「VSA 全消し + 1 人だけ入れ直し」は 2 名体制の
    **相方まで消してしまう**ため、こちらは *抜ける人の行だけ* を外し、引き受け先の
    行だけを足す。``set_primary=False`` なら primary は触らない
    (= 抜ける人が 2 人目としてだけ入っている訪問)。

    Raises:
        OpLogConflictError: 対象の訪問が見つからない (undo/redo 経路の楽観ロック)。
    """
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象の訪問が見つかりません)")
    if set_primary:
        visit.primary_staff_id = primary_staff_id
        visit.manual_staff_override = manual
    if vsa_remove:
        # ORM 経由 + 先 flush: bulk delete は同一セッションに残る旧行と同一 PK の
        # 再 INSERT が flush 順序 (INSERT→DELETE) で相殺される罠がある。
        rows = (
            await db.scalars(
                select(VisitStaffAssignment).where(
                    VisitStaffAssignment.visit_id == visit_id,
                    VisitStaffAssignment.staff_id.in_(vsa_remove),
                )
            )
        ).all()
        for row in rows:
            await db.delete(row)
        await db.flush()
    if vsa_add:
        current = await visit_staff_ids(db, visit_id)
        for staff_id in vsa_add:
            # (visit_id, staff_id) は複合 PK。既に居るなら足さない (二重登録は 500)。
            if staff_id not in current:
                db.add(VisitStaffAssignment(visit_id=visit_id, staff_id=staff_id))
    await db.flush()


async def _set_visit_staff_slot(db: AsyncSession, payload: dict[str, Any]) -> None:
    """``set_visit_staff_slot`` の payload を実行する (undo/redo 経路)."""
    raw_primary = payload.get("primary_staff_id")
    await set_visit_staff_slot(
        db,
        visit_id=UUID(payload["visit_id"]),
        set_primary=bool(payload.get("set_primary")),
        primary_staff_id=UUID(raw_primary) if raw_primary else None,
        manual=bool(payload.get("manual", False)),
        vsa_add=[UUID(v) for v in payload.get("vsa_add") or []],
        vsa_remove=[UUID(v) for v in payload.get("vsa_remove") or []],
    )


async def _load_staff_override(
    db: AsyncSession, staff_id: UUID, iso_year: int, iso_week: int, weekday: int
) -> StaffWeeklyOverride | None:
    """(staff, iso 週, weekday) の休み / 時間変更の行を 1 件引く (無ければ None)."""
    return await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id == staff_id,
            StaffWeeklyOverride.iso_year == iso_year,
            StaffWeeklyOverride.iso_week == iso_week,
            StaffWeeklyOverride.weekday == weekday,
        )
    )


async def _assert_staff_override(
    db: AsyncSession, payload: dict[str, Any], expected_type: str | None
) -> None:
    """set_staff_off の undo/redo 前検証: 休みの行が期待どおりか.

    ``expected_type`` が None なら「行が無いこと」を、そうでなければ
    「その ``override_type`` の行があること」を要求する。
    """
    row = await _load_staff_override(
        db,
        UUID(payload["staff_id"]),
        int(payload["iso_year"]),
        int(payload["iso_week"]),
        int(payload["weekday"]),
    )
    if expected_type is None:
        if row is not None:
            raise OpLogConflictError(
                "他の変更があったため戻せません (この日の休みが既に登録されています)"
            )
        return
    if row is None:
        raise OpLogConflictError("他の変更があったため戻せません (この日の休みが見つかりません)")
    if row.override_type != expected_type:
        raise OpLogConflictError(
            "他の変更があったため戻せません (この日の休みが既に変更されています)"
        )


async def set_staff_override(
    db: AsyncSession,
    *,
    staff_id: UUID,
    iso_year: int,
    iso_week: int,
    weekday: int,
    override_type: str | None,
    override_id: UUID | None = None,
    reason: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> UUID | None:
    """休み / 時間変更の行を upsert / 削除する (エンドポイントと undo の単一ソース).

    ``staff_weekly_overrides`` は (staff, iso 年, iso 週, weekday) で一意なので、
    既存行があれば **流用** する (同じ日を 2 回「休みにする」しても 1 件のまま)。

    Args:
        override_type: ``'off'`` / ``'custom_time'``。``None`` = 行を削除する
            (= 「休みにする」の undo)。
        override_id: 新規作成するときの id。省略時は採番。

    Returns:
        書いた行の id。削除したときは ``None``。
    """
    existing = await _load_staff_override(db, staff_id, iso_year, iso_week, weekday)
    if override_type is None:
        if existing is not None:
            await db.delete(existing)
            await db.flush()
        return None

    if existing is not None:
        existing.override_type = override_type
        existing.reason = reason
        existing.start_time = _parse_time(start_time) if start_time else None
        existing.end_time = _parse_time(end_time) if end_time else None
        await db.flush()
        return existing.id

    row = StaffWeeklyOverride(
        staff_id=staff_id,
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        override_type=override_type,
        start_time=_parse_time(start_time) if start_time else None,
        end_time=_parse_time(end_time) if end_time else None,
        reason=reason,
    )
    if override_id is not None:
        row.id = override_id
    db.add(row)
    await db.flush()
    return row.id


async def _set_staff_override(db: AsyncSession, payload: dict[str, Any]) -> None:
    """``set_staff_off`` の payload を実行する (undo/redo 経路)."""
    raw_id = payload.get("override_id")
    await set_staff_override(
        db,
        staff_id=UUID(payload["staff_id"]),
        iso_year=int(payload["iso_year"]),
        iso_week=int(payload["iso_week"]),
        weekday=int(payload["weekday"]),
        override_type=payload.get("type"),
        override_id=UUID(raw_id) if raw_id else None,
        reason=payload.get("reason"),
        start_time=payload.get("start_time"),
        end_time=payload.get("end_time"),
    )


async def _assert_staff_events_cancelled(
    db: AsyncSession, event_ids: list[UUID], expected_cancelled: bool
) -> None:
    """cancel_staff_event の undo/redo 前検証: 取消印が期待どおりか."""
    if not event_ids:
        return
    rows = list((await db.scalars(select(StaffEvent).where(StaffEvent.id.in_(event_ids)))).all())
    if len(rows) != len(set(event_ids)):
        raise OpLogConflictError("他の変更があったため戻せません (対象のイベントが見つかりません)")
    for row in rows:
        if (row.cancelled_at is not None) != expected_cancelled:
            raise OpLogConflictError(
                "他の変更があったため戻せません (イベントの取消状態が既に変更されています)"
            )


async def set_staff_events_cancelled(db: AsyncSession, event_ids: list[UUID], cancel: bool) -> None:
    """staff_events の取消印を掛ける / 外す (エンドポイントと undo の単一ソース).

    書込は ``staff_events.cancel-week`` (「今週だけ外す」) と同一 —
    ``cancelled_at`` に印を付けるだけで **行は消さない**。行を消すと
    ``expand_staff_event_defaults`` の冪等キーが空いて次の週生成で復活する。
    冪等: 既に同じ状態の行は触らない。
    """
    if not event_ids:
        return
    rows = list((await db.scalars(select(StaffEvent).where(StaffEvent.id.in_(event_ids)))).all())
    now = datetime.now(UTC)
    for row in rows:
        if cancel:
            if row.cancelled_at is None:
                row.cancelled_at = now
        else:
            row.cancelled_at = None
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


async def _assert_course_weekday(db: AsyncSession, course_id: UUID, expected: int) -> None:
    """move_course_weekday の undo/redo 前検証: コースの現曜日が期待値のままか."""
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象のコースが見つかりません)")
    if course.weekday != expected:
        raise OpLogConflictError(
            "他の変更があったため戻せません (コースの曜日が既に変更されています)"
        )


async def _move_course_weekday(
    db: AsyncSession, course_id: UUID, iso_year: int, iso_week: int, to_weekday: int
) -> None:
    """course の weekday と配下 planned visits を to_weekday へ移動 (週空間 A2後段).

    endpoint 側 (schedule_v2.course_move_weekday_week_only) と同一の書込:
    course.weekday + 配下 visits の visit_date (+source='manual_week')。PFV 不変。
    """
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise OpLogConflictError("他の変更があったため戻せません (対象のコースが見つかりません)")
    # 移動先の同 code コース衝突 (endpoint と同じガード — レビュー指摘 2026-08-21:
    # 無検査だと DB partial UNIQUE が IntegrityError=500 になる。409 の丁寧な文言へ)。
    dup = await db.scalar(
        select(Course.id).where(
            Course.iso_year == course.iso_year,
            Course.iso_week == course.iso_week,
            Course.weekday == to_weekday,
            Course.code == course.code,
            Course.office_id == course.office_id,
            Course.course_status != "proposed",
            Course.deleted_at.is_(None),
            Course.id != course.id,
        )
    )
    if dup is not None:
        raise OpLogConflictError(
            "他の変更があったため戻せません (戻り先の曜日に同じコースが既にあります)"
        )
    to_date = date.fromisocalendar(iso_year, iso_week, to_weekday + 1)
    # 取消 (cancelled) も planned と一緒に動かす = endpoint と同じ移動対象。
    # (取消枠だけ元の曜日へ取り残すと、取消をやめた瞬間に別曜日へ復活する)
    visits = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.course_id == course_id,
                    Visit.deleted_at.is_(None),
                    Visit.status.in_((VISIT_STATUS_PLANNED, VISIT_STATUS_CANCELLED)),
                )
            )
        ).all()
    )
    for v in visits:
        v.visit_date = to_date
        v.source = VISIT_SOURCE_MANUAL_WEEK
    course.weekday = to_weekday
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
