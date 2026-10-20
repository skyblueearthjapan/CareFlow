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

from dataclasses import dataclass
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
NOTIFY_LEAVE_APPROVED: Final = "leave_approved"
NOTIFY_LEAVE_UNASSIGNED: Final = "leave_unassigned"
#: 職員から休みの申請が届いた (管理者向け)。
NOTIFY_LEAVE_REQUESTED: Final = "leave_requested"

#: 休みの種類 → 画面・通知の言葉 (休み設計 §2-1)。
_LEAVE_LABELS: Final = {
    "off": "休み",
    "am_off": "午前休",
    "pm_off": "午後休",
    "paid_off": "有給",
    "custom_time": "時間変更",
}

_REFERENCE_PENDING_REQUEST: Final = "pending_request"
#: 新しい休みの申請の通知の reference_type (reference_id = 申請の id)。
REFERENCE_LEAVE_REQUEST: Final = "leave_request"

_WEEKDAYS_JP: Final = ("月", "火", "水", "木", "金", "土", "日")


def leave_type_label(leave_type: str) -> str:
    """休みの種類 (``off`` など) → 「休み」「午前休」「午後休」「有給」(API の読み出しのラベルと同じ)。"""
    return _LEAVE_LABELS.get(leave_type, leave_type)


def _fmt_md(d: date) -> str:
    return f"{d.month}/{d.day}（{_WEEKDAYS_JP[d.weekday()]}）"


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
    """月の休みの確定をスタッフ本人へ通知する (再確定 = 再通知)。"""
    user = await _staff_user(db, staff_id)
    if user is None:
        return 0
    db.add(
        Notification(
            user_id=user.id,
            type=NOTIFY_SHIFT_CONFIRMED,
            title=f"{month.year}年{month.month}月の休みが確定しました",
            body=(
                f"{month.year}年{month.month}月の出勤日・お休みが確定しました。\n"
                "アプリの「休み」からご確認ください。"
            ),
            data={"staff_id": str(staff_id), "month": month.replace(day=1).isoformat()},
        )
    )
    return 1


async def notify_leave_approved(
    db: AsyncSession, *, staff_id: UUID, target: date, leave_type: str
) -> int:
    """休みの申請の承認を本人へ通知する (「M/D（曜）の午前休が承認されました」・毎回通知)."""
    user = await _staff_user(db, staff_id)
    if user is None:
        return 0
    label = leave_type_label(leave_type)
    db.add(
        Notification(
            user_id=user.id,
            type=NOTIFY_LEAVE_APPROVED,
            title=f"{_fmt_md(target)}の{label}が承認されました",
            body=f"{_fmt_date_jp(target)} の{label}が承認されました。",
            data={"staff_id": str(staff_id), "date": target.isoformat(), "leave_type": leave_type},
        )
    )
    return 1


@dataclass(frozen=True)
class UnassignedNotice:
    """休みで担当なしへ移した 1 人 1 日ぶん (管理者への通知の材料)."""

    staff_name: str
    day: date
    #: 移した訪問の患者の数 (「N 名の訪問」)。
    patient_count: int
    visit_count: int
    #: 本人が同行者に入っている休みの時間帯の訪問の数 (同行はそのまま)。
    accompaniment_count: int = 0
    #: 職員 (通知の ``data`` に入れる・ベルから表のマスを開く)。
    staff_id: UUID | None = None


def _notice_item(n: UnassignedNotice) -> dict[str, object]:
    return {
        "staff_id": str(n.staff_id) if n.staff_id is not None else None,
        "staff_name": n.staff_name,
        "date": n.day.isoformat(),
        "visit_count": n.visit_count,
    }


async def notify_leave_unassigned(
    db: AsyncSession,
    notices: list[UnassignedNotice],
    *,
    summary_prefix: str = "",
) -> int:
    """休みで訪問を担当なしへ移したことを管理者全員へ知らせる (1 通にまとめる・§3-3 の 7).

    1 件なら「スタッフの〇〇さんは M/D（曜）に N 名の訪問を予定していましたが、未割り当てと
    しました。…」、2 件以上なら「{summary_prefix}休みを N 件入れ、訪問 M 件を未割り当てと
    しました（〇〇さん 11/12 3 件・…）」。
    """
    notices = [n for n in notices if n.visit_count > 0]
    if not notices:
        return 0
    from app.services.constraint_override_notify import _active_admin_users

    tail = (
        "職員スケジュールの「（担当なし）」から割り当て直してください。"
        "割り当て直すまでカイポケへは送られません。"
        "コースの担当を替えても、担当なしの訪問は 1 件ずつ付け替えてください。"
    )
    accompaniments = sum(n.accompaniment_count for n in notices)
    if len(notices) == 1:
        n = notices[0]
        title = f"{n.staff_name}さんの{_fmt_md(n.day)}の訪問を未割り当てとしました"
        body = (
            f"スタッフの{n.staff_name}さんは{_fmt_md(n.day)}に{n.patient_count}名の訪問を"
            f"予定していましたが、未割り当てとしました。{tail}"
        )
    else:
        visits = sum(n.visit_count for n in notices)
        detail = "・".join(
            f"{n.staff_name}さん {n.day.month}/{n.day.day} {n.visit_count}件" for n in notices
        )
        title = f"休みで訪問{visits}件を未割り当てとしました"
        body = (
            f"{summary_prefix}休みを{len(notices)}件入れ、訪問{visits}件を未割り当てとしました"
            f"（{detail}）。{tail}"
        )
    if accompaniments:
        body += f"\n同行{accompaniments}件はそのままです。職員スケジュールで外してください。"
    # 画面はここから日付と職員を読む (文面から M/D を読まない)。先頭の 1 件のマスを開く。
    items = [_notice_item(n) for n in notices]
    data: dict[str, object] = {
        "staff_id": items[0]["staff_id"],
        "date": items[0]["date"],
        "items": items,
    }
    users = await _active_admin_users(db)
    for u in users:
        db.add(
            Notification(
                user_id=u.id, type=NOTIFY_LEAVE_UNASSIGNED, title=title, body=body, data=data
            )
        )
    return len(users)


async def notify_leave_requested(db: AsyncSession, *, request: PendingRequest) -> int:
    """職員の休みの申請 (staff_off の作成) を管理者全員へ知らせる (ベル・メールなし・§5-2).

    ``reference`` は申請 (``pending_request``・id)、``data`` は ``staff_id``・``date``・
    ``override_type``・``kind`` (ベルから「休み・勤務表」のそのマスを開く)。管理者が自分で
    出した申請 (即時反映など) では呼ばない。
    """
    if request.request_type != "staff_off":
        return 0
    from app.services.constraint_override_notify import _active_admin_users

    payload = dict(request.payload or {})
    raw_staff = request.target_staff_id or payload.get("staff_id")
    staff_id: UUID | None = None
    if isinstance(raw_staff, UUID):
        staff_id = raw_staff
    elif raw_staff:
        try:
            staff_id = UUID(str(raw_staff))
        except ValueError:
            staff_id = None
    target = request.target_date
    override_type = str(payload.get("override_type") or "off")
    kind = "change" if payload.get("kind") == "change" else "normal"
    staff_name = None
    if staff_id is not None:
        from app.models.staff import Staff

        staff_name = await db.scalar(select(Staff.name).where(Staff.id == staff_id))
    who = f"{staff_name}さん" if staff_name else "職員"
    if override_type == "cancel":
        what = "休みの取り消し"
    else:
        what = leave_type_label(override_type)
    when = _fmt_md(target) if target is not None else ""
    head = "変更のお願い" if kind == "change" else "休みの申請"
    title = f"{who}から{head}（{when} {what}）"
    body = (
        f"{who}から{when}の{what}の{head}が届きました。「休み・勤務表」で承認か却下をしてください。"
    )
    if payload.get("note"):
        body += f"\nメモ: {payload['note']}"
    data = {
        "staff_id": str(staff_id) if staff_id is not None else None,
        "date": target.isoformat() if target is not None else None,
        "override_type": override_type,
        "kind": kind,
    }
    users = await _active_admin_users(db)
    for u in users:
        db.add(
            Notification(
                user_id=u.id,
                type=NOTIFY_LEAVE_REQUESTED,
                title=title[:200],
                body=body,
                # 却下の通知 (pending_request) と別の種類にする: 申請者が管理者でも
                # (user, reference_type, reference_id) の部分 UNIQUE にぶつからない。
                reference_type=REFERENCE_LEAVE_REQUEST,
                reference_id=request.id,
                data=data,
            )
        )
    return len(users)
