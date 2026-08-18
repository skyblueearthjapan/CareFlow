"""休み申請・出勤カレンダー関連のスタッフ向け通知 producer.

正典 = ``docs/plans/staff-shift-confirmation-design.md`` §2-b。

宛先はスタッフ本人のユーザーアカウント (``User.staff_id`` — 生存ユーザーでは
1 staff = 1 user が部分 UNIQUE で保証される)。アカウント未紐付けの staff は
no-op (既存 producer の流儀)。**いずれも commit しない** — 呼び出し側の
トランザクション境界に乗せる。

冪等性の方針:
  * 却下通知 = ``reference=(pending_request, request.id)`` で 1 申請 1 通。
    reject 自体が 1 度しか成功しない (409 ガード) ため実質的な保険。
  * 取消/確定通知 = **毎回通知** (dedup なし・reference なし)。
    「削除→再作成→再削除」「再確定 = 再周知」で 2 通目が必要なため。
    reference_id=None を dedup クエリに通すと IS NULL 側へ展開されて以後
    永久に沈黙する既知の罠 (services/checkin/notify.py:59-61) があるので、
    そもそも dedup を通さない。
"""

from __future__ import annotations

from datetime import date
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.pending_request import PendingRequest
from app.models.user import User

NOTIFY_LEAVE_REJECTED: Final = "leave_rejected"
NOTIFY_LEAVE_CANCELLED: Final = "leave_cancelled"
NOTIFY_SHIFT_CONFIRMED: Final = "shift_confirmed"

_REFERENCE_PENDING_REQUEST: Final = "pending_request"

_WEEKDAYS_JP: Final = ("月", "火", "水", "木", "金", "土", "日")


def _fmt_date_jp(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日（{_WEEKDAYS_JP[d.weekday()]}）"


async def _alive_user(db: AsyncSession, user_id: UUID | None) -> User | None:
    if user_id is None:
        return None
    return await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))


async def _staff_user(db: AsyncSession, staff_id: UUID | None) -> User | None:
    """staff に紐付く生存ユーザー (未紐付けなら None = 通知 no-op)."""
    if staff_id is None:
        return None
    return await db.scalar(select(User).where(User.staff_id == staff_id, User.deleted_at.is_(None)))


async def notify_leave_rejected(db: AsyncSession, *, request: PendingRequest) -> int:
    """休み申請 (staff_off) の却下を申請者本人へ通知する。

    宛先は ``requester_user_id`` (NOT NULL・SET NULL されない最安全経路)。
    staff_off 以外の request_type は対象外 (呼び出し側で無条件に呼んでよい)。
    """
    if request.request_type != "staff_off":
        return 0
    user = await _alive_user(db, request.requester_user_id)
    if user is None:
        return 0

    # 冪等: 同一申請への却下通知は 1 ユーザー 1 行
    exists = await db.scalar(
        select(Notification.id)
        .where(
            Notification.user_id == user.id,
            Notification.reference_type == _REFERENCE_PENDING_REQUEST,
            Notification.reference_id == request.id,
        )
        .limit(1)
    )
    if exists is not None:
        return 0

    when = _fmt_date_jp(request.target_date) if request.target_date else "申請日"
    reason = request.rejection_reason or "(理由の記載なし)"
    db.add(
        Notification(
            user_id=user.id,
            type=NOTIFY_LEAVE_REJECTED,
            title="休み申請が却下されました",
            body=f"{when} の休み申請は承認されませんでした。\n理由: {reason}",
            reference_type=_REFERENCE_PENDING_REQUEST,
            reference_id=request.id,
        )
    )
    return 1


async def notify_leave_cancelled(
    db: AsyncSession,
    *,
    staff_id: UUID,
    target: date,
    type_label: str,
) -> int:
    """登録済みの休み (staff_weekly_overrides) の取消をスタッフ本人へ通知する。

    毎回通知 (dedup なし)。type_label は日本語ラベル ('休み'/'時間変更' 等)。
    """
    user = await _staff_user(db, staff_id)
    if user is None:
        return 0
    db.add(
        Notification(
            user_id=user.id,
            type=NOTIFY_LEAVE_CANCELLED,
            title=f"登録済みの「{type_label}」が取り消されました",
            body=(
                f"{_fmt_date_jp(target)} の「{type_label}」が管理者により取り消されました。\n"
                "内容にご不明な点があれば管理者へご確認ください。"
            ),
        )
    )
    return 1


async def notify_shift_confirmed(db: AsyncSession, *, staff_id: UUID, month: date) -> int:
    """月次出勤カレンダーの確定をスタッフ本人へ通知する (再確定 = 再通知)。"""
    user = await _staff_user(db, staff_id)
    if user is None:
        return 0
    db.add(
        Notification(
            user_id=user.id,
            type=NOTIFY_SHIFT_CONFIRMED,
            title=f"{month.year}年{month.month}月の出勤カレンダーが確定しました",
            body=(
                f"{month.year}年{month.month}月の出勤日・お休みが確定しました。\n"
                "アプリの「出勤カレンダー」からご確認ください。"
            ),
        )
    )
    return 1
