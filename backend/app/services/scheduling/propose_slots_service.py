"""提案 API (Phase2-2) オーケストレーション層.

``POST /api/v1/schedule/v2/propose-slots`` の本体ロジック:
    1. 対象週 × 拠点の実 Visit を 1 回ロードし、(office, weekday, course_code)
       単位の既存訪問列・担当スタッフ・拠点名を構築する (N+1 回避).
    2. 各 (拠点開講コース × 候補希望曜日) について Stage1 純ソルバ
       (``find_available_slots_for_candidate``) を呼び、実現可能な開始時刻のみ取得.
    3. 移動効率 (近接) / 希望適合 / 空きの平準化 を合成スコアでランキング.
       同住所ペア成立は最優先.
    4. 各スロットに理由バッジ・警告・ミニスケジュールを付与して返す.

read-only: DB は読むだけ. 実現不能な時刻は一切返さない (ソルバが弾く).

設計方針:
    - 既存ローダ (schedule.py の ``Visit JOIN Course OUTER JOIN Staff`` 集計) と
      同じ手法で当週 visit を読む.
    - 距離 / 移動 / バッファー / 昼休み / 18:00 / 容量 / time_type / 同住所は
      すべて ``proposal_solver`` (= auto_allocator_v2 と同手法) に委譲する.
    - 昼休み window は自動割当と同じ ``compute_lunch_window`` をコース単位で算出.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff, StaffEvent, StaffShift, StaffWeeklyOverride
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.patient_excel.schema import OFFICE_CODE_TO_SHORT
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_DURATION_PREFERRED as _LUNCH_DURATION_PREFERRED,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_EARLIEST_START as _LUNCH_EARLIEST_START,
)
from app.services.scheduling.auto_allocator_v2 import (
    LUNCH_LATEST_END as _LUNCH_LATEST_END,
)
from app.services.scheduling.auto_allocator_v2 import (
    NOON_HOUR,
    _add_minutes,
    _address_bucket,
    compute_lunch_window,
    haversine_km,
)
from app.services.scheduling.auto_allocator_v2 import (
    V2Visit as _V2Visit,
)
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG, SchedulingConfig
from app.services.scheduling.constants import (
    COURSE_MAX_MINUTES as _COURSE_MAX_MINUTES,
)
from app.services.scheduling.constants import (
    MAX_PATIENTS_PER_COURSE as _MAX_PATIENTS_PER_COURSE,
)

# N-3: 性別ハード制約の解釈は Layer3 割当と単一ソースにする (AND 意味論 /
# 'female_only'→'female' 正規化). layer3_assignment は propose_slots_service を
# import しないため循環にならない (import 方向: propose → layer3 の一方向).
# 公開エイリアス (sex_satisfies_restrictions) 経由でプライベート関数を直接参照しない.
from app.services.scheduling.layer3_assignment import (
    sex_satisfies_restrictions as _sex_satisfies_restrictions,
)
from app.services.scheduling.proposal_solver import (
    Candidate,
    EventWindow,
    ExistingVisit,
    Slot,
    _course_total_minutes_from_existing,
    find_available_slots_for_candidate,
    slot_fits_exact,
)

# ランキング合成スコアの重み.
# 移動効率 (近接): そのコース既存訪問への最小距離が小さいほど高.
# 希望適合: 希望曜日 / 時間帯 / time_type 一致で加点.
# 空きの平準化: コース残容量 (件数 + 分) が多いほど高.
_W_PROXIMITY: float = 50.0
_W_PREFERENCE: float = 30.0
_W_BALANCE: float = 20.0
# 同住所ペア成立は最優先で大きなボーナスを与える.
_PAIR_BONUS: float = 1000.0
# W-12a: 2 名体制ペア成立ボーナス. 同住所ペア (1000) より下位・通常候補より上位に置き、
# 同住所ペアとの併発時は同住所が勝つ既存序列を維持する.
_TWO_STAFF_BONUS: float = 800.0
# 近接スコアの飽和距離 (km). これ以上離れると近接スコア 0.
_PROXIMITY_SAT_KM: float = 5.0

# P-1a: 厳密限界コスト (delta) を計算する上位候補数. 従来の加点スコアで粗ランキングした
# 上位 DELTA_EVAL_LIMIT 件のみ delta を厳密計算し、delta 昇順に再ソートする (計算量2段構え).
# それ未満の下位候補は marginal_cost_minutes=None のまま従来順で末尾に残す.
DELTA_EVAL_LIMIT: int = 20

# Phase G-88: コース容量定数は ``constants`` に単一ソース化.
# 既存ローカル名 (``_COURSE_MAX_MINUTES`` = 480 / ``_MAX_PATIENTS_PER_COURSE`` = 6) は
# 上部の import 別名で維持される.


def _fmt_hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


@dataclass
class _CourseBucket:
    """対象週 1 コース (office × weekday × course_code) の集計."""

    office_id: UUID
    weekday: int
    course_code: str
    office_code: str | None = None
    staff_name: str | None = None
    # W-12a: 2 名体制ペアの相方 slot1 採用 (A経路) で FE が使う course_template_id.
    # Course.template_id を load 時に控える (無ければ None). 後方互換 default None.
    course_template_id: UUID | None = None
    # N-3: 割付スタッフ実態判定用. assigned_staff_id が None なら staff_unassigned.
    # staff_sex は性別不適合判定 (candidate.sex_restriction と突合) に使う.
    # staff_absent は当該 weekday の非番/当週休みを load 時に事前計算した結果.
    assigned_staff_id: UUID | None = None
    staff_sex: str | None = None
    staff_absent: bool = False
    # NG スタッフ (patient_ng_staff / 設計書 §6): このコースの割付スタッフを NG 指定して
    # いる患者 ID 集合 (逆引き). load 時に ix_patient_ng_staff_staff で 1 クエリ一括ロード
    # する. 候補患者がこの集合に居れば staff_ng_mismatch (除外はせず警告 + 降格).
    ng_patient_ids: frozenset[UUID] = frozenset()
    visits: list[_V2Visit] = field(default_factory=list)
    # イベント考慮2段階提案 (PO確定 2026-07-27): 割付スタッフの当該曜日の
    # staff_events (カイポケ取り込み含む) 占有窓. solver へそのまま渡す.
    event_windows: list[EventWindow] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateInput:
    """提案算出に渡す候補患者 (座標確定済み)."""

    lat: float
    lng: float
    service_minutes: int
    time_type: str | None
    preferred_start: time | None
    preferred_end: time | None
    preferred_weekdays: frozenset[int]
    requires_multiple_staff: bool
    existing_patient_id: UUID | None
    # ③ 表示統一: 提案行 (is_here) の色分け用. 候補患者自身の性別制限.
    sex_restriction: str | None = None


@dataclass
class ProposedSlot:
    """ランキング済み提案スロット (API schema に詰める前の内部表現)."""

    office_id: UUID
    office_name: str | None
    weekday: int
    course_code: str
    course_label: str
    staff_name: str | None
    start: time
    end: time
    score: float
    reasons: list[str]
    warnings: list[str]
    is_pair: bool
    pair_partner: str | None
    mini_schedule: list[dict[str, object]]
    # P3-④ (効率優先の代替枠): time_type/曜日のハード制約を外して再列挙し、通常候補と
    # 重複しない「希望外だが効率的」な枠を後付けしたものは True. 既定 False で従来と不変.
    is_efficiency_alternative: bool = False
    # P-1a: 挿入の厳密限界コスト (分/週) = course_travel_buffer_total(挿入後) −
    # (挿入前). improvement_engine の compute_exact_marginal 再利用 (正典・コピー禁止).
    # 上位 DELTA_EVAL_LIMIT 件のみ厳密計算し、それ以外の下位候補は None のまま.
    marginal_cost_minutes: float | None = None
    # 定員超過の管理者相談プロセス (方式b): base 定員では入らないが定員 +1 なら入る
    # 「定員超過候補」か. ``compute_overcapacity_slots`` の結果のみ True になる.
    overcapacity: bool = False
    # W-12a (2 名体制ペア D-1/D-2): このスロットが slot0+slot1 同時刻・別コースの原子ペア
    # 候補なら相方 (slot1) 情報を持つ (後方互換 optional・非ペア候補は全て None).
    # A経路採用は self の course_template_id を slot0、partner_course_template_id を slot1 と
    # して同時刻の2行を書く. delta は 2コース独立 compute_exact_marginal の合計.
    partner_course_code: str | None = None
    partner_course_label: str | None = None
    partner_course_template_id: UUID | None = None
    partner_staff_name: str | None = None
    partner_mini_schedule: list[dict[str, object]] | None = None
    # イベント考慮2段階提案: フォールバック枠 (パスB) が重なるソフトイベント.
    # 空ならクリーン枠. [{"title": ..., "start": "HH:MM", "end": "HH:MM"}].
    event_conflicts: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class _BucketExclusion:
    """1 コース (office × weekday × course_code) が候補 0 件だった生の除外理由 (集約前)."""

    reason: str
    weekday: int
    course_code: str


@dataclass
class ExcludedReasonSummary:
    """P-1b: 候補 0 件時の除外理由の集約 1 件 (reason × weekday でグルーピング).

    reason 語彙 (N-6「黙って消さない」): ``capacity_full`` (容量上限) / ``lunch_window``
    (昼休み重複) / ``travel_shortage`` (移動時間不足) / ``no_gap`` (空きギャップなし) /
    ``course_closed`` (希望曜日に開講コース無し) / ``pair_blocked`` (I-11: pair_mode=blocked
    のペア相手と同時間帯=90分同住所占有で重なるため除外) / ``no_pair_slot`` (W-12a D-2:
    2 名体制で slot0 は入るが同時刻に入れる相方コースが見つからない).
    """

    reason: str
    count: int
    weekday: int
    sample_course_code: str | None


# 集約時の理由優先度 (1 コースが複数地点で候補を落とした場合、代表理由を決定的に選ぶ).
# capacity_full を最優先: 容量上限は「そもそも走査に入れない」根本原因のため.
# I-11: pair_blocked は同住所同時刻ペアの hard 除外なので capacity_full の次に強い.
# W-12a: no_pair_slot は「slot0 は入るが相方全滅」= 2 名体制固有の詰まりなので no_gap の上.
_EXCLUSION_REASON_PRIORITY: tuple[str, ...] = (
    "capacity_full",
    "pair_blocked",
    "travel_shortage",
    "lunch_window",
    "no_pair_slot",
    "no_gap",
)


def _pick_bucket_reason(sink: list[str]) -> str:
    """1 コースの solver 除外コード列から代表理由を決定的に選ぶ (優先度順. 空なら no_gap)."""
    for reason in _EXCLUSION_REASON_PRIORITY:
        if reason in sink:
            return reason
    return "no_gap"


async def load_week_course_buckets(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
) -> tuple[dict[tuple[UUID, int, str], _CourseBucket], dict[UUID, str], dict[UUID, str | None]]:
    """対象週 × 拠点の実 Visit を 1 回ロードし、コース単位に集計する.

    schedule.py の ``Visit JOIN Course OUTER JOIN Staff`` と同じ読み方:
        - planned かつ未削除の visit のみ.
        - Course (= 当該週 weekday/code/office, 担当 staff) 経由でグルーピング.
        - patient 座標 (lat/lng) は Patient から引く (visit には無い).

    Returns:
        ``({(office_id, weekday, course_code): _CourseBucket}, {office_id: name},
        {office_id: code})``. ``code`` は course_label 用の正準短縮
        (OFFICE_CODE_TO_SHORT) を引くために使う (board と同一短縮へ統一).
    """
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        week_sunday_plus1 = date.fromisocalendar(iso_year, iso_week, 7)
    except ValueError as exc:
        raise ValueError(f"無効な ISO 週: {iso_year}-W{iso_week}") from exc
    # week_sunday_plus1 を排他上限にするため +1 日.
    week_upper = week_sunday_plus1 + timedelta(days=1)

    stmt = (
        select(Visit, Course, Staff)
        .join(Course, Course.id == Visit.course_id)
        .outerjoin(Staff, Staff.id == Course.assigned_staff_id)
        .where(
            Visit.deleted_at.is_(None),
            Visit.status == VISIT_STATUS_PLANNED,
            Visit.visit_date >= week_monday,
            Visit.visit_date < week_upper,
            Course.deleted_at.is_(None),
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
        )
    )
    if office_ids:
        stmt = stmt.where(Course.office_id.in_(office_ids))

    rows = (await db.execute(stmt)).all()

    # patient 座標を 1 回でまとめてロード.
    patient_ids = {v.patient_id for (v, _c, _s) in rows}
    patients_by_id: dict[UUID, Patient] = {}
    if patient_ids:
        prows = await db.scalars(
            select(Patient).where(
                Patient.id.in_(patient_ids),
                Patient.deleted_at.is_(None),
            )
        )
        patients_by_id = {p.id: p for p in prows.all()}

    # 拠点 name / code map (対象 office + バケットに出た office) を先に 1 回ロード.
    # code は course_label の正準短縮 (OFFICE_CODE_TO_SHORT) を引くために bucket へ持たせる.
    office_ids_in_use: set[UUID] = {course.office_id for (_v, course, _s) in rows} | set(office_ids)
    office_name_by_id: dict[UUID, str] = {}
    office_code_by_id: dict[UUID, str | None] = {}
    if office_ids_in_use:
        orows = await db.scalars(select(Office).where(Office.id.in_(office_ids_in_use)))
        for o in orows.all():
            office_name_by_id[o.id] = o.name
            office_code_by_id[o.id] = o.code

    buckets: dict[tuple[UUID, int, str], _CourseBucket] = {}
    for v, course, staff in rows:
        patient = patients_by_id.get(v.patient_id)
        if patient is None or patient.lat is None or patient.lng is None:
            continue
        code = course.code or "M"
        key = (course.office_id, course.weekday, code)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _CourseBucket(
                office_id=course.office_id,
                weekday=course.weekday,
                course_code=code,
                office_code=office_code_by_id.get(course.office_id),
                staff_name=staff.name if staff is not None else None,
                course_template_id=course.template_id,
                # N-3: staff 実態判定用. assigned_staff_id は Course 属性
                # (visit の primary_staff_id ではなくコース割付を正とする).
                assigned_staff_id=course.assigned_staff_id,
                staff_sex=staff.sex if staff is not None else None,
            )
            buckets[key] = bucket
        start_min = _time_to_min(v.start_time)
        end_min = _time_to_min(v.end_time)
        duration = end_min - start_min if end_min > start_min else 30
        am_pm = "am" if v.start_time.hour < NOON_HOUR else "pm"
        bucket.visits.append(
            _V2Visit(
                patient_id=patient.id,
                patient_name=patient.name,
                patient_code=patient.code,
                weekday=course.weekday,
                start_time=v.start_time,
                end_time=v.end_time,
                service_minutes=duration,
                lat=float(patient.lat),
                lng=float(patient.lng),
                office_id=course.office_id,
                am_pm=am_pm,  # type: ignore[arg-type]
                source_kind="fixed",
                course_code=code,
                # ③ 表示統一: ミニスケジュールで通常リストと同じ色分け
                # (性別制限・2名体制) を出すため患者属性を流す.
                sex_restriction=patient.sex_restriction,
                requires_multiple_staff=bool(patient.requires_multiple_staff),
                # T-4: 性別ウォッシュ (タイムラインのカード視覚言語) 用.
                sex=patient.sex,
            )
        )

    # N-3: 割付スタッフの非番/当週休みを判定する.
    # bucket 内の全 assigned_staff_id をまとめて 2 クエリで取得 (N+1 回避):
    #   1) StaffShift  … 固定週シフト (weekday ごとの is_on). 行が無い weekday は
    #      「非番」とみなす (StaffShift は通常 staff あたり 7 行揃うため, 欠落 =
    #      未設定 = 非番扱い. schedule-advisor N-3 の定義に従う).
    #   2) StaffWeeklyOverride(override_type='off') … 当週の休み (iso_year/iso_week/
    #      weekday). is_on=True のシフトでも当週 off なら非番になる.
    staff_ids = {b.assigned_staff_id for b in buckets.values() if b.assigned_staff_id is not None}
    if staff_ids:
        shift_on_by_key: dict[tuple[UUID, int], bool] = {}
        shift_rows = await db.execute(
            select(StaffShift.staff_id, StaffShift.weekday, StaffShift.is_on).where(
                StaffShift.staff_id.in_(staff_ids)
            )
        )
        for sid, wd, is_on in shift_rows.all():
            shift_on_by_key[(sid, wd)] = bool(is_on)

        override_off_keys: set[tuple[UUID, int]] = set()
        override_rows = await db.execute(
            select(StaffWeeklyOverride.staff_id, StaffWeeklyOverride.weekday).where(
                StaffWeeklyOverride.staff_id.in_(staff_ids),
                StaffWeeklyOverride.iso_year == iso_year,
                StaffWeeklyOverride.iso_week == iso_week,
                StaffWeeklyOverride.override_type == "off",
            )
        )
        for sid, wd in override_rows.all():
            override_off_keys.add((sid, wd))

        # NG スタッフ (設計書 §6): バケット割付スタッフを NG 指定している患者を
        # **逆引きで 1 クエリ** 一括ロードする (ix_patient_ng_staff_staff の用途そのもの).
        # 候補患者は呼出時まで分からないので「staff → NG 指定患者集合」の向きで持ち、
        # 判定は `_staff_ng_mismatch(candidate.existing_patient_id, bucket.ng_patient_ids)`.
        ng_patients_by_staff: dict[UUID, set[UUID]] = {}
        ng_rows = await db.execute(
            select(PatientNgStaff.staff_id, PatientNgStaff.patient_id).where(
                PatientNgStaff.staff_id.in_(staff_ids)
            )
        )
        for sid, pid in ng_rows.all():
            ng_patients_by_staff.setdefault(sid, set()).add(pid)

        for b in buckets.values():
            sid = b.assigned_staff_id
            if sid is None:
                continue
            shift_key = (sid, b.weekday)
            shift_on = shift_on_by_key.get(shift_key)  # None = シフト行なし
            shift_absent = shift_on is None or shift_on is False
            b.staff_absent = shift_absent or (shift_key in override_off_keys)
            b.ng_patient_ids = frozenset(ng_patients_by_staff.get(sid, ()))

        # イベント考慮2段階提案: 割付スタッフの当該週 staff_events を占有窓として
        # 各バケットへ配る (solver のパスA/パスB判定に使う).
        windows_by_staff_wd = await load_week_event_windows(
            db, staff_ids=list(staff_ids), week_monday=week_monday
        )
        for b in buckets.values():
            if b.assigned_staff_id is None:
                continue
            b.event_windows = windows_by_staff_wd.get((b.assigned_staff_id, b.weekday), [])

    return buckets, office_name_by_id, office_code_by_id


async def load_week_event_windows(
    db: AsyncSession,
    *,
    staff_ids: list[UUID],
    week_monday: date,
) -> dict[tuple[UUID, int], list[EventWindow]]:
    """当該週の staff_events を (staff_id, weekday) 別の EventWindow 列へ変換する.

    - starts_at/ends_at は naive 壁時計として比較する (Layer3 ``_strip_tz`` と同規約:
      staff_events API は naive を INSERT し、PostgreSQL 経由では UTC tz が付くため剥がす).
    - 日を跨ぐイベントは各日に切り出す (現状の運用ではほぼ同日内).
    - ゼロ長 (メモ) は solver 側でも無視するがここでも落とす.
    """
    if not staff_ids:
        return {}
    range_start = datetime.combine(week_monday, time(0, 0))
    range_end = datetime.combine(week_monday + timedelta(days=7), time(0, 0))
    rows = await db.scalars(
        select(StaffEvent).where(
            StaffEvent.staff_id.in_(staff_ids),
            StaffEvent.starts_at < range_end,
            StaffEvent.ends_at >= range_start,
        )
    )

    def _naive(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

    out: dict[tuple[UUID, int], list[EventWindow]] = {}
    for ev in rows.all():
        ev_start = _naive(ev.starts_at)
        ev_end = _naive(ev.ends_at)
        if ev_end <= ev_start:
            continue  # ゼロ長メモ / 異常値.
        for offset in range(7):
            day = week_monday + timedelta(days=offset)
            day_start = datetime.combine(day, time(0, 0))
            day_end = day_start + timedelta(days=1)
            seg_start = max(ev_start, day_start)
            seg_end = min(ev_end, day_end)
            if seg_end <= seg_start:
                continue
            out.setdefault((ev.staff_id, offset), []).append(
                EventWindow(
                    start=seg_start.time(),
                    end=time(23, 59) if seg_end == day_end else seg_end.time(),
                    title=ev.title or "イベント",
                    blocking=bool(ev.blocking),
                )
            )
    return out


def _to_existing_visits(bucket: _CourseBucket) -> list[ExistingVisit]:
    """コースの V2Visit 列を solver 用 ``ExistingVisit`` 列に変換 (start 昇順)."""
    out = [
        ExistingVisit(
            start_time=v.start_time,
            end_time=v.end_time,
            lat=v.lat,
            lng=v.lng,
            service_minutes=v.service_minutes,
            patient_id=str(v.patient_id),
        )
        for v in bucket.visits
    ]
    out.sort(key=lambda e: _time_to_min(e.start_time))
    return out


def _marginal_cost_minutes(
    existing: list[ExistingVisit],
    slot_start: time,
    slot_end: time,
    candidate: CandidateInput,
    *,
    config: SchedulingConfig | None,
) -> float:
    """挿入の厳密限界コスト (分/週) = course_travel_buffer_total(挿入後) − (挿入前).

    正典 (improvement_engine ``compute_exact_marginal``) を import 再利用する
    (コピー禁止・正規化ルール N 系). 診断 / 改善提案 / 範囲最適化と同一の物差し.

    循環 import 回避のため関数内 import: improvement_engine は本モジュールを
    module 直下で import しているため (import 方向: improvement → propose の一方向)、
    本モジュールが improvement を module 直下で import すると循環になる. 呼び出し時
    import なら両モジュール読込済みで安全.
    """
    from app.services.scheduling.improvement_engine import compute_exact_marginal

    member = ExistingVisit(
        start_time=slot_start,
        end_time=slot_end,
        lat=candidate.lat,
        lng=candidate.lng,
        service_minutes=candidate.service_minutes,
        patient_id="__candidate__",
    )
    minutes, _km = compute_exact_marginal(existing, member, config=config)
    return float(minutes)


def _min_distance_km(bucket: _CourseBucket, lat: float, lng: float) -> float | None:
    """候補とコース既存訪問群との最小直線距離 (km). 既存 0 件なら None."""
    if not bucket.visits:
        return None
    return min(haversine_km(lat, lng, v.lat, v.lng) for v in bucket.visits)


def _course_label(office_code: str | None, course_code: str) -> str:
    """UI 表示用ラベル (拠点短縮 + コード, 例: 稲A / 津A).

    office_code 基準の正準短縮 (``patient_excel.schema.OFFICE_CODE_TO_SHORT``,
    INAGE→稲 / TSUGA→津) を使い board / 患者 Excel / グリッド集計と同一表記へ統一する.
    マップに無い拠点コードはコードそのものを使う. office_code が無い場合は code のみ.
    """
    if not office_code:
        return course_code
    short = OFFICE_CODE_TO_SHORT.get(office_code, office_code)
    return f"{short}{course_code}"


def _build_mini_schedule(
    bucket: _CourseBucket,
    slot: Slot,
    *,
    candidate_name: str,
    pair_partner: str | None,
    candidate_sex_restriction: str | None = None,
    candidate_requires_multiple_staff: bool = False,
) -> list[dict[str, object]]:
    """コース当日の既存訪問 + 提案枠を時刻順に並べたミニスケジュール.

    ③ 表示統一: 各行に ``sex_restriction`` / ``is_multi_staff`` を載せ、 FE が通常リストと
    同じ色分け (性別制限・2名体制) を出せるようにする (提案行 is_here は候補患者の属性).
    """
    entries: list[dict[str, object]] = []
    for v in bucket.visits:
        entries.append(
            {
                "time": _fmt_hhmm(v.start_time),
                "name": v.patient_name,
                "ins": None,
                "is_here": False,
                "is_pair": bool(pair_partner is not None and v.patient_name == pair_partner),
                "sex_restriction": v.sex_restriction,
                "is_multi_staff": bool(v.requires_multiple_staff),
                # T-4: 性別ウォッシュ用 (male/female/unknown/None).
                "sex": v.sex,
                "_sort": _time_to_min(v.start_time),
            }
        )
    entries.append(
        {
            "time": _fmt_hhmm(slot.start),
            "name": candidate_name,
            "ins": None,
            "is_here": True,
            "is_pair": slot.same_address_pair,
            "sex_restriction": candidate_sex_restriction,
            "is_multi_staff": bool(candidate_requires_multiple_staff),
            # T-4: 提案行 (is_here) の性別は FE が対象患者マスタから補完する (None 固定).
            "sex": None,
            "_sort": _time_to_min(slot.start),
        }
    )
    # イベント表示 (2026-07-27 PO指示): 担当スタッフのイベントもその日の予定として
    # 時系列に混ぜる (FE は is_event=True を緑のイベントカードで描画)。ゼロ長メモは除外。
    for w in bucket.event_windows:
        if _time_to_min(w.end) <= _time_to_min(w.start):
            continue
        entries.append(
            {
                "time": _fmt_hhmm(w.start),
                "name": w.title or "イベント",
                "ins": None,
                "is_here": False,
                "is_pair": False,
                "sex_restriction": None,
                "is_multi_staff": False,
                "sex": None,
                "is_event": True,
                "end_time": _fmt_hhmm(w.end),
                "_sort": _time_to_min(w.start),
            }
        )
    entries.sort(key=lambda e: e["_sort"])  # type: ignore[index,arg-type,return-value]
    for e in entries:
        e.pop("_sort", None)
    return entries


# 警告種別の日本語ラベル (UI バッジ用).
_WARNING_LABEL_JA: dict[str, str] = {
    "travel_time_shortage": "移動時間がやや不足 (前訪問の早終わりで吸収)",
}

# P3-④: 効率優先の代替枠 (希望外だが近接/余裕が良い枠) に付ける理由バッジ.
_REASON_EFFICIENCY_ALTERNATIVE = "希望外だが効率的（近接/余裕）"

# N-3 (schedule-advisor P0-1 / L0→L1.5): 候補コースの割付スタッフ実態を検出し、
# 候補から除外はせず「理由コード付き警告 + スコア降格」する.
# 除外は絶対にしない: 0 件になるより「警告付きで出す」が正 (設計原則).
# 警告コードは two_staff_not_guaranteed と同じく raw code で載せ、FE が日本語化する.
_WARN_STAFF_UNASSIGNED = "staff_unassigned"
_WARN_STAFF_ABSENT = "staff_absent"
_WARN_STAFF_SEX_MISMATCH = "staff_sex_mismatch"
# NG スタッフ (設計書 patient-ng-staff-design.md §6): 性別制限と完全に同格の扱い.
# エンジン (Layer3) はハード制約だが、提案系は **除外せず警告 + 降格** (方針 N-3 の L1.5).
_WARN_STAFF_NG_MISMATCH = "staff_ng_mismatch"

# I-07 (H5 受入カレンダー / N-6): diff-add (auto_allocator_v2._filter_unavailable_and_lunch)
# は受入 × を enforce (除外) するが、propose-slots では **warning 方式** にする.
# 理由 (N-6): enforce だと候補が全滅して「なぜ出ないか」が分からなくなる. warning なら
# 管理者が判断できる. 判定ロジックは diff-add と同一データ源
# (auto_allocator_v2._load_unavailable_slots が返す (office, weekday)->{time} の
# set 到達判定) を再利用し、除外せず raw code で警告 + スコア降格する (FE が日本語化).
_WARN_ACCEPTANCE_CALENDAR = "acceptance_calendar"

# スコア降格ペナルティ. 既存重み (proximity 50 / preference 30 / balance 20 /
# pair 1000) を基準にした相対値:
#   ABSENT / SEX_MISMATCH / NG_MISMATCH = 60.0 … proximity 満点 (50) + preference
#     片側一致 (15) を上回る大きさ. クリーンな候補より確実に下位へ沈める (現場の信頼
#     確保のため「休みスタッフ/性別不適合/NG スタッフ」の枠は必ず後ろに回す).
#     pair 1000 は超えないので同住所ペアの最優先は維持される.
#   UNASSIGNED = 20.0 … 週次 Layer3 割付で解消され得る軽度事象なので軽い降格.
_PENALTY_STAFF_ABSENT: float = 60.0
_PENALTY_STAFF_SEX_MISMATCH: float = 60.0
# NG スタッフ: 性別制限と同格 (設計書 §2) なので同値・同層のペナルティにする.
_PENALTY_STAFF_NG_MISMATCH: float = 60.0
_PENALTY_STAFF_UNASSIGNED: float = 20.0

# I-07 (H5): 受入カレンダー × の降格幅. P0-1 の staff_absent と同じ機構・同じ幅 (60.0)
# を使う (受入不可の枠を確実にクリーン候補より下位へ沈める). pair 1000 は超えないので
# 同住所ペア最優先は維持. enforce ではないので候補は除外しない (warning + 降格のみ).
_PENALTY_ACCEPTANCE_CALENDAR: float = 60.0

# イベント考慮2段階提案 (PO確定 2026-07-27): フォールバック枠 (solver パスBで
# ソフトイベントと衝突) の警告コードと降格幅. staff_absent と同じ機構・同じ幅で、
# 他コースのクリーン候補より確実に下位へ沈める. 同一バケット内では solver が
# 「クリーン枠があれば衝突枠を出さない」を保証するため、この降格はコース間比較用.
_WARN_EVENT_CONFLICT = "event_conflict"
_PENALTY_EVENT_CONFLICT: float = 60.0


def _pair_mode_of(pair_modes: dict[tuple[UUID, UUID], str], a: UUID, b: UUID) -> str | None:
    """(a, b) の pair_mode を両順序で引く (DB 正規化 a<b に依存しない robust 参照).

    auto_allocator_v2 の blocked 判定 (``_enforce_h2_split_overflow`` /
    ``_enforce_same_address_pair_mode``) と同じく ``(a,b)`` / ``(b,a)`` 両引きする.
    map に無い pair は暗黙 ``preferred`` (None 返し).
    """
    return pair_modes.get((a, b), pair_modes.get((b, a)))


def _has_same_address_blocked_partner(
    bucket: _CourseBucket,
    candidate: CandidateInput,
    pair_modes: dict[tuple[UUID, UUID], str],
) -> bool:
    """候補が bucket 内の同住所既存患者と pair_mode='blocked' 関係にあるか (I-11).

    blocked ペアは PatientSameAddressLink 由来なので必ず同住所. 同住所ペア slot
    (``same_address_pair=True``) は候補を同時刻・90分同住所占有で相手の隣に入れる枠
    であり、blocked 相手とはこれが「同時間帯に重なる組合せ」になる. True のとき当該
    bucket の同住所ペア slot を除外する (diff-add Stage3 の blocked=同 set 禁止と同義).
    """
    cand_pid = candidate.existing_patient_id
    if cand_pid is None:
        return False
    cand_key = _address_bucket(candidate.lat, candidate.lng)
    for v in bucket.visits:
        if v.patient_id == cand_pid:
            continue
        if _address_bucket(v.lat, v.lng) != cand_key:
            continue
        if _pair_mode_of(pair_modes, cand_pid, v.patient_id) == "blocked":
            return True
    return False


def _staff_sex_mismatch(staff_sex: str | None, sex_restriction: str | None) -> bool:
    """割付スタッフ性別がリクエストの性別制限に不適合なら True.

    ``layer3_assignment._sex_satisfies_restrictions`` と同一解釈 (AND 意味論,
    'female_only'→'female' 正規化) を再利用する. ただし誤検知で信頼を失う方が
    害なので、以下は不適合と断定せず判定 skip (False):
      - ``sex_restriction`` が None / 空 → 制限なし.
      - ``staff_sex`` が None → 性別不明 (単一候補コースでは None を不適合扱いしない).
    """
    if not sex_restriction:
        return False
    if staff_sex is None:
        return False
    return not _sex_satisfies_restrictions(staff_sex, frozenset({sex_restriction}))


def _staff_ng_mismatch(patient_id: UUID | None, ng_patient_ids: frozenset[UUID]) -> bool:
    """候補患者がそのコース割付スタッフを NG 指定していれば True (設計書 §6).

    ``_staff_sex_mismatch`` と同じ流儀の純関数. 判定は「patient_ng_staff に
    (候補患者, 割付スタッフ) 行があるか」だけで、除外はしない (警告 + 降格のみ).
    以下は判定 skip (False):
      - ``patient_id`` が None → 新規候補 (DB 未登録) なので NG 行は存在し得ない.
      - ``ng_patient_ids`` が空 → コース割付スタッフを NG 指定した患者が居ない
        (割付スタッフ未定 = ``assigned_staff_id`` None のバケットも空のまま).
    """
    if patient_id is None:
        return False
    return patient_id in ng_patient_ids


def _slot_reasons_and_warnings(
    slot: Slot,
    candidate: CandidateInput,
    *,
    min_dist_km: float | None,
    remaining_count: int,
    matched_weekday: bool,
    staff_unassigned: bool = False,
    staff_absent: bool = False,
    staff_sex_mismatch: bool = False,
    staff_ng_mismatch: bool = False,
    acceptance_blocked: bool = False,
    is_efficiency_alternative: bool = False,
    suppress_two_staff: bool = False,
) -> tuple[list[str], list[str]]:
    """スロットの理由バッジ + 警告ラベルを組み立てる.

    P3-④: ``is_efficiency_alternative=True`` の枠は「希望外」なので希望曜日 /
    希望時間帯一致の理由は付けず、代わりに「希望外だが効率的」の理由を付ける.
    近接 / 余裕 (効率の根拠) と全警告 (P0-1 スタッフ実態含む) は通常候補と同一に扱う.
    """
    reasons: list[str] = []
    warnings: list[str] = []

    if slot.same_address_pair:
        reasons.append("同住所 / 移動0")
    elif min_dist_km is not None and min_dist_km <= 1.0:
        reasons.append("近い")

    # 希望適合: 曜日一致 + time_type / 時間帯一致 (効率代替は希望外なので付けない).
    if not is_efficiency_alternative:
        if matched_weekday and candidate.preferred_weekdays:
            reasons.append("希望曜日")
        if candidate.time_type in ("固定", "時間帯", "午前", "午後"):
            reasons.append("希望時間帯一致")

    # 空きの平準化: 残容量が多い (= 余裕).
    if remaining_count >= 3:
        reasons.append("余裕あり")

    # P3-④: 効率代替の明示ラベル (希望外だが近接/余裕で効率的).
    if is_efficiency_alternative:
        reasons.append(_REASON_EFFICIENCY_ALTERNATIVE)

    if slot.warning:
        warnings.append(_WARNING_LABEL_JA.get(slot.warning, slot.warning))
    # 課題1 (2名体制): propose-slots ソルバは「コース=1スタッフ」モデルで 2 人目の
    # スタッフ確保までは判定しない (auto_allocator も警告のみ). 2 名体制患者には
    # 「2 人目要確認」の警告を付け、 自動で 2 名確保済とは誤認させない.
    # W-12a: ただしペア候補 (slot0+slot1 同時刻・別コースで 2 名確保済) では相方が保証
    # されるため付けない (suppress_two_staff=True で抑制. §2.3 の FE 契約).
    if candidate.requires_multiple_staff and not suppress_two_staff:
        warnings.append("two_staff_not_guaranteed")
    # N-3: 割付スタッフ実態の警告 (除外はせず注意喚起のみ). FE が日本語ラベル化する.
    if staff_unassigned:
        warnings.append(_WARN_STAFF_UNASSIGNED)
    if staff_absent:
        warnings.append(_WARN_STAFF_ABSENT)
    if staff_sex_mismatch:
        warnings.append(_WARN_STAFF_SEX_MISMATCH)
    # NG スタッフ (§6): 性別と同格. 除外はせず警告 + 降格のみ.
    if staff_ng_mismatch:
        warnings.append(_WARN_STAFF_NG_MISMATCH)
    # I-07 (H5): 受入カレンダー × の時刻に該当する枠は除外せず警告のみ (N-6).
    if acceptance_blocked:
        warnings.append(_WARN_ACCEPTANCE_CALENDAR)
    return reasons, warnings


def _score_slot(
    slot: Slot,
    *,
    min_dist_km: float | None,
    remaining_count: int,
    remaining_minutes: int,
    matched_weekday: bool,
    matched_time_type: bool,
    max_patients: int = _MAX_PATIENTS_PER_COURSE,
    staff_unassigned: bool = False,
    staff_absent: bool = False,
    staff_sex_mismatch: bool = False,
    staff_ng_mismatch: bool = False,
    acceptance_blocked: bool = False,
) -> float:
    """合成スコア (降順用). 同住所ペアは大ボーナスで最優先.

    Phase G-88 Step3: 残容量正規化の分母 (``max_patients``) を config 化可能にする.
    既定は module 定数 ``_MAX_PATIENTS_PER_COURSE`` (= 6) で挙動不変.
    """
    score = 0.0
    if slot.same_address_pair:
        score += _PAIR_BONUS

    # 移動効率 (近接): 最小距離が小さいほど高. 既存 0 件は中立 (満点の半分).
    score += _W_PROXIMITY * _proximity_ratio(min_dist_km)

    # 希望適合.
    pref = 0.0
    if matched_weekday:
        pref += 0.5
    if matched_time_type:
        pref += 0.5
    score += _W_PREFERENCE * pref

    # 空きの平準化: 残件数 + 残分の平均的余裕度 (0..1).
    score += _W_BALANCE * _balance_ratio(remaining_count, remaining_minutes, max_patients)

    # 警告ありは僅かに減点 (実現可能だが注意).
    if slot.warning:
        score -= 1.0
    # N-3: 割付スタッフ実態の降格 (該当時のみ加算. 警告なし候補のスコア・順位は不変).
    if staff_absent:
        score -= _PENALTY_STAFF_ABSENT
    if staff_sex_mismatch:
        score -= _PENALTY_STAFF_SEX_MISMATCH
    # NG スタッフ (§6): 性別と同値・同層で降格 (除外はしない).
    if staff_ng_mismatch:
        score -= _PENALTY_STAFF_NG_MISMATCH
    if staff_unassigned:
        score -= _PENALTY_STAFF_UNASSIGNED
    # I-07 (H5): 受入カレンダー × の枠は staff_absent と同じ幅で降格 (除外はしない).
    if acceptance_blocked:
        score -= _PENALTY_ACCEPTANCE_CALENDAR
    return score


def _proximity_ratio(min_dist_km: float | None) -> float:
    """近接度 0..1 (最小距離が小さいほど高い). 既存 0 件は中立 0.5.

    P3-④ レビューMEDIUM: ``_score_slot`` と ``_efficiency_component`` の式重複を
    共通ヘルパ化 (「効率 = full score の proximity+balance 部分集合」を構造的に担保)。
    """
    if min_dist_km is None:
        return 0.5
    return max(0.0, 1.0 - min(min_dist_km, _PROXIMITY_SAT_KM) / _PROXIMITY_SAT_KM)


def _balance_ratio(remaining_count: int, remaining_minutes: int, max_patients: int) -> float:
    """空きの平準化 0..1 (残件数 + 残分の平均的余裕度). ``_proximity_ratio`` と対の共通ヘルパ."""
    count_ratio = remaining_count / max_patients
    min_ratio = remaining_minutes / _COURSE_MAX_MINUTES
    return max(0.0, min(1.0, (count_ratio + min_ratio) / 2.0))


def _efficiency_component(
    *,
    min_dist_km: float | None,
    remaining_count: int,
    remaining_minutes: int,
    max_patients: int,
) -> float:
    """効率代替の採択判定に使う「近接 (proximity) + 平準化 (balance)」合計.

    P3-④: ``_score_slot`` と同一の共通ヘルパで算出する
    (pair ボーナス / 希望適合 / 警告降格は含めない = 純粋な効率指標).
    バケット単位の指標なので同一コースの全 slot で共通の値になる.
    """
    return _W_PROXIMITY * _proximity_ratio(min_dist_km) + _W_BALANCE * _balance_ratio(
        remaining_count, remaining_minutes, max_patients
    )


def _enumerate_candidate_slots(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    solver_candidate: Candidate,
    *,
    target_weekdays: frozenset[int],
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None,
    candidate_name: str,
    config: SchedulingConfig | None,
    max_patients: int,
    lunch_duration: int,
    lunch_window_start: time,
    lunch_window_end: time,
    is_efficiency_alternative: bool = False,
    exclusions_out: list[_BucketExclusion] | None = None,
    unavailable_slots: dict[tuple[UUID, int], set[time]] | None = None,
    pair_modes: dict[tuple[UUID, UUID], str] | None = None,
) -> list[tuple[ProposedSlot, float]]:
    """1 候補 (solver_candidate) で全コース × target_weekdays を回し提案枠を列挙.

    ``compute_all_proposed_slots`` の内部ループを共有化したもの (通常候補 / 効率代替の
    両方で使う). 戻り値は ``(ProposedSlot, 効率指標)`` のタプル列 (未ソート).
    効率指標は効率代替の採択判定 (_efficiency_component) にのみ使う.

    ``is_efficiency_alternative=True`` のとき:
        - 希望適合 (matched_weekday / matched_time_type) はスコア・理由とも無効化
          (希望外の枠なので希望適合で加点しない).
        - reason に「希望外だが効率的」を付与し、ProposedSlot.is_efficiency_alternative
          を True にする. 警告 (P0-1 スタッフ実態含む) は通常候補と同一に付く.
    """
    results: list[tuple[ProposedSlot, float]] = []
    for (office_id, weekday, course_code), bucket in buckets.items():
        if office_ids and office_id not in office_ids:
            continue
        if weekday not in target_weekdays:
            continue

        existing = _to_existing_visits(bucket)
        lunch = compute_lunch_window(
            bucket.visits,
            warnings=None,
            weekday=weekday,
            duration=lunch_duration,
            window_start=lunch_window_start,
            window_end=lunch_window_end,
        )

        # P-1b: bucket ごとに除外理由を収集 (exclusions_out 指定時のみ). 純観測フックで
        # solver の判定は不変. slot が 1 件でも出れば理由は集約しない (0 件時のみ意味を持つ).
        bucket_sink: list[str] | None = [] if exclusions_out is not None else None
        slots = find_available_slots_for_candidate(
            existing,
            solver_candidate,
            lunch_window=lunch,
            weekday=weekday,
            config=config,
            exclusion_sink=bucket_sink,
            event_windows=bucket.event_windows,
        )
        # I-11: pair_mode='blocked' のペア相手と同時間帯 (同住所同時刻=90分占有) に重なる
        # 同住所ペア slot を除外する (diff-add Stage3 の blocked=同 set 禁止と同義).
        # blocked 相手は必ず同住所なので、当該 bucket に同住所 blocked 相手が居れば
        # same_address_pair slot 全てが対象. 非ペア slot (別時刻) は blocked に該当しない.
        if pair_modes and _has_same_address_blocked_partner(bucket, candidate, pair_modes):
            filtered = [s for s in slots if not s.same_address_pair]
            if len(filtered) != len(slots) and bucket_sink is not None:
                bucket_sink.append("pair_blocked")
            slots = filtered
        if not slots:
            if exclusions_out is not None:
                exclusions_out.append(
                    _BucketExclusion(
                        reason=_pick_bucket_reason(bucket_sink or []),
                        weekday=weekday,
                        course_code=course_code,
                    )
                )
            continue

        min_dist = _min_distance_km(bucket, candidate.lat, candidate.lng)
        remaining_count = max(0, max_patients - len(bucket.visits))
        used_minutes = sum(v.service_minutes for v in bucket.visits)
        remaining_minutes = max(0, _COURSE_MAX_MINUTES - used_minutes)
        # N-3: 割付スタッフ実態フラグ (bucket 単位で 1 回算出し全 slot に共有).
        staff_unassigned = bucket.assigned_staff_id is None
        staff_absent = bucket.staff_absent
        staff_sex_mismatch = _staff_sex_mismatch(bucket.staff_sex, candidate.sex_restriction)
        # NG スタッフ (§6): 候補患者がこのコースの割付スタッフを NG 指定しているか.
        staff_ng_mismatch = _staff_ng_mismatch(candidate.existing_patient_id, bucket.ng_patient_ids)
        # I-07 (H5): 当該 (office, weekday) の受入 × 時刻集合 (diff-add と同一データ源).
        # slot ごとに start が × 時刻に一致するかを判定する (診断は per-slot).
        blocked_starts = (
            unavailable_slots.get((office_id, weekday), set())
            if unavailable_slots is not None
            else set()
        )
        # 効率代替は希望外なので希望適合フラグを無効化する.
        if is_efficiency_alternative:
            matched_weekday = False
            matched_time_type = False
        else:
            matched_weekday = (
                bool(candidate.preferred_weekdays) and weekday in candidate.preferred_weekdays
            )
            matched_time_type = candidate.time_type in ("固定", "時間帯", "午前", "午後")
        office_name = office_name_by_id.get(office_id)
        # course_label は office_code 基準の正準短縮 (稲A/津A) を使う. bucket が持つ
        # code を優先し、無ければ map から引く (どちらも無ければ code のみ).
        office_code = bucket.office_code
        if office_code is None and office_code_by_id is not None:
            office_code = office_code_by_id.get(office_id)
        label = _course_label(office_code, course_code)

        eff = _efficiency_component(
            min_dist_km=min_dist,
            remaining_count=remaining_count,
            remaining_minutes=remaining_minutes,
            max_patients=max_patients,
        )

        for slot in slots:
            # 同住所ペア相手の名前を引く (slot.start に既存単独患者がいる).
            pair_partner: str | None = None
            if slot.same_address_pair:
                for v in bucket.visits:
                    if _address_bucket(v.lat, v.lng) == _address_bucket(
                        candidate.lat, candidate.lng
                    ):
                        pair_partner = v.patient_name
                        break

            # I-07 (H5): 同住所ペア枠は既存訪問の時刻を継ぐが、単独枠は slot.start を
            # × 時刻集合と突合する (auto_allocator の v.start_time in blocked と同判定).
            acceptance_blocked = slot.start in blocked_starts
            reasons, warnings = _slot_reasons_and_warnings(
                slot,
                candidate,
                min_dist_km=min_dist,
                remaining_count=remaining_count,
                matched_weekday=matched_weekday,
                staff_unassigned=staff_unassigned,
                staff_absent=staff_absent,
                staff_sex_mismatch=staff_sex_mismatch,
                staff_ng_mismatch=staff_ng_mismatch,
                acceptance_blocked=acceptance_blocked,
                is_efficiency_alternative=is_efficiency_alternative,
            )
            score = _score_slot(
                slot,
                min_dist_km=min_dist,
                remaining_count=remaining_count,
                remaining_minutes=remaining_minutes,
                matched_weekday=matched_weekday,
                matched_time_type=matched_time_type,
                max_patients=max_patients,
                staff_unassigned=staff_unassigned,
                staff_absent=staff_absent,
                staff_sex_mismatch=staff_sex_mismatch,
                staff_ng_mismatch=staff_ng_mismatch,
                acceptance_blocked=acceptance_blocked,
            )
            # イベント考慮2段階提案: フォールバック枠は warning + 降格 (除外しない原則)
            # + 表示用の衝突イベント詳細を載せる.
            if slot.event_conflicts:
                warnings = [*warnings, _WARN_EVENT_CONFLICT]
                score -= _PENALTY_EVENT_CONFLICT
            mini = _build_mini_schedule(
                bucket,
                slot,
                candidate_name=candidate_name,
                pair_partner=pair_partner,
                candidate_sex_restriction=candidate.sex_restriction,
                candidate_requires_multiple_staff=candidate.requires_multiple_staff,
            )
            results.append(
                (
                    ProposedSlot(
                        office_id=office_id,
                        office_name=office_name,
                        weekday=weekday,
                        course_code=course_code,
                        course_label=label,
                        staff_name=bucket.staff_name,
                        start=slot.start,
                        end=slot.end,
                        score=round(score, 4),
                        reasons=reasons,
                        warnings=warnings,
                        is_pair=slot.same_address_pair,
                        pair_partner=pair_partner,
                        mini_schedule=mini,
                        is_efficiency_alternative=is_efficiency_alternative,
                        event_conflicts=[
                            {
                                "title": c.title,
                                "start": _fmt_hhmm(c.start),
                                "end": _fmt_hhmm(c.end),
                            }
                            for c in slot.event_conflicts
                        ],
                    ),
                    eff,
                )
            )
    return results


def _build_pair_proposed_slot(
    *,
    primary_bucket: _CourseBucket,
    partner_bucket: _CourseBucket,
    office_id: UUID,
    weekday: int,
    start: time,
    candidate: CandidateInput,
    candidate_name: str,
    office_code_by_id: dict[UUID, str | None] | None,
    office_name_by_id: dict[UUID, str],
    max_patients: int,
    unavailable_slots: dict[tuple[UUID, int], set[time]] | None = None,
) -> ProposedSlot:
    """成立した 2 名体制ペア (primary=slot0 / partner=slot1) を 1 件の ProposedSlot 化する.

    score / reasons / warnings は primary コースの bucket 指標で組み立て (通常候補と同一物差し)、
    ``_TWO_STAFF_BONUS`` を上乗せする. two_staff_not_guaranteed 警告は付けない (相方保証済).
    delta (marginal_cost_minutes) は後段 ``_assign_marginal_and_resort`` が 2 コース合計で付与する.

    W-12a review FIX-1: staff_unassigned / staff_absent / staff_sex_mismatch /
    staff_ng_mismatch はどちらかのコースで該当すれば警告 / ペナルティを出す (OR 合成).
    W-12a review FIX-2: アンカー時刻 T が受入カレンダー × なら acceptance_blocked=True.
    """
    service = int(candidate.service_minutes)
    end = _add_minutes(start, service)
    block = "am" if start.hour < NOON_HOUR else "pm"
    slot = Slot(start=start, end=end, block=block, same_address_pair=False)

    min_dist = _min_distance_km(primary_bucket, candidate.lat, candidate.lng)
    remaining_count = max(0, max_patients - len(primary_bucket.visits))
    used_minutes = sum(v.service_minutes for v in primary_bucket.visits)
    remaining_minutes = max(0, _COURSE_MAX_MINUTES - used_minutes)
    matched_weekday = bool(candidate.preferred_weekdays) and weekday in candidate.preferred_weekdays
    matched_time_type = candidate.time_type in ("固定", "時間帯", "午前", "午後")
    # FIX-1: OR 合成 — primary か partner どちらか一方でも該当すれば警告/ペナルティを付与する.
    staff_unassigned = (primary_bucket.assigned_staff_id is None) or (
        partner_bucket.assigned_staff_id is None
    )
    staff_absent = primary_bucket.staff_absent or partner_bucket.staff_absent
    staff_sex_mismatch = _staff_sex_mismatch(
        primary_bucket.staff_sex, candidate.sex_restriction
    ) or _staff_sex_mismatch(partner_bucket.staff_sex, candidate.sex_restriction)
    # NG スタッフ (§6) も FIX-1 と同じ OR 合成: 2 名のどちらかが NG なら警告 + 降格.
    staff_ng_mismatch = _staff_ng_mismatch(
        candidate.existing_patient_id, primary_bucket.ng_patient_ids
    ) or _staff_ng_mismatch(candidate.existing_patient_id, partner_bucket.ng_patient_ids)
    # FIX-2: 受入カレンダー × — primary/partner は同 office/weekday グループなので 1 回だけ判定.
    blocked_set = unavailable_slots.get((office_id, weekday), set()) if unavailable_slots else set()
    acceptance_blocked = start in blocked_set

    reasons, warnings = _slot_reasons_and_warnings(
        slot,
        candidate,
        min_dist_km=min_dist,
        remaining_count=remaining_count,
        matched_weekday=matched_weekday,
        staff_unassigned=staff_unassigned,
        staff_absent=staff_absent,
        staff_sex_mismatch=staff_sex_mismatch,
        staff_ng_mismatch=staff_ng_mismatch,
        acceptance_blocked=acceptance_blocked,
        suppress_two_staff=True,
    )
    score = (
        _score_slot(
            slot,
            min_dist_km=min_dist,
            remaining_count=remaining_count,
            remaining_minutes=remaining_minutes,
            matched_weekday=matched_weekday,
            matched_time_type=matched_time_type,
            max_patients=max_patients,
            staff_unassigned=staff_unassigned,
            staff_absent=staff_absent,
            staff_sex_mismatch=staff_sex_mismatch,
            staff_ng_mismatch=staff_ng_mismatch,
            acceptance_blocked=acceptance_blocked,
        )
        + _TWO_STAFF_BONUS
    )

    office_name = office_name_by_id.get(office_id)
    office_code = primary_bucket.office_code
    if office_code is None and office_code_by_id is not None:
        office_code = office_code_by_id.get(office_id)
    label = _course_label(office_code, primary_bucket.course_code)
    partner_label = _course_label(office_code, partner_bucket.course_code)

    mini = _build_mini_schedule(
        primary_bucket,
        slot,
        candidate_name=candidate_name,
        pair_partner=None,
        candidate_sex_restriction=candidate.sex_restriction,
        candidate_requires_multiple_staff=candidate.requires_multiple_staff,
    )
    partner_mini = _build_mini_schedule(
        partner_bucket,
        Slot(start=start, end=end, block=block, same_address_pair=False),
        candidate_name=candidate_name,
        pair_partner=None,
        candidate_sex_restriction=candidate.sex_restriction,
        candidate_requires_multiple_staff=candidate.requires_multiple_staff,
    )

    return ProposedSlot(
        office_id=office_id,
        office_name=office_name,
        weekday=weekday,
        course_code=primary_bucket.course_code,
        course_label=label,
        staff_name=primary_bucket.staff_name,
        start=start,
        end=end,
        score=round(score, 4),
        reasons=reasons,
        warnings=warnings,
        is_pair=False,
        pair_partner=None,
        mini_schedule=mini,
        partner_course_code=partner_bucket.course_code,
        partner_course_label=partner_label,
        partner_course_template_id=partner_bucket.course_template_id,
        partner_staff_name=partner_bucket.staff_name,
        partner_mini_schedule=partner_mini,
    )


def _enumerate_pair_slots(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    solver_candidate: Candidate,
    *,
    target_weekdays: frozenset[int],
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None,
    candidate_name: str,
    config: SchedulingConfig | None,
    max_patients: int,
    lunch_duration: int,
    lunch_window_start: time,
    lunch_window_end: time,
    exclusions_out: list[_BucketExclusion] | None = None,
    unavailable_slots: dict[tuple[UUID, int], set[time]] | None = None,
) -> list[tuple[ProposedSlot, float]]:
    """W-12a D-1/D-2: 2 名体制候補のペア (slot0+slot1 同時刻・別コース) を列挙する.

    主従アンカー方式: 各コースで slot0 候補時刻を既存ソルバで列挙 → 同 office・同 weekday の
    他コースでその時刻ちょうどに相方 (slot1) が入るか (``slot_fits_exact`` + 容量) を点検 →
    両立時刻のみペア化する. 逆走査 union は「両コースの feasible 時刻集合の和集合を anchor に
    両側を ``slot_fits_exact`` で点検」することで対称に達成し、(T, {X,Y}) 無順序キーの重複は
    course_code 昇順の unordered pair 走査で自然排除する (決定性: course_code 昇順 → 時刻昇順).

    片肺 (slot0 は入るが相方全滅) は候補化しない (D-2). ``exclusions_out`` 指定時、slot0 が
    1 件でも入る曜日で 1 組もペアが成立しなければ ``no_pair_slot`` を記録する (N-6).
    戻り値は ``(ProposedSlot, 効率指標=0.0)`` のタプル列 (未ソート・効率代替は 2 名体制で非対象).
    """
    # bucket ごとに slot0 feasible 時刻 / 容量 / メタを集め、(office, weekday) でグルーピング.
    groups: dict[
        tuple[UUID, int],
        list[
            tuple[
                str, _CourseBucket, list[ExistingVisit], tuple[time, time] | None, set[time], bool
            ]
        ],
    ] = defaultdict(list)
    for (office_id, weekday, course_code), bucket in buckets.items():
        if office_ids and office_id not in office_ids:
            continue
        if weekday not in target_weekdays:
            continue
        existing = _to_existing_visits(bucket)
        lunch = compute_lunch_window(
            bucket.visits,
            warnings=None,
            weekday=weekday,
            duration=lunch_duration,
            window_start=lunch_window_start,
            window_end=lunch_window_end,
        )
        bucket_sink: list[str] | None = [] if exclusions_out is not None else None
        slots = find_available_slots_for_candidate(
            existing,
            solver_candidate,
            lunch_window=lunch,
            weekday=weekday,
            config=config,
            exclusion_sink=bucket_sink,
            event_windows=bucket.event_windows,
        )
        # 2名体制ペア (W-12a) はクリーン枠のみ対象: フォールバック (イベント衝突) の
        # anchor で2コース同時のペアを組むと衝突が伝播して複雑になるため、イベントと
        # 重なる場合は単独候補側 (通常経路) の警告付き提案に任せる.
        feasible_starts = {
            s.start for s in slots if not s.same_address_pair and not s.event_conflicts
        }
        used_minutes = _course_total_minutes_from_existing(existing, config=config)
        cap_ok = (
            len(bucket.visits) < max_patients
            and used_minutes + int(candidate.service_minutes) <= _COURSE_MAX_MINUTES
        )
        # slot0 自体が入らないコースは通常の除外理由 (capacity_full / no_gap 等) を記録.
        if exclusions_out is not None and not feasible_starts:
            exclusions_out.append(
                _BucketExclusion(
                    reason=_pick_bucket_reason(bucket_sink or []),
                    weekday=weekday,
                    course_code=course_code,
                )
            )
        groups[(office_id, weekday)].append(
            (course_code, bucket, existing, lunch, feasible_starts, cap_ok)
        )

    results: list[tuple[ProposedSlot, float]] = []
    for (office_id, weekday), items in groups.items():
        items.sort(key=lambda it: it[0])  # course_code 昇順で primary/partner を決定的化.
        any_slot0 = any(it[4] for it in items)
        pair_formed = False
        for xi in range(len(items)):
            for yi in range(xi + 1, len(items)):
                x_code, x_bucket, x_existing, x_lunch, x_starts, x_cap = items[xi]
                y_code, y_bucket, y_existing, y_lunch, y_starts, y_cap = items[yi]
                if not (x_cap and y_cap):
                    continue
                for anchor in sorted(x_starts | y_starts, key=_time_to_min):
                    x_ok = anchor in x_starts or slot_fits_exact(
                        x_existing,
                        solver_candidate,
                        anchor,
                        lunch_window=x_lunch,
                        config=config,
                        event_windows=x_bucket.event_windows,
                    )
                    if not x_ok:
                        continue
                    y_ok = anchor in y_starts or slot_fits_exact(
                        y_existing,
                        solver_candidate,
                        anchor,
                        lunch_window=y_lunch,
                        config=config,
                        event_windows=y_bucket.event_windows,
                    )
                    if not y_ok:
                        continue
                    results.append(
                        (
                            _build_pair_proposed_slot(
                                primary_bucket=x_bucket,
                                partner_bucket=y_bucket,
                                office_id=office_id,
                                weekday=weekday,
                                start=anchor,
                                candidate=candidate,
                                candidate_name=candidate_name,
                                office_code_by_id=office_code_by_id,
                                office_name_by_id=office_name_by_id,
                                max_patients=max_patients,
                                unavailable_slots=unavailable_slots,
                            ),
                            0.0,
                        )
                    )
                    pair_formed = True
        # D-2: slot0 は入るが相方全滅 → no_pair_slot (曜日別).
        if exclusions_out is not None and any_slot0 and not pair_formed:
            sample_code = next((it[0] for it in items if it[4]), "")
            exclusions_out.append(
                _BucketExclusion(reason="no_pair_slot", weekday=weekday, course_code=sample_code)
            )
    return results


def _assign_marginal_and_resort(
    results: list[ProposedSlot],
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    candidate: CandidateInput,
    *,
    config: SchedulingConfig | None,
) -> None:
    """P-1a: results (スコア降順済み) の上位 ``DELTA_EVAL_LIMIT`` 件に厳密限界コストを付与し、
    delta 昇順を主キーに in-place 再ソートする.

    delta を計算した上位群のみを再ソートし、それ未満の下位候補は
    ``marginal_cost_minutes=None`` のまま従来 (スコア) 順で末尾に残す. 同値時は
    従来スコア降順 → 開始時刻昇順 → course_code 昇順で決定的に並べる (最後に拠点/曜日で
    完全決定化). 同住所ペアは delta≈0 になり自然に最上位へ来る.
    """
    if not results:
        return
    head = results[:DELTA_EVAL_LIMIT]
    tail = results[DELTA_EVAL_LIMIT:]
    # existing コース列はバケット単位で不変なので 1 回だけ構築してキャッシュする.
    existing_cache: dict[tuple[UUID, int, str], list[ExistingVisit]] = {}
    for ps in head:
        key = (ps.office_id, ps.weekday, ps.course_code)
        bucket = buckets.get(key)
        if bucket is None:
            continue
        existing = existing_cache.get(key)
        if existing is None:
            existing = _to_existing_visits(bucket)
            existing_cache[key] = existing
        delta = _marginal_cost_minutes(existing, ps.start, ps.end, candidate, config=config)
        # W-12a: 2 名体制ペアは相方コースの delta も独立に加算 (別コース = 相互作用なし).
        if ps.partner_course_code is not None:
            pkey = (ps.office_id, ps.weekday, ps.partner_course_code)
            pbucket = buckets.get(pkey)
            if pbucket is not None:
                pexisting = existing_cache.get(pkey)
                if pexisting is None:
                    pexisting = _to_existing_visits(pbucket)
                    existing_cache[pkey] = pexisting
                delta += _marginal_cost_minutes(
                    pexisting, ps.start, ps.end, candidate, config=config
                )
        ps.marginal_cost_minutes = delta
    head.sort(
        key=lambda r: (
            r.marginal_cost_minutes if r.marginal_cost_minutes is not None else float("inf"),
            -r.score,
            _time_to_min(r.start),
            r.course_code,
            str(r.office_id),
            r.weekday,
        )
    )
    results[:] = head + tail


def _aggregate_exclusions(
    raw: list[_BucketExclusion],
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    *,
    office_ids: list[UUID],
    target_weekdays: frozenset[int],
) -> list[ExcludedReasonSummary]:
    """P-1b: 生の bucket 除外理由を ``reason × weekday`` で集約する (非空を保証).

    希望曜日に開講コースが 1 つも無い曜日は ``course_closed`` を補完する. 最終的に空
    (bucket も除外理由も無い異常系) なら汎用 ``no_gap`` を 1 件足して「黙って 0 件」を防ぐ.
    """
    records = list(raw)
    # course_closed: scope 内で希望曜日に bucket が 1 つも無い曜日を補完.
    present_weekdays = {wd for (oid, wd, _cc) in buckets if (not office_ids or oid in office_ids)}
    for wd in sorted(target_weekdays):
        if wd not in present_weekdays:
            records.append(_BucketExclusion(reason="course_closed", weekday=wd, course_code=""))

    counts: dict[tuple[str, int], int] = {}
    samples: dict[tuple[str, int], str | None] = {}
    order: list[tuple[str, int]] = []
    for rec in records:
        k = (rec.reason, rec.weekday)
        if k not in counts:
            counts[k] = 0
            samples[k] = None
            order.append(k)
        counts[k] += 1
        if samples[k] is None and rec.course_code:
            samples[k] = rec.course_code

    summaries = [
        ExcludedReasonSummary(
            reason=reason,
            count=counts[(reason, wd)],
            weekday=wd,
            sample_course_code=samples[(reason, wd)],
        )
        for (reason, wd) in order
    ]
    if not summaries:
        fallback_wd = min(target_weekdays) if target_weekdays else 0
        summaries.append(
            ExcludedReasonSummary(
                reason="no_gap", count=0, weekday=fallback_wd, sample_course_code=None
            )
        )
    return summaries


def compute_all_proposed_slots(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    *,
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None = None,
    candidate_name: str = "(提案)",
    config: SchedulingConfig | None = None,
    include_efficiency_alternatives: bool = False,
    exclusions_out: list[ExcludedReasonSummary] | None = None,
    unavailable_slots: dict[tuple[UUID, int], set[time]] | None = None,
    pair_modes: dict[tuple[UUID, UUID], str] | None = None,
) -> list[ProposedSlot]:
    """全 (開講コース × 候補希望曜日) でソルバを回し、ランキング済み全スロットを返す.

    ``compute_proposed_slots`` の limit なし版. slots[] と coverage で同一の
    実現可能性判定 / ランキングを共有するための内部 API.

    実現不能なコース / 曜日は候補に出さない (= 入らない時間は提案しない).

    Phase G-88 Step3: ``config`` を ``compute_lunch_window`` /
    ``find_available_slots_for_candidate`` / 残容量計算へ伝播する. ``config=None`` は
    module 定数を使い挙動不変. full-optimize と同一 config を渡して一貫性を保つ.

    P3-④: ``include_efficiency_alternatives=True`` のとき、通常の希望適合候補に加え、
    time_type / preferred_weekdays のハード制約を外した候補で再列挙し、通常候補と
    重複せず「proximity+balance > 通常候補の最高スコア」を満たす効率的な枠を最大 3 件、
    ``is_efficiency_alternative=True`` を付けて通常候補リストの後ろに追加する.

    P-1a (物差し統一): 従来の加点スコアで粗ランキングした上位 ``DELTA_EVAL_LIMIT`` 件のみ
    厳密限界コスト (``marginal_cost_minutes``) を計算し、**delta 昇順を主キー** に再ソート
    する (同値は従来スコア降順 → 開始時刻昇順 → course_code 昇順で決定的). 下位候補は
    ``marginal_cost_minutes=None`` のまま従来順で末尾に残る. 効率代替は delta 対象外で
    従来どおり末尾に付く. **既定の並びが delta 主キーに変わるのは設計どおりの仕様変更**.

    P-1b (N-6): ``exclusions_out`` に list を渡すと、通常候補が 0 件のとき除外理由を
    ``reason × weekday`` で集約して append する (1 件でも候補があれば空のまま).

    I-07 (H5 warning): ``unavailable_slots`` (auto_allocator_v2._load_unavailable_slots
    と同一データ源 = (office, weekday)->{受入× time}) を渡すと、slot.start が × 時刻に
    一致する枠に警告 ``acceptance_calendar`` を付けスコア降格する (除外はしない = N-6).

    I-11 (pair blocked): ``pair_modes`` (auto_allocator_v2._load_same_address_pair_modes
    と同一データ源) を渡すと、候補 (existing_patient_id) と同住所 blocked ペア相手が
    bucket に居るとき、その同住所ペア slot (同時刻90分占有) を除外し ``pair_blocked``
    を excluded_summary に集約する (blocked=同時刻NG. diff-add Stage3 と同義).
    """
    # 残容量 (件数) の上限: config 化. 残分 (COURSE_MAX_MINUTES) は据え置き.
    _max_patients = (
        config.max_patients_per_course if config is not None else _MAX_PATIENTS_PER_COURSE
    )
    # 昼休み標準長 / 取得時間帯 (compute_lunch_window へ渡す既定 = module 定数).
    _lunch_duration = config.lunch_duration_min if config is not None else _LUNCH_DURATION_PREFERRED
    _lunch_window_start = config.lunch_window_start if config is not None else _LUNCH_EARLIEST_START
    _lunch_window_end = config.lunch_window_end if config is not None else _LUNCH_LATEST_END
    target_weekdays = candidate.preferred_weekdays or frozenset(range(7))
    cand = Candidate(
        lat=candidate.lat,
        lng=candidate.lng,
        service_minutes=candidate.service_minutes,
        time_type=candidate.time_type,
        preferred_start=candidate.preferred_start,
        preferred_end=candidate.preferred_end,
        patient_id=str(candidate.existing_patient_id) if candidate.existing_patient_id else None,
    )

    # 通常候補 (希望適合): 従来どおり希望 time_type / 希望曜日で列挙・ランキング.
    # P-1b: 0 件時の除外理由収集用 sink (exclusions_out 指定時のみ).
    raw_exclusions: list[_BucketExclusion] | None = [] if exclusions_out is not None else None
    # W-12a (D-1/D-2): 2 名体制候補はペア (slot0+slot1 同時刻・別コース) のみを列挙する.
    # 片肺提案は出さない (相方が入る時刻がなければ候補 0 件 → no_pair_slot). include_overcapacity
    # 経路 (compute_overcapacity_slots) はこの分岐を通らず従来挙動不変 (設計書 §2.1).
    if candidate.requires_multiple_staff:
        normal = _enumerate_pair_slots(
            buckets,
            office_name_by_id,
            candidate,
            cand,
            target_weekdays=target_weekdays,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            candidate_name=candidate_name,
            config=config,
            max_patients=_max_patients,
            lunch_duration=_lunch_duration,
            lunch_window_start=_lunch_window_start,
            lunch_window_end=_lunch_window_end,
            exclusions_out=raw_exclusions,
            unavailable_slots=unavailable_slots,
        )
    else:
        normal = _enumerate_candidate_slots(
            buckets,
            office_name_by_id,
            candidate,
            cand,
            target_weekdays=target_weekdays,
            office_ids=office_ids,
            office_code_by_id=office_code_by_id,
            candidate_name=candidate_name,
            config=config,
            max_patients=_max_patients,
            lunch_duration=_lunch_duration,
            lunch_window_start=_lunch_window_start,
            lunch_window_end=_lunch_window_end,
            is_efficiency_alternative=False,
            exclusions_out=raw_exclusions,
            unavailable_slots=unavailable_slots,
            pair_modes=pair_modes,
        )
    results: list[ProposedSlot] = [ps for ps, _eff in normal]

    # 粗ランキング: 合成スコア降順 → 同点は (近さ, 早い時刻, 拠点, コード) で安定化.
    # (delta を厳密計算する上位 DELTA_EVAL_LIMIT 件の母集団選定に使う.)
    results.sort(
        key=lambda r: (
            -r.score,
            _time_to_min(r.start),
            str(r.office_id),
            r.weekday,
            r.course_code,
        )
    )

    # P3-④ 効率代替の閾値 (通常候補の最高スコア) は delta 再ソート前に確定する.
    max_normal_score = results[0].score if results else 0.0

    # P-1a: 上位 DELTA_EVAL_LIMIT 件のみ厳密限界コストを計算し、delta 昇順に再ソート.
    _assign_marginal_and_resort(results, buckets, candidate, config=config)

    # P-1b: 通常候補 0 件のとき、除外理由を集約して exclusions_out へ (非空を保証).
    if exclusions_out is not None and not results:
        exclusions_out.extend(
            _aggregate_exclusions(
                raw_exclusions or [],
                buckets,
                office_ids=office_ids,
                target_weekdays=target_weekdays,
            )
        )

    if not include_efficiency_alternatives or not results or candidate.requires_multiple_staff:
        # 既定 (False) / 通常候補ゼロ / 2 名体制 (ペアのみ) 時は効率代替を付けない.
        return results

    # P3-④ 効率優先の代替枠:
    #   time_type / preferred_weekdays のハード制約を外した候補 (time_type=None・全曜日)
    #   で再列挙し、通常候補と重複しない slot (office×weekday×course×start) のうち
    #   「proximity+balance 合計 > 通常候補の最高スコア」を満たすものを効率的とみなす.
    #   最大 3 件を通常候補リストの後ろに付加する (通常候補の順位は不変).
    #   delta 再ソートで results[0] は最高スコアとは限らないため、事前確定した値を使う.
    threshold = max_normal_score
    normal_keys = {(r.office_id, r.weekday, r.course_code, r.start) for r in results}
    alt_cand = Candidate(
        lat=candidate.lat,
        lng=candidate.lng,
        service_minutes=candidate.service_minutes,
        time_type=None,
        preferred_start=None,
        preferred_end=None,
        patient_id=str(candidate.existing_patient_id) if candidate.existing_patient_id else None,
    )
    alt = _enumerate_candidate_slots(
        buckets,
        office_name_by_id,
        candidate,
        alt_cand,
        target_weekdays=frozenset(range(7)),
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        candidate_name=candidate_name,
        config=config,
        max_patients=_max_patients,
        lunch_duration=_lunch_duration,
        lunch_window_start=_lunch_window_start,
        lunch_window_end=_lunch_window_end,
        is_efficiency_alternative=True,
        unavailable_slots=unavailable_slots,
        pair_modes=pair_modes,
    )
    picked: list[tuple[float, ProposedSlot]] = []
    for ps, eff in alt:
        key = (ps.office_id, ps.weekday, ps.course_code, ps.start)
        if key in normal_keys:
            continue  # 通常候補と重複する枠は効率代替にしない.
        if eff <= threshold:
            continue  # 通常候補の最高スコアを超える効率のものだけを採る.
        picked.append((eff, ps))
    # 効率 (proximity+balance) 降順 → 同点は (早い時刻, 拠点, 曜日, コード) で安定化.
    picked.sort(
        key=lambda t: (
            -t[0],
            _time_to_min(t[1].start),
            str(t[1].office_id),
            t[1].weekday,
            t[1].course_code,
        )
    )
    results.extend(ps for _eff, ps in picked[:3])
    return results


def compute_proposed_slots(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    *,
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None = None,
    candidate_name: str = "(提案)",
    limit: int = 10,
    config: SchedulingConfig | None = None,
) -> list[ProposedSlot]:
    """全 (開講コース × 候補希望曜日) でソルバを回し、上位 ``limit`` 件を返す.

    実現不能なコース / 曜日は候補に出さない (= 入らない時間は提案しない).

    Phase G-88 Step3: ``config`` を ``compute_all_proposed_slots`` へ伝播する.
    """
    return compute_all_proposed_slots(
        buckets,
        office_name_by_id,
        candidate,
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        candidate_name=candidate_name,
        config=config,
    )[:limit]


def compute_overcapacity_slots(
    buckets: dict[tuple[UUID, int, str], _CourseBucket],
    office_name_by_id: dict[UUID, str],
    candidate: CandidateInput,
    *,
    office_ids: list[UUID],
    office_code_by_id: dict[UUID, str | None] | None = None,
    candidate_name: str = "(提案)",
    config: SchedulingConfig | None = None,
    unavailable_slots: dict[tuple[UUID, int], set[time]] | None = None,
    pair_modes: dict[tuple[UUID, UUID], str] | None = None,
    assign_marginal: bool = True,
) -> list[ProposedSlot]:
    """定員超過の管理者相談プロセス (方式b): 定員 +1 なら入る「定員超過候補」を列挙する.

    通常列挙 (``compute_all_proposed_slots``) とは独立した **2 回目の列挙**:
        - ``config`` の ``max_patients_per_course`` を +1 したコピー (``dataclasses.replace``
          で作り、元 ``config`` は一切 mutate しない) を solver へ渡し、容量判定だけを
          1 名ぶん緩める. 時間系制約 (90分同住所占有 / 昼休み / 移動 / 営業枠 / 18:00 /
          COURSE_MAX_MINUTES) は通常とまったく同一に効く (= 「時間は入る」保証はソルバが担保).
        - 「base 定員では入らないが +1 なら入る」= 当該バケットの現在人数が **base 定員以上**
          のバケットの slot のみを残す (base 定員未満のバケットは通常候補と同じ枠なので除外).
        - 各 slot に ``overcapacity=True`` を付け、delta (``marginal_cost_minutes``) も通常候補と
          同一の物差しで付与する (``assign_marginal=False`` のとき delta 計算はスキップ = 件数のみ).

    1 回目の通常列挙・既存経路 (``compute_all_proposed_slots``) は一切変更しない.
    """
    base_max = config.max_patients_per_course if config is not None else _MAX_PATIENTS_PER_COURSE
    base_config = config if config is not None else DEFAULT_SCHEDULING_CONFIG
    over_config = replace(base_config, max_patients_per_course=base_max + 1)
    over_max = base_max + 1

    target_weekdays = candidate.preferred_weekdays or frozenset(range(7))
    cand = Candidate(
        lat=candidate.lat,
        lng=candidate.lng,
        service_minutes=candidate.service_minutes,
        time_type=candidate.time_type,
        preferred_start=candidate.preferred_start,
        preferred_end=candidate.preferred_end,
        patient_id=str(candidate.existing_patient_id) if candidate.existing_patient_id else None,
    )

    enumerated = _enumerate_candidate_slots(
        buckets,
        office_name_by_id,
        candidate,
        cand,
        target_weekdays=target_weekdays,
        office_ids=office_ids,
        office_code_by_id=office_code_by_id,
        candidate_name=candidate_name,
        config=over_config,
        max_patients=over_max,
        lunch_duration=over_config.lunch_duration_min,
        lunch_window_start=over_config.lunch_window_start,
        lunch_window_end=over_config.lunch_window_end,
        is_efficiency_alternative=False,
        unavailable_slots=unavailable_slots,
        pair_modes=pair_modes,
    )

    # 「base 定員では入らないが +1 なら入る」= バケットの現在人数が base 定員以上の枠のみ.
    results: list[ProposedSlot] = []
    for ps, _eff in enumerated:
        bucket = buckets.get((ps.office_id, ps.weekday, ps.course_code))
        if bucket is None:
            continue
        if len(bucket.visits) < base_max:
            continue  # base 定員未満 = 通常候補で既に出る枠 (超過ではない).
        ps.overcapacity = True
        results.append(ps)

    # 粗ランキング (通常候補と同じ物差し) → delta 昇順に再ソート.
    results.sort(
        key=lambda r: (
            -r.score,
            _time_to_min(r.start),
            str(r.office_id),
            r.weekday,
            r.course_code,
        )
    )
    if assign_marginal:
        _assign_marginal_and_resort(results, buckets, candidate, config=config)
    return results


@dataclass
class CoverageDay:
    """カバレッジ 1 曜日ぶん (実現可否 + その曜日の最良枠)."""

    weekday: int
    has_slot: bool
    best_slot: ProposedSlot | None


@dataclass
class Coverage:
    """週N日カバレッジ集計 (API schema に詰める前の内部表現)."""

    required_days: int
    requested_weekdays: list[int]
    per_day: list[CoverageDay]
    covered_days: int
    fully_covered: bool


def compute_coverage(
    all_proposed: list[ProposedSlot],
    *,
    requested_weekdays: frozenset[int],
    required_days: int,
) -> Coverage:
    """ランキング済み全スロットを曜日ごとにグルーピングしカバレッジを算出する.

    ``all_proposed`` はスコア降順済み (``compute_all_proposed_slots`` の戻り値)
    を想定. 各希望曜日について、その曜日の最上位スロットを最良枠とし、
    1 つでもあれば has_slot=True とする (実現可能性判定自体は流用; 再計算しない).

    Args:
        all_proposed: limit なしのランキング済みスロット列.
        requested_weekdays: 希望曜日 (空なら全曜日を対象にする).
        required_days: 必要日数 (frequency_per_week 優先, 無ければ希望曜日数).
    """
    target_weekdays = sorted(requested_weekdays) if requested_weekdays else list(range(7))

    # 曜日 → その曜日の最上位スロット (all_proposed はスコア降順なので最初に出た物).
    best_by_weekday: dict[int, ProposedSlot] = {}
    for slot in all_proposed:
        best_by_weekday.setdefault(slot.weekday, slot)

    per_day: list[CoverageDay] = []
    covered_days = 0
    for wd in target_weekdays:
        best = best_by_weekday.get(wd)
        has_slot = best is not None
        if has_slot:
            covered_days += 1
        per_day.append(CoverageDay(weekday=wd, has_slot=has_slot, best_slot=best))

    fully_covered = required_days > 0 and covered_days >= required_days
    return Coverage(
        required_days=required_days,
        requested_weekdays=target_weekdays,
        per_day=per_day,
        covered_days=covered_days,
        fully_covered=fully_covered,
    )


__all__ = [
    "DELTA_EVAL_LIMIT",
    "CandidateInput",
    "Coverage",
    "CoverageDay",
    "ExcludedReasonSummary",
    "ProposedSlot",
    "compute_all_proposed_slots",
    "compute_coverage",
    "compute_overcapacity_slots",
    "compute_proposed_slots",
    "load_week_course_buckets",
]
