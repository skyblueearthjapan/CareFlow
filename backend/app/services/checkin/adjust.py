"""実績の時刻を合わせる・読取時刻に戻す (検証・追記・監査).

正典設計書: ``docs/plans/actual-time-adjust-design-2026-09-30.md`` §5 / §6。

* ``visit_time_adjustments`` に 1 行 **追記** する (``visit_checkins`` には触れない)。
* **通常の訪問の予定 (``visits.start_time`` / ``end_time`` / ``visit_date``) には
  触れない**。予定外訪問 (``is_unplanned``) だけは予定欄に実績が写してあるので、
  到着を合わせたら ``start_time``、退出を合わせたら ``end_time`` も追随させる
  (``start < end`` が崩れる場合は更新しない)。
* 読み取りの無い退出を入れたら ``in_progress`` → ``completed``。それを読取時刻に
  戻したら (= 退出が無くなったら) **手で入れる前の状態へ** 戻す: 手で入れる前が
  ``in_progress`` だった訪問だけ ``in_progress`` に戻し (元から ``completed`` だった
  訪問はそのまま)、予定外訪問の ``end_time`` も手で入れる前の値に戻す。手で入れる前の
  値は調整行 (``prev_visit_status`` / ``prev_visit_end_time``) に控えてある。
* 監査は ``audit_logs`` に before / after つきで明示記録する
  (``visit_actual_time_adjust`` / ``visit_actual_time_reset``)。role / method / path /
  IP などは API 層が ``audit_meta`` で渡す (打刻履歴の Excel / A4 の監査行と同じ項目)。

可視性・権限 (誰がどの訪問を合わせられるか) の判定は呼び出し側 (API) が先に行う。
ここは ``can_adjust_actual_time`` (純関数) だけを提供する。**commit しない**。

検証に通らない場合は ``HTTPException`` (409 / 422)。``detail`` はそのまま画面に
出せる日本語。
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User, normalize_user_role
from app.models.visit import VISIT_STATUS_COMPLETED, VISIT_STATUS_IN_PROGRESS, Visit
from app.models.visit_time_adjustment import (
    ADJUST_KINDS,
    ADJUST_REASON_LABELS,
    VisitTimeAdjustment,
)
from app.services.checkin.actuals import KindActual, VisitActuals, load_actuals
from app.services.checkin.judge import load_thresholds

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

# 上限 (到着をさかのぼれる分・退出を後ろへ動かせる分・スタッフが合わせられる日数) は
# ``checkin_settings`` の設定 (mig 0089・無ければ ``judge.DEFAULT_THRESHOLDS`` の
# 90 分 / 30 分 / 7 日) を ``load_thresholds`` で読む。
#: 理由の自由記述の上限 (字)。
REASON_TEXT_MAX = 200

AUDIT_ADJUST = "visit_actual_time_adjust"
AUDIT_RESET = "visit_actual_time_reset"
AUDIT_TARGET_TABLE = "visit_time_adjustments"

DETAIL_DELETED = "削除された訪問の時刻は合わせられません"
DETAIL_NO_ARRIVAL = "到着の記録がありません"
DETAIL_BAD_TIME = "時刻は HH:MM の形で指定してください"
DETAIL_BAD_KIND = "到着・退出のどちらの時刻かを指定してください"
DETAIL_BAD_REASON = "理由の選択が正しくありません"
DETAIL_REASON_TOO_LONG = f"理由は {REASON_TEXT_MAX} 字以内で入力してください"


def out_of_window_detail(window_days: int) -> str:
    """スタッフが合わせられる期間の外の訪問に返す文言 (403)。"""
    return f"合わせられるのは {window_days} 日前までの訪問です。管理者に依頼してください"


DETAIL_FUTURE_VISIT = "まだ訪問日になっていない訪問の時刻は合わせられません"

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def can_adjust_actual_time(
    *,
    is_admin: bool,
    visit_date: date,
    deleted: bool,
    related: bool,
    today: date,
    has_arrival_read: bool,
    window_days: int,
) -> bool:
    """今のユーザーがこの訪問の実績を合わせられるか (設計 §6-1 の権限)。

    admin はすべての訪問。staff は ``visit_date`` が今日から ``window_days`` 日前まで
    (JST・``checkin_settings.staff_adjust_window_days``、既定 7) で、
    かつ自分がその訪問に関わっている (``related`` = 担当集合に入る、または自分が
    到着・退出を打刻した) 場合。削除済みの訪問は誰も合わせられない。

    ``has_arrival_read`` = 到着の読み取りがあるか。無い訪問は合わせる対象が無い
    (到着も、手で入れる退出も、到着の読み取りが前提) ので、誰に対しても False。
    """
    if deleted or not has_arrival_read:
        return False
    if is_admin:
        return True
    return related and today - timedelta(days=window_days) <= visit_date <= today


def parse_hhmm(value: str | None) -> time:
    """JST の ``HH:MM`` を ``time`` にする。形が違えば 422。"""
    match = _HHMM.match(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=DETAIL_BAD_TIME
        )
    return time(int(match.group(1)), int(match.group(2)))


def _require_kind(kind: str | None) -> str:
    if kind not in ADJUST_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=DETAIL_BAD_KIND
        )
    return kind


def _floor_jst(value: datetime) -> datetime:
    """JST の分に切り捨てる (画面の HH:MM と同じ粒度で比べるため)。"""
    return value.astimezone(JST).replace(second=0, microsecond=0)


def _hm(value: datetime) -> str:
    return value.astimezone(JST).strftime("%H:%M")


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _snapshot(kind: str, actual: KindActual | None) -> dict[str, object | None]:
    """監査ログの before (その時点の実績)。"""
    adjustment = actual.adjustment if actual is not None else None
    return {
        "kind": kind,
        "at": _iso(actual.at) if actual is not None else None,
        "read_at": _iso(actual.read_at) if actual is not None else None,
        "adjusted": bool(actual is not None and actual.adjusted),
        "reason_code": adjustment.reason_code if adjustment is not None else None,
    }


def _follow_unplanned(visit: Visit, kind: str, at: datetime | None) -> None:
    """予定外訪問の予定欄 (= 実績の写し) を実績時刻に追随させる (設計 §5 の例外)。

    通常の訪問では何もしない。``start < end`` が崩れる値は採らない。
    """
    if not visit.is_unplanned or at is None:
        return
    wall = _floor_jst(at).time()
    if kind == "arrival":
        if wall < visit.end_time:
            visit.start_time = wall
    elif visit.start_time < wall:
        visit.end_time = wall


async def _current_actuals(db: AsyncSession, visit: Visit) -> VisitActuals:
    return (await load_actuals(db, [visit.id])).get(visit.id) or VisitActuals()


def _require_read_on_visit_date(visit: Visit, actuals: VisitActuals) -> None:
    """読み取った日 (JST) が ``visit_date`` と違う訪問は合わせられない (409)。

    取込で打刻済みの訪問の日付が動くと、読取時刻と訪問日が別の日になる。合わせる時刻は
    ``visit_date`` の ``HH:MM`` として組むので、そのまま通すと読み取りと無関係な日の
    時刻ができる。
    """
    for actual in (actuals.arrival, actuals.departure):
        if actual is None or actual.read_at is None:
            continue
        read_day = actual.read_at.astimezone(JST).date()
        if read_day != visit.visit_date:
            raise _conflict(
                f"読み取った日（{read_day.month}/{read_day.day}）と訪問日"
                f"（{visit.visit_date.month}/{visit.visit_date.day}）が違うため、"
                "時刻を合わせられません。管理者に連絡してください"
            )


async def adjust_actual_time(
    db: AsyncSession,
    *,
    visit: Visit,
    kind: str | None,
    hhmm: str | None,
    reason_code: str | None,
    reason_text: str | None,
    source: str,
    actor: User,
    now: datetime,
    audit_meta: dict[str, object] | None = None,
) -> VisitTimeAdjustment:
    """実績の時刻を ``hhmm`` (JST・``visit_date`` の日) に合わせる (**commit しない**)。

    検証 (設計 §6-1):

    * 到着: 到着の読み取りが必要。``読取時刻 - 90 分 <= time <= 読取時刻`` (分に
      切り捨て)。退出の実績があればそれより前。admin は 90 分の下限なし。
    * 退出 (読み取りあり): ``到着の実績 < time <= 読取時刻 + 30 分``。
    * 90 分 / 30 分は ``checkin_settings`` の ``arrival_max_back_min`` /
      ``departure_max_ahead_min`` (既定の値)。
    * 退出 (読み取りなし = 手入力): 到着の読み取りが必要。``到着の実績 < time``。
    * 退出はどちらも、今日の訪問なら現在時刻まで。
    * 読み取った日 (JST) が ``visit_date`` と違う訪問は 409。
    """
    if visit.deleted_at is not None:
        raise _conflict(DETAIL_DELETED)
    kind = _require_kind(kind)
    wall = parse_hhmm(hhmm)
    if reason_code is not None and reason_code not in ADJUST_REASON_LABELS:
        raise _invalid(DETAIL_BAD_REASON)
    text = reason_text.strip() if reason_text and reason_text.strip() else None
    if text is not None and len(text) > REASON_TEXT_MAX:
        raise _invalid(DETAIL_REASON_TOO_LONG)
    is_admin = normalize_user_role(actor.role) == "admin"
    limits = await load_thresholds(db)
    arrival_max_back_min = limits["arrival_max_back_min"]
    departure_max_ahead_min = limits["departure_max_ahead_min"]

    actuals = await _current_actuals(db, visit)
    _require_read_on_visit_date(visit, actuals)
    arrival = actuals.arrival
    departure = actuals.departure
    target = datetime.combine(visit.visit_date, wall, tzinfo=JST)
    now_jst = now.astimezone(JST)

    if kind == "arrival":
        if arrival is None or arrival.checkin is None or arrival.read_at is None:
            raise _conflict(DETAIL_NO_ARRIVAL)
        read = _floor_jst(arrival.read_at)
        if target > read:
            raise _invalid(f"到着は読み取った時刻（{_hm(read)}）より後にはできません")
        earliest = read - timedelta(minutes=arrival_max_back_min)
        if not is_admin and target < earliest:
            raise _invalid(
                f"到着をさかのぼれるのは読み取った時刻の {arrival_max_back_min} 分前"
                f"（{_hm(earliest)}）までです"
            )
        if departure is not None and target >= _floor_jst(departure.at):
            raise _invalid(f"到着は退出（{_hm(departure.at)}）より前の時刻にしてください")
        current, base = arrival, arrival.checkin
    else:
        has_read = departure is not None and departure.checkin is not None
        if not has_read and (arrival is None or arrival.checkin is None):
            raise _conflict(DETAIL_NO_ARRIVAL)
        if arrival is not None and target <= _floor_jst(arrival.at):
            raise _invalid(f"退出は到着（{_hm(arrival.at)}）より後の時刻にしてください")
        if has_read:
            latest = _floor_jst(departure.read_at) + timedelta(minutes=departure_max_ahead_min)
            if target > latest:
                raise _invalid(
                    f"退出は読み取った時刻の {departure_max_ahead_min} 分後"
                    f"（{_hm(latest)}）までです"
                )
        if visit.visit_date >= now_jst.date() and target > now_jst:
            raise _invalid("退出はいまの時刻より後にはできません")
        current, base = departure, (departure.checkin if departure is not None else None)

    adjusted_at = target.astimezone(UTC)
    # 読み取りの無い退出 (手入力): 手で入れる前の status と予定外訪問の end_time を控える
    # (戻すときに使う)。手で入れた退出を続けて合わせ直す場合は、最初の値を引き継ぐ。
    prev_status: str | None = None
    prev_end_time: time | None = None
    if kind == "departure" and base is None:
        carried = departure.adjustment if departure is not None and departure.manual else None
        if carried is not None:
            prev_status = carried.prev_visit_status
            prev_end_time = carried.prev_visit_end_time
        else:
            prev_status = visit.status
            prev_end_time = visit.end_time if visit.is_unplanned else None
    # ``created_at`` を明示するのは、同じリクエストで記録した打刻 (``judge_checkin`` が
    # 同じ時計で ``created_at`` を入れる) との前後を確定させるため (設計 §4 の規則)。
    adjustment = VisitTimeAdjustment(
        visit_id=visit.id,
        kind=kind,
        adjusted_at=adjusted_at,
        base_checkin_id=base.id if base is not None else None,
        reason_code=reason_code,
        reason_text=text,
        source=source,
        created_by_user_id=actor.id,
        created_by_staff_id=actor.staff_id,
        prev_visit_status=prev_status,
        prev_visit_end_time=prev_end_time,
        created_at=now,
        updated_at=now,
    )
    db.add(adjustment)

    _follow_unplanned(visit, kind, adjusted_at)
    # 読み取りの無い退出を入れた = 訪問は終わっている。
    if kind == "departure" and base is None and visit.status == VISIT_STATUS_IN_PROGRESS:
        visit.status = VISIT_STATUS_COMPLETED

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=AUDIT_ADJUST,
            target_table=AUDIT_TARGET_TABLE,
            target_id=str(visit.id),
            before=_snapshot(kind, current),
            after={
                "kind": kind,
                "at": _iso(adjusted_at),
                "reason_code": reason_code,
                "reason_text": text,
                "source": source,
            },
            **(audit_meta or {}),
        )
    )
    return adjustment


async def reset_actual_time(
    db: AsyncSession,
    *,
    visit: Visit,
    kind: str | None,
    source: str,
    actor: User,
    now: datetime,
    audit_meta: dict[str, object] | None = None,
) -> VisitTimeAdjustment | None:
    """実績の時刻を読取時刻に戻す (``adjusted_at = NULL`` の行を追記・**commit しない**)。

    効いている調整が無ければ何もしない (None を返す = 冪等)。
    """
    if visit.deleted_at is not None:
        raise _conflict(DETAIL_DELETED)
    kind = _require_kind(kind)
    actuals = await _current_actuals(db, visit)
    current = actuals.kind(kind)
    if current is None or not current.adjusted:
        return None
    departure = actuals.departure
    # 退出を合わせてある場合だけ、戻した到着が退出より前になるかを確かめる。退出が
    # 読取時刻のまま (到着と退出を同じ分に読んだ場合など) なら、どちらも読み取った事実
    # なので、到着を読取時刻に戻すのは許す。
    if (
        kind == "arrival"
        and current.read_at is not None
        and departure is not None
        and departure.adjusted
        and _floor_jst(current.read_at) >= _floor_jst(departure.at)
    ):
        raise _invalid(
            f"読み取った時刻（{_hm(current.read_at)}）が退出（{_hm(departure.at)}）より"
            "前にならないため戻せません。先に退出の時刻を合わせてください"
        )

    base = current.checkin
    adjustment = VisitTimeAdjustment(
        visit_id=visit.id,
        kind=kind,
        adjusted_at=None,
        base_checkin_id=base.id if base is not None else None,
        source=source,
        created_by_user_id=actor.id,
        created_by_staff_id=actor.staff_id,
        created_at=now,
        updated_at=now,
    )
    db.add(adjustment)

    _follow_unplanned(visit, kind, current.read_at)
    # 読み取りの無い退出を戻した = 退出が無くなった。手で入れる前の状態へ戻す
    # (元から completed だった訪問は completed のまま)。
    if kind == "departure" and base is None:
        manual = current.adjustment
        if (
            visit.status == VISIT_STATUS_COMPLETED
            and manual.prev_visit_status == VISIT_STATUS_IN_PROGRESS
        ):
            visit.status = VISIT_STATUS_IN_PROGRESS
        if (
            visit.is_unplanned
            and manual.prev_visit_end_time is not None
            and visit.start_time < manual.prev_visit_end_time
        ):
            visit.end_time = manual.prev_visit_end_time

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=AUDIT_RESET,
            target_table=AUDIT_TARGET_TABLE,
            target_id=str(visit.id),
            before=_snapshot(kind, current),
            after={"kind": kind, "at": _iso(current.read_at), "source": source},
            **(audit_meta or {}),
        )
    )
    return adjustment


async def apply_bundled_adjustment(
    db: AsyncSession,
    *,
    visit: Visit,
    kind: str,
    adjusted_time: str | None,
    reason_code: str | None,
    actor: User,
    now: datetime,
    audit_meta: dict[str, object] | None = None,
) -> None:
    """打刻リクエストに同梱された ``adjusted_time`` を調整として記録する (設計 §6-2)。

    打刻を ``db.add`` した直後に呼ぶ。**検証に通らない ``adjusted_time`` は黙って
    無視する** — 退避キューの再送は 4xx で破棄されるため、調整の不備で訪問の記録
    そのものを失わないようにする。調整の書き込みは SAVEPOINT の中で行い、失敗しても
    打刻の transaction を壊さない。
    """
    if not adjusted_time:
        return
    # 打刻を先に確定させる (``load_actuals`` がこの打刻を読めるように。ここでの失敗は
    # 打刻そのものの失敗なので握りつぶさない)。
    await db.flush()
    try:
        async with db.begin_nested():
            await adjust_actual_time(
                db,
                visit=visit,
                kind=kind,
                hhmm=adjusted_time,
                reason_code=reason_code if reason_code in ADJUST_REASON_LABELS else None,
                reason_text=None,
                source="checkin",
                actor=actor,
                now=now,
                audit_meta=audit_meta,
            )
            await db.flush()
    except HTTPException as exc:
        logger.info("checkin: bundled adjusted_time ignored (%s)", exc.status_code)
    except Exception:  # noqa: BLE001 — 調整の失敗で打刻を落とさない
        logger.exception("checkin: bundled adjusted_time failed (ignored)")
