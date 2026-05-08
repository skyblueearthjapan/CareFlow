"""Layer3Assigner — W4-BE9.

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.4 / §5.4 / §3.6.7
と API 契約 ``docs/plans/v2-api-contracts.md`` §4.6
(`POST /api/v1/courses/assign-staff`) に対応する Layer 3 アルゴリズム。

責務 (§5.4):
    - 確定済みコース (course_status='course_fixed') を読み出し
    - 当該週の稼働スタッフリストとマッチング (ハンガリアン法 + ローテーション)
    - 結果を courses.assigned_staff_id に書き込み course_status='staff_assigned' に遷移

ハード制約 (§3.6.4):
    - 性別: 患者の sex_restriction を満たすスタッフ
    - 勤務日: 当該曜日にスタッフ勤務 (StaffShift.is_on=True)
    - 単一性: 1 スタッフは 1 日 1 コース
    - 役割: マネージャー (M1) は対象外 (role='manager' を除外)

MVP 前提 (Q3=ハイブリッド, §5.4 / 受入基準):
    - 直近 1 週の担当者 (= staff × course の組み合わせ) は強制除外
    - それ以前 N 週分はソフトペナルティ (rotation_penalty)

コスト関数 (§5.4):
    cost(weekday, course, staff) =
        α × distance_score(staff の主拠点 → コース重心)
      + β × rotation_penalty(staff × course の最近 N 週の担当回数)
      + γ × gender_mismatch_penalty(該当があれば INF)
      + δ × work_day_violation(勤務曜日違反は INF)

アルゴリズム:
    - ハンガリアン法 (二部マッチング、O(n^3))
    - 規模が小さい (~24 patients × 6 staff × 6 weekdays) ので自前 Hungarian 実装
      (scipy 依存を避ける)。各曜日ごとに独立に解く。
    - 1 曜日あたり O(max(C, S)^3); 全曜日合算 O(W × max(C, S)^3) (§5.4)

トランザクション:
    本サービスは ``db.commit()`` / ``db.rollback()`` を **呼ばない**。
    呼び出し側 (``app/api/v1/courses.py``) が 1 HTTP リクエストを 1
    トランザクションとして包む。
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from datetime import time as time_cls
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
    Course,
)
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff, StaffEvent, StaffShift, StaffWeeklyOverride
from app.models.staff_companion_assignment import StaffCompanionAssignment
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling.layer2_clustering import haversine_km

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — cost function weights (§5.4)
# ---------------------------------------------------------------------------

# α: 距離スコアの係数 (km) — 主拠点 → コース重心
COST_ALPHA_DISTANCE: float = 1.0

# β: ローテーションペナルティの係数 — 最近 N 週で同一 course を担当した回数
#    1 回担当 = 5.0 のペナルティ (距離換算で 5 km 相当)
COST_BETA_ROTATION: float = 5.0

# γ: 性別ミスマッチ — INF 相当 (ハード制約)
COST_GAMMA_GENDER_MISMATCH: float = math.inf

# δ: 勤務曜日違反 — INF 相当 (ハード制約)
COST_DELTA_WORK_DAY: float = math.inf

# ローテーション履歴で参照する週数 (直近 N 週)
ROTATION_HISTORY_WEEKS: int = 4

# Q3 ハイブリッド: 直近 EXCLUSION_WEEKS 週の担当者は強制除外
ROTATION_EXCLUSION_WEEKS: int = 1

# W16: 前日と同じコースを同一スタッフが担当した場合のペナルティ
# 距離 km 換算で十分大きい (=実用上ほぼ強制回避) ようにする。
# ハード INF にしないのは「他に勤務可能なスタッフが居ない」場合の救済のため。
COST_W16_PREV_DAY_SAME_COURSE: float = 100.0

# ハンガリアン法でダミー行/列に使う「実質的に無限大」のコスト。
# math.inf は加算で扱いにくいので有限大の値を使う。
HUNGARIAN_INFINITY: float = 1.0e12

# 1 件 = 大きな整数化のための倍率 (Hungarian 内部で int 化したいときに使う)
# 浮動小数のまま処理するので未使用。残しておくと後々 numpy 化のヒントになる。
COST_SCALE: int = 10_000

# W33: 移動・準備のためのバッファ (分)。event 終了後 / visit 開始前に
# BUFFER_MINUTES 分未満の余裕しかない場合はハード除外する。
# 運用要件に応じて変更可 (例: 10 分 / 30 分)。
BUFFER_MINUTES: int = 15  # 移動・準備の余裕 (Wave 33)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Layer3AssignmentError(Exception):
    """サービス層から HTTP 層へ伝播する業務エラー (4xx に翻訳される)."""

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


# ---------------------------------------------------------------------------
# Input / Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StaffInfo:
    """ハンガリアン法に渡すスタッフ情報."""

    staff_id: UUID
    name: str
    sex: str | None  # "male" / "female" / None
    role: str  # "admin" / "manager" / "staff"
    primary_office_lat: float | None
    primary_office_lng: float | None
    work_days: frozenset[int]  # 0=Mon..6=Sun
    is_trainee: bool = False  # W10-BE2: 新人フラグ


@dataclass(frozen=True)
class VisitTimeSlot:
    """W27 — Layer 3 で event 重複判定に使う visit 時間帯."""

    start_time: time_cls
    end_time: time_cls


@dataclass
class CourseAssignmentTarget:
    """1 つの (weekday × course) を 1 行と見立てた assignment 対象."""

    course_id: UUID
    weekday: int
    course_code: str
    centroid_lat: float | None
    centroid_lng: float | None
    # 患者の性別制限の集合 (例: {"female"} は女性スタッフのみ受け入れ可)
    gender_restrictions: frozenset[str]
    patient_ids: list[UUID] = field(default_factory=list)
    # W27: コース所属 visit の時間帯 (start_time, end_time) の一覧.
    # StaffEvent との重複判定に用いる. 空リストのときは event 除外を skip.
    visits: list[VisitTimeSlot] = field(default_factory=list)


class StaffAssignment(BaseModel):
    """API レスポンスの 1 件 (`assignments[]` の要素)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    course_code: str
    course_id: UUID
    staff_id: UUID


@dataclass
class Layer3Result:
    """Layer 3 の総合出力."""

    assignments: list[StaffAssignment] = field(default_factory=list)
    rotation_score: float = 0.0  # ローテ分散度 (低いほど分散している)
    total_distance_km: float = 0.0


# ---------------------------------------------------------------------------
# W27 Phase A: StaffEvent overlap helpers
# ---------------------------------------------------------------------------


def _strip_tz(dt: datetime) -> datetime:
    """tz-aware と naive の datetime を一律 naive (壁時計) に揃える.

    ``StaffEvent.starts_at`` / ``ends_at`` は DB 上 ``DateTime(timezone=True)``
    だが、 ``app.api.v1.staff_events._combine`` は naive を INSERT する。
    SQLAlchemy + SQLite では naive がそのまま戻るが、PostgreSQL 経由では
    UTC tz が付与されるため、両方に対応するため tz を剥がして比較する。
    visit 側は ``datetime.combine(visit_date, time)`` で naive として作るので
    比較相手も naive に揃えた方が安全 (= naive 同士なら DB 種別に依らず
    壁時計比較で一致する).
    """
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _has_event_overlap(
    *,
    staff_id: UUID,
    course: CourseAssignmentTarget,
    weekday: int,
    events_by_staff: dict[UUID, list[StaffEvent]],
    week_monday: date_cls,
) -> bool:
    """``staff`` の event がそのコースの visits 時間帯と 1 件でも重なるなら True.

    重なり判定 (半開区間): ``event.starts_at < visit_end AND event.ends_at > visit_start``
    """
    events = events_by_staff.get(staff_id)
    if not events:
        return False
    if not course.visits:
        return False
    target_date = week_monday + timedelta(days=weekday)
    for visit in course.visits:
        visit_start_dt = datetime.combine(target_date, visit.start_time)
        visit_end_dt = datetime.combine(target_date, visit.end_time)
        for event in events:
            ev_start = _strip_tz(event.starts_at)
            ev_end = _strip_tz(event.ends_at)
            if ev_start < visit_end_dt and ev_end > visit_start_dt:
                return True
    return False


def _has_event_overlap_with_buffer(
    *,
    staff_id: UUID,
    course: CourseAssignmentTarget,
    weekday: int,
    events_by_staff: dict[UUID, list[StaffEvent]],
    week_monday: date_cls,
) -> bool:
    """``staff`` の event が visit 時間帯と **バッファ込みで** 重なるなら True.

    W33 追加: ``BUFFER_MINUTES`` 分のバッファを event 両端に付与した拡張区間と
    visit 時間帯との重複を判定する。

    例: event 14:00-14:30, visit 14:45 開始
        → event_end_buffered = 14:30 + 15 分 = 14:45
        → 半開区間で 14:45 > 14:45 は False → ギリギリ OK (= 16 分以上の余裕なし)
        ※ visit 14:46 開始ならセーフ (event_end_buffered 14:45 < 14:46)

    「15:00-15:30 event + 15:30 visit」のような詰め込みを防止する。

    重なり判定 (半開区間、バッファ込み):
        ``event_start_buffered < visit_end AND event_end_buffered > visit_start``
    """
    events = events_by_staff.get(staff_id)
    if not events:
        return False
    if not course.visits:
        return False
    target_date = week_monday + timedelta(days=weekday)
    buf = timedelta(minutes=BUFFER_MINUTES)
    for visit in course.visits:
        visit_start_dt = datetime.combine(target_date, visit.start_time)
        visit_end_dt = datetime.combine(target_date, visit.end_time)
        for event in events:
            ev_start = _strip_tz(event.starts_at)
            ev_end = _strip_tz(event.ends_at)
            ev_start_buffered = ev_start - buf
            ev_end_buffered = ev_end + buf
            if ev_start_buffered < visit_end_dt and ev_end_buffered > visit_start_dt:
                return True
    return False


# ---------------------------------------------------------------------------
# Hungarian algorithm (square matrix)
# ---------------------------------------------------------------------------


def hungarian_min_cost(cost: list[list[float]]) -> list[int]:
    """正方行列 ``cost`` に対する最小コストの割当を返す.

    入力は n×n の浮動小数行列で、``cost[i][j]`` は行 i を列 j に割り当てたときの
    コスト。返り値は長さ n の int 配列で、``result[i] = j`` なら行 i に列 j を
    割り当てる。

    実装: O(n^3) の Jonker–Volgenant 形式 (= Kuhn-Munkres の標準実装).

    用途: 規模が小さい (n <= 10) ので自前実装で十分。scipy 依存を避ける。

    禁止セルは ``HUNGARIAN_INFINITY`` で表現する。math.inf は使わない
    (内部で双対変数を引き算するときに NaN になり得るため)。
    """
    n = len(cost)
    if n == 0:
        return []
    for row in cost:
        if len(row) != n:
            raise ValueError(f"cost matrix is not square: row len {len(row)} != n {n}")

    # u[0..n], v[0..n], p[0..n]: 1-based の補助配列 (慣例).
    inf_sentinel = HUNGARIAN_INFINITY * 10  # ループ内のセンチネル
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf_sentinel] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf_sentinel
            j1 = 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        # augment
        while j0 != 0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    # p[j] = i  →  row (i-1) is assigned to col (j-1)
    assignment = [-1] * n
    for j in range(1, n + 1):
        i = p[j]
        if i != 0:
            assignment[i - 1] = j - 1
    return assignment


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Layer3Assigner:
    """Layer 3: スタッフ割付 (ハンガリアン法 + ローテーション)."""

    async def assign(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        office_id: UUID | None = None,
    ) -> Layer3Result:
        """指定週の確定済みコースに対しスタッフを割り付ける.

        Args:
            db: 共有 SQLAlchemy セッション.
            iso_year: ISO 年.
            iso_week: ISO 週 (1-53).
            office_id: W16 — 対象拠点 (None=全拠点合算).

        Returns:
            Layer3Result: 割当結果 + ローテーションスコア + 総距離.

        Raises:
            Layer3AssignmentError: 入力範囲外などの業務エラー.
        """
        if iso_year < 2000 or iso_year > 2100:
            raise Layer3AssignmentError(f"iso_year out of range: {iso_year}")
        if iso_week < 1 or iso_week > 53:
            raise Layer3AssignmentError(f"iso_week out of range: {iso_week}")
        try:
            week_monday = date_cls.fromisocalendar(iso_year, iso_week, 1)
        except ValueError as exc:
            raise Layer3AssignmentError(
                f"invalid ISO week: year={iso_year} week={iso_week}",
            ) from exc

        # ---------- 1. 確定済みコース取得 (course_fixed) ----------
        course_targets = await self._load_course_targets(
            db, iso_year=iso_year, iso_week=iso_week, office_id=office_id
        )

        # ---------- 2. 稼働スタッフ取得 ----------
        staff_pool = await self._load_active_staff(
            db, iso_year=iso_year, iso_week=iso_week, week_monday=week_monday
        )

        # ---------- 3. ローテーション履歴取得 ----------
        history = await self._load_rotation_history(
            db,
            iso_year=iso_year,
            iso_week=iso_week,
            history_weeks=ROTATION_HISTORY_WEEKS,
        )

        # ---------- 4. W16 固定割当 (manager -> M / 都賀 staff -> 都賀 A) ----------
        fixed_staff_by_course = await self._build_fixed_assignments(
            db, iso_year=iso_year, iso_week=iso_week, office_id=office_id
        )

        # ---------- 4b. Bug #2 fix (W25): staff_assigned コースを保護 ----------
        # assign-staff-only 再実行時に、既に staff_assigned 状態のコースの担当スタッフが
        # 別コースのハンガリアンに巻き込まれて重複割付される問題を防ぐ。
        # staff_assigned コースを fixed_staff_by_course に追加することで、
        # Layer 3 内の既存「固定スタッフ除外」ロジックが自動適用される。
        already_assigned_stmt = select(Course).where(
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.deleted_at.is_(None),
            Course.course_status == COURSE_STATUS_STAFF_ASSIGNED,
            Course.assigned_staff_id.isnot(None),
        )
        already_assigned_courses = list((await db.scalars(already_assigned_stmt)).all())
        for c in already_assigned_courses:
            if c.id not in fixed_staff_by_course and c.assigned_staff_id is not None:
                fixed_staff_by_course[c.id] = c.assigned_staff_id

        # ---------- 4c. W27 Phase A: StaffEvent 取得 (event 時間帯で重なる
        #               staff×course をハード除外するため) ----------
        events_by_staff = await self._load_staff_events(
            db,
            staff_ids=[s.staff_id for s in staff_pool],
            week_monday=week_monday,
        )

        # ---------- 5. 計算 ----------
        result = self.solve(
            course_targets,
            staff_pool,
            history=history,
            fixed_staff_by_course=fixed_staff_by_course,
            events_by_staff=events_by_staff,
            week_monday=week_monday,
        )

        # ---------- 6. DB 反映 ----------
        trainee_ids = frozenset(s.staff_id for s in staff_pool if s.is_trainee)
        await self._persist(db, result.assignments, trainee_staff_ids=trainee_ids)

        return result

    # ------------------------------------------------------------------ #
    # 純粋関数: solve()
    # ------------------------------------------------------------------ #

    def solve(
        self,
        course_targets: list[CourseAssignmentTarget],
        staff_pool: list[StaffInfo],
        *,
        history: list[tuple[int, str, UUID]] | None = None,
        fixed_staff_by_course: dict[UUID, UUID] | None = None,
        same_course_prev_day_penalty: bool = True,
        events_by_staff: dict[UUID, list[StaffEvent]] | None = None,
        week_monday: date_cls | None = None,
    ) -> Layer3Result:
        """純粋関数版エントリポイント (テスト / fixture 評価で直接使う).

        Args:
            course_targets: 確定済みコースのリスト (=対象).
            staff_pool: 稼働スタッフのリスト.
            history: ``[(weeks_ago, course_code, staff_id), ...]`` の履歴.
                ``weeks_ago`` は当該週からの距離 (1 = 直近 1 週前).
            fixed_staff_by_course: W16 — 事前固定割当 (course_id -> staff_id).
                指定された course はマッチングを skip し直接当該 staff を割当て、
                かつ当該 staff はその曜日の他の course から除外される.
                典型例: 川名(manager) -> M / 関谷 -> 都賀 A.
            same_course_prev_day_penalty: W16 — True のとき、同一スタッフが
                前日と同じ course_code を担当することにペナルティを付与する
                (= 同患者連続回避).
            events_by_staff: W27 Phase A — ``staff_id -> [StaffEvent, ...]``.
                event 時間帯と course 内 visit 時間帯が重なる staff は
                当該 course から **ハード除外** される (HUNGARIAN_INFINITY).
                ``None`` または空辞書 = event 除外無し (regression 互換).
            week_monday: W27 Phase A — 当該 ISO 週の月曜日 (date).
                ``events_by_staff`` 利用時に必須。weekday から実日付を
                算出して event 時間帯と比較する。

        Returns:
            Layer3Result.

        Notes:
            - マネージャー (role='manager') は **fixed_staff_by_course で割当
              対象外でない限り** staff_pool から自動除外する.
              W16 では manager を M コースに固定割当する用途のため、
              fixed 経由で割り当てられた manager は許容する.
            - 各曜日ごとに独立にハンガリアン法を適用 (1 スタッフ 1 日 1 コース原則).
            - W16: 曜日順 (Mon -> Sat) に解き、前日割当を後続曜日へ伝搬する.
        """
        if history is None:
            history = []
        if fixed_staff_by_course is None:
            fixed_staff_by_course = {}
        if events_by_staff is None:
            events_by_staff = {}

        # 固定割当先となる staff_id 集合 (= manager を除外しない対象)
        fixed_staff_ids: set[UUID] = set(fixed_staff_by_course.values())

        # マネージャー除外 (§3.6.4) — ただし fixed 割当対象の manager は保持
        # (fixed 経路で M コースへの lookup に使用するため eligible_staff に残す必要がある)
        eligible_staff = [
            s for s in staff_pool if s.role != "manager" or s.staff_id in fixed_staff_ids
        ]

        # 曜日でグルーピング
        by_weekday: dict[int, list[CourseAssignmentTarget]] = {}
        for ct in course_targets:
            by_weekday.setdefault(ct.weekday, []).append(ct)

        all_assignments: list[StaffAssignment] = []
        total_distance = 0.0

        # W16: 「前日同コースペナルティ」用. (course_code, staff_id) を直前曜日
        # の割当から構築する.
        prev_day_pairs: set[tuple[str, UUID]] = set()

        # 各曜日ごとに独立して解く (= 1 スタッフ 1 日 1 コース制約は曜日内で閉じる)
        # W16: 曜日順 (Mon -> Sat) に解き、前日割当を後続曜日へ伝搬する.
        for weekday in sorted(by_weekday.keys()):
            day_courses = by_weekday[weekday]
            day_assignments = self._solve_one_day(
                weekday=weekday,
                day_courses=day_courses,
                staff_pool=eligible_staff,
                history=history,
                fixed_staff_by_course=fixed_staff_by_course,
                prev_day_pairs=prev_day_pairs if same_course_prev_day_penalty else set(),
                events_by_staff=events_by_staff,
                week_monday=week_monday,
            )
            for a in day_assignments:
                all_assignments.append(a)
                # 距離集計
                course = next(c for c in day_courses if c.course_id == a.course_id)
                staff = next(s for s in eligible_staff if s.staff_id == a.staff_id)
                total_distance += self._distance_km(course, staff)

            # 当日割当を「前日割当」として次曜日へ受け渡し
            prev_day_pairs = {(a.course_code, a.staff_id) for a in day_assignments}

        # ローテーション分散度 (Gini) — fixed 割当は分散度計算対象外
        # (固定スタッフはローテーション対象ではないため)
        rotatable_assignments = [a for a in all_assignments if a.staff_id not in fixed_staff_ids]
        rotatable_staff_count = max(
            1, len([s for s in eligible_staff if s.staff_id not in fixed_staff_ids])
        )
        rotation_score = self._gini_index(
            [a.staff_id for a in rotatable_assignments],
            staff_count=rotatable_staff_count,
        )

        return Layer3Result(
            assignments=all_assignments,
            rotation_score=round(rotation_score, 6),
            total_distance_km=round(total_distance, 4),
        )

    # ------------------------------------------------------------------ #
    # private: per-weekday solver
    # ------------------------------------------------------------------ #

    def _solve_one_day(
        self,
        *,
        weekday: int,
        day_courses: list[CourseAssignmentTarget],
        staff_pool: list[StaffInfo],
        history: list[tuple[int, str, UUID]],
        fixed_staff_by_course: dict[UUID, UUID] | None = None,
        prev_day_pairs: set[tuple[str, UUID]] | None = None,
        events_by_staff: dict[UUID, list[StaffEvent]] | None = None,
        week_monday: date_cls | None = None,
    ) -> list[StaffAssignment]:
        """1 曜日内で (course × staff) のハンガリアンを解く.

        Args:
            weekday: 0=Mon..6=Sun.
            day_courses: 当該曜日の course list.
            staff_pool: 稼働スタッフ.
            history: ローテーション履歴.
            fixed_staff_by_course: W16 — 事前固定割当 (course_id -> staff_id).
                対象 course はマッチング対象外とし結果に直接含める. 当該 staff
                は他の course への割当からも除外する.
            prev_day_pairs: W16 — 前日の (course_code, staff_id) 集合.
                同一ペアの再選択にペナルティを付与する (= 同患者連続回避).
            events_by_staff: W27 Phase A — staff_id -> [StaffEvent, ...].
            week_monday: W27 Phase A — 当該週月曜日 (visit 時刻と event の
                重複判定に必要).
        """
        if not day_courses or not staff_pool:
            return []
        if fixed_staff_by_course is None:
            fixed_staff_by_course = {}
        if prev_day_pairs is None:
            prev_day_pairs = set()
        if events_by_staff is None:
            events_by_staff = {}

        # ----- W16: 固定割当を先に剥がす -----
        result: list[StaffAssignment] = []
        fixed_courses: list[CourseAssignmentTarget] = []
        free_courses: list[CourseAssignmentTarget] = []
        for course in day_courses:
            staff_id = fixed_staff_by_course.get(course.course_id)
            if staff_id is None:
                free_courses.append(course)
                continue
            # 当該 staff が当日の staff_pool に存在しなければ skip (work_days 違反など)
            staff = next((s for s in staff_pool if s.staff_id == staff_id), None)
            if staff is None or weekday not in staff.work_days:
                # 固定スタッフが当日勤務でない → 当該コースは未割当のまま
                fixed_courses.append(course)
                continue
            result.append(
                StaffAssignment(
                    weekday=weekday,
                    course_code=course.course_code,
                    course_id=course.course_id,
                    staff_id=staff.staff_id,
                )
            )
            fixed_courses.append(course)

        # 固定で取られたスタッフは free のマッチング対象から除外。
        # Bug #1 fix (W25): result に含まれる staff だけでなく、fixed_staff_by_course の
        # 全 staff_id を除外する。当該曜日に勤務しない manager は fixed 経路で skip され
        # result に含まれないが、eligible_staff には残っているため free_staff に混入し
        # ハンガリアンで E 等のコースに重複割付される問題を修正。
        all_fixed_staff_ids: set[UUID] = set(fixed_staff_by_course.values())
        fixed_assigned_staff_ids: set[UUID] = {a.staff_id for a in result}
        free_staff = [
            s
            for s in staff_pool
            if s.staff_id not in fixed_assigned_staff_ids and s.staff_id not in all_fixed_staff_ids
        ]

        if not free_courses or not free_staff:
            return result

        n_courses = len(free_courses)
        n_staff = len(free_staff)
        n = max(n_courses, n_staff)  # 正方化

        # cost[i][j] = (course i, staff j) のコスト. ダミー行/列は 0.0 で埋める.
        cost: list[list[float]] = [[0.0] * n for _ in range(n)]

        for i, course in enumerate(free_courses):
            for j, staff in enumerate(free_staff):
                cost[i][j] = self._cost_single_cell(
                    weekday=weekday,
                    course=course,
                    staff=staff,
                    history=history,
                    prev_day_pairs=prev_day_pairs,
                    events_by_staff=events_by_staff,
                    week_monday=week_monday,
                )
        # ダミー行 (i >= n_courses): 全列コスト 0  → 「未割当の course/staff」を吸収
        # ダミー列 (j >= n_staff): 全行コスト 0
        # → 既に 0 で埋めている。OK.

        assignment = hungarian_min_cost(cost)

        # 結果フィルタリング: 実コース × 実スタッフかつ INF 未満のもののみ採用
        for i in range(n_courses):
            j = assignment[i]
            if j < 0 or j >= n_staff:
                continue
            if cost[i][j] >= HUNGARIAN_INFINITY:
                # ハード制約違反のセル — 割当不能
                continue
            course = free_courses[i]
            staff = free_staff[j]
            result.append(
                StaffAssignment(
                    weekday=weekday,
                    course_code=course.course_code,
                    course_id=course.course_id,
                    staff_id=staff.staff_id,
                )
            )

        return result

    def _cost_single_cell(
        self,
        *,
        weekday: int,
        course: CourseAssignmentTarget,
        staff: StaffInfo,
        history: list[tuple[int, str, UUID]],
        prev_day_pairs: set[tuple[str, UUID]] | None = None,
        events_by_staff: dict[UUID, list[StaffEvent]] | None = None,
        week_monday: date_cls | None = None,
    ) -> float:
        """単一セル (course, staff) のコストを返す.

        コスト関数 (§5.4 + W16 拡張 + W27 Phase A 拡張 + W33 バッファ拡張):
            cost = α * distance
                 + β * rotation_penalty
                 + γ * gender
                 + δ * work_day
                 + W16: 前日同コース penalty
                 + W27/W33: event 時間帯重複 + BUFFER_MINUTES バッファ (ハード除外)

        γ / δ / W27/W33 はハード制約なので INF 相当 (= ``HUNGARIAN_INFINITY``).
        """
        if prev_day_pairs is None:
            prev_day_pairs = set()
        if events_by_staff is None:
            events_by_staff = {}

        # ---------- δ: 勤務曜日違反 (ハード制約) ----------
        if weekday not in staff.work_days:
            return HUNGARIAN_INFINITY

        # ---------- γ: 性別ミスマッチ (ハード制約) ----------
        # 患者の sex_restriction (例: "female") はそのスタッフの sex と一致する必要あり
        if course.gender_restrictions:
            if staff.sex is None:
                return HUNGARIAN_INFINITY
            for restriction in course.gender_restrictions:
                if restriction != staff.sex:
                    return HUNGARIAN_INFINITY

        # ---------- W27/W33: StaffEvent 時間帯重複 + バッファ (ハード制約) ----------
        # W27: event 時間帯と course 内 visit 時間帯が重なる場合は除外。
        # W33: BUFFER_MINUTES 分のバッファを加味した拡張区間で判定。
        #      「15:00-15:30 event + 15:30 visit」のような無茶な詰め込みを防止。
        if events_by_staff and week_monday is not None and course.visits:
            if _has_event_overlap_with_buffer(
                staff_id=staff.staff_id,
                course=course,
                weekday=weekday,
                events_by_staff=events_by_staff,
                week_monday=week_monday,
            ):
                return HUNGARIAN_INFINITY

        # ---------- Q3 ハイブリッド: 直近 1 週は強制除外 ----------
        for weeks_ago, course_code, staff_id in history:
            if (
                weeks_ago <= ROTATION_EXCLUSION_WEEKS
                and course_code == course.course_code
                and staff_id == staff.staff_id
            ):
                return HUNGARIAN_INFINITY

        # ---------- α: 距離スコア ----------
        distance = self._distance_km(course, staff)

        # ---------- β: ローテーションペナルティ (ソフト) ----------
        # 直近 ROTATION_HISTORY_WEEKS 週で同一 course_code を担当した回数
        # (直近ほど重み大, weeks_ago=1 -> 重み 1.0, weeks_ago=4 -> 重み 0.25)
        rotation_count = 0.0
        for weeks_ago, course_code, staff_id in history:
            if course_code == course.course_code and staff_id == staff.staff_id:
                if weeks_ago <= ROTATION_EXCLUSION_WEEKS:
                    # ハード除外で既に処理済み
                    continue
                if weeks_ago > ROTATION_HISTORY_WEEKS:
                    continue
                weight = 1.0 / max(1, weeks_ago)
                rotation_count += weight

        # ---------- W16: 前日同コースペナルティ (ソフト) ----------
        # 前日と同じ (course_code, staff_id) を選ぶと大きなコストを足す.
        # ハード INF にしないのは「他に勤務可能なスタッフが居ない」場合の救済のため.
        prev_day_penalty = 0.0
        if (course.course_code, staff.staff_id) in prev_day_pairs:
            prev_day_penalty = COST_W16_PREV_DAY_SAME_COURSE

        return (
            COST_ALPHA_DISTANCE * distance + COST_BETA_ROTATION * rotation_count + prev_day_penalty
        )

    def _distance_km(self, course: CourseAssignmentTarget, staff: StaffInfo) -> float:
        """主拠点 → コース重心の Haversine 距離 (km).

        座標欠損 (None) のときは 0.0 を返す (= 距離項を無効化).
        """
        if (
            course.centroid_lat is None
            or course.centroid_lng is None
            or staff.primary_office_lat is None
            or staff.primary_office_lng is None
        ):
            return 0.0
        return haversine_km(
            staff.primary_office_lat,
            staff.primary_office_lng,
            course.centroid_lat,
            course.centroid_lng,
        )

    def _gini_index(self, items: list[Any], *, staff_count: int) -> float:
        """ローテ分散度を Gini 係数で表現する.

        各スタッフの担当回数の不均等度を 0.0 (完全均等) 〜 1.0 (1 人独占) で返す。
        受入基準: naive round-robin 以下。
        """
        if not items:
            return 0.0
        counts = Counter(items)
        # 担当ゼロのスタッフも分散度評価に含める (= staff_count 全員を母集団とする)
        values = list(counts.values())
        # 担当ゼロのスタッフ数だけ 0 を追加
        zero_staff = max(0, staff_count - len(counts))
        values.extend([0] * zero_staff)
        n = len(values)
        if n == 0:
            return 0.0
        s = sum(values)
        if s == 0:
            return 0.0
        values.sort()
        # Gini = (2 * Σ(i * x_i) ) / (n * Σx) - (n + 1) / n   (1-based i)
        cum_idx = sum((i + 1) * v for i, v in enumerate(values))
        gini = (2.0 * cum_idx) / (n * s) - (n + 1.0) / n
        return max(0.0, gini)

    # ------------------------------------------------------------------ #
    # private: DB I/O
    # ------------------------------------------------------------------ #

    async def _load_course_targets(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        office_id: UUID | None = None,
        include_manager_courses: bool = True,
    ) -> list[CourseAssignmentTarget]:
        """確定済みコース (course_status='course_fixed') をロードして対象に変換.

        各コースの重心は所属する visits の患者 lat/lng の平均で算出。
        性別制限はコース内全患者の sex_restriction 集合。

        Args:
            office_id: 指定時は当該拠点のコースに絞る (W16).
            include_manager_courses: W16 — True のとき code='M' も対象に含める
                (manager 固定割当のため). False で旧挙動 (M を除外).
        """
        where_clauses = [
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.deleted_at.is_(None),
            Course.course_status == COURSE_STATUS_COURSE_FIXED,
        ]
        if not include_manager_courses:
            where_clauses.append(Course.code != "M")  # マネージャー枠は対象外 (§3.6.5)
        if office_id is not None:
            where_clauses.append(Course.office_id == office_id)

        stmt = select(Course).where(*where_clauses).order_by(Course.weekday, Course.code)
        courses = (await db.scalars(stmt)).all()

        targets: list[CourseAssignmentTarget] = []
        for course in courses:
            # コース所属 visits → patient_id → patient (lat/lng/sex_restriction)
            patient_stmt = (
                select(Patient)
                .join(Visit, Visit.patient_id == Patient.id)
                .where(
                    Visit.course_id == course.id,
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                    Patient.deleted_at.is_(None),
                )
            )
            patients = list((await db.scalars(patient_stmt)).all())

            # 重心算出
            lats = [float(p.lat) for p in patients if p.lat is not None]
            lngs = [float(p.lng) for p in patients if p.lng is not None]
            centroid_lat = sum(lats) / len(lats) if lats else None
            centroid_lng = sum(lngs) / len(lngs) if lngs else None

            # 性別制限集合
            restrictions: set[str] = set()
            for p in patients:
                if p.sex_restriction:
                    restrictions.add(p.sex_restriction)

            # W27 Phase A: コース所属 visit の時間帯一覧 (event 重複判定用)
            visit_stmt = select(Visit).where(
                Visit.course_id == course.id,
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
            )
            course_visit_rows = list((await db.scalars(visit_stmt)).all())
            visit_slots = [
                VisitTimeSlot(start_time=v.start_time, end_time=v.end_time)
                for v in course_visit_rows
            ]

            targets.append(
                CourseAssignmentTarget(
                    course_id=course.id,
                    weekday=course.weekday,
                    course_code=course.code,
                    centroid_lat=centroid_lat,
                    centroid_lng=centroid_lng,
                    gender_restrictions=frozenset(restrictions),
                    patient_ids=[p.id for p in patients],
                    visits=visit_slots,
                )
            )
        return targets

    # ------------------------------------------------------------------ #
    # private: companion helpers (W10-BE2 Phase 2 Layer 3)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _visit_part(
        visit_start: time_cls,
        shift_start: time_cls | None,
        shift_end: time_cls | None,
    ) -> str:
        """visit の start_time が午前 ('am') か午後 ('pm') かを返す.

        companion スタッフのシフト (start_time / end_time) の中央時刻を境界とする。
        シフト情報が欠落している場合はデフォルト境界 12:00 を使う。

        Args:
            visit_start: 訪問開始時刻。
            shift_start: companion スタッフのシフト開始時刻 (None = 欠落)。
            shift_end: companion スタッフのシフト終了時刻 (None = 欠落)。

        Returns:
            'am' or 'pm'
        """
        if shift_start is not None and shift_end is not None:
            # シフト中央時刻を秒で算出
            start_sec = shift_start.hour * 3600 + shift_start.minute * 60 + shift_start.second
            end_sec = shift_end.hour * 3600 + shift_end.minute * 60 + shift_end.second
            mid_sec = (start_sec + end_sec) // 2
        else:
            # デフォルト: 正午 (12:00)
            mid_sec = 12 * 3600

        visit_sec = visit_start.hour * 3600 + visit_start.minute * 60 + visit_start.second
        return "am" if visit_sec < mid_sec else "pm"

    async def _resolve_companion_staff_id(
        self,
        db: AsyncSession,
        *,
        trainee_staff_id: UUID,
        weekday: int,
        visit_start: time_cls,
    ) -> UUID | None:
        """新人スタッフ・曜日・訪問時刻に対応する companion staff_id を返す.

        優先順位:
          1. part='full' の companion 割付が存在すれば無条件で採用
          2. visit_start が午前なら part='am'、午後なら part='pm' の割付を採用
          3. 対応する割付がない場合は None を返す

        Args:
            db: SQLAlchemy 非同期セッション。
            trainee_staff_id: 新人スタッフの UUID。
            weekday: 訪問曜日 (0=Mon..6=Sun)。
            visit_start: 訪問開始時刻。

        Returns:
            companion_staff_id または None (割付なし)。
        """
        stmt = select(StaffCompanionAssignment).where(
            StaffCompanionAssignment.trainee_staff_id == trainee_staff_id,
            StaffCompanionAssignment.weekday == weekday,
        )
        rows = list((await db.scalars(stmt)).all())
        if not rows:
            return None

        # part='full' が最優先
        for row in rows:
            if row.part == "full":
                return row.companion_staff_id

        # companion シフトを取得してシフト中央時刻で am/pm 判定
        # (全 companion が同じと仮定; 異なる companion は稀なので最初の am/pm で判断)
        companion_ids = {row.companion_staff_id for row in rows}
        shift_map: dict[UUID, StaffShift] = {}
        for cid in companion_ids:
            shift_stmt = select(StaffShift).where(
                StaffShift.staff_id == cid,
                StaffShift.weekday == weekday,
            )
            shift = (await db.scalars(shift_stmt)).first()
            if shift is not None:
                shift_map[cid] = shift

        # visit の part を判定
        # am/pm 割付が複数ある場合は各 companion の shift で独立判定
        for row in rows:
            shift = shift_map.get(row.companion_staff_id)
            shift_start = shift.start_time if shift is not None else None
            shift_end = shift.end_time if shift is not None else None
            part = self._visit_part(visit_start, shift_start, shift_end)
            if row.part == part:
                return row.companion_staff_id

        return None

    async def _load_active_staff(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        week_monday: date_cls,
    ) -> list[StaffInfo]:
        """稼働スタッフ + 主拠点座標 + 勤務曜日 を取得.

        - ``Staff.status='active'`` のみ
        - ``StaffShift`` から ``is_on=True`` の曜日集合を構築
        - ``StaffWeeklyOverride`` (override_type='off') があれば当該曜日を除外
        """
        # スタッフ + 主拠点 (Office) を取得
        stmt = (
            select(Staff, Office)
            .outerjoin(Office, Office.id == Staff.primary_office_id)
            .where(
                Staff.status == "active",
                Staff.deleted_at.is_(None),
            )
        )
        rows = (await db.execute(stmt)).all()

        # シフト (固定週次) 取得
        staff_ids = [s.id for s, _ in rows]
        if not staff_ids:
            return []
        shift_stmt = select(StaffShift).where(StaffShift.staff_id.in_(staff_ids))
        shifts = list((await db.scalars(shift_stmt)).all())
        shift_map: dict[UUID, set[int]] = {}
        for sh in shifts:
            if sh.is_on:
                shift_map.setdefault(sh.staff_id, set()).add(sh.weekday)

        # 当該週の override を取得 (off は除外, custom_time は曜日 ON とみなす)
        override_stmt = select(StaffWeeklyOverride).where(
            and_(
                StaffWeeklyOverride.iso_year == iso_year,
                StaffWeeklyOverride.iso_week == iso_week,
                StaffWeeklyOverride.staff_id.in_(staff_ids),
            )
        )
        overrides = list((await db.scalars(override_stmt)).all())
        off_days: dict[UUID, set[int]] = {}
        on_days_override: dict[UUID, set[int]] = {}
        for ov in overrides:
            if ov.override_type == "off":
                off_days.setdefault(ov.staff_id, set()).add(ov.weekday)
            elif ov.override_type == "custom_time":
                on_days_override.setdefault(ov.staff_id, set()).add(ov.weekday)

        result: list[StaffInfo] = []
        for staff, office in rows:
            base_days = shift_map.get(staff.id, set())
            # override で off になっている曜日を除外 / custom_time で追加
            effective = (base_days | on_days_override.get(staff.id, set())) - off_days.get(
                staff.id, set()
            )
            result.append(
                StaffInfo(
                    staff_id=staff.id,
                    name=staff.name,
                    sex=staff.sex,
                    role=staff.role,
                    primary_office_lat=(
                        float(office.lat) if office is not None and office.lat is not None else None
                    ),
                    primary_office_lng=(
                        float(office.lng) if office is not None and office.lng is not None else None
                    ),
                    work_days=frozenset(effective),
                    is_trainee=staff.is_trainee,
                )
            )
        return result

    async def _load_staff_events(
        self,
        db: AsyncSession,
        *,
        staff_ids: list[UUID],
        week_monday: date_cls,
    ) -> dict[UUID, list[StaffEvent]]:
        """W27 Phase A: 当該週 (月曜〜日曜) に存在する StaffEvent を staff 別に取得.

        重なり判定の正確性のため、event 区間が週末をまたぐケースも拾えるよう、
        ``starts_at < (week_sunday+1)`` かつ ``ends_at >= week_monday`` で SELECT する.

        ``staff_ids`` が空のときは空 dict を返す.
        """
        if not staff_ids:
            return {}
        week_sunday_plus1 = week_monday + timedelta(days=7)
        # naive datetime で比較する (DB 側 tz の有無に依らず動作させるため
        # SQLAlchemy が tz を付与/剥離するハンドリングに任せる).
        # SQLite (テスト) と PostgreSQL (本番) いずれも naive 値を渡せば
        # column type に合わせた比較に変換される.
        range_start = datetime.combine(week_monday, time_cls(0, 0))
        range_end = datetime.combine(week_sunday_plus1, time_cls(0, 0))
        stmt = select(StaffEvent).where(
            StaffEvent.staff_id.in_(staff_ids),
            StaffEvent.starts_at < range_end,
            StaffEvent.ends_at >= range_start,
        )
        rows = list((await db.scalars(stmt)).all())
        result: dict[UUID, list[StaffEvent]] = {}
        for ev in rows:
            result.setdefault(ev.staff_id, []).append(ev)
        return result

    async def _build_fixed_assignments(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        office_id: UUID | None = None,
    ) -> dict[UUID, UUID]:
        """W16: 固定割当 (manager -> M / 都賀 staff -> 都賀 A) を構築する.

        ロジック:
        1. role='manager' かつ status='active' の各スタッフ
           → そのスタッフの primary_office で M / M2 / .. の course (course_fixed)
              に当該スタッフを 1:1 で割り当てる
           → ラベル並びは M < M2 < M3 < ... の順 (= staff.code 昇順 + manager 順)
        2. 拠点名に '都賀' を含む office の active staff (role='staff')
           → 当該 office の code='A' course にスタッフを 1 名固定
              (複数人居る場合は staff.code 昇順で先頭の 1 名)

        Returns:
            ``{course_id: staff_id}`` の dict.
        """
        result: dict[UUID, UUID] = {}

        # ---------- 対象拠点を取得 ----------
        office_stmt = select(Office).where(Office.deleted_at.is_(None))
        if office_id is not None:
            office_stmt = office_stmt.where(Office.id == office_id)
        offices = list((await db.scalars(office_stmt)).all())
        offices_by_id: dict[UUID, Office] = {o.id: o for o in offices}

        # ---------- 1) manager 固定割当 ----------
        for office in offices:
            mgr_stmt = (
                select(Staff)
                .where(
                    Staff.status == "active",
                    Staff.role == "manager",
                    Staff.deleted_at.is_(None),
                    Staff.primary_office_id == office.id,
                )
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
            )
            managers = list((await db.scalars(mgr_stmt)).all())

            # M / M2 / .. の course (course_fixed) を取得
            mgr_course_stmt = (
                select(Course)
                .where(
                    Course.iso_year == iso_year,
                    Course.iso_week == iso_week,
                    Course.deleted_at.is_(None),
                    Course.course_status == COURSE_STATUS_COURSE_FIXED,
                    Course.office_id == office.id,
                    Course.code == "M",
                )
                .order_by(Course.weekday)
            )
            mgr_courses = list((await db.scalars(mgr_course_stmt)).all())

            # NOTE: code 列は ('A','B','C','D','E','M') CHECK 制約があるため
            # 複数 manager の場合も全て code='M' で運用される (W16 想定 N=1).
            # 同 (year, week, weekday) 内に M course が複数あればラベル順に
            # 1 manager ずつ割当てる.
            # 曜日ごとに (course_id) のリストを構築
            by_weekday: dict[int, list[Course]] = {}
            for c in mgr_courses:
                by_weekday.setdefault(c.weekday, []).append(c)
            for _weekday, day_courses in by_weekday.items():
                for idx, course in enumerate(day_courses):
                    if idx >= len(managers):
                        break
                    result[course.id] = managers[idx].id

        # ---------- 2) 都賀 staff 固定割当 ----------
        for office in offices:
            if "都賀" not in (office.name or ""):
                continue
            tsuga_stmt = (
                select(Staff)
                .where(
                    Staff.status == "active",
                    Staff.role == "staff",
                    Staff.deleted_at.is_(None),
                    Staff.primary_office_id == office.id,
                )
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
            )
            tsuga_staff = list((await db.scalars(tsuga_stmt)).all())
            if not tsuga_staff:
                continue
            primary_staff = tsuga_staff[0]

            tsuga_course_stmt = (
                select(Course)
                .where(
                    Course.iso_year == iso_year,
                    Course.iso_week == iso_week,
                    Course.deleted_at.is_(None),
                    Course.course_status == COURSE_STATUS_COURSE_FIXED,
                    Course.office_id == office.id,
                    Course.code == "A",
                )
                .order_by(Course.weekday)
            )
            tsuga_courses = list((await db.scalars(tsuga_course_stmt)).all())
            for course in tsuga_courses:
                result[course.id] = primary_staff.id

        # offices_by_id は将来拡張用 (mypy 警告抑止のため軽く参照)
        del offices_by_id

        return result

    async def _load_rotation_history(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        history_weeks: int,
    ) -> list[tuple[int, str, UUID]]:
        """過去 ``history_weeks`` 週の (course_code, staff_id) 履歴を返す.

        対象は ``course_status='staff_assigned'`` のコースのみ。
        ``weeks_ago`` は当該週との距離 (1 = 直前の週).

        ISO 週の境界をまたぐ年差を考慮するため、月曜日ベースの date 演算で
        history_weeks 分の (year, week) ペアを構築する。
        """
        try:
            cur_monday = date_cls.fromisocalendar(iso_year, iso_week, 1)
        except ValueError:
            return []

        # 過去 history_weeks 週分の (iso_year, iso_week, weeks_ago) を組み立て
        target_weeks: list[tuple[int, int, int]] = []
        for w in range(1, history_weeks + 1):
            past_monday = date_cls.fromordinal(cur_monday.toordinal() - 7 * w)
            iso = past_monday.isocalendar()
            target_weeks.append((iso.year, iso.week, w))

        if not target_weeks:
            return []

        # OR 連結 (year=Y AND week=W) ... の集合
        from sqlalchemy import or_

        stmt = select(Course).where(
            Course.deleted_at.is_(None),
            Course.course_status == COURSE_STATUS_STAFF_ASSIGNED,
            Course.assigned_staff_id.isnot(None),
            or_(*[and_(Course.iso_year == y, Course.iso_week == w) for y, w, _ in target_weeks]),
        )
        rows = list((await db.scalars(stmt)).all())

        # weeks_ago マップ
        wa_map = {(y, w): wa for y, w, wa in target_weeks}
        result: list[tuple[int, str, UUID]] = []
        for c in rows:
            wa = wa_map.get((c.iso_year, c.iso_week))
            if wa is None:
                continue
            if c.assigned_staff_id is None:
                continue
            result.append((wa, c.code, c.assigned_staff_id))
        return result

    async def _persist(
        self,
        db: AsyncSession,
        assignments: list[StaffAssignment],
        *,
        trainee_staff_ids: frozenset[UUID] | None = None,
    ) -> None:
        """割当結果を DB に反映する (W7-BE4 / Codex Must-fix #7).

        実施内容:
            1. ``courses.assigned_staff_id`` / ``course_status`` を更新
               (``staff_assigned`` 遷移)
            2. **当該コース配下の planned visits** を取得し、v2 正規表現で
               ある ``visit_staff_assignments`` に行を INSERT
               (Staff の visit 可視性は本テーブル経由)
            3. 2 名体制 (``required_staff_count == 2`` または ``visit_group_id``
               が設定済み) の場合は primary + secondary の 2 行を INSERT
               (secondary は同じ ``visit_group_id`` グループ内の partner visit
               の所属 course の ``assigned_staff_id`` から導出)
            4. レガシー互換のため visits.primary_staff_id / secondary_staff_id
               も同期更新 (Wave 6 まで併用)
            5. 冪等性: 既存の ``visit_staff_assignments`` 行は DELETE してから
               再 INSERT する
            6. W10-BE2: is_trainee=True のスタッフが割り当てられた visit に
               companion を自動追加 (``staff_companion_assignments`` を参照)

        本サービスは commit しない。呼び出し側がトランザクション境界を握る
        (§5.4 / module docstring)。
        """
        if trainee_staff_ids is None:
            trainee_staff_ids = frozenset()
        if not assignments:
            return

        # ---------- 1. course の更新 ----------
        course_ids = [a.course_id for a in assignments]
        stmt = select(Course).where(Course.id.in_(course_ids))
        courses = list((await db.scalars(stmt)).all())
        course_by_id = {c.id: c for c in courses}
        now = datetime.now(UTC)
        # course.id -> assigned staff_id (今回の Layer 3 出力)
        staff_by_course: dict[UUID, UUID] = {}
        for a in assignments:
            c = course_by_id.get(a.course_id)
            if c is None:
                continue
            c.assigned_staff_id = a.staff_id
            c.course_status = COURSE_STATUS_STAFF_ASSIGNED
            c.staff_assigned_at = now
            staff_by_course[a.course_id] = a.staff_id

        if not staff_by_course:
            await db.flush()
            return

        # ---------- 2. 対象 visits をロード ----------
        # 当該コース配下の planned visits + (2 名体制ペア解決のため) 同一
        # visit_group_id を共有する別コースの visits も併せて取得する。
        visit_stmt = select(Visit).where(
            Visit.course_id.in_(list(staff_by_course.keys())),
            Visit.status == VISIT_STATUS_PLANNED,
            Visit.deleted_at.is_(None),
        )
        target_visits = list((await db.scalars(visit_stmt)).all())

        if not target_visits:
            await db.flush()
            return

        # 2 名体制グループの partner 解決のため、同じ visit_group_id を持つ
        # visit を全件取得 (別コースに所属する partner も含む)
        group_ids = {v.visit_group_id for v in target_visits if v.visit_group_id is not None}
        group_visits: list[Visit] = []
        if group_ids:
            partner_stmt = select(Visit).where(
                Visit.visit_group_id.in_(list(group_ids)),
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
            )
            group_visits = list((await db.scalars(partner_stmt)).all())

        # visit_group_id -> [Visit, Visit, ...]
        visits_by_group: dict[UUID, list[Visit]] = {}
        for v in group_visits:
            if v.visit_group_id is None:
                continue
            visits_by_group.setdefault(v.visit_group_id, []).append(v)

        # partner visit の course から secondary staff_id を解決するために、
        # まだ staff_by_course にない course_id の assigned_staff_id を補完
        # (= 別コースが今回の assignments に含まれていないケース)
        unknown_course_ids: set[UUID] = set()
        for vs in visits_by_group.values():
            for v in vs:
                if v.course_id is not None and v.course_id not in staff_by_course:
                    unknown_course_ids.add(v.course_id)
        if unknown_course_ids:
            extra_stmt = select(Course).where(Course.id.in_(list(unknown_course_ids)))
            for c in (await db.scalars(extra_stmt)).all():
                if c.assigned_staff_id is not None:
                    staff_by_course[c.id] = c.assigned_staff_id

        # ---------- 3. 既存の visit_staff_assignments を冪等のため DELETE ----------
        target_visit_ids = [v.id for v in target_visits]
        await db.execute(
            delete(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(target_visit_ids))
        )

        # ---------- 4. visit_staff_assignments を再 INSERT ----------
        for visit in target_visits:
            primary_staff_id = (
                staff_by_course.get(visit.course_id) if visit.course_id is not None else None
            )
            if primary_staff_id is None:
                # 当該 visit のコースが今回の出力に含まれていない (= 想定外)
                continue

            staff_ids: list[UUID] = [primary_staff_id]
            secondary_staff_id: UUID | None = None

            # 2 名体制: required_staff_count == 2 もしくは visit_group_id がある
            is_two_person = visit.required_staff_count == 2 or visit.visit_group_id is not None
            if is_two_person and visit.visit_group_id is not None:
                # 同 group の partner visit の course から secondary を解決
                for partner in visits_by_group.get(visit.visit_group_id, []):
                    if partner.id == visit.id:
                        continue
                    if partner.course_id is None:
                        continue
                    partner_staff = staff_by_course.get(partner.course_id)
                    if partner_staff is None or partner_staff == primary_staff_id:
                        continue
                    secondary_staff_id = partner_staff
                    break
                if secondary_staff_id is not None:
                    staff_ids.append(secondary_staff_id)

            # ---------- W10-BE2: 新人スタッフへの companion 自動付与 ----------
            # is_trainee=True の staff が割り当てられている場合のみ実行
            if primary_staff_id in trainee_staff_ids:
                # visit の weekday は visit_date から算出 (0=Mon..6=Sun)
                visit_weekday = visit.visit_date.weekday()
                companion_id = await self._resolve_companion_staff_id(
                    db,
                    trainee_staff_id=primary_staff_id,
                    weekday=visit_weekday,
                    visit_start=visit.start_time,
                )
                if companion_id is not None:
                    staff_ids.append(companion_id)
                else:
                    logger.warning(
                        "W10-BE2: trainee staff %s has no companion for weekday=%d visit=%s"
                        " (visit proceeds with trainee only)",
                        primary_staff_id,
                        visit_weekday,
                        visit.id,
                    )

            # 重複排除 (primary == secondary になり得ないが念のため)
            seen: set[UUID] = set()
            for sid in staff_ids:
                if sid in seen:
                    continue
                seen.add(sid)
                db.add(
                    VisitStaffAssignment(
                        visit_id=visit.id,
                        staff_id=sid,
                    )
                )

            # ---------- 5. レガシー互換: visits.primary/secondary_staff_id ----------
            visit.primary_staff_id = primary_staff_id
            visit.secondary_staff_id = secondary_staff_id

        await db.flush()


# ---------------------------------------------------------------------------
# Naive round-robin baseline (受入基準: ローテ分散度の比較対象)
# ---------------------------------------------------------------------------


def naive_round_robin(
    course_targets: list[CourseAssignmentTarget],
    staff_pool: list[StaffInfo],
) -> Layer3Result:
    """素朴な順番割当 (= ローテ分散度の比較ベースライン).

    各曜日内で、(course_code 順) に staff を 0,1,2,... と割り当てるだけの実装。
    ハード制約 (性別 / 勤務曜日) は無視するため不正な割当を含み得るが、
    分散度の比較対象としてのみ使う。
    """
    eligible = [s for s in staff_pool if s.role != "manager"]
    by_weekday: dict[int, list[CourseAssignmentTarget]] = {}
    for ct in course_targets:
        by_weekday.setdefault(ct.weekday, []).append(ct)

    all_assignments: list[StaffAssignment] = []
    for weekday in sorted(by_weekday.keys()):
        day_courses = sorted(by_weekday[weekday], key=lambda c: c.course_code)
        for i, course in enumerate(day_courses):
            if not eligible:
                continue
            staff = eligible[i % len(eligible)]
            all_assignments.append(
                StaffAssignment(
                    weekday=weekday,
                    course_code=course.course_code,
                    course_id=course.course_id,
                    staff_id=staff.staff_id,
                )
            )
    # 分散度
    asgn = Layer3Assigner()
    rotation_score = asgn._gini_index(
        [a.staff_id for a in all_assignments],
        staff_count=max(1, len(eligible)),
    )
    return Layer3Result(
        assignments=all_assignments,
        rotation_score=round(rotation_score, 6),
        total_distance_km=0.0,
    )


# ---------------------------------------------------------------------------
# Fixture helpers (tests/fixtures/layer3_4weeks.json 用)
# ---------------------------------------------------------------------------


_WEEKDAY_CODE_TO_INT: dict[str, int] = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}


def _stable_uuid(s: str) -> UUID:
    """ダミー文字列を決定的な UUID に変換 (fixture 評価用)."""
    import hashlib

    digest = hashlib.md5(s.encode("utf-8")).hexdigest()
    return UUID(digest)


def staff_from_fixture_dict(d: dict[str, Any]) -> StaffInfo:
    """fixture JSON の 1 staff を ``StaffInfo`` に変換."""
    sid_raw = str(d["staff_id"])
    try:
        sid = UUID(sid_raw)
    except ValueError:
        sid = _stable_uuid(sid_raw)
    work_days_raw = d.get("work_days") or []
    if isinstance(work_days_raw, list):
        work_days_int: list[int] = []
        for w in work_days_raw:
            if isinstance(w, int):
                work_days_int.append(w)
            else:
                if w in _WEEKDAY_CODE_TO_INT:
                    work_days_int.append(_WEEKDAY_CODE_TO_INT[w])
        work_days = frozenset(work_days_int)
    else:
        work_days = frozenset()

    office_lat = d.get("primary_office_lat")
    office_lng = d.get("primary_office_lng")
    return StaffInfo(
        staff_id=sid,
        name=d.get("name", ""),
        sex=d.get("gender") or d.get("sex"),
        role=d.get("role", "staff"),
        primary_office_lat=float(office_lat) if office_lat is not None else None,
        primary_office_lng=float(office_lng) if office_lng is not None else None,
        work_days=work_days,
        is_trainee=bool(d.get("is_trainee", False)),
    )


def course_target_from_fixture_dict(d: dict[str, Any]) -> CourseAssignmentTarget:
    """fixture JSON の 1 course を ``CourseAssignmentTarget`` に変換."""
    cid_raw = str(d.get("course_id") or f"{d['iso_week']}-{d['weekday']}-{d['course_code']}")
    try:
        cid = UUID(cid_raw)
    except ValueError:
        cid = _stable_uuid(cid_raw)

    patients = d.get("patients", []) or []
    lats: list[float] = []
    lngs: list[float] = []
    pids: list[UUID] = []
    restrictions: set[str] = set()
    for p in patients:
        if p.get("lat") is not None:
            lats.append(float(p["lat"]))
        if p.get("lng") is not None:
            lngs.append(float(p["lng"]))
        gr = p.get("gender_restriction")
        if gr:
            restrictions.add(gr)
        pid_raw = str(p.get("patient_id", ""))
        try:
            pids.append(UUID(pid_raw))
        except ValueError:
            pids.append(_stable_uuid(pid_raw))

    weekday_raw = d.get("weekday")
    if isinstance(weekday_raw, str):
        weekday_int = _WEEKDAY_CODE_TO_INT.get(weekday_raw, 0)
    else:
        weekday_int = int(weekday_raw or 0)

    return CourseAssignmentTarget(
        course_id=cid,
        weekday=weekday_int,
        course_code=str(d.get("course_code", "A")),
        centroid_lat=(sum(lats) / len(lats)) if lats else None,
        centroid_lng=(sum(lngs) / len(lngs)) if lngs else None,
        gender_restrictions=frozenset(restrictions),
        patient_ids=pids,
    )


def history_from_fixture_list(
    raw: list[dict[str, Any]],
    *,
    current_iso_year: int,
    current_iso_week: int,
) -> list[tuple[int, str, UUID]]:
    """fixture の history 配列を ``[(weeks_ago, course_code, staff_id), ...]`` に変換."""
    try:
        cur_monday = date_cls.fromisocalendar(current_iso_year, current_iso_week, 1)
    except ValueError:
        return []
    out: list[tuple[int, str, UUID]] = []
    for h in raw:
        try:
            past_monday = date_cls.fromisocalendar(int(h["iso_year"]), int(h["iso_week"]), 1)
        except (ValueError, KeyError):
            # iso_year 省略時は current_iso_year を使う
            try:
                past_monday = date_cls.fromisocalendar(current_iso_year, int(h["iso_week"]), 1)
            except (ValueError, KeyError):
                continue
        weeks_ago = (cur_monday.toordinal() - past_monday.toordinal()) // 7
        if weeks_ago <= 0:
            continue
        sid_raw = str(h["staff_id"])
        try:
            sid = UUID(sid_raw)
        except ValueError:
            sid = _stable_uuid(sid_raw)
        out.append((weeks_ago, str(h["course_code"]), sid))
    return out


__all__ = [
    "COST_ALPHA_DISTANCE",
    "COST_BETA_ROTATION",
    "CourseAssignmentTarget",
    "HUNGARIAN_INFINITY",
    "Layer3AssignmentError",
    "Layer3Assigner",
    "Layer3Result",
    "ROTATION_EXCLUSION_WEEKS",
    "ROTATION_HISTORY_WEEKS",
    "StaffAssignment",
    "StaffInfo",
    "VisitTimeSlot",
    "course_target_from_fixture_dict",
    "history_from_fixture_list",
    "hungarian_min_cost",
    "naive_round_robin",
    "staff_from_fixture_dict",
]
