"""訪問ごとの実績時刻の単一ソース — **ここだけが実績時刻を決める**.

正典設計書: ``docs/plans/actual-time-adjust-design-2026-09-30.md`` §3 / §5
(前段: ``docs/plans/visit-history-design-2026-09-30.md`` §2)。

用語 (設計 §2):

* **読取時刻** = QR を読んだ時刻。打刻 1 行から決まる (``checkin_read_at``)。
  ``device_time`` が妥当ならそれ、そうでなければ ``scanned_at`` (サーバ受信時刻)。
  圏外で退避して後から届いた打刻は、読み取った日の訪問に付き、読取時刻は
  ``device_time`` のまま (``checkin-late-delivery-design-2026-10-01.md``)。
* **調整** = スタッフまたは管理者が実績の時刻を合わせた記録
  (``visit_time_adjustments`` の 1 行)。
* **実績時刻** = 画面・集計・レポートが使う時刻。調整があれば調整後、無ければ読取時刻。

``visit_checkins`` は追記専用で、再スキャンは新しい行になる。採るのは **kind ごとの
最新 1 件** (``scanned_at DESC, id DESC``)。``no_show`` は実績に数えない。

どの調整が効くか (設計 §4): (visit, kind) ごとに ``created_at DESC, id DESC`` の
先頭 1 行。その行の ``adjusted_at`` が NULL でなく、同じ kind の最新の打刻より後に
作られていれば有効 (打刻が無ければ無条件)。打刻し直すと、それより前の調整は
効かなくなる。ただし **同じ読み取りの再送** は打刻し直しに数えない: 最新の打刻の
``device_time`` が、調整の元になった打刻 (``base_checkin_id``) の ``device_time`` と
一致する (どちらも非 NULL) なら、調整は有効のまま (スマホは成功した打刻を再送する
ことがある。``device_time`` が無い・違う場合は読み直しとして扱う)。

滞在分 (``stay_minutes``) もここで決める: 到着・退出をそれぞれ JST の分に切り捨てて
からの差 (画面の HH:MM の引き算と一致)。

訪問詳細・一覧 (``api/v1/visits.py``)・訪問モニター (``monitor.py``)・未訪問の検知
(``notify.py``)・打刻履歴 (``history.py``) はすべてここを通す。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.staff import Staff
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_checkin import VisitCheckin
from app.models.visit_time_adjustment import ADJUST_REASON_LABELS, VisitTimeAdjustment

JST = ZoneInfo("Asia/Tokyo")

#: 実績として数える kind (``no_show`` は「行かなかった」記録なので含めない)。
ACTUAL_KINDS: tuple[str, ...] = ("arrival", "departure")

#: 端末の時計がこれより進んでいる ``device_time`` は採らない (設計 §3)。
DEVICE_TIME_FUTURE_TOLERANCE = timedelta(seconds=120)
#: 圏外で退避した打刻を受け付ける期限 (読み取りから受信まで・PO 決定 2026-10-01)。
#: これより古い ``device_time`` は採らず、前日以前の訪問への打刻も受け付けない
#: (``checkin-late-delivery-design-2026-10-01.md``)。``checkin_settings`` に列が無いので
#: 今はモジュール定数 (将来の設定候補)。
LATE_DELIVERY_MAX_AGE = timedelta(hours=72)
#: サーバ受信よりこれ以上古い ``device_time`` は採らない (設計 §3・遅れて届いた打刻の期限)。
DEVICE_TIME_MAX_AGE = LATE_DELIVERY_MAX_AGE
#: 「遅れて届いた」と表示する目安: 読み取りから受信までがこれを超えた打刻
#: (日付をまたいだ打刻は時間の長さに依らず「遅れて届いた」)。
LATE_DELIVERY_MARK_AFTER = timedelta(minutes=30)


def as_utc(value: datetime) -> datetime:
    """timestamptz (SQLite では naive = UTC) を UTC aware に揃える。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def device_time_in_window(device_time: datetime | None, scanned_at: datetime) -> bool:
    """``device_time`` が受信時刻から見て採れる範囲にあるか (日付は問わない)。

    ``scanned_at - 72 時間 <= device_time <= scanned_at + 120 秒``。端末の時計が進んで
    いる場合と、72 時間より古い読み取りは採らない。
    """
    if device_time is None:
        return False
    scanned = as_utc(scanned_at)
    device = as_utc(device_time)
    return scanned - DEVICE_TIME_MAX_AGE <= device <= scanned + DEVICE_TIME_FUTURE_TOLERANCE


def resolve_read_time(
    device_time: datetime | None, scanned_at: datetime, visit_date: date | None = None
) -> datetime:
    """読取時刻 (設計 §3)。``device_time`` が妥当ならそれ、そうでなければ ``scanned_at``。

    妥当の条件: ``device_time_in_window`` (受信の 72 時間前〜120 秒後) かつ JST の日付が
    ``scanned_at`` と同じ **または** 訪問日 ``visit_date`` と同じ (圏外で退避して翌日以降に
    届いた打刻は、読み取った日の訪問に付く・``checkin-late-delivery-design``)。
    ``visit_date`` を渡さない呼び出しは受信日だけで比べる。返り値は UTC aware。
    """
    scanned = as_utc(scanned_at)
    if not device_time_in_window(device_time, scanned):
        return scanned
    device = as_utc(device_time)
    day = device.astimezone(JST).date()
    if day == scanned.astimezone(JST).date() or day == visit_date:
        return device
    return scanned


def late_received_at(
    device_time: datetime | None, scanned_at: datetime, visit_date: date | None = None
) -> datetime | None:
    """「遅れて届いた」打刻なら受信時刻 (UTC aware)、そうでなければ None。

    読取時刻に ``device_time`` を採った打刻のうち、受信が読み取りから 30 分を超えて
    遅れた、または JST の日付をまたいで届いたもの。``device_time`` を採らなかった
    (= 読取時刻が受信時刻) 打刻は遅れていない扱い。
    """
    scanned = as_utc(scanned_at)
    read = resolve_read_time(device_time, scanned, visit_date)
    if read == scanned:
        return None
    if (
        scanned - read > LATE_DELIVERY_MARK_AFTER
        or read.astimezone(JST).date() != scanned.astimezone(JST).date()
    ):
        return scanned
    return None


def checkin_read_at(checkin: VisitCheckin, visit_date: date | None = None) -> datetime:
    """打刻 1 行の読取時刻 (UTC aware)。``visit_date`` = その打刻が付いた訪問の日付。"""
    return resolve_read_time(checkin.device_time, checkin.scanned_at, visit_date)


def checkin_late_received_at(
    checkin: VisitCheckin, visit_date: date | None = None
) -> datetime | None:
    """打刻 1 行が「遅れて届いた」なら受信時刻 (``late_received_at``)。"""
    return late_received_at(checkin.device_time, checkin.scanned_at, visit_date)


@dataclass
class KindActual:
    """到着または退出の実績。"""

    #: 実績時刻 (調整後。無ければ読取時刻)。UTC aware。
    at: datetime
    #: 読取時刻。読み取りの無い手入力は None。
    read_at: datetime | None = None
    adjusted: bool = False
    #: 読み取りが無く、調整だけで成り立っている。
    manual: bool = False
    #: 最新の打刻行 (位置判定・打刻者など)。
    checkin: VisitCheckin | None = None
    #: 効いている調整。
    adjustment: VisitTimeAdjustment | None = None
    #: 最新の打刻が「遅れて届いた」なら受信時刻 (``late_received_at``)。それ以外は None。
    late_received_at: datetime | None = None


@dataclass
class VisitActuals:
    """1 訪問の実績。"""

    arrival: KindActual | None = None
    departure: KindActual | None = None
    #: 最新の no_show。
    no_show: VisitCheckin | None = None
    #: 到着・退出の全打刻者 (新しい順・重複なし)。最新の打刻者だけでなく、打ち直される
    #: 前に打った人も含む = 代行判定・「自分が打刻した訪問」の判定用。
    checkin_staff_ids: list[UUID] = field(default_factory=list)
    #: kind を問わない最新 1 件。
    latest_checkin: VisitCheckin | None = None

    def kind(self, kind: str) -> KindActual | None:
        return self.arrival if kind == "arrival" else self.departure

    @property
    def adjustments(self) -> list[VisitTimeAdjustment]:
        """効いている調整 (到着 → 退出の順)。"""
        return [
            actual.adjustment
            for actual in (self.arrival, self.departure)
            if actual is not None and actual.adjustment is not None
        ]


def stay_minutes(
    arrival_at: datetime | None,
    departure_at: datetime | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """滞在分 — **ここだけが滞在分を決める** (訪問モニター・打刻履歴・Excel・A4 が共用)。

    到着・退出の実績時刻をそれぞれ JST の分に切り捨ててからの差 (画面の HH:MM の
    引き算と一致する)。退出が無い進行中は ``now`` を同じ規則で使う (``now`` も無ければ
    None)。打ち直しや後送りで退出が到着より前になった場合は 0 (負の滞在を出さない)。
    """
    end = departure_at if departure_at is not None else now
    if arrival_at is None or end is None:
        return None
    arr = as_utc(arrival_at).astimezone(JST).replace(second=0, microsecond=0)
    dep = as_utc(end).astimezone(JST).replace(second=0, microsecond=0)
    return max(0, int((dep - arr).total_seconds() // 60))


def _is_resend(checkin: VisitCheckin, base: VisitCheckin | None) -> bool:
    """``checkin`` が、調整の元になった打刻 ``base`` と同じ読み取りの再送か。

    読み取った瞬間 (``device_time``) がどちらも非 NULL で一致すれば再送とみなす。
    ``device_time`` が無い・違う場合は読み直し (= 調整は効かなくなる)。
    """
    if base is None or checkin.device_time is None or base.device_time is None:
        return False
    return as_utc(checkin.device_time) == as_utc(base.device_time)


def _resolve_kind(
    checkin: VisitCheckin | None,
    adjustment: VisitTimeAdjustment | None,
    base_checkin: VisitCheckin | None = None,
    visit_date: date | None = None,
) -> KindActual | None:
    """最新の打刻と、その kind の最新の調整行から実績を決める (設計 §4 の規則)。

    ``base_checkin`` は調整の元になった打刻 (``adjustment.base_checkin_id`` の行)。
    ``visit_date`` は訪問日 (読取時刻と「遅れて届いた」の判定に使う)。
    """
    effective = adjustment
    if effective is not None and effective.adjusted_at is None:
        effective = None  # 「読取時刻に戻す」の行。
    if (
        effective is not None
        and checkin is not None
        and as_utc(effective.created_at) < as_utc(checkin.created_at)
        and not _is_resend(checkin, base_checkin)
    ):
        effective = None  # 調整の後に打刻し直した (同じ読み取りの再送は除く)。
    if checkin is None and effective is None:
        return None
    read_at = checkin_read_at(checkin, visit_date) if checkin is not None else None
    late = checkin_late_received_at(checkin, visit_date) if checkin is not None else None
    if effective is not None:
        return KindActual(
            at=as_utc(effective.adjusted_at),
            read_at=read_at,
            adjusted=True,
            manual=checkin is None,
            checkin=checkin,
            adjustment=effective,
            late_received_at=late,
        )
    return KindActual(at=read_at, read_at=read_at, checkin=checkin, late_received_at=late)


async def load_actuals(db: AsyncSession, visit_ids: Collection[UUID]) -> dict[UUID, VisitActuals]:
    """訪問 ID の集合から実績 (到着・退出・未訪問・打刻者) をまとめて引く (2 クエリ)。

    打刻も効いている調整も無い訪問はキーごと現れない。PostgreSQL 専用の
    ``DISTINCT ON`` は使わず、新しい順に読んで最初に出た行を採る (テストの SQLite
    でも同じ結果)。
    """
    if not visit_ids:
        return {}
    # ``IN`` 句の大きさ: 呼び出し側の最大は打刻履歴の 92 日分 (数千件) の前提。
    ids = list(visit_ids)
    # 訪問日は読取時刻の判定に要る (遅れて届いた打刻は読み取った日の訪問に付く)。
    checkin_rows = (
        await db.execute(
            select(VisitCheckin, Visit.visit_date)
            .join(Visit, Visit.id == VisitCheckin.visit_id)
            .where(VisitCheckin.visit_id.in_(ids))
            # 第 2 キー (id DESC) は同一 scanned_at が並んだときの順序を決定化する。
            .order_by(VisitCheckin.scanned_at.desc(), VisitCheckin.id.desc())
        )
    ).all()
    checkins = [row for row, _day in checkin_rows]
    visit_dates: dict[UUID, date] = {row.visit_id: day for row, day in checkin_rows}
    adjustment_rows = (
        await db.scalars(
            select(VisitTimeAdjustment)
            .where(VisitTimeAdjustment.visit_id.in_(ids))
            .order_by(VisitTimeAdjustment.created_at.desc(), VisitTimeAdjustment.id.desc())
        )
    ).all()

    # DESC 並びなので、キーごとに最初に出た行 = 最新。
    latest: dict[tuple[UUID, str], VisitCheckin] = {}
    checkin_by_id = {row.id: row for row in checkins}
    result: dict[UUID, VisitActuals] = {}
    for row in checkins:
        actuals = result.setdefault(row.visit_id, VisitActuals())
        if actuals.latest_checkin is None:
            actuals.latest_checkin = row
        latest.setdefault((row.visit_id, row.kind), row)
        if (
            row.kind in ACTUAL_KINDS
            and row.staff_id is not None
            and row.staff_id not in actuals.checkin_staff_ids
        ):
            actuals.checkin_staff_ids.append(row.staff_id)
    latest_adjustment: dict[tuple[UUID, str], VisitTimeAdjustment] = {}
    for adj in adjustment_rows:
        latest_adjustment.setdefault((adj.visit_id, adj.kind), adj)

    def resolve(visit_id: UUID, kind: str) -> KindActual | None:
        adjustment = latest_adjustment.get((visit_id, kind))
        base = (
            checkin_by_id.get(adjustment.base_checkin_id)
            if adjustment is not None and adjustment.base_checkin_id is not None
            else None
        )
        return _resolve_kind(
            latest.get((visit_id, kind)), adjustment, base, visit_dates.get(visit_id)
        )

    for visit_id in {vid for vid, _kind in (*latest, *latest_adjustment)}:
        arrival = resolve(visit_id, "arrival")
        departure = resolve(visit_id, "departure")
        no_show = latest.get((visit_id, "no_show"))
        if visit_id not in result and arrival is None and departure is None:
            continue  # 効いていない調整しか無い訪問。
        actuals = result.setdefault(visit_id, VisitActuals())
        actuals.arrival = arrival
        actuals.departure = departure
        actuals.no_show = no_show
    return result


#: staff ロールへの応答で、スタッフ名の無い調整者 (スタッフ未紐付けの管理者など) に出す名前。
ADJUSTER_LABEL_ADMIN = "管理者"


async def load_adjuster_names(
    db: AsyncSession, actuals: Iterable[VisitActuals], *, for_admin: bool
) -> dict[UUID, str | None]:
    """効いている調整を作った人の表示名を ``adjustment.id -> 名前`` で引く (最大 2 クエリ)。

    スタッフ名 → 無ければ、admin への応答 (``for_admin=True``) では利用者の email /
    username (``visit_review`` の確認者名と同じ規則)。staff への応答では email /
    username を見せず「管理者」とする (アカウントの識別子をスタッフに配らない)。
    """
    adjustments = [adj for a in actuals for adj in a.adjustments]
    if not adjustments:
        return {}
    staff_ids = {a.created_by_staff_id for a in adjustments if a.created_by_staff_id is not None}
    user_ids = (
        {
            a.created_by_user_id
            for a in adjustments
            if a.created_by_staff_id is None and a.created_by_user_id is not None
        }
        if for_admin
        else set()
    )
    staff_names: dict[UUID, str] = {}
    if staff_ids:
        staff_names = {
            sid: name
            for sid, name in (
                await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))
            ).all()
        }
    user_names: dict[UUID, str | None] = {}
    if user_ids:
        user_names = {
            uid: (email or username)
            for uid, email, username in (
                await db.execute(
                    select(User.id, User.email, User.username).where(User.id.in_(user_ids))
                )
            ).all()
        }
    names: dict[UUID, str | None] = {}
    for adj in adjustments:
        name: str | None = None
        if adj.created_by_staff_id is not None:
            name = staff_names.get(adj.created_by_staff_id)
        elif adj.created_by_user_id is not None:
            name = user_names.get(adj.created_by_user_id)
        names[adj.id] = name if for_admin or name is not None else ADJUSTER_LABEL_ADMIN
    return names


def adjustment_payloads(actuals: VisitActuals, names: dict[UUID, str | None]) -> list[dict]:
    """効いている調整の配列 (設計 §6-3 の ``adjustments``。モニターと打刻履歴で同じ形)。"""
    return [
        {
            "kind": adj.kind,
            "reason_code": adj.reason_code,
            "reason_label": ADJUST_REASON_LABELS.get(adj.reason_code or ""),
            "reason_text": adj.reason_text,
            "by_name": names.get(adj.id),
            "created_at": as_utc(adj.created_at),
        }
        for adj in actuals.adjustments
    ]
