"""「カイポケへ自動送信できない予定がある」ことを admin へ知らせる producer.

正典 = ``docs/plans/kaipoke-service-content-design.md`` §3-2 / §4。

producer は 2 本。冪等・upsert の作法は共通 (``_upsert_for_admins``) で、
**reference_type だけ分ける** — 理由が違えば対処も違うため:

  * ``notify_rpa_unsupported_candidates`` — 准看/一般で RPA が登録できない。
  * ``notify_unassigned_candidates`` — 担当なし ('-') で送れない (2026-09-03)。

## なぜ要るか

``rpa_capability`` は准看/一般の新規 (とその対になる取消) を送信対象から
黙って外す。安全側の判断だが、**外したことに誰も気付かない** と該当の訪問が
いつまでもカイポケに登録されないまま残る (画面上は「送れる 0 件」で平穏に
見える)。そこで、未送信の中に自動送信できない分が現れた時点で admin の
お知らせに 1 通落とし、カイポケの画面から直接登録してもらう。

## 冪等 (週ごとに 1 通・件数は上書き)

``unsent-summary`` は同期バーを開くたびに走るため、素朴に通知すると 1 日に
何十通も積む。冪等キーは **週だけ** の uuid5:

  * 同じ週の通知は常に 1 通。件数が変わったら本文を **UPDATE** して
    ``read_at`` を null に戻す (= 未読として再浮上させる)。件数ごとに
    新しい行を作ると、古い数字の通知が受信箱に溜まって「結局いま何件？」が
    分からなくなる。最新の 1 通だけが残る形にする。
  * 件数が同じなら何もしない (既読を勝手に未読へ戻さない)。

``Notification`` の部分 UNIQUE ``(user_id, reference_type, reference_id)`` が
同時実行の二重挿入も弾く。**commit しない** — 呼び出し側のトランザクション
境界に乗せる (leave_notify と同じ作法)。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.user import User

NOTIFY_RPA_UNSUPPORTED: Final = "rpa_unsupported"

# 担当なし (職員1が空/'-') で送れない予定のお知らせ (2026-09-03 の事故)。
# 上と同じ冪等・同じ作法だが **別の reference_type** にする — 混ぜると
# 「准看で送れない」と「担当が無くて送れない」が 1 通に潰れて対処が分からない。
NOTIFY_UNASSIGNED: Final = "unassigned_unsent"

# uuid5 の名前空間 (この producer 専用の固定 UUID)。週から決定的に
# reference_id を作るために使う。値そのものに意味は無いが **変えないこと** —
# 変えると過去に送った通知と突き合わなくなり、全部もう一度届く。
_NAMESPACE: Final = uuid.UUID("6f1d2c48-6a2f-5b31-9d4a-7c0e8b5a1f30")


def dedup_reference_id(week_start: date) -> uuid.UUID:
    """週 → 冪等キー (決定的 UUID)。件数は含めない (同じ週は 1 通に保つ)。"""
    return uuid.uuid5(_NAMESPACE, f"rpa-unsupported:{week_start.isoformat()}")


def unassigned_dedup_reference_id(week_start: date) -> uuid.UUID:
    """担当なしのお知らせの冪等キー。接頭辞が違う = RPA 未対応とは別の 1 通。"""
    return uuid.uuid5(_NAMESPACE, f"unassigned-unsent:{week_start.isoformat()}")


def _title(count: int) -> str:
    return f"カイポケへ自動送信できない予定が {count} 件あります"


def _body(week_start: date, count: int) -> str:
    return (
        f"らく助からカイポケへ自動で登録できない予定が {count} 件あります"
        "（准看護師の訪問・一般の訪問看護）。\n"
        f"対象週: {week_start.year}年{week_start.month}月{week_start.day}日の週\n"
        "お手数ですが、カイポケの画面から直接ご登録ください。"
    )


def _unassigned_title(count: int) -> str:
    return f"担当なしでカイポケへ送れない予定が {count} 件あります"


def _unassigned_body(week_start: date, count: int) -> str:
    return (
        f"担当なしの予定が {count} 件あり、カイポケへ送れません"
        "（先に担当を付けてください）。\n"
        f"対象週: {week_start.year}年{week_start.month}月{week_start.day}日の週\n"
        "カイポケのスケジュール表は職員未割当の予定を持てないため、"
        "担当が決まるまで自動送信の対象から外しています。"
    )


async def _upsert_for_admins(
    db: AsyncSession,
    *,
    reference_type: str,
    reference_id: uuid.UUID,
    title: str,
    body: str,
) -> int:
    """active な admin 全員に 1 通ずつ upsert する (冪等キーは reference_id)。

    **commit しない** — 呼び出し側のトランザクション境界に乗せる。
    件数が変わった (= 本文が変わった) ときだけ書き換えて未読に戻す。
    """
    users = list(
        (
            await db.scalars(
                select(User).where(
                    # ロール二軸分離 (mig 0069) 後に 'manager' は存在しないが、
                    # 万一の残存行も受け取れるよう別名込みで引く
                    # (constraint_override_notify._active_admin_users と同作法)。
                    User.role.in_(("admin", "manager")),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
    )
    if not users:
        return 0

    existing = {
        n.user_id: n
        for n in (
            await db.scalars(
                select(Notification).where(
                    Notification.reference_type == reference_type,
                    Notification.reference_id == reference_id,
                )
            )
        ).all()
    }

    touched = 0
    for u in users:
        current = existing.get(u.id)
        if current is None:
            db.add(
                Notification(
                    user_id=u.id,
                    type=reference_type,
                    title=title,
                    body=body,
                    reference_type=reference_type,
                    reference_id=reference_id,
                )
            )
            touched += 1
            continue
        if current.title == title and current.body == body:
            # 件数が変わっていない = 何もしない。既読を勝手に未読へ戻さない。
            continue
        current.title = title
        current.body = body
        # 数字が変わったら読み直してほしいので未読に戻す。
        current.read_at = None
        touched += 1
    return touched


async def notify_rpa_unsupported_candidates(
    db: AsyncSession, *, week_start: date, count: int
) -> int:
    """自動送信できない予定 (准看/一般) があることを active な admin へ通知する。

    **commit しない**。同じ週の通知は 1 ユーザー 1 通で、件数が変わったときは
    本文を書き換えて未読に戻す。

    Returns:
        作成 **または更新** した通知の件数 (0 = 変化なし / 対象 admin なし)。
    """
    if count <= 0:
        return 0
    return await _upsert_for_admins(
        db,
        reference_type=NOTIFY_RPA_UNSUPPORTED,
        reference_id=dedup_reference_id(week_start),
        title=_title(count),
        body=_body(week_start, count),
    )


async def notify_unassigned_candidates(db: AsyncSession, *, week_start: date, count: int) -> int:
    """担当なしで送れない予定があることを active な admin へ通知する。

    ⇧上書き / 連携ページの「全件送る」は BE が担当なしを黙って外すため、
    画面上は「送れる 0 件」で平穏に見えてしまう。外したことに気付けるよう
    週ごとに 1 通落とす (RPA 未対応のお知らせと同じ冪等・別の reference_type)。

    **commit しない**。

    Returns:
        作成 **または更新** した通知の件数 (0 = 変化なし / 対象 admin なし)。
    """
    if count <= 0:
        return 0
    return await _upsert_for_admins(
        db,
        reference_type=NOTIFY_UNASSIGNED,
        reference_id=unassigned_dedup_reference_id(week_start),
        title=_unassigned_title(count),
        body=_unassigned_body(week_start, count),
    )
