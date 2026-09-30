"""打刻履歴 (期間指定の一覧) の行の組み立て.

正典設計書: ``docs/plans/visit-history-design-2026-09-30.md`` §2 / §3 (Phase 1) と
``docs/plans/actual-time-adjust-design-2026-09-30.md`` §6-3 (Phase 2: 実績の時刻を
合わせる)。到着・退出は **実績時刻** (調整があれば調整後、無ければ読取時刻) で、
決めるのは ``actuals.py`` だけ。

1 行 = 1 訪問。期間内の訪問と打刻を読み込み、**Python 側で** 行・state・備考を
組んでから絞り込み・並び替え・集計をする (1 か月で約 600 訪問。SQL で state を
組み立てない)。一覧 API・Excel・A4 の 3 つが同じ行を使う。

クエリ数は期間の長さに依らず一定 (訪問・打刻・調整・担当割当・同行 2 本・
コース担当・患者・スタッフ・拠点・調整した人の名前)。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.accompaniment import resolve_accompaniment_staff_by_visit
from app.services.checkin.actuals import (
    ACTUAL_KINDS,
    VisitActuals,
    adjustment_payloads,
    load_actuals,
    load_adjuster_names,
    stay_minutes,
)
from app.services.checkin.monitor import visit_staff_id_set

JST = ZoneInfo("Asia/Tokyo")

# state (UI と契約・設計 §2)。
STATE_DONE = "done"
STATE_IN_PROGRESS = "in_progress"
STATE_NO_DEPARTURE = "no_departure"
STATE_NONE = "none"
STATE_FUTURE = "future"

#: 到着と退出の間がこれ未満なら「まとめて読んだ」可能性として備考に出す。
SHORT_STAY_MIN = 5

#: 位置判定のうち備考「場所 要確認」に出すもの。
_LOCATION_REVIEW_STATUSES = frozenset({"review", "mismatch", "no_gps"})

# 備考の語彙 (設計 §2。配列にはこの順で入れる)。
REMARK_NO_DEPARTURE = "退出なし"
REMARK_UNPLANNED = "予定外の訪問"
REMARK_MANUAL = "QRなし"
REMARK_LOCATION = "場所 要確認"
REMARK_ADJUSTED = "時刻調整"
REMARK_CANCELLED = "取消済みの予定に記録"
REMARK_SHORT_STAY = "到着と退出が近い"


def substitute_remark(planned_staff_name: str | None) -> str:
    return f"代行（予定: {planned_staff_name or '未割当'}）"


@dataclass
class HistoryRow:
    """打刻履歴の 1 行 (= ``VisitHistoryItem`` の中身 ＋ 絞り込み・並び替え用の値)。"""

    visit_id: UUID
    visit_date: date
    office_id: UUID | None
    office_name: str | None
    patient_id: UUID
    patient_name: str | None
    # 予定 ("HH:MM")。予定外の訪問は None (打刻時刻が予定欄に入っているだけなので)。
    start_time: str | None
    end_time: str | None
    # 予定の担当 = 主担当。主担当が空 (かつ手動の付け替えでない) の訪問はコース担当
    # (``VisitRead.staff_name`` と同じ規則)。予定外の訪問は None。
    planned_staff_id: UUID | None
    planned_staff_name: str | None
    actual_staff_id: UUID | None
    actual_staff_name: str | None
    # 実績時刻 (調整後。無ければ読取時刻)。UTC の aware datetime。
    arrival_at: datetime | None
    departure_at: datetime | None
    # 読取時刻 (QR を読んだ時刻)。読み取りが無ければ None。
    arrival_read_at: datetime | None
    departure_read_at: datetime | None
    arrival_adjusted: bool
    departure_adjusted: bool
    #: 読み取りの無い退出 (手で入れた時刻)。
    departure_manual: bool
    #: 効いている調整 (``actuals.adjustment_payloads`` の形。訪問モニターと同じ)。
    adjustments: list[dict]
    stay_minutes: int | None
    checkin_source: str | None
    match_status: str | None
    is_substitute: bool
    is_unplanned: bool
    is_cancelled: bool
    state: str
    remarks: list[str]
    # ---- 応答には出さない ----
    #: 並び替え用の開始時刻 (予定外は打刻時刻が入っている = 到着順に並ぶ)。
    sort_time: time
    #: 担当集合 ∪ コース担当フォールバック ∪ 到着・退出を打った人全員
    #: (staff ロールの「自分の分」判定)。
    related_staff_ids: frozenset[UUID]
    #: 削除済みの訪問 (打刻があるので載せている。実績の時刻は合わせられない)。
    is_deleted: bool = False
    #: 到着の読み取りがあるか (無ければ合わせる対象が無い = ``adjust_allowed`` は偽)。
    has_arrival_read: bool = False
    # ---- 応答に出す (見ているユーザーごとに API 層が決める) ----
    #: 今のユーザーがこの訪問の実績を合わせられるか。
    adjust_allowed: bool = False

    @property
    def is_adjusted(self) -> bool:
        return self.arrival_adjusted or self.departure_adjusted

    @property
    def nurse_name(self) -> str | None:
        """看護師名 (並び替え・グループ見出し用) = 実際の打刻者 ?? 予定の担当。"""
        return self.actual_staff_name or self.planned_staff_name

    @property
    def arrival_jst(self) -> datetime | None:
        return self.arrival_at.astimezone(JST) if self.arrival_at is not None else None

    @property
    def departure_jst(self) -> datetime | None:
        return self.departure_at.astimezone(JST) if self.departure_at is not None else None


def compute_state(
    *,
    has_arrival: bool,
    has_departure: bool,
    visit_date: date,
    start_time: time,
    now_jst: datetime,
) -> str:
    """行の state (設計 §2)。

    退出だけがある訪問 (到着の読み取りが無い) は ``none`` にする — ``future`` にすると
    集計・Excel・A4 から落ちて、打刻の事実が見えなくなるため。
    """
    if has_arrival:
        if has_departure:
            return STATE_DONE
        return STATE_NO_DEPARTURE if visit_date < now_jst.date() else STATE_IN_PROGRESS
    if has_departure:
        return STATE_NONE
    start_dt = datetime.combine(visit_date, start_time, tzinfo=JST)
    return STATE_NONE if start_dt < now_jst else STATE_FUTURE


async def _names(
    db: AsyncSession, model: type[Office] | type[Staff], ids: set[UUID]
) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = (await db.execute(select(model.id, model.name).where(model.id.in_(ids)))).all()
    return {row_id: name for row_id, name in rows}


async def _course_fallback_staff(db: AsyncSession, visits: list[Visit]) -> dict[UUID, UUID]:
    """主担当が空の訪問のコース担当を ``course_id -> staff_id`` で引く (1 クエリ)。

    スマホの「今日の訪問」(``api/v1/visits._course_fallback_condition`` /
    ``_course_fallback_staff_ids``) と **同じ規則**: 主担当 NULL・
    ``manual_staff_override`` でない・コースが未削除・コース担当が在籍中。
    スマホには出るのに打刻履歴に出ない、という食い違いを作らない。
    """
    course_ids = {
        v.course_id
        for v in visits
        if v.primary_staff_id is None and not v.manual_staff_override and v.course_id is not None
    }
    if not course_ids:
        return {}
    rows = (
        await db.execute(
            select(Course.id, Course.assigned_staff_id)
            .join(Staff, Staff.id == Course.assigned_staff_id)
            .where(
                Course.id.in_(course_ids),
                Course.deleted_at.is_(None),
                Staff.deleted_at.is_(None),
                Staff.status == "active",
            )
        )
    ).all()
    return {course_id: staff_id for course_id, staff_id in rows}


async def load_history_rows(
    db: AsyncSession,
    date_from: date,
    date_to: date,
    *,
    now: datetime | None = None,
    viewer_is_admin: bool = False,
) -> list[HistoryRow]:
    """期間内 (``visit_date``・両端含む) の打刻履歴の行を組む (絞り込み・並び替え前)。

    ``viewer_is_admin`` は調整した人の名前 (``adjustments[].by_name``) の出し方だけに
    効く (スタッフ名の無い調整者は admin には email / username、それ以外には「管理者」)。

    載せる訪問 (設計 §2): 有効な訪問 (未削除・未取消) ＋ **到着か退出の打刻がある
    訪問は取消・削除済みでも載せる** (取込が打刻済みの予定を取り消した実例がある。
    訪問の事実を落とさない)。
    """
    now_jst = (now or datetime.now(UTC)).astimezone(JST)

    has_actual = select(VisitCheckin.visit_id).where(VisitCheckin.kind.in_(ACTUAL_KINDS))
    visits = list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.visit_date >= date_from,
                    Visit.visit_date <= date_to,
                    or_(
                        and_(Visit.deleted_at.is_(None), Visit.status != VISIT_STATUS_CANCELLED),
                        Visit.id.in_(has_actual),
                    ),
                )
            )
        ).all()
    )
    if not visits:
        return []
    visit_ids = [v.id for v in visits]

    actuals_by_visit = await load_actuals(db, visit_ids)
    adjuster_names = await load_adjuster_names(
        db, actuals_by_visit.values(), for_admin=viewer_is_admin
    )
    course_fallback = await _course_fallback_staff(db, visits)

    assignments: dict[UUID, set[UUID]] = defaultdict(set)
    for vid, sid in (
        await db.execute(
            select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
                VisitStaffAssignment.visit_id.in_(visit_ids)
            )
        )
    ).all():
        assignments[vid].add(sid)
    accompaniments = await resolve_accompaniment_staff_by_visit(db, visits)

    patients: dict[UUID, tuple[str, UUID | None]] = {
        pid: (name, office_id)
        for pid, name, office_id in (
            await db.execute(
                select(Patient.id, Patient.name, Patient.primary_office_id).where(
                    Patient.id.in_({v.patient_id for v in visits})
                )
            )
        ).all()
    }
    office_names = await _names(
        db, Office, {oid for _name, oid in patients.values() if oid is not None}
    )
    staff_ids: set[UUID] = {v.primary_staff_id for v in visits if v.primary_staff_id is not None}
    staff_ids.update(course_fallback.values())
    for checked in actuals_by_visit.values():
        staff_ids.update(checked.checkin_staff_ids)
    staff_names = await _names(db, Staff, staff_ids)

    rows: list[HistoryRow] = []
    for v in visits:
        actuals = actuals_by_visit.get(v.id) or VisitActuals()
        arrival_actual = actuals.arrival
        departure_actual = actuals.departure
        # 生の打刻 (打刻者・位置判定)。読み取りの無い退出 (手入力) は None。
        arrival = arrival_actual.checkin if arrival_actual is not None else None
        departure = departure_actual.checkin if departure_actual is not None else None
        arrival_at = arrival_actual.at if arrival_actual is not None else None
        departure_at = departure_actual.at if departure_actual is not None else None
        stay = stay_minutes(arrival_at, departure_at)

        # 予定の担当 = 主担当。主担当が空 (かつ手動の付け替えでない) の訪問はコース担当
        # (``VisitRead.staff_name`` のフォールバックと同じ規則 = スマホと食い違わせない)。
        planned_staff_id = v.primary_staff_id
        if planned_staff_id is None and not v.manual_staff_override:
            planned_staff_id = course_fallback.get(v.course_id)
        assigned = visit_staff_id_set(
            v,
            assignment_staff_ids=assignments.get(v.id, ()),
            accompaniment_staff_ids=accompaniments.get(v.id, ()),
        )
        # 打刻履歴では、コース担当本人の打刻を代行にしない (モニターと通知の代行判定は
        # ``visit_staff_id_set`` のまま = コース担当フォールバックを含めない)。
        if planned_staff_id is not None:
            assigned.add(planned_staff_id)
        # 実際に訪問した人 = 最新の到着の打刻者。到着が無ければ最新の退出の打刻者。
        actual_source = arrival if arrival is not None else departure
        actual_staff_id = actual_source.staff_id if actual_source is not None else None
        # 代行 = 到着・退出の全打刻者の **いずれか** が担当集合の外 (訪問モニターと同じ
        # 判定。担当本人が後から打ち直しても、代行の事実を消さない)。
        is_substitute = any(sid not in assigned for sid in actuals.checkin_staff_ids)
        is_cancelled = v.deleted_at is not None or v.status == VISIT_STATUS_CANCELLED
        state = compute_state(
            has_arrival=arrival_actual is not None,
            has_departure=departure_actual is not None,
            visit_date=v.visit_date,
            start_time=v.start_time,
            now_jst=now_jst,
        )
        checkin_source = arrival.checkin_source if arrival is not None else None
        match_status = arrival.match_status if arrival is not None else None
        planned_name = staff_names.get(planned_staff_id) if planned_staff_id is not None else None

        remarks: list[str] = []
        if state == STATE_NO_DEPARTURE:
            remarks.append(REMARK_NO_DEPARTURE)
        if v.is_unplanned:
            remarks.append(REMARK_UNPLANNED)
        if is_substitute:
            remarks.append(substitute_remark(planned_name))
        if checkin_source == "manual":
            remarks.append(REMARK_MANUAL)
        if match_status in _LOCATION_REVIEW_STATUSES:
            remarks.append(REMARK_LOCATION)
        if actuals.adjustments:
            remarks.append(REMARK_ADJUSTED)
        if is_cancelled:
            remarks.append(REMARK_CANCELLED)
        if stay is not None and stay < SHORT_STAY_MIN:
            remarks.append(REMARK_SHORT_STAY)

        patient_name, office_id = patients.get(v.patient_id, (None, None))
        planned = not v.is_unplanned
        # ``assigned`` はコース担当フォールバックを含む (上で足してある)。
        related = set(assigned) | set(actuals.checkin_staff_ids)
        rows.append(
            HistoryRow(
                visit_id=v.id,
                visit_date=v.visit_date,
                office_id=office_id,
                office_name=office_names.get(office_id) if office_id is not None else None,
                patient_id=v.patient_id,
                patient_name=patient_name,
                start_time=v.start_time.strftime("%H:%M") if planned else None,
                end_time=v.end_time.strftime("%H:%M") if planned else None,
                planned_staff_id=planned_staff_id if planned else None,
                planned_staff_name=planned_name if planned else None,
                actual_staff_id=actual_staff_id,
                actual_staff_name=(
                    staff_names.get(actual_staff_id) if actual_staff_id is not None else None
                ),
                arrival_at=arrival_at,
                departure_at=departure_at,
                arrival_read_at=arrival_actual.read_at if arrival_actual is not None else None,
                departure_read_at=(
                    departure_actual.read_at if departure_actual is not None else None
                ),
                arrival_adjusted=arrival_actual is not None and arrival_actual.adjusted,
                departure_adjusted=departure_actual is not None and departure_actual.adjusted,
                departure_manual=departure_actual is not None and departure_actual.manual,
                adjustments=adjustment_payloads(actuals, adjuster_names),
                stay_minutes=stay,
                checkin_source=checkin_source,
                match_status=match_status,
                is_substitute=is_substitute,
                is_unplanned=v.is_unplanned,
                is_cancelled=is_cancelled,
                state=state,
                remarks=remarks,
                sort_time=v.start_time,
                related_staff_ids=frozenset(related),
                is_deleted=v.deleted_at is not None,
                has_arrival_read=arrival is not None,
            )
        )
    return rows


def filter_rows(
    rows: Iterable[HistoryRow],
    *,
    scope_staff_id: UUID | None = None,
    patient_id: UUID | None = None,
    office_id: UUID | None = None,
    staff_id: UUID | None = None,
    state: str | None = None,
    q: str | None = None,
) -> list[HistoryRow]:
    """絞り込み (設計 §3 の共通クエリ)。

    ``scope_staff_id`` は staff ロールの可視範囲 (自分が担当集合に入る訪問・主担当が
    空で自分がコース担当の訪問・自分が打刻した訪問)。``staff_id`` は画面の絞り込み (予定の担当 **または** 実際の
    打刻者が一致)。``q`` は 2 文字未満なら無視する。
    """
    needle = (q or "").strip().casefold()
    if len(needle) < 2:
        needle = ""

    def keep(r: HistoryRow) -> bool:
        if scope_staff_id is not None and scope_staff_id not in r.related_staff_ids:
            return False
        if patient_id is not None and r.patient_id != patient_id:
            return False
        if office_id is not None and r.office_id != office_id:
            return False
        if staff_id is not None and staff_id not in (r.planned_staff_id, r.actual_staff_id):
            return False
        if state == "in" and r.arrival_at is None:
            return False
        if state == "nodep" and r.state != STATE_NO_DEPARTURE:
            return False
        if state == "none" and r.state != STATE_NONE:
            return False
        if state == "adjusted" and not r.is_adjusted:
            return False
        if state == "special" and not (r.is_substitute or r.is_unplanned):
            return False
        if needle and not any(
            needle in (name or "").casefold()
            for name in (r.patient_name, r.planned_staff_name, r.actual_staff_name)
        ):
            return False
        return True

    return [r for r in rows if keep(r)]


def _date_key(r: HistoryRow) -> tuple:
    return (r.visit_date, r.sort_time, r.patient_name or "", str(r.visit_id))


def sort_rows(rows: Iterable[HistoryRow], sort: str = "date") -> list[HistoryRow]:
    """並び替え: ``date`` (日付→予定開始→患者名) / ``staff`` / ``patient`` (名前→日付)。

    名前の無い行 (担当なし・打刻なし) は末尾に寄せる。最後は visit_id で決着を付け、
    ページングをまたいで行が重複・欠落しないようにする。
    """
    if sort == "staff":
        return sorted(rows, key=lambda r: (r.nurse_name is None, r.nurse_name or "", *_date_key(r)))
    if sort == "patient":
        return sorted(
            rows, key=lambda r: (r.patient_name is None, r.patient_name or "", *_date_key(r))
        )
    return sorted(rows, key=_date_key)


def without_future(rows: Iterable[HistoryRow]) -> list[HistoryRow]:
    """まだ来ていない予定 (``future``) を除く (集計・Excel・A4 の対象)。"""
    return [r for r in rows if r.state != STATE_FUTURE]


def summarize(rows: Iterable[HistoryRow]) -> dict[str, int]:
    """集計帯 (設計 §3): ``future`` を除いて数える。"""
    counted = without_future(rows)
    return {
        "visits": len(counted),
        "with_arrival": sum(1 for r in counted if r.arrival_at is not None),
        "with_departure": sum(1 for r in counted if r.departure_at is not None),
        "no_departure": sum(1 for r in counted if r.state == STATE_NO_DEPARTURE),
        "none": sum(1 for r in counted if r.state == STATE_NONE),
        "adjusted": sum(1 for r in counted if r.is_adjusted),
    }


#: 名前の無いグループの見出し (画面の見出し行と完全一致で突き合わせる)。
GROUP_LABEL_NO_STAFF = "（担当なし）"
GROUP_LABEL_NO_PATIENT = "（患者名なし）"


def group_counts(rows: Iterable[HistoryRow], sort: str) -> list[dict[str, object]]:
    """見出し行の件数 (設計 actual-time-adjust §6-3 の ``groups``)。

    ``sort`` が ``staff`` / ``patient`` のとき、**ページングする前の絞り込み結果全体**
    でのグループ別件数を、渡された行の順 (= 画面の並び) で返す。``date`` は空配列。
    ``label`` は看護師名 (実際の打刻者 ?? 予定の担当) または患者名。名前が無ければ
    ``（担当なし）`` / ``（患者名なし）``。``future`` の行も数える (一覧の行と揃える)。
    """
    if sort not in ("staff", "patient"):
        return []
    groups: dict[str, dict[str, object]] = {}
    for r in rows:
        if sort == "staff":
            label = r.nurse_name or GROUP_LABEL_NO_STAFF
        else:
            label = r.patient_name or GROUP_LABEL_NO_PATIENT
        group = groups.setdefault(label, {"label": label, "count": 0, "with_arrival": 0})
        group["count"] += 1
        if r.arrival_at is not None:
            group["with_arrival"] += 1
    return list(groups.values())


def adjust_reason_labels(r: HistoryRow) -> list[str]:
    """効いている調整の理由の表示名 (到着 → 退出の順・重複なし・理由なしは除く)。"""
    labels: list[str] = []
    for adj in r.adjustments:
        label = adj.get("reason_label")
        if label and label not in labels:
            labels.append(label)
    return labels


def xlsx_remarks(r: HistoryRow) -> str:
    """Excel の備考。理由の無い調整は「時刻調整」だけ。

    理由は 2026-10-01 の PO 決定で画面から尋ねなくなった。理由のある過去の調整だけ
    添える: 「時刻調整（インターホン待ち）」。
    """
    reasons = "・".join(adjust_reason_labels(r))
    adjusted = f"{REMARK_ADJUSTED}（{reasons}）" if reasons else REMARK_ADJUSTED
    return "、".join(adjusted if remark == REMARK_ADJUSTED else remark for remark in r.remarks)


def report_remarks(r: HistoryRow) -> str:
    """A4 の備考。``時刻調整`` を「調整（読取 13:06）」にする。

    A4 には読取時刻の列が無いので、読み取った時刻を備考に出す。到着と退出の両方を
    合わせてある場合は「／」で並べ、退出側には「退出の」を付ける。理由のある過去の
    調整だけ理由を添える: 「調整（読取 13:06・インターホン待ち）」(理由は 2026-10-01 の
    PO 決定で画面から尋ねなくなった)。
    """
    parts: list[str] = []
    for adj in r.adjustments:
        label = adj.get("reason_label")
        if adj.get("kind") == "arrival":
            read = r.arrival_read_at
            part = f"読取 {read.astimezone(JST):%H:%M}" if read is not None else "読み取りなし"
        elif r.departure_read_at is not None:
            part = f"退出の読取 {r.departure_read_at.astimezone(JST):%H:%M}"
        else:
            part = "退出は読み取りなし"
            if label == "読み取りなし":
                label = None
        parts.append(f"{part}・{label}" if label else part)
    adjusted = f"調整（{'／'.join(parts)}）" if parts else "調整"
    return "、".join(adjusted if remark == REMARK_ADJUSTED else remark for remark in r.remarks)


@dataclass
class StaffCount:
    """看護師別の件数 (Excel「看護師別」シート・A4 の 1 枚目)。"""

    name: str
    planned: int = 0
    arrival: int = 0
    departure: int = 0
    no_departure: int = 0


def staff_counts(rows: Iterable[HistoryRow]) -> list[StaffCount]:
    """看護師ごとの件数。予定は「予定の担当」、読み取りは「実際に読んだ人」で数える。

    予定の件数は取消済みを除く。読み取りは到着のある訪問だけを数える。名前順。
    """
    counts: dict[UUID, StaffCount] = {}
    for r in rows:
        if r.planned_staff_id is not None and not r.is_cancelled:
            counts.setdefault(
                r.planned_staff_id, StaffCount(name=r.planned_staff_name or "")
            ).planned += 1
        if r.actual_staff_id is not None and r.arrival_at is not None:
            mine = counts.setdefault(r.actual_staff_id, StaffCount(name=r.actual_staff_name or ""))
            mine.arrival += 1
            if r.departure_at is not None:
                mine.departure += 1
            if r.state == STATE_NO_DEPARTURE:
                mine.no_departure += 1
    return sorted(counts.values(), key=lambda c: c.name)


# ---------------------------------------------------------------------------
# Excel「読み方」シートと A4 の 1 枚目に出す文章 (2 つの出力で同じ文言にする)
# ---------------------------------------------------------------------------

REPORT_TITLE = "訪問時刻の記録（QR 読み取り）"


def period_label(date_from: date, date_to: date) -> str:
    """期間の見出し。暦の 1 か月ちょうどなら「2026 年 9 月」(月次レポートと同じ書き方)。"""
    if (
        date_from.day == 1
        and (date_from.year, date_from.month) == (date_to.year, date_to.month)
        and (date_to + timedelta(days=1)).day == 1
    ):
        return f"{date_from.year} 年 {date_from.month} 月"
    return f"{date_from:%Y/%m/%d}〜{date_to:%Y/%m/%d}"


def caution_note(rows: Iterable[HistoryRow]) -> str:
    """注意書き: 記録が無い = 訪問していない、ではないことを先に伝える。"""
    summary = summarize(rows)
    return (
        f"この期間は {summary['visits']} 件の訪問のうち {summary['with_arrival']} 件に"
        "到着の記録があります。記録のない訪問は「訪問していない」という意味ではありません。"
        "紙の時間確認表とあわせてご確認ください。"
    )


def reading_notes(rows: Iterable[HistoryRow]) -> list[str]:
    """「読み方」の箇条書き (備考の語彙の説明)。"""
    notes = [
        "到着・退出は、スタッフが利用者宅の QR を読み取った時刻です。家に入ってから読むため、"
        "インターホン待ちなどの時間は含まれず、実際の到着より数分遅いことがあります。",
        "「時刻調整」は、スタッフが実際に着いた（出た）時刻に合わせたものです。"
        "QR を読み取った時刻も記録に残しています。",
        "「訪問した看護師」は実際に QR を読み取った人です。予定の担当と違う場合は備考に"
        "「代行」と出ます。",
        "「退出なし」は到着だけ読み取り、退出の読み取りが無い訪問です。滞在時間は計算していません。",
        "「予定外の訪問」は予定に無い訪問を QR で記録したものです。予定の欄は空です。",
        "「QRなし」は QR を読み取らずに記録したものです。",
        f"「到着と退出が近い」は間が {SHORT_STAY_MIN} 分未満のものです。"
        "訪問の後にまとめて読み取った可能性があります。",
        "「場所 要確認」は読み取り時の位置が利用者宅から離れていた、または位置が取れなかったものです。",
        "「取消済みの予定に記録」は、読み取りの後に予定の側が取り消されたものです。"
        "訪問の事実として載せています。",
        "時刻は分単位（秒は切り捨て）です。",
    ]
    first = min((r.visit_date for r in rows if r.arrival_at is not None), default=None)
    if first is not None:
        notes.insert(0, f"この期間で最初の読み取りは {first.month}/{first.day} です。")
    return notes
