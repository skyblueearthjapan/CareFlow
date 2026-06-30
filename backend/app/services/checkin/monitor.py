"""訪問モニター集計 (実効状態の合成) — QR チェックイン Phase 3.

その日の visits を visit_checkins と突き合わせ、スタッフ (= 1 日 1 コース) ごとに
予定 / 到着 / 退出 / 滞在 / 次距離 / 実効状態を組み立てる。

実効状態は **集計時に合成** する (過去 checkin の位置判定 ``match_status`` は不変):

* ``phase``  : 時間ベースの進捗 (future / awaiting / inprogress / done / missing)。
* ``alert_level`` : 要対応レベル (none / review / mismatch / missing)。

位置判定 (``arrival.match_status``) は judge が記録時に確定済み。ここでは時間
(遅延 late_min / 未訪問 grace_min) を JST で都度評価して合成する (設計 §2 末尾の
申し送り「実効状態 = worst(位置, 時間)」)。

しきい値は ``checkin_settings`` (無ければ既定) を ``load_thresholds`` で読む。
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_checkin import VisitCheckin
from app.schemas.visit_monitor import (
    MonitorCheckin,
    MonitorOffice,
    MonitorResponse,
    MonitorStaffRow,
    MonitorThresholds,
    MonitorVisit,
    NearbyPatient,
    NearbyResponse,
)
from app.services.checkin.judge import load_thresholds
from app.utils.geo import haversine_m

JST = ZoneInfo("Asia/Tokyo")

# phase 定数 (UI と契約).
PHASE_FUTURE = "future"
PHASE_AWAITING = "awaiting"
PHASE_INPROGRESS = "inprogress"
PHASE_DONE = "done"
PHASE_MISSING = "missing"

# alert_level 定数.
ALERT_NONE = "none"
ALERT_REVIEW = "review"
ALERT_MISMATCH = "mismatch"
ALERT_MISSING = "missing"

# 退出忘れ (長時間 inprogress) の要確認しきい値 (分)。到着済・退出未記録のまま
# この分数を超えて滞在し続けている訪問を「退出未記録の可能性」として review に上げる。
# Phase4 で checkin_settings 化予定 (現状はサーバ定数)。
MAX_INPROGRESS_MIN = 240


def _as_jst(dt: datetime) -> datetime:
    """timestamptz (or SQLite の naive) を JST aware に正規化する.

    SQLite (テスト) は ``DateTime(timezone=True)`` を naive で返すため、tz 無しは
    UTC とみなしてから JST に変換する (judge が scanned_at に UTC aware を保存する)。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST)


def compute_phase(
    *,
    arrival_scanned: datetime | None,
    departure_scanned: datetime | None,
    has_no_show: bool,
    start_dt: datetime,
    now: datetime,
    grace_min: int,
) -> str:
    """時間ベースの進捗を合成する (設計 §3 の合意アルゴリズム).

    全ての datetime は JST aware。``start_dt`` は visit_date + start_time。
    """
    if arrival_scanned is None:
        if has_no_show:
            return PHASE_MISSING
        if now >= start_dt + timedelta(minutes=grace_min):
            return PHASE_MISSING
        if now < start_dt:
            return PHASE_FUTURE
        return PHASE_AWAITING
    if departure_scanned is None:
        return PHASE_INPROGRESS
    return PHASE_DONE


def compute_alert(
    *,
    phase: str,
    arrival_match_status: str | None,
    arrival_scanned: datetime | None,
    start_dt: datetime,
    late_min: int,
    stay_minutes: int | None = None,
) -> str:
    """要対応レベルを合成する (位置判定 + 時間遅延の worst).

    - missing 中は missing。
    - 到着あり & match_status==mismatch → mismatch。
    - match_status in (review, no_gps) / 到着遅延 (>= late_min) / 退出忘れ
      (inprogress かつ 滞在 > MAX_INPROGRESS_MIN) → review。
    - それ以外 (未到着の future/awaiting を含む) → none。
    """
    if phase == PHASE_MISSING:
        return ALERT_MISSING
    if arrival_match_status is None:
        # 未到着 (future / awaiting)。時間アラートは missing で別途拾う。
        return ALERT_NONE
    if arrival_match_status == "mismatch":
        return ALERT_MISMATCH
    late = arrival_scanned is not None and (arrival_scanned - start_dt) >= timedelta(
        minutes=late_min
    )
    # 退出忘れ: 到着済・退出未記録のまま長時間 inprogress (退出未記録の可能性)。
    long_inprogress = (
        phase == PHASE_INPROGRESS
        and stay_minutes is not None
        and stay_minutes > MAX_INPROGRESS_MIN
    )
    if arrival_match_status in ("review", "no_gps") or late or long_inprogress:
        return ALERT_REVIEW
    return ALERT_NONE


def _project_checkin(row: VisitCheckin) -> MonitorCheckin:
    return MonitorCheckin(
        kind=row.kind,
        scanned_at=row.scanned_at,
        device_time=row.device_time,
        lat=float(row.lat) if row.lat is not None else None,
        lng=float(row.lng) if row.lng is not None else None,
        distance_m=float(row.distance_m) if row.distance_m is not None else None,
        accuracy_m=float(row.accuracy_m) if row.accuracy_m is not None else None,
        match_status=row.match_status,
        reason=row.reason,
        is_override=row.is_override,
    )


def _effective_time(c: MonitorCheckin) -> datetime:
    """滞在計算用の実効時刻 (device_time 優先、無ければ scanned_at)。JST aware."""
    return _as_jst(c.device_time if c.device_time is not None else c.scanned_at)


def _stay_minutes(
    arrival: MonitorCheckin | None,
    departure: MonitorCheckin | None,
    now_jst: datetime,
) -> int | None:
    """滞在分 (到着〜退出、進行中は now 迄)。device_time 優先で逆転対策。"""
    if arrival is None:
        return None
    arr = _effective_time(arrival)
    if departure is not None:
        dep = _effective_time(departure)
    else:
        dep = now_jst
    minutes = (dep - arr).total_seconds() / 60.0
    # オフライン同期で逆転 (dep < arr) した場合は 0 に丸める。
    return max(0, round(minutes))


async def build_monitor(
    db: AsyncSession,
    target_date: date,
    *,
    office_id: UUID | None = None,
    now: datetime | None = None,
) -> MonitorResponse:
    """指定日の訪問モニター集計を組み立てる (DB 読み取りのみ)."""
    if now is None:
        now = datetime.now(UTC)
    now_jst = _as_jst(now)

    thresholds = await load_thresholds(db)

    # その日の visits (削除・取消除く)。スタッフ / 患者を eager-load。
    visits = (
        await db.scalars(
            select(Visit)
            .where(
                Visit.visit_date == target_date,
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
            )
            .options(selectinload(Visit.patient), selectinload(Visit.primary_staff))
            .order_by(Visit.start_time)
            # 同一 session の identity map に既存の visit が居ても eager load を
            # 確実に反映する (直接呼び出しテスト対策; 本番は request-scoped session)。
            .execution_options(populate_existing=True)
        )
    ).all()

    visit_ids = [v.id for v in visits]

    # 全 checkin を 1 クエリで取得し、(visit_id, kind) ごと最新を採用 (scanned_at DESC)。
    latest: dict[tuple[UUID, str], VisitCheckin] = {}
    if visit_ids:
        rows = (
            await db.scalars(
                select(VisitCheckin)
                .where(VisitCheckin.visit_id.in_(visit_ids))
                .order_by(VisitCheckin.scanned_at.desc())
            )
        ).all()
        for r in rows:
            key = (r.visit_id, r.kind)
            if key not in latest:  # DESC 並びなので最初に出た = 最新。
                latest[key] = r

    # コース名ラベル (course_id → "Xコース")。
    course_ids = {v.course_id for v in visits if v.course_id is not None}
    course_code: dict[UUID, str] = {}
    if course_ids:
        for cid, code in (
            await db.execute(select(Course.id, Course.code).where(Course.id.in_(course_ids)))
        ).all():
            course_code[cid] = code

    # スタッフごとにグルーピング (primary_staff_id; None は「担当未設定」行)。
    by_staff: dict[UUID | None, list[Visit]] = defaultdict(list)
    for v in visits:
        by_staff[v.primary_staff_id].append(v)

    # 拠点名解決 (staff.primary_office_id → office.name)。
    office_ids = {
        v.primary_staff.primary_office_id
        for v in visits
        if v.primary_staff is not None and v.primary_staff.primary_office_id is not None
    }
    office_name: dict[UUID, str] = {}
    if office_ids:
        for oid, name in (
            await db.execute(select(Office.id, Office.name).where(Office.id.in_(office_ids)))
        ).all():
            office_name[oid] = name

    staff_rows: list[MonitorStaffRow] = []
    for staff_id, staff_visits in by_staff.items():
        staff = staff_visits[0].primary_staff if staff_id is not None else None
        oid = staff.primary_office_id if staff is not None else None
        # 拠点フィルタ: 指定があれば一致行のみ残す (担当未設定 / 他拠点は除外)。
        if office_id is not None and oid != office_id:
            continue

        # 時刻順 (next 距離計算のため)。
        staff_visits = sorted(staff_visits, key=lambda v: (v.start_time, v.end_time))

        # course_label = 当該スタッフの visits に登場するコースコード (重複排除)。
        codes = sorted(
            {course_code[v.course_id] for v in staff_visits if v.course_id in course_code}
        )
        course_label = "/".join(f"{c}コース" for c in codes) if codes else None

        mvisits: list[MonitorVisit] = []
        for v in staff_visits:
            arrival = latest.get((v.id, "arrival"))
            departure = latest.get((v.id, "departure"))
            no_show = latest.get((v.id, "no_show"))
            arr_p = _project_checkin(arrival) if arrival is not None else None
            dep_p = _project_checkin(departure) if departure is not None else None
            ns_p = _project_checkin(no_show) if no_show is not None else None

            start_dt = datetime.combine(v.visit_date, v.start_time, tzinfo=JST)
            arr_scanned = _as_jst(arrival.scanned_at) if arrival is not None else None
            dep_scanned = _as_jst(departure.scanned_at) if departure is not None else None

            phase = compute_phase(
                arrival_scanned=arr_scanned,
                departure_scanned=dep_scanned,
                has_no_show=no_show is not None,
                start_dt=start_dt,
                now=now_jst,
                grace_min=thresholds["no_show_grace_min"],
            )
            stay = _stay_minutes(arr_p, dep_p, now_jst)
            alert_level = compute_alert(
                phase=phase,
                arrival_match_status=arrival.match_status if arrival is not None else None,
                arrival_scanned=arr_scanned,
                start_dt=start_dt,
                late_min=thresholds["late_min"],
                stay_minutes=stay,
            )

            arrival_delay_min = (
                round((arr_scanned - start_dt).total_seconds() / 60.0)
                if arr_scanned is not None
                else None
            )

            # 表示用の理由: 未訪問の理由 ?? 到着の理由。
            reason = (ns_p.reason if ns_p is not None else None) or (
                arr_p.reason if arr_p is not None else None
            )

            patient = v.patient
            mvisits.append(
                MonitorVisit(
                    visit_id=v.id,
                    visit_group_id=v.visit_group_id,
                    patient_id=v.patient_id,
                    patient_name=getattr(patient, "name", None) if patient is not None else None,
                    patient_code=getattr(patient, "code", None) if patient is not None else None,
                    patient_lat=(
                        float(patient.lat)
                        if patient is not None and patient.lat is not None
                        else None
                    ),
                    patient_lng=(
                        float(patient.lng)
                        if patient is not None and patient.lng is not None
                        else None
                    ),
                    start_time=_fmt_time(v.start_time),
                    end_time=_fmt_time(v.end_time),
                    phase=phase,
                    alert_level=alert_level,
                    arrival=arr_p,
                    departure=dep_p,
                    no_show=ns_p,
                    stay_minutes=stay,
                    arrival_delay_min=arrival_delay_min,
                    reason=reason,
                )
            )

        # 次訪問までの距離 (時刻順で連続する患者宅間の haversine)。
        for i in range(len(mvisits) - 1):
            cur, nxt = mvisits[i], mvisits[i + 1]
            if (
                cur.patient_lat is not None
                and cur.patient_lng is not None
                and nxt.patient_lat is not None
                and nxt.patient_lng is not None
            ):
                cur.distance_to_next_m = round(
                    haversine_m(
                        cur.patient_lat, cur.patient_lng, nxt.patient_lat, nxt.patient_lng
                    ),
                    1,
                )

        staff_rows.append(
            MonitorStaffRow(
                staff_id=staff_id,
                staff_name=getattr(staff, "name", None) if staff is not None else None,
                office_id=oid,
                office_name=office_name.get(oid) if oid is not None else None,
                course_label=course_label,
                visits=mvisits,
            )
        )

    # 行の並び: 拠点名 → スタッフ名 (担当未設定は末尾)。
    staff_rows.sort(key=lambda r: (r.office_name or "￿", r.staff_name or "￿"))

    # フィルタチップ用の拠点一覧 (当日 visits に登場する拠点)。
    offices = sorted(
        {(r.office_id, r.office_name) for r in staff_rows if r.office_id is not None},
        key=lambda t: t[1] or "",
    )
    monitor_offices = [MonitorOffice(id=oid, name=name or "") for oid, name in offices]

    return MonitorResponse(
        date=target_date,
        now=now,
        thresholds=MonitorThresholds(**thresholds),
        offices=monitor_offices,
        staff=staff_rows,
    )


def _fmt_time(t: time) -> str:
    return t.strftime("%H:%M")


async def find_nearby_patients(
    db: AsyncSession,
    *,
    lat: float,
    lng: float,
    radius_m: float = 150.0,
    limit: int = 5,
) -> NearbyResponse:
    """指定座標付近の active 患者 (lat/lng 有) を距離付きで返す (近隣候補).

    SQL 側で緯度経度のバウンディングボックスに事前絞り込みしてから Python の
    haversine で精密判定する (全患者スキャンの回避)。box は superset になるよう
    経度デルタを cos(lat) で補正する (高緯度ほど経度 1 度が短いため広めに取る)。
    """
    lat_delta = radius_m / 111000.0
    cos_lat = math.cos(math.radians(lat))
    lng_delta = radius_m / (111000.0 * cos_lat) if abs(cos_lat) > 1e-6 else 180.0
    rows = (
        await db.scalars(
            select(Patient).where(
                Patient.status == "active",
                Patient.deleted_at.is_(None),
                Patient.lat.is_not(None),
                Patient.lng.is_not(None),
                Patient.lat.between(lat - lat_delta, lat + lat_delta),
                Patient.lng.between(lng - lng_delta, lng + lng_delta),
            )
        )
    ).all()

    scored: list[NearbyPatient] = []
    for p in rows:
        d = haversine_m(lat, lng, float(p.lat), float(p.lng))
        if d <= radius_m:
            scored.append(
                NearbyPatient(
                    patient_id=p.id,
                    name=p.name,
                    code=p.code,
                    lat=float(p.lat),
                    lng=float(p.lng),
                    distance_m=round(d, 1),
                )
            )
    scored.sort(key=lambda n: n.distance_m)
    return NearbyResponse(items=scored[:limit])
