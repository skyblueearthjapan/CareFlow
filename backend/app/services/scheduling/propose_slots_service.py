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

from dataclasses import dataclass, field
from datetime import date, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff, StaffShift, StaffWeeklyOverride
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
    _address_bucket,
    compute_lunch_window,
    haversine_km,
)
from app.services.scheduling.auto_allocator_v2 import (
    V2Visit as _V2Visit,
)
from app.services.scheduling.config import SchedulingConfig
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
    ExistingVisit,
    Slot,
    find_available_slots_for_candidate,
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
    # N-3: 割付スタッフ実態判定用. assigned_staff_id が None なら staff_unassigned.
    # staff_sex は性別不適合判定 (candidate.sex_restriction と突合) に使う.
    # staff_absent は当該 weekday の非番/当週休みを load 時に事前計算した結果.
    assigned_staff_id: UUID | None = None
    staff_sex: str | None = None
    staff_absent: bool = False
    visits: list[_V2Visit] = field(default_factory=list)


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
    ``course_closed`` (希望曜日に開講コース無し).
    """

    reason: str
    count: int
    weekday: int
    sample_course_code: str | None


# 集約時の理由優先度 (1 コースが複数地点で候補を落とした場合、代表理由を決定的に選ぶ).
# capacity_full を最優先: 容量上限は「そもそも走査に入れない」根本原因のため.
_EXCLUSION_REASON_PRIORITY: tuple[str, ...] = (
    "capacity_full",
    "travel_shortage",
    "lunch_window",
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
    from datetime import timedelta

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

        for b in buckets.values():
            sid = b.assigned_staff_id
            if sid is None:
                continue
            shift_key = (sid, b.weekday)
            shift_on = shift_on_by_key.get(shift_key)  # None = シフト行なし
            shift_absent = shift_on is None or shift_on is False
            b.staff_absent = shift_absent or (shift_key in override_off_keys)

    return buckets, office_name_by_id, office_code_by_id


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
            "_sort": _time_to_min(slot.start),
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

# スコア降格ペナルティ. 既存重み (proximity 50 / preference 30 / balance 20 /
# pair 1000) を基準にした相対値:
#   ABSENT / SEX_MISMATCH = 60.0 … proximity 満点 (50) + preference 片側一致 (15)
#     を上回る大きさ. クリーンな候補より確実に下位へ沈める (現場の信頼確保のため
#     「休みスタッフ/性別不適合」の枠は必ず後ろに回す). pair 1000 は超えないので
#     同住所ペアの最優先は維持される.
#   UNASSIGNED = 20.0 … 週次 Layer3 割付で解消され得る軽度事象なので軽い降格.
_PENALTY_STAFF_ABSENT: float = 60.0
_PENALTY_STAFF_SEX_MISMATCH: float = 60.0
_PENALTY_STAFF_UNASSIGNED: float = 20.0


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
    is_efficiency_alternative: bool = False,
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
    if candidate.requires_multiple_staff:
        warnings.append("two_staff_not_guaranteed")
    # N-3: 割付スタッフ実態の警告 (除外はせず注意喚起のみ). FE が日本語ラベル化する.
    if staff_unassigned:
        warnings.append(_WARN_STAFF_UNASSIGNED)
    if staff_absent:
        warnings.append(_WARN_STAFF_ABSENT)
    if staff_sex_mismatch:
        warnings.append(_WARN_STAFF_SEX_MISMATCH)
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
    if staff_unassigned:
        score -= _PENALTY_STAFF_UNASSIGNED
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
        )
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

            reasons, warnings = _slot_reasons_and_warnings(
                slot,
                candidate,
                min_dist_km=min_dist,
                remaining_count=remaining_count,
                matched_weekday=matched_weekday,
                staff_unassigned=staff_unassigned,
                staff_absent=staff_absent,
                staff_sex_mismatch=staff_sex_mismatch,
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
            )
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
                    ),
                    eff,
                )
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
        ps.marginal_cost_minutes = _marginal_cost_minutes(
            existing, ps.start, ps.end, candidate, config=config
        )
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

    if not include_efficiency_alternatives or not results:
        # 既定 (False) または通常候補ゼロ時は効率代替を付けない.
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
    "compute_proposed_slots",
    "load_week_course_buckets",
]
