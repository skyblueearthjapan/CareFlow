"""訪問モニター集計 (実効状態の合成) — QR チェックイン Phase 3.

その日の visits を visit_checkins と突き合わせ、コースごとに (2026-07-10 PO要望。
旧: スタッフごと) 予定 / 到着 / 退出 / 滞在 / 次距離 / 実効状態を組み立てる。

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
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_checkin import VisitCheckin
from app.models.visit_review import VisitReview
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
from app.services.trainee_accompaniment import resolve_accompaniment_by_visit
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
# Phase 4 で checkin_settings (max_inprogress_min) 化済。この定数は設定が無い場合の
# コード既定値 (= DEFAULT_THRESHOLDS["max_inprogress_min"]) として残す。
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
    effective_start_dt: datetime | None = None,
) -> str:
    """時間ベースの進捗を合成する (設計 §3 の合意アルゴリズム).

    全ての datetime は JST aware。``start_dt`` は visit_date + start_time。

    ``effective_start_dt`` は同住所・同時刻ペアの補正後起点 (相方の退出時刻等)。
    未訪問 (missing) 判定の起点だけをこの補正後起点に置き換える。future/awaiting
    の切り替えは従来どおり ``start_dt`` (予定開始) を使う (補正で予定より前が
    future に化けないように)。None なら ``start_dt`` と同一 = 従来挙動。
    """
    eff_start = effective_start_dt if effective_start_dt is not None else start_dt
    if arrival_scanned is None:
        if has_no_show:
            return PHASE_MISSING
        if now >= eff_start + timedelta(minutes=grace_min):
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
    max_inprogress_min: int = MAX_INPROGRESS_MIN,
    reviewed: bool = False,
    effective_start_dt: datetime | None = None,
) -> str:
    """要対応レベルを合成する (位置判定 + 時間遅延の worst).

    - **reviewed (確認済み) は最優先で none** (要対応トレイから外す / Phase 5-3)。
    - missing 中は missing。
    - 到着あり & match_status==mismatch → mismatch。
    - match_status in (review, no_gps) / 到着遅延 (>= late_min) / 退出忘れ
      (inprogress かつ 滞在 > max_inprogress_min) → review。
    - それ以外 (未到着の future/awaiting を含む) → none。

    ``max_inprogress_min`` は checkin_settings の設定値 (無ければ既定 240)。
    """
    if reviewed:
        return ALERT_NONE
    if phase == PHASE_MISSING:
        return ALERT_MISSING
    if arrival_match_status is None:
        # 未到着 (future / awaiting)。時間アラートは missing で別途拾う。
        return ALERT_NONE
    if arrival_match_status == "mismatch":
        return ALERT_MISMATCH
    # 到着遅延の起点は同住所・同時刻ペア補正後起点 (相方の退出時刻等)。None なら予定開始。
    eff_start = effective_start_dt if effective_start_dt is not None else start_dt
    late = arrival_scanned is not None and (arrival_scanned - eff_start) >= timedelta(
        minutes=late_min
    )
    # 退出忘れ: 到着済・退出未記録のまま長時間 inprogress (退出未記録の可能性)。
    long_inprogress = (
        phase == PHASE_INPROGRESS and stay_minutes is not None and stay_minutes > max_inprogress_min
    )
    if arrival_match_status in ("review", "no_gps") or late or long_inprogress:
        return ALERT_REVIEW
    return ALERT_NONE


def _visit_duration_min(v: Visit) -> int:
    """visit の予定所要分 (end_time - start_time, JST 壁時計の分差)。負値は 0。"""
    s = v.start_time.hour * 60 + v.start_time.minute
    e = v.end_time.hour * 60 + v.end_time.minute
    return max(0, e - s)


def compute_pair_effective_starts(
    visits: Sequence[Visit],
    arrival_at: dict[UUID, datetime],
    departure_at: dict[UUID, datetime],
) -> dict[UUID, datetime]:
    """同住所・同時刻ペアの「補正後起点」を visit_id ごとに返す (対称補正).

    ペア定義 (PO 合意 / 現物調査で確定):
      ``visit_group_id`` は **2 名体制 (同一患者に 2 スタッフ)** のキーであり
      「同住所 2 患者ペア」ではない。同住所・同時刻ペアはこの codebase では
      レスポンス時に座標で導出する概念のため、ここでは保守的に
      **同 primary_staff_id (未割当同士は同 course_id)・同 start_time・患者座標一致
      (lat/lng を小数 6 桁で丸め)・患者が別 (patient_id が異なる)** で束ねる。

    補正: ペアの一方 B に到着 QR が無いとき、相方 A に到着があれば B の起点を
      ``max(予定開始, A の退出時刻 ?? (A の到着時刻 + A の予定所要分))`` に置き換える。
      対称: どちらが先に読まれても未読側が既読側を起点にする。B が既に到着済み
      (遅延判定用) の場合は「B より前に到着した相方」のみ起点候補にする
      (先攻の真の遅延を隠さないため)。

    返却は **補正が実際に起点を後ろへ動かした visit のみ** (= 予定開始より後)。
    それ以外 (相方未到着・非ペア) はキー無し = 従来どおり予定開始を使う。
    """
    # (staff_key, start_time, coord6) でグルーピング。
    groups: dict[tuple[object, time, tuple[float, float]], list[Visit]] = defaultdict(list)
    for v in visits:
        p = v.patient
        if p is None or p.lat is None or p.lng is None:
            continue
        coord = (round(float(p.lat), 6), round(float(p.lng), 6))
        # レビュー指摘 (誤ペア化ガード): (0,0) は未設定の既定値でありうるため
        # ペア判定に使わない (無関係患者を束ねて本物の未訪問を隠さない)。
        if coord == (0.0, 0.0):
            continue
        staff_key: object = (
            v.primary_staff_id if v.primary_staff_id is not None else ("course", v.course_id)
        )
        groups[(staff_key, v.start_time, coord)].append(v)

    eff: dict[UUID, datetime] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        for v in members:
            v_start = datetime.combine(v.visit_date, v.start_time, tzinfo=JST)
            v_arr = arrival_at.get(v.id)
            best_finish: datetime | None = None
            for partner in members:
                if partner.id == v.id or partner.patient_id == v.patient_id:
                    continue
                p_arr = arrival_at.get(partner.id)
                if p_arr is None:
                    continue
                # v が到着済み (遅延判定) の場合は「v より先に来た相方」のみ起点候補。
                # v が未到着 (未訪問判定) の場合はどの既読相方も起点候補。
                if v_arr is not None and not (p_arr < v_arr):
                    continue
                p_dep = departure_at.get(partner.id)
                finish = (
                    p_dep
                    if p_dep is not None
                    else p_arr + timedelta(minutes=_visit_duration_min(partner))
                )
                if best_finish is None or finish > best_finish:
                    best_finish = finish
            if best_finish is not None and best_finish > v_start:
                eff[v.id] = best_finish
    return eff


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


def _course_label_first_code(label: str | None) -> str:
    """course_label ("Aコース" / "A/Bコース") から並べ替え用の先頭コースコードを取り出す。

    コース無し (担当未設定など) は "￿" を返し、拠点内の末尾に並ぶ。
    行の並び (拠点 → コース → スタッフ名) をスケジュール本体と揃えるためのキー。
    """
    if not label:
        return "￿"
    return label.split("/", 1)[0].removesuffix("コース") or "￿"


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

    # 新人同行 (§6.4): その日の訪問群の同行者を 1 度に解決し、行ヘッダ/詳細パネルの
    # 「＋◯◯（同行）」表示に使う。同行リンクは trainee_accompaniments が唯一の正典で、
    # ここで JOIN 解決する (visits には書かない)。
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, list(visits))

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

    # 同住所・同時刻ペア補正: 到着/退出の実効時刻マップを組み、補正後起点を導出する。
    arrival_at: dict[UUID, datetime] = {}
    departure_at: dict[UUID, datetime] = {}
    for (vid, kind), row in latest.items():
        if kind == "arrival":
            arrival_at[vid] = _as_jst(row.scanned_at)
        elif kind == "departure":
            departure_at[vid] = _as_jst(row.scanned_at)
    pair_eff = compute_pair_effective_starts(visits, arrival_at, departure_at)

    # 「確認済み」(visit 単位の review) を 1 クエリで取得。確認者名は
    # users → staff を outer join で解決 (staff 未紐付け / 退職は email/None に fallback)。
    reviews: dict[UUID, tuple[VisitReview, str | None]] = {}
    if visit_ids:
        review_rows = (
            await db.execute(
                select(VisitReview, User, Staff)
                .outerjoin(User, VisitReview.reviewed_by == User.id)
                .outerjoin(Staff, User.staff_id == Staff.id)
                .where(VisitReview.visit_id.in_(visit_ids))
            )
        ).all()
        for review, user, staff in review_rows:
            name = (
                staff.name
                if staff is not None
                else ((user.email or user.username) if user is not None else None)
            )
            reviews[review.visit_id] = (review, name)

    # コース情報 (course_id → code / office_id / スケジュール側のコース担当)。
    course_ids = {v.course_id for v in visits if v.course_id is not None}
    course_code: dict[UUID, str] = {}
    course_office: dict[UUID, UUID] = {}
    course_assigned: dict[UUID, UUID] = {}
    if course_ids:
        for cid, code, coid, asid in (
            await db.execute(
                select(Course.id, Course.code, Course.office_id, Course.assigned_staff_id).where(
                    Course.id.in_(course_ids)
                )
            )
        ).all():
            course_code[cid] = code
            course_office[cid] = coid
            if asid is not None:
                course_assigned[cid] = asid

    # コース担当のスタッフ名 (visits に登場しないスタッフでも表示できるよう別引き)。
    course_staff_names: dict[UUID, str] = {}
    if course_assigned:
        for sid, sname in (
            await db.execute(
                select(Staff.id, Staff.name).where(Staff.id.in_(set(course_assigned.values())))
            )
        ).all():
            course_staff_names[sid] = sname

    # 行 = コース単位 (2026-07-10 PO要望。旧: スタッフ単位)。
    # 1 人が複数コースを掛け持ちしてもコースごとに 1 行になり、
    # スケジュール本体 (拠点→コース列) と同じ読み順で追える。
    # コース無し visit は従来どおり担当スタッフ単位 (どちらも無ければ "" で 1 行)。
    by_row: dict[tuple[str, UUID | str], list[Visit]] = defaultdict(list)
    for v in visits:
        if v.course_id is not None:
            group_key: tuple[str, UUID | str] = ("course", v.course_id)
        elif v.primary_staff_id is not None:
            group_key = ("staff", v.primary_staff_id)
        else:
            group_key = ("none", "")
        by_row[group_key].append(v)

    # 拠点名解決: コースの office_id ∪ スタッフの primary_office_id (フォールバック用)。
    office_ids = set(course_office.values())
    office_ids |= {
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
    for group_key, staff_visits in by_row.items():
        kind, ident = group_key

        # 時刻順 (next 距離計算のため)。
        staff_visits = sorted(staff_visits, key=lambda v: (v.start_time, v.end_time))

        # 行内の担当スタッフ (訪問の登場順で重複排除)。
        # visits.primary_staff_id はモバイル「今日の訪問」と同一ソースのため、
        # モニターの行に出る担当とスタッフ端末の表示は常に一致する。
        row_staff: dict[UUID, Staff] = {}
        for v in staff_visits:
            if v.primary_staff_id is not None and v.primary_staff is not None:
                row_staff.setdefault(v.primary_staff_id, v.primary_staff)

        course_id: UUID | None = None
        if kind == "course" and isinstance(ident, UUID):
            course_id = ident
            code = course_code.get(course_id)
            course_label = f"{code}コース" if code else None
            oid = course_office.get(course_id)
        else:
            codes = sorted(
                {course_code[v.course_id] for v in staff_visits if v.course_id in course_code}
            )
            course_label = "/".join(f"{c}コース" for c in codes) if codes else None
            first_staff = next(iter(row_staff.values()), None)
            oid = first_staff.primary_office_id if first_staff is not None else None

        # 拠点フィルタ: 指定があれば一致行のみ残す。
        if office_id is not None and oid != office_id:
            continue

        staff_ids = list(row_staff.keys())
        staff_names = [s.name for s in row_staff.values() if s.name]
        staff_id = staff_ids[0] if len(staff_ids) == 1 else None
        staff_name = "・".join(staff_names) if staff_names else None

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
            # 同住所・同時刻ペア補正後起点 (無ければ予定開始と同一)。
            effective_start = pair_eff.get(v.id)

            phase = compute_phase(
                arrival_scanned=arr_scanned,
                departure_scanned=dep_scanned,
                has_no_show=no_show is not None,
                start_dt=start_dt,
                now=now_jst,
                grace_min=thresholds["no_show_grace_min"],
                effective_start_dt=effective_start,
            )
            stay = _stay_minutes(arr_p, dep_p, now_jst)
            review_entry = reviews.get(v.id)
            reviewed = review_entry is not None
            alert_level = compute_alert(
                phase=phase,
                arrival_match_status=arrival.match_status if arrival is not None else None,
                arrival_scanned=arr_scanned,
                start_dt=start_dt,
                late_min=thresholds["late_min"],
                stay_minutes=stay,
                max_inprogress_min=thresholds["max_inprogress_min"],
                reviewed=reviewed,
                effective_start_dt=effective_start,
            )
            # ペア待ち: 予定 + grace は過ぎたが、ペア補正で awaiting に留まっている間。
            pair_waiting = (
                phase == PHASE_AWAITING
                and effective_start is not None
                and now_jst >= start_dt + timedelta(minutes=thresholds["no_show_grace_min"])
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
            _acc = accompaniment_by_visit.get(v.id)
            mvisits.append(
                MonitorVisit(
                    visit_id=v.id,
                    staff_id=v.primary_staff_id,
                    staff_name=(
                        getattr(v.primary_staff, "name", None)
                        if v.primary_staff is not None
                        else None
                    ),
                    accompaniment_staff_name=_acc[1] if _acc is not None else None,
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
                    pair_waiting=pair_waiting,
                    arrival=arr_p,
                    departure=dep_p,
                    no_show=ns_p,
                    stay_minutes=stay,
                    arrival_delay_min=arrival_delay_min,
                    reason=reason,
                    reviewed=reviewed,
                    reviewed_by_name=review_entry[1] if review_entry is not None else None,
                    reviewed_at=review_entry[0].reviewed_at if review_entry is not None else None,
                    review_comment=review_entry[0].comment if review_entry is not None else None,
                )
            )

        # 次訪問までの距離 (時刻順で連続する患者宅間の haversine)。
        # 行=コースでも従来の意味「同スタッフの次の訪問まで」を保つため、
        # 行内を担当スタッフごとのチェーンに分けて計算する。
        chains: dict[UUID | None, list[MonitorVisit]] = defaultdict(list)
        for mv in mvisits:
            chains[mv.staff_id].append(mv)
        for chain in chains.values():
            for i in range(len(chain) - 1):
                cur, nxt = chain[i], chain[i + 1]
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

        course_staff_id = course_assigned.get(course_id) if course_id is not None else None
        staff_rows.append(
            MonitorStaffRow(
                course_id=course_id,
                course_staff_id=course_staff_id,
                course_staff_name=(
                    course_staff_names.get(course_staff_id) if course_staff_id is not None else None
                ),
                staff_id=staff_id,
                staff_name=staff_name,
                staff_ids=staff_ids,
                office_id=oid,
                office_name=office_name.get(oid) if oid is not None else None,
                course_label=course_label,
                visits=mvisits,
            )
        )

    # 行の並び: 拠点名 → コース (A,B,C,..,M) → スタッフ名。
    # スケジュール本体 (拠点 → コース順の列) と同じ読み順に揃える (PO要望 2026-07-10。
    # 旧: 拠点 → スタッフ名 順で、コースで見ると順不同・両拠点の「A」が離れて見えた)。
    staff_rows.sort(
        key=lambda r: (
            r.office_name or "￿",
            _course_label_first_code(r.course_label),
            r.staff_name or "￿",
        )
    )

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
