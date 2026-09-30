"""訪問モニター集計 (実効状態の合成) — QR チェックイン Phase 3.

その日の visits を visit_checkins と突き合わせ、職員ごとに (2026-10-01・
``docs/plans/monitor-staff-rows-design-2026-09-30.md``。2026-07-10〜09-30 はコースごと)
予定 / 到着 / 退出 / 滞在 / 次距離 / 実効状態を組み立てる。コースは訪問ごとの札になる。

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
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff, StaffEvent, StaffWeeklyOverride
from app.models.user import User
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_checkin import VisitCheckin
from app.models.visit_review import VisitReview
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.visit_monitor import (
    MonitorCheckin,
    MonitorCourseTag,
    MonitorDayOverride,
    MonitorOffice,
    MonitorResponse,
    MonitorStaffRow,
    MonitorThresholds,
    MonitorVisit,
    NearbyPatient,
    NearbyResponse,
)
from app.services.accompaniment import resolve_accompaniment_by_visit
from app.services.checkin.actuals import (
    VisitActuals,
    adjustment_payloads,
    load_actuals,
    load_adjuster_names,
    stay_minutes,
)
from app.services.checkin.judge import load_thresholds
from app.services.patient_status_sync import status_since_date
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
    is_substitute: bool = False,
    is_unplanned: bool = False,
) -> str:
    """要対応レベルを合成する (位置判定 + 時間遅延の worst).

    - **reviewed (確認済み) は最優先で none** (要対応トレイから外す / Phase 5-3)。
    - missing 中は missing。
    - 到着あり & match_status==mismatch → mismatch。
    - match_status in (review, no_gps) / 到着遅延 (>= late_min) / 退出忘れ
      (inprogress かつ 滞在 > max_inprogress_min) → review。
    - **代行 (is_substitute) / 予定外 (is_unplanned) は最低 review**
      (設計 ``docs/plans/qr-open-checkin-design.md`` §6: mismatch と同格で
      トレイに載せ、既存の「確認済み」で消す運用に乗せる)。
    - それ以外 (未到着の future/awaiting を含む) → none。

    ``max_inprogress_min`` は checkin_settings の設定値 (無ければ既定 240)。
    """
    if reviewed:
        return ALERT_NONE
    if phase == PHASE_MISSING:
        return ALERT_MISSING
    # 代行 / 予定外は位置・時間が正常でもトレイに載せる (§6)。
    flagged = is_substitute or is_unplanned
    if arrival_match_status is None:
        # 未到着 (future / awaiting)。時間アラートは missing で別途拾う。
        return ALERT_REVIEW if flagged else ALERT_NONE
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
    if arrival_match_status in ("review", "no_gps") or late or long_inprogress or flagged:
        return ALERT_REVIEW
    return ALERT_NONE


def visit_staff_id_set(
    visit: Visit,
    *,
    assignment_staff_ids: Iterable[UUID] = (),
    accompaniment_staff_ids: Iterable[UUID] = (),
) -> set[UUID]:
    """visit の **担当集合** (予定として紐付いたスタッフ全員) を組む.

    正典設計書 ``docs/plans/qr-open-checkin-design.md`` §3 末尾: 代行 (substitute)
    の検出 = 「打刻 (arrival / departure) の staff_id がこの集合に含まれない」。
    集合の定義を通知 producer (打刻 1 件ずつ) とモニター合成 (当日分を一括・
    **いずれかの**到着 / 退出打刻者が外なら代行) で共有し、「代行」の意味が
    2 箇所でズレないようにするための単一ソース。

    ``assignment_staff_ids`` (visit_staff_assignments) と
    ``accompaniment_staff_ids`` (同行) は呼び出し側が解決して渡す
    (1 件引きと一括引きでクエリ形が違うため)。

    同行は**複数名**になり得る (一般化 決定#5)。ここを代表 1 名にすると 2 人目の
    同行者が担当集合から漏れ、正規に同行した人の打刻が「代行」と誤判定されて
    管理者へ誤通知が飛ぶ — 表示は代表 1 名でも、**この集合は全件**を受ける。
    """
    ids = {
        visit.primary_staff_id,
        visit.secondary_staff_id,
        visit.mentor_staff_id,
    }
    ids.update(accompaniment_staff_ids)
    ids.update(assignment_staff_ids)
    return {sid for sid in ids if sid is not None}


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


def staff_code_sort_key(code: str | None, name: str | None) -> tuple:
    """職員コード順の並べ替えキー (FE ``lib/kana-sort.ts`` ``compareByStaffCode`` と同じ規則).

    - コードの数字部分は数値として比べる (S2 < S10)。英字部分は大文字小文字を区別しない。
    - コード未設定は末尾。同順位は氏名。
    職員スケジュールと同じ並びにするためのキー (monitor-staff-rows-design §1)。
    """
    c = (code or "").strip()
    if not c:
        return (1, (), name or "")
    parts = tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p.casefold()) for p in re.findall(r"\d+|\D+", c)
    )
    return (0, parts, name or "")


def office_short(short_label: str | None, office_name: str | None) -> str:
    """拠点の略称 = ``offices.short_label``。未設定なら拠点名の 1 文字目。

    PO 決定 (2026-10-01): 札の略称は現場ボード・コース表・患者 Excel と同じ
    ``short_label`` に揃える (本番: 稲毛=稲・都賀=津)。``short_label`` 自体を
    書き換えると Excel の拠点の対応が崩れるため、表示側を揃える。
    """
    label = (short_label or "").strip()
    return label or (office_name or "")[:1]


def course_tag_label(short: str | None, code: str | None) -> str | None:
    """コースの札 = 拠点の略称 (``office_short``) + コースコード (例「稲D」「津臨2」)。コード無しは None。"""
    if not code:
        return None
    return f"{short or ''}{code}"


def _office_sort_key(sort_order: int | None, name: str | None) -> tuple:
    """拠点の並べ替えキー (``offices.sort_order`` → 名前。sort_order 無しは末尾)。"""
    return (sort_order is None, sort_order or 0, name or "")


async def build_monitor(
    db: AsyncSession,
    target_date: date,
    *,
    office_id: UUID | None = None,
    now: datetime | None = None,
    viewer_is_admin: bool = False,
) -> MonitorResponse:
    """指定日の訪問モニター集計を組み立てる (DB 読み取りのみ).

    ``viewer_is_admin`` = 見ているユーザーが admin か。効くのは 2 か所: 調整した人の名前
    (``adjustments[].by_name``) — スタッフ名の無い調整者は、admin には email / username、
    それ以外には「管理者」と出す — と、休み・時間変更の理由 (``day_override.reason``) —
    admin 以外には null (既定は出さない側)。

    代行 / 実績スタッフの合成規則 (設計 ``qr-open-checkin-design.md`` §6):

    * ``is_substitute`` / ``substitute_staff_*`` = **到着・退出いずれかの**打刻者が
      visit の担当集合の外なら代行 (「到着は担当本人・退出だけ代行」も拾う)。
    * ``actual_staff_*`` = 最新 **arrival** の打刻者 (「到着した人」の表示)。
      arrival が 1 件も無く departure だけがある場合に限り、最新 departure へ
      フォールバックする (実績欄を空にするより打った人を出す)。

    行の組み方 (``docs/plans/monitor-staff-rows-design-2026-09-30.md`` §2):

    * 行 = 職員 (訪問の担当。空ならコース担当。どちらも無ければ末尾の「担当なし」行)。
    * 訪問が無くても、その日のイベント (取消でないもの)・休み・時間変更がある在籍中の職員は行。
    * 同行・副担当はその人の行に ``companion_visit_ids`` として載せる (訪問本体は主担当の行)。
    * 並び = 所属拠点の ``sort_order`` → 職員コード。``office_id`` 指定時は、その拠点の
      訪問を 1 件でも持つ人 (訪問の無い人は所属) だけを返す。
    """
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

    # 同行 (§6.4): その日の訪問群の同行者を 1 度に解決し、行ヘッダ/詳細パネルの
    # 「＋◯◯（同行）」表示に使う。同行リンクは accompaniments が唯一の正典で、
    # ここで JOIN 解決する (visits には書かない)。
    # 解決は**全件** (複数同行・決定的順序)。表示枠は 1 名分しか無いので行の
    # ヘッダには先頭 (= support 優先 → 名前昇順) を出すが、代行判定の担当集合には
    # 全員を渡す (2 人目の同行者を代行と誤判定しないため)。
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, list(visits))

    # 実績 (到着・退出の実績時刻 / 未訪問 / 打刻者) は単一ソース ``actuals.py`` から引く
    # (設計 actual-time-adjust §5)。ここでは ``scanned_at`` を読まない — 実績時刻は
    # 調整があれば調整後、無ければ読取時刻で、phase・遅延・滞在・退出忘れ・ペア補正が
    # すべてこの時刻を基準にする。
    # 代行判定は「最新」ではなく **到着・退出の全打刻者** (``checkin_staff_ids``) を見る:
    #   - 最新だけだと、先に打った代行の事実が担当本人の打ち直しで消える
    #     (通知条件とも食い違う)。
    #   - arrival だけだと「到着は担当本人・退出だけ代行」がモニターに出ない
    #     (通知側は退出でも代行を出すので、こちらだけ黙る不整合になる)。
    # no_show は担当専用の記録なので集めない。並びは新しい順 = 先頭が新しい打刻者。
    actuals_by_visit = await load_actuals(db, visit_ids)
    no_actuals = VisitActuals()
    adjuster_names = await load_adjuster_names(
        db, actuals_by_visit.values(), for_admin=viewer_is_admin
    )

    # 代行検出 (設計 §6): visit の担当集合 (assignments + 同行込み) を 1 クエリで
    # 束ね、**いずれかの** 到着 / 退出打刻者がその外なら代行とみなす。
    assignments_by_visit: dict[UUID, set[UUID]] = defaultdict(set)
    if visit_ids:
        for vid, sid in (
            await db.execute(
                select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
                    VisitStaffAssignment.visit_id.in_(visit_ids)
                )
            )
        ).all():
            assignments_by_visit[vid].add(sid)

    # 打刻者の氏名: staff_id は visits に登場しないスタッフ (代行) のことが
    # あるため別引きする。実績 (actual_staff_*) と代行者 (substitute_staff_*) の
    # 両方がこの 1 マップを引く。
    checkin_staff_id_set: set[UUID] = set()
    for visit_actuals in actuals_by_visit.values():
        checkin_staff_id_set |= set(visit_actuals.checkin_staff_ids)
    checkin_staff_names: dict[UUID, str] = {}
    if checkin_staff_id_set:
        for sid, sname in (
            await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(checkin_staff_id_set)))
        ).all():
            checkin_staff_names[sid] = sname

    # 同住所・同時刻ペア補正: 到着/退出の実績時刻マップを組み、補正後起点を導出する。
    arrival_at: dict[UUID, datetime] = {}
    departure_at: dict[UUID, datetime] = {}
    for vid, visit_actuals in actuals_by_visit.items():
        if visit_actuals.arrival is not None:
            arrival_at[vid] = _as_jst(visit_actuals.arrival.at)
        if visit_actuals.departure is not None:
            departure_at[vid] = _as_jst(visit_actuals.departure.at)
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

    # コース情報 (course_id → code / office_id / コース担当 / 削除済みか)。
    course_ids = {v.course_id for v in visits if v.course_id is not None}
    course_code: dict[UUID, str] = {}
    course_office: dict[UUID, UUID] = {}
    course_assigned: dict[UUID, UUID] = {}
    live_course_ids: set[UUID] = set()
    if course_ids:
        for cid, code, coid, asid, cdeleted in (
            await db.execute(
                select(
                    Course.id,
                    Course.code,
                    Course.office_id,
                    Course.assigned_staff_id,
                    Course.deleted_at,
                ).where(Course.id.in_(course_ids))
            )
        ).all():
            course_code[cid] = code
            course_office[cid] = coid
            if asid is not None:
                course_assigned[cid] = asid
            if cdeleted is None:
                live_course_ids.add(cid)

    # その日のイベント (取消でないもの) と休み・時間変更。訪問が無くても、これがある
    # 在籍中の職員は行にする (monitor-staff-rows-design §2)。イベントは壁時計 (JST) の
    # naive で保存されている (``api/v1/staff_events._combine``) ので naive の 1 日で引く。
    day_start = datetime.combine(target_date, time.min)
    event_staff_ids = set(
        (
            await db.scalars(
                select(StaffEvent.staff_id)
                .where(
                    StaffEvent.starts_at >= day_start,
                    StaffEvent.starts_at < day_start + timedelta(days=1),
                    StaffEvent.cancelled_at.is_(None),
                )
                .distinct()
            )
        ).all()
    )
    iso = target_date.isocalendar()
    day_overrides: dict[UUID, StaffWeeklyOverride] = {
        o.staff_id: o
        for o in (
            await db.scalars(
                select(StaffWeeklyOverride).where(
                    StaffWeeklyOverride.iso_year == iso.year,
                    StaffWeeklyOverride.iso_week == iso.week,
                    StaffWeeklyOverride.weekday == target_date.weekday(),
                )
            )
        ).all()
    }

    # 同行・副担当 (visit_id → 関わる職員)。主担当の行とは別に、その人の行へ薄く出す。
    companion_staff_by_visit: dict[UUID, set[UUID]] = {}
    for v in visits:
        ids = {v.secondary_staff_id, v.mentor_staff_id}
        ids.update(e.staff_id for e in accompaniment_by_visit.get(v.id, []))
        ids.discard(None)
        if ids:
            companion_staff_by_visit[v.id] = {sid for sid in ids if sid is not None}

    # 職員 (行の候補全員を 1 クエリで)。
    candidate_staff_ids: set[UUID] = {
        v.primary_staff_id for v in visits if v.primary_staff_id is not None
    }
    candidate_staff_ids |= set(course_assigned.values())
    candidate_staff_ids |= event_staff_ids | set(day_overrides)
    for sids in companion_staff_by_visit.values():
        candidate_staff_ids |= sids
    staff_by_id: dict[UUID, Staff] = {}
    if candidate_staff_ids:
        staff_by_id = {
            s.id: s
            for s in (
                await db.scalars(select(Staff).where(Staff.id.in_(candidate_staff_ids)))
            ).all()
        }

    def _is_active(sid: UUID) -> bool:
        s = staff_by_id.get(sid)
        return s is not None and s.deleted_at is None and s.status == "active"

    def _row_staff_id(v: Visit) -> UUID | None:
        """行の職員 = 訪問の担当。空ならコース担当 (スマホ「今日の訪問」と同じ規則).

        ``api/v1/visits._course_fallback_staff_ids`` と同じ条件: 主担当 NULL・
        ``manual_staff_override`` でない・コースが未削除・コース担当が在籍中。
        """
        if v.primary_staff_id is not None:
            return v.primary_staff_id
        if v.manual_staff_override or v.course_id is None or v.course_id not in live_course_ids:
            return None
        sid = course_assigned.get(v.course_id)
        return sid if sid is not None and _is_active(sid) else None

    # 拠点 (名前・並び順): コースの拠点 ∪ 職員の所属 ∪ 患者の主担当拠点 (コース無しの札の色)。
    office_ids: set[UUID] = set(course_office.values())
    office_ids |= {s.primary_office_id for s in staff_by_id.values() if s.primary_office_id}
    office_ids |= {
        v.patient.primary_office_id
        for v in visits
        if v.patient is not None and v.patient.primary_office_id is not None
    }
    office_name: dict[UUID, str] = {}
    office_sort: dict[UUID, int | None] = {}
    # 拠点の略称 (札の 1 文字目・凡例)。short_label 未設定は拠点名の 1 文字目。
    office_short_label: dict[UUID, str] = {}
    if office_ids:
        for oid, name, sort_order, short_label in (
            await db.execute(
                select(Office.id, Office.name, Office.sort_order, Office.short_label).where(
                    Office.id.in_(office_ids)
                )
            )
        ).all():
            office_name[oid] = name
            office_sort[oid] = sort_order
            office_short_label[oid] = office_short(short_label, name)

    def _visit_office_id(v: Visit) -> UUID | None:
        """訪問の拠点 = コースの拠点。コース無しは患者の主担当拠点."""
        if v.course_id is not None and course_office.get(v.course_id) is not None:
            return course_office[v.course_id]
        return v.patient.primary_office_id if v.patient is not None else None

    # 1 訪問の状態 (phase・alert・代行・ペア補正・確認済み・実績時刻) は行の組み方に
    # 依らないので、先に全訪問ぶん作ってから行へ配る。
    mvisit_by_id: dict[UUID, MonitorVisit] = {}
    for v in visits:
        actuals = actuals_by_visit.get(v.id, no_actuals)
        arrival_actual = actuals.arrival
        departure_actual = actuals.departure
        # 生の打刻 (位置判定・打刻者・理由)。読み取りの無い退出 (手入力) は None。
        arrival = arrival_actual.checkin if arrival_actual is not None else None
        departure = departure_actual.checkin if departure_actual is not None else None
        no_show = actuals.no_show
        arr_p = _project_checkin(arrival) if arrival is not None else None
        dep_p = _project_checkin(departure) if departure is not None else None
        ns_p = _project_checkin(no_show) if no_show is not None else None

        # 実績スタッフ (最新 arrival の打刻者) と代行判定 (§6)。
        # 代行は **いずれかの** 到着 / 退出打刻者が担当集合の外なら true にする
        # (担当本人が後から打ち直しても代行の事実を消さない = 通知条件と一致)。
        # ただし actual_staff_* は最新の打刻者なので、代行後に担当本人が打ち直すと
        # 「代行バッジ + 担当本人名」の自己矛盾になる。誰が代行したかは
        # substitute_staff_* (担当集合外の打刻者のうち最新 1 名) で別に返す。
        # actual_staff_* は「到着した人」なので最新 arrival が正だが、arrival が
        # 1 件も無い (退出だけ打たれた) ときは最新 departure にフォールバック
        # する (実績欄が空になるより打った人を出す方が読める)。
        actual_source = arrival if arrival is not None else departure
        actual_staff_id = actual_source.staff_id if actual_source is not None else None
        _acc_entries = accompaniment_by_visit.get(v.id, [])
        _assigned = visit_staff_id_set(
            v,
            assignment_staff_ids=assignments_by_visit.get(v.id, set()),
            accompaniment_staff_ids=[e.staff_id for e in _acc_entries],
        )
        # checkin_staff_ids は新しい順 = 先頭が最新の代行者。
        _substitute_ids = [sid for sid in actuals.checkin_staff_ids if sid not in _assigned]
        is_substitute = bool(_substitute_ids)
        substitute_staff_id = _substitute_ids[0] if _substitute_ids else None
        is_unplanned = v.is_unplanned

        start_dt = datetime.combine(v.visit_date, v.start_time, tzinfo=JST)
        # 実績時刻 (JST)。読み取りの無い退出 (手入力) があれば phase は done になる。
        arr_scanned = _as_jst(arrival_actual.at) if arrival_actual is not None else None
        dep_scanned = _as_jst(departure_actual.at) if departure_actual is not None else None
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
        # 滞在分は ``actuals.stay_minutes`` (到着・退出を分に切り捨ててからの差。
        # 進行中は現在時刻まで)。打刻履歴・Excel・A4 と同じ値になる。
        stay = stay_minutes(arr_scanned, dep_scanned, now=now_jst)
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
            is_substitute=is_substitute,
            is_unplanned=is_unplanned,
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

        # コースの札・拠点・担当の食い違い (予定外は予定のコースを語らないので札なし)。
        v_course_id = v.course_id if not is_unplanned else None
        v_office_id = _visit_office_id(v)
        v_course_staff = course_assigned.get(v.course_id) if v.course_id is not None else None
        course_staff_mismatch = (
            v.course_id is not None
            and not v.manual_staff_override
            and v.primary_staff_id is not None
            and v_course_staff is not None
            and v.primary_staff_id != v_course_staff
        )

        patient = v.patient
        mvisit_by_id[v.id] = MonitorVisit(
            visit_id=v.id,
            staff_id=v.primary_staff_id,
            staff_name=(
                getattr(v.primary_staff, "name", None) if v.primary_staff is not None else None
            ),
            accompaniment_staff_name=(_acc_entries[0].staff_name if _acc_entries else None),
            accompaniment_staff_names=[
                e.staff_name for e in _acc_entries if e.staff_name is not None
            ],
            # 実績 (打刻した人) と予定の乖離 (§6)。予定側の担当は書き換えない。
            actual_staff_id=actual_staff_id,
            actual_staff_name=(
                checkin_staff_names.get(actual_staff_id) if actual_staff_id is not None else None
            ),
            # 代行した人 (担当集合外の打刻者のうち最新)。is_substitute の根拠。
            substitute_staff_id=substitute_staff_id,
            substitute_staff_name=(
                checkin_staff_names.get(substitute_staff_id)
                if substitute_staff_id is not None
                else None
            ),
            is_substitute=is_substitute,
            is_unplanned=is_unplanned,
            visit_group_id=v.visit_group_id,
            patient_id=v.patient_id,
            patient_name=getattr(patient, "name", None) if patient is not None else None,
            patient_code=getattr(patient, "code", None) if patient is not None else None,
            # 患者ステータス連動 Phase 3 §3-4 (非破壊追加)。patient は
            # selectinload 済みなので追加クエリは発生しない。
            source=v.source,
            patient_status=(getattr(patient, "status", None) if patient is not None else None),
            patient_status_since=(status_since_date(patient) if patient is not None else None),
            patient_lat=(
                float(patient.lat) if patient is not None and patient.lat is not None else None
            ),
            patient_lng=(
                float(patient.lng) if patient is not None and patient.lng is not None else None
            ),
            start_time=_fmt_time(v.start_time),
            end_time=_fmt_time(v.end_time),
            phase=phase,
            alert_level=alert_level,
            pair_waiting=pair_waiting,
            arrival=arr_p,
            departure=dep_p,
            no_show=ns_p,
            # 実績時刻 (調整後。無ければ読取時刻) と読取時刻・調整の有無。
            arrival_at=arrival_actual.at if arrival_actual is not None else None,
            departure_at=departure_actual.at if departure_actual is not None else None,
            arrival_read_at=(arrival_actual.read_at if arrival_actual is not None else None),
            departure_read_at=(departure_actual.read_at if departure_actual is not None else None),
            arrival_adjusted=arrival_actual is not None and arrival_actual.adjusted,
            departure_adjusted=(departure_actual is not None and departure_actual.adjusted),
            departure_manual=departure_actual is not None and departure_actual.manual,
            adjustments=adjustment_payloads(actuals, adjuster_names),
            stay_minutes=stay,
            arrival_delay_min=arrival_delay_min,
            reason=reason,
            reviewed=reviewed,
            reviewed_by_name=review_entry[1] if review_entry is not None else None,
            reviewed_at=review_entry[0].reviewed_at if review_entry is not None else None,
            review_comment=review_entry[0].comment if review_entry is not None else None,
            course_id=v_course_id,
            course_tag=(
                course_tag_label(
                    office_short_label.get(course_office[v_course_id])
                    if course_office.get(v_course_id) is not None
                    else None,
                    course_code.get(v_course_id),
                )
                if v_course_id is not None
                else None
            ),
            course_office_id=v_office_id,
            course_office_name=office_name.get(v_office_id) if v_office_id else None,
            course_staff_mismatch=course_staff_mismatch,
        )

    # ── 行 = 職員 (monitor-staff-rows-design §2) ──
    # 行キー = 訪問の担当 (visits.primary_staff_id = スマホ「今日の訪問」と同じ源)。
    # 空ならコース担当へフォールバック。どちらも無い訪問は「担当なし」行 (キー None)。
    # MonitorVisit.staff_id は primary_staff_id のまま (意味を変えない)。
    own_visits: dict[UUID | None, list[Visit]] = defaultdict(list)
    for v in visits:
        own_visits[_row_staff_id(v)].append(v)
    # 2 名体制 (Layer3): 組 (visit_group_id) の両方の訪問に副担当が入る (A=主X・副Y、
    # B=主Y・副X)。組の訪問を自分の行に持つ人には、その組を同行として重ねて出さない。
    # カイポケ取込の「1 訪問に主と副」(visit_group_id 無し) は従来どおり同行に出す。
    own_group_ids: dict[UUID, set[UUID]] = defaultdict(set)
    for sid, vs in own_visits.items():
        if sid is not None:
            own_group_ids[sid] = {v.visit_group_id for v in vs if v.visit_group_id is not None}
    companion_ids: dict[UUID, list[UUID]] = defaultdict(list)
    for v in visits:
        owner = _row_staff_id(v)
        for sid in sorted(companion_staff_by_visit.get(v.id, set()), key=str):
            s = staff_by_id.get(sid)
            if sid == owner or s is None or s.deleted_at is not None:
                continue
            if v.visit_group_id is not None and v.visit_group_id in own_group_ids.get(sid, ()):
                continue
            companion_ids[sid].append(v.id)

    row_staff_ids: set[UUID] = {sid for sid in own_visits if sid is not None}
    row_staff_ids |= set(companion_ids)
    # 訪問が無くてもイベント・休み・時間変更がある在籍中の職員。
    row_staff_ids |= {sid for sid in event_staff_ids | set(day_overrides) if _is_active(sid)}
    row_keys: list[UUID | None] = [sid for sid in row_staff_ids if sid in staff_by_id]
    if None in own_visits:
        row_keys.append(None)

    staff_rows_with_key: list[tuple[tuple, MonitorStaffRow]] = []
    for sid in row_keys:
        staff = staff_by_id.get(sid) if sid is not None else None
        row_office = staff.primary_office_id if staff is not None else None
        mine = sorted(own_visits.get(sid, []), key=lambda v: (v.start_time, v.end_time))

        # 拠点の絞り込み: その拠点の訪問を 1 件でも持つ人 (行の中身はその人の 1 日全部)。
        # 訪問の無い人は所属で判定。
        if office_id is not None:
            if mine:
                if not any((_visit_office_id(v) or row_office) == office_id for v in mine):
                    continue
            elif row_office != office_id:
                continue

        mvisits = [mvisit_by_id[v.id] for v in mine]

        # 次訪問までの距離 = その人の 1 日の時刻順 (担当なし行は 1 人の順路ではないので出さない)。
        if sid is not None:
            for cur, nxt in zip(mvisits, mvisits[1:], strict=False):
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

        # コースの札 (重複なし・初出順)。
        tags: list[MonitorCourseTag] = []
        seen_courses: set[UUID] = set()
        for mv in mvisits:
            if mv.course_id is None or mv.course_tag is None or mv.course_id in seen_courses:
                continue
            seen_courses.add(mv.course_id)
            tags.append(
                MonitorCourseTag(
                    label=mv.course_tag,
                    course_id=mv.course_id,
                    office_id=mv.course_office_id,
                    office_name=mv.course_office_name,
                )
            )

        override = day_overrides.get(sid) if sid is not None else None
        row = MonitorStaffRow(
            staff_id=sid,
            staff_name=staff.name if staff is not None else None,
            staff_ids=[sid] if sid is not None else [],
            office_id=row_office,
            office_name=office_name.get(row_office) if row_office is not None else None,
            course_tags=tags,
            visits=mvisits,
            companion_visit_ids=companion_ids.get(sid, []) if sid is not None else [],
            day_override=(
                MonitorDayOverride(
                    kind=override.override_type,
                    start_time=_fmt_time(override.start_time) if override.start_time else None,
                    end_time=_fmt_time(override.end_time) if override.end_time else None,
                    # 休みの理由は admin だけに返す (他人の休みの一覧 overrides-week も
                    # admin 専用。モニターは staff も見られる)。
                    reason=override.reason if viewer_is_admin else None,
                )
                if override is not None
                else None
            ),
        )
        # 並び: 所属拠点の sort_order → 職員コード (職員スケジュールと同じ)。担当なしは末尾。
        sort_key = (
            (
                1,
                (),
                (),
            )
            if sid is None
            else (
                0,
                (row_office is None,)
                + _office_sort_key(
                    office_sort.get(row_office) if row_office else None,
                    office_name.get(row_office) if row_office else None,
                ),
                staff_code_sort_key(staff.code if staff else None, row.staff_name),
            )
        )
        staff_rows_with_key.append((sort_key, row))

    staff_rows_with_key.sort(key=lambda item: item[0])
    staff_rows = [row for _key, row in staff_rows_with_key]

    # フィルタチップ用の拠点 = 当日の訪問のコース拠点 ∪ 行の職員の所属拠点。
    chip_office_ids = {r.office_id for r in staff_rows if r.office_id is not None}
    chip_office_ids |= {
        mv.course_office_id for r in staff_rows for mv in r.visits if mv.course_office_id
    }
    monitor_offices = [
        MonitorOffice(
            id=oid,
            name=office_name.get(oid, ""),
            short_label=office_short_label.get(oid) or None,
        )
        for oid in sorted(
            chip_office_ids,
            key=lambda oid: _office_sort_key(office_sort.get(oid), office_name.get(oid)),
        )
    ]

    # 札の色の基準 = 拠点マスタの順 (その日に出る拠点に依らない)。削除済みでも
    # その日の訪問が指す拠点は含める (札が中立色に落ちないように)。
    office_order = [
        oid
        for oid, _sort, _name in sorted(
            (
                await db.execute(
                    select(Office.id, Office.sort_order, Office.name).where(
                        or_(Office.deleted_at.is_(None), Office.id.in_(office_ids))
                    )
                )
            ).all(),
            key=lambda r: (*_office_sort_key(r[1], r[2]), str(r[0])),
        )
    ]

    return MonitorResponse(
        date=target_date,
        now=now,
        thresholds=MonitorThresholds(**thresholds),
        offices=monitor_offices,
        office_order=office_order,
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
