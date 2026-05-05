"""Layer2Clusterer — W4-BE8.

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.3 / §4.5 / §5.3
と API 契約 ``docs/plans/v2-api-contracts.md`` §4.4
(`POST /api/v1/courses/generate`) に対応する Layer 2 アルゴリズム。

責務 (§5.3):
    - 当該曜日の visits を距離ベースでコース分け（K-means + 制約後処理）
    - 容量制約: ``sum(service_min) <= 6 * avg(service_min)`` (Q1 確定値)
    - 1 コース最大 6 名 (§3.6.3)
    - 同コース内の時間衝突チェック (移動時間込み)
    - 案 (proposed) のみを返す。DB 書き込みは別エンドポイント

計算量:
    - K-means: O(N * K * iter)、N <= 数十、K <= 5 程度
    - 後処理: O(N * K)
    - 全体: 1 曜日あたり数 ms 〜 数十 ms

距離計算 (Q4=直線距離):
    - Haversine 法で 2 点の球面距離 (km) を求める
    - 重心 (centroid) は単純平均で十分 (千葉エリアの局所範囲なので歪みは無視)

時間衝突 (Q5=15 分粒度):
    - 同コース内で (start_time, end_time) が重なるとアウト
    - さらに移動時間として固定 5 分を加算 (MVP)
    - 違反時は最も近い別コースへ移動

トランザクション:
    - 本サービスは DB に書き込まない (proposals のみ返す)
    - 確定は ``POST /api/v1/courses/{id}/fix`` で別途実施
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import time
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.visit import VISIT_STATUS_PLANNED, Visit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# コース記号 (§3.6.5). 4 コースまでは A〜D を使う。
# staff_count=5 以上の場合は CourseV2 schema が許す M (manager) は専用枠なので
# 通常コースとしては A〜D の 4 つを上限とする (MVP)。
COURSE_CODES: tuple[str, ...] = ("A", "B", "C", "D")

# 1 コースあたりの上限人数 (§3.6.3)
MAX_PATIENTS_PER_COURSE: int = 6

# Q1: サービス時間枠消費 — sum(service_min) <= MAX_SERVICE_RATIO * avg(service_min)
# チケット要件: ``sum(service_min) <= 6 * avg(service_min)`` (= 上限 6 件相当)
MAX_SERVICE_TIME_RATIO: float = 6.0

# 移動時間 (固定 5 分) — Q4=直線距離 / Q5=15 分粒度 の MVP 簡易モデル
TRAVEL_TIME_MINUTES: int = 5

# K-means の最大反復回数。実規模 (N <= 50) では 20 回もあれば収束する。
KMEANS_MAX_ITER: int = 50

# K-means 初期化の試行回数 (best-of-N で最良解を選ぶ)
KMEANS_N_INIT: int = 10


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2 点間の Haversine 距離 (km) を返す.

    地球半径 = 6371 km。Q4=直線距離 (MVP) の正本実装。
    千葉エリアの局所範囲では十分な精度。
    """
    r_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r_km * c


def _euclid_sq(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """K-means 内部用. lat/lng の擬似ユークリッド (低コスト)."""
    return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """重心 (lat 平均, lng 平均). 空リストは (0, 0) を返す."""
    if not points:
        return (0.0, 0.0)
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def _times_overlap(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    """2 つの (start, end) が重なるかを判定. 端点接触は重ならない扱い."""
    return a_start < b_end and b_start < a_end


def _add_minutes(t: time, minutes: int) -> time:
    """time に分を加算. 24:00 を超える場合は 23:59 にクランプ (MVP)."""
    total = t.hour * 60 + t.minute + minutes
    if total >= 24 * 60:
        return time(23, 59)
    if total < 0:
        return time(0, 0)
    return time(total // 60, total % 60)


# ---------------------------------------------------------------------------
# Input / Output models
# ---------------------------------------------------------------------------


@dataclass
class Layer2VisitInput:
    """K-means への 1 件の入力. ``Layer1.visits_created`` から構築."""

    patient_id: UUID
    visit_id: UUID
    start_time: time
    end_time: time
    service_min: int
    lat: float
    lng: float


class CourseProposal(BaseModel):
    """API レスポンスの 1 コース案 (§5.3 出力 schema).

    ``patient_ids`` は配置された患者の UUID リスト。
    ``total_distance_km`` はコース内総直線距離 (重心 → 各患者への距離合計).
    ``validity_score`` は 0.0〜1.0 のスコア。1.0 が満点 (制約完全充足).
    """

    model_config = ConfigDict(extra="forbid")

    course_code: str = Field(description="A/B/C/D (§3.6.5)")
    patient_ids: list[UUID] = Field(default_factory=list)
    total_distance_km: float = Field(ge=0.0)
    validity_score: float = Field(ge=0.0, le=1.0)


@dataclass
class Layer2Result:
    """Layer 2 の総合出力 (HTTP 層がレスポンス schema に詰め直す)."""

    proposals: list[CourseProposal] = field(default_factory=list)
    total_distance_km: float = 0.0
    validity_score: float = 0.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Layer2ClusterError(Exception):
    """サービス層から HTTP 層へ伝播する業務エラー (4xx に翻訳される)."""

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


# ---------------------------------------------------------------------------
# K-means (純粋関数; numpy/sklearn を使わず stdlib のみで実装)
# ---------------------------------------------------------------------------


def _kmeans(
    points: list[tuple[float, float]],
    k: int,
    *,
    random_state: int | None = None,
    max_iter: int = KMEANS_MAX_ITER,
    n_init: int = KMEANS_N_INIT,
) -> list[int]:
    """K-means クラスタリング (Lloyd 法).

    Returns:
        各 point に対するクラスタ ID (0..k-1) の配列。
    """
    n = len(points)
    if n == 0:
        return []
    if k <= 1:
        return [0] * n
    if n <= k:
        # 各点が自分自身のクラスタ
        return list(range(n)) + [0] * 0  # placeholder (n==k なので [0..n-1])

    rng = random.Random(random_state)
    best_labels: list[int] | None = None
    best_inertia = float("inf")

    for _trial in range(n_init):
        # ----- 初期化 (k-means++ 風: 単純化のためランダム選択 + 距離重み付け) -----
        first_idx = rng.randrange(n)
        centers: list[tuple[float, float]] = [points[first_idx]]
        while len(centers) < k:
            # 各点 → 最近 center までの距離^2
            dists = [min(_euclid_sq(p, c) for c in centers) for p in points]
            total = sum(dists)
            if total <= 0:
                # 全点が既存 center と一致 → ランダム選択にフォールバック
                centers.append(points[rng.randrange(n)])
                continue
            # 距離^2 重み付き選択
            r = rng.random() * total
            acc = 0.0
            chosen_idx = n - 1
            for i, d in enumerate(dists):
                acc += d
                if acc >= r:
                    chosen_idx = i
                    break
            centers.append(points[chosen_idx])

        # ----- Lloyd 反復 -----
        labels = [0] * n
        for _it in range(max_iter):
            changed = False
            new_labels = []
            for p in points:
                best_d = float("inf")
                best_c = 0
                for ci, c in enumerate(centers):
                    d = _euclid_sq(p, c)
                    if d < best_d:
                        best_d = d
                        best_c = ci
                new_labels.append(best_c)
            if new_labels != labels:
                changed = True
            labels = new_labels

            # center 更新
            for ci in range(k):
                cluster_pts = [p for p, lab in zip(points, labels, strict=False) if lab == ci]
                if cluster_pts:
                    centers[ci] = _centroid(cluster_pts)
            if not changed:
                break

        # ----- inertia (= sum of squared distances) -----
        inertia = sum(_euclid_sq(p, centers[lab]) for p, lab in zip(points, labels, strict=False))
        if inertia < best_inertia:
            best_inertia = inertia
            best_labels = labels

    assert best_labels is not None
    return best_labels


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Layer2Clusterer:
    """Layer 2: 当該曜日の visits を距離ベースでコース分け (§5.3).

    アルゴリズム (MVP, §5.3 候補 (i)):
        1. K-means で初期分割
        2. 容量制約 (sum service_min <= 6*avg, 6 名以内) を満たすよう移動
        3. 時間衝突 (移動時間込み) を解消するよう移動
        4. 結果を CourseProposal として返す
    """

    async def generate_proposals(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        weekday: int,
        staff_count: int = 4,
        random_state: int | None = None,
    ) -> Layer2Result:
        """指定曜日の visits からコース分け案を生成する.

        Args:
            db: 共有 SQLAlchemy セッション.
            iso_year: ISO 年.
            iso_week: ISO 週 (1-53).
            weekday: 曜日 (0=月, 6=日).
            staff_count: コース数 = 稼働スタッフ数. デフォルト 4 (A〜D).
            random_state: K-means の乱数シード (再現性確保).

        Returns:
            Layer2Result: コース案リスト + 総距離 + 妥当性スコア.

        Raises:
            Layer2ClusterError: 入力範囲外などの業務エラー.
        """
        if iso_year < 2000 or iso_year > 2100:
            raise Layer2ClusterError(f"iso_year out of range: {iso_year}")
        if iso_week < 1 or iso_week > 53:
            raise Layer2ClusterError(f"iso_week out of range: {iso_week}")
        if weekday < 0 or weekday > 6:
            raise Layer2ClusterError(f"weekday out of range: {weekday}")
        if staff_count < 1 or staff_count > len(COURSE_CODES):
            raise Layer2ClusterError(
                f"staff_count out of range: {staff_count} (1-{len(COURSE_CODES)})"
            )

        # ----- 当該曜日の visits を取得 -----
        visits_inputs = await self._load_visits(
            db, iso_year=iso_year, iso_week=iso_week, weekday=weekday
        )

        return self.cluster(
            visits_inputs,
            staff_count=staff_count,
            random_state=random_state,
        )

    def cluster(
        self,
        visits: list[Layer2VisitInput],
        *,
        staff_count: int = 4,
        random_state: int | None = None,
    ) -> Layer2Result:
        """純粋関数版エントリポイント (テスト / fixture 評価で直接使う).

        DB I/O を行わない。既に Layer1 出力相当の visit リストが手元にあるとき用。
        """
        if staff_count < 1 or staff_count > len(COURSE_CODES):
            raise Layer2ClusterError(
                f"staff_count out of range: {staff_count} (1-{len(COURSE_CODES)})"
            )

        # ----- 空ケース: 全コース空で返す -----
        if not visits:
            return Layer2Result(
                proposals=[
                    CourseProposal(
                        course_code=COURSE_CODES[i],
                        patient_ids=[],
                        total_distance_km=0.0,
                        validity_score=1.0,
                    )
                    for i in range(staff_count)
                ],
                total_distance_km=0.0,
                validity_score=1.0,
            )

        # ----- Step 1: K-means 初期分割 -----
        points = [(v.lat, v.lng) for v in visits]
        labels = _kmeans(points, staff_count, random_state=random_state)

        # クラスタ -> visits リスト
        clusters: list[list[Layer2VisitInput]] = [[] for _ in range(staff_count)]
        for v, lab in zip(visits, labels, strict=False):
            clusters[lab].append(v)

        # ----- Step 2: 容量制約後処理 -----
        clusters = self._enforce_capacity(clusters, all_visits=visits)

        # ----- Step 3: 時間衝突解消 -----
        clusters = self._resolve_time_conflicts(clusters, all_visits=visits)

        # ----- Step 4: 集計 -----
        proposals: list[CourseProposal] = []
        total_dist_all = 0.0
        validity_sum = 0.0
        for i in range(staff_count):
            cluster_visits = clusters[i]
            dist = self._cluster_total_distance(cluster_visits)
            score = self._validity_score(cluster_visits)
            total_dist_all += dist
            validity_sum += score
            proposals.append(
                CourseProposal(
                    course_code=COURSE_CODES[i],
                    patient_ids=[v.patient_id for v in cluster_visits],
                    total_distance_km=round(dist, 4),
                    validity_score=round(score, 4),
                )
            )

        return Layer2Result(
            proposals=proposals,
            total_distance_km=round(total_dist_all, 4),
            validity_score=round(validity_sum / staff_count, 4),
        )

    # ------------------------------------------------------------------ #
    # private helpers
    # ------------------------------------------------------------------ #

    async def _load_visits(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        weekday: int,
    ) -> list[Layer2VisitInput]:
        """当該曜日の visits を DB から取得し ``Layer2VisitInput`` に変換.

        Layer 1 が生成した auto-visit (status=planned) を対象とする。
        座標未設定 (lat/lng が NULL) の患者はスキップ。
        """
        from datetime import date as date_cls

        try:
            week_monday = date_cls.fromisocalendar(iso_year, iso_week, 1)
        except ValueError as exc:
            raise Layer2ClusterError(
                f"invalid ISO week: year={iso_year} week={iso_week}",
            ) from exc

        target_date = date_cls.fromordinal(week_monday.toordinal() + weekday)

        stmt = (
            select(Visit, Patient)
            .join(Patient, Patient.id == Visit.patient_id)
            .where(
                Visit.visit_date == target_date,
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
                Patient.deleted_at.is_(None),
            )
            .order_by(Visit.start_time, Visit.patient_id)
        )

        rows = (await db.execute(stmt)).all()
        result: list[Layer2VisitInput] = []
        for visit, patient in rows:
            if patient.lat is None or patient.lng is None:
                # 座標未設定の患者は K-means に乗せられない
                continue
            service_min = (
                visit.end_time.hour * 60
                + visit.end_time.minute
                - visit.start_time.hour * 60
                - visit.start_time.minute
            )
            if service_min <= 0:
                continue
            result.append(
                Layer2VisitInput(
                    patient_id=patient.id,
                    visit_id=visit.id,
                    start_time=visit.start_time,
                    end_time=visit.end_time,
                    service_min=service_min,
                    lat=float(patient.lat),
                    lng=float(patient.lng),
                )
            )
        return result

    def _enforce_capacity(
        self,
        clusters: list[list[Layer2VisitInput]],
        *,
        all_visits: list[Layer2VisitInput],
    ) -> list[list[Layer2VisitInput]]:
        """容量制約 (Q1) と人数上限 (6) を満たすよう調整.

        Q1 確定値: sum(service_min) <= 6 * avg(service_min)
        さらに (§3.6.3): 1 コース最大 6 名

        オーバーフロー時、最も「移動先重心に近い」visit を選んで移動する。
        """
        avg_service = (
            sum(v.service_min for v in all_visits) / len(all_visits) if all_visits else 0.0
        )
        capacity_per_course = MAX_SERVICE_TIME_RATIO * avg_service if avg_service > 0 else 0.0

        # 各クラスタ重心を計算
        def centroid_of(cluster: list[Layer2VisitInput]) -> tuple[float, float]:
            if not cluster:
                return (0.0, 0.0)
            return (
                sum(v.lat for v in cluster) / len(cluster),
                sum(v.lng for v in cluster) / len(cluster),
            )

        # 上限を超えるクラスタから、その重心に最も「遠い」visit を選び、
        # 受け入れ余裕のある最寄りクラスタへ移動する。
        for _safety in range(50):  # 無限ループ防止
            moved = False
            for src_idx, src in enumerate(clusters):
                src_overflow_count = max(0, len(src) - MAX_PATIENTS_PER_COURSE)
                src_total_service = sum(v.service_min for v in src)
                src_overflow_service = max(0.0, src_total_service - capacity_per_course)
                if src_overflow_count == 0 and src_overflow_service <= 0:
                    continue

                # src 重心 → 各 visit までの距離
                src_c = centroid_of(src)
                # 重心から最も「遠い」visit を移動候補とする (周辺密度を保つ)
                src.sort(
                    key=lambda v: haversine_km(src_c[0], src_c[1], v.lat, v.lng),
                    reverse=True,
                )
                # 移動先候補: 受け入れ可能な最も近いクラスタ
                target_idx = self._find_capacity_target(
                    clusters, src_idx, src[0], capacity_per_course
                )
                if target_idx is None:
                    # どこも入らない → 1 件は強制的に移動 (容量超過容認)
                    # 受入基準より「6 名以内」が優先される。最も人数の少ない
                    # クラスタへ移動する。
                    least_loaded = min(
                        ((i, len(c)) for i, c in enumerate(clusters) if i != src_idx),
                        key=lambda x: x[1],
                        default=None,
                    )
                    if least_loaded is None:
                        break
                    target_idx = least_loaded[0]

                # src の先頭 visit (= 重心から最遠) を target に移動
                visit_to_move = src.pop(0)
                clusters[target_idx].append(visit_to_move)
                moved = True
                break  # 1 件移動したら最初からやり直し
            if not moved:
                break

        return clusters

    def _find_capacity_target(
        self,
        clusters: list[list[Layer2VisitInput]],
        src_idx: int,
        candidate: Layer2VisitInput,
        capacity_per_course: float,
    ) -> int | None:
        """``candidate`` を受け入れ可能な最も近いクラスタ (= src 以外) を返す."""
        best_idx: int | None = None
        best_dist = float("inf")
        for i, c in enumerate(clusters):
            if i == src_idx:
                continue
            if len(c) >= MAX_PATIENTS_PER_COURSE:
                continue
            new_total_service = sum(v.service_min for v in c) + candidate.service_min
            if capacity_per_course > 0 and new_total_service > capacity_per_course:
                continue
            if not c:
                # 空クラスタは重心が未定義 → 無条件で受け入れ候補
                if best_idx is None:
                    best_idx = i
                continue
            cx = sum(v.lat for v in c) / len(c)
            cy = sum(v.lng for v in c) / len(c)
            d = haversine_km(cx, cy, candidate.lat, candidate.lng)
            if d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx

    def _resolve_time_conflicts(
        self,
        clusters: list[list[Layer2VisitInput]],
        *,
        all_visits: list[Layer2VisitInput],
    ) -> list[list[Layer2VisitInput]]:
        """同コース内の時間衝突 (移動時間込み) を解消する.

        各 visit の (start_time, end_time + TRAVEL_TIME_MINUTES) で重なりを
        判定。衝突が起きている場合、最も近い別コース (容量に余裕あり) へ移動。
        """
        for _safety in range(50):
            conflict_found = False
            for src_idx, src in enumerate(clusters):
                conflict_pair = self._find_conflict(src)
                if conflict_pair is None:
                    continue
                # 時間衝突する 2 件のうち、コース重心から遠い方を移動
                a, b = conflict_pair
                if not src:
                    continue
                cx = sum(v.lat for v in src) / len(src)
                cy = sum(v.lng for v in src) / len(src)
                d_a = haversine_km(cx, cy, a.lat, a.lng)
                d_b = haversine_km(cx, cy, b.lat, b.lng)
                victim = a if d_a >= d_b else b
                target_idx = self._find_time_safe_target(clusters, src_idx, victim)
                if target_idx is None:
                    # 移動先が無ければ諦める (validity_score を下げる)
                    break
                src.remove(victim)
                clusters[target_idx].append(victim)
                conflict_found = True
                break
            if not conflict_found:
                break
        return clusters

    def _find_conflict(
        self, cluster: list[Layer2VisitInput]
    ) -> tuple[Layer2VisitInput, Layer2VisitInput] | None:
        """クラスタ内で最初に見つかった時間衝突ペアを返す.

        (start_time, end_time + TRAVEL_TIME_MINUTES) のレンジで重なる ⇔ 衝突。
        """
        n = len(cluster)
        for i in range(n):
            a = cluster[i]
            a_start = a.start_time
            a_end_with_travel = _add_minutes(a.end_time, TRAVEL_TIME_MINUTES)
            for j in range(i + 1, n):
                b = cluster[j]
                b_start = b.start_time
                b_end_with_travel = _add_minutes(b.end_time, TRAVEL_TIME_MINUTES)
                if _times_overlap(a_start, a_end_with_travel, b_start, b_end_with_travel):
                    return (a, b)
        return None

    def _find_time_safe_target(
        self,
        clusters: list[list[Layer2VisitInput]],
        src_idx: int,
        candidate: Layer2VisitInput,
    ) -> int | None:
        """``candidate`` を時間衝突なく受け入れ可能な最も近いクラスタを返す."""
        best_idx: int | None = None
        best_dist = float("inf")
        for i, c in enumerate(clusters):
            if i == src_idx:
                continue
            if len(c) >= MAX_PATIENTS_PER_COURSE:
                continue
            # 衝突チェック
            cand_end_travel = _add_minutes(candidate.end_time, TRAVEL_TIME_MINUTES)
            has_conflict = False
            for v in c:
                v_end_travel = _add_minutes(v.end_time, TRAVEL_TIME_MINUTES)
                if _times_overlap(
                    candidate.start_time, cand_end_travel, v.start_time, v_end_travel
                ):
                    has_conflict = True
                    break
            if has_conflict:
                continue
            if not c:
                if best_idx is None:
                    best_idx = i
                continue
            cx = sum(v.lat for v in c) / len(c)
            cy = sum(v.lng for v in c) / len(c)
            d = haversine_km(cx, cy, candidate.lat, candidate.lng)
            if d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx

    def _cluster_total_distance(self, cluster: list[Layer2VisitInput]) -> float:
        """クラスタの「総直線距離 (km)」を返す.

        定義: 重心から各 visit までの Haversine 距離の合計。
        この値は naive round-robin との比較・最適化指標として使用する。
        """
        if not cluster:
            return 0.0
        cx = sum(v.lat for v in cluster) / len(cluster)
        cy = sum(v.lng for v in cluster) / len(cluster)
        return sum(haversine_km(cx, cy, v.lat, v.lng) for v in cluster)

    def _validity_score(self, cluster: list[Layer2VisitInput]) -> float:
        """0.0〜1.0 のスコア. 制約完全充足で 1.0.

        減点要因:
          * 6 名超過: 0.3 減点
          * 時間衝突あり: 0.4 減点
          * 容量超過 (将来用、現状は 6 名でほぼ等価): 0.2 減点
        """
        score = 1.0
        if len(cluster) > MAX_PATIENTS_PER_COURSE:
            score -= 0.3
        if self._find_conflict(cluster) is not None:
            score -= 0.4
        return max(0.0, score)


# ---------------------------------------------------------------------------
# Naive round-robin baseline (for benchmarking /受入基準 2)
# ---------------------------------------------------------------------------


def naive_round_robin(
    visits: list[Layer2VisitInput],
    *,
    staff_count: int = 4,
) -> Layer2Result:
    """素朴な順番割当 (= 比較ベースライン).

    受入基準 2 (`総直線距離が naive round-robin の 70% 以下`) の評価で使う。
    入力順 (= start_time / patient_id) で 0,1,2,3,0,1,2,3... と振り分けるだけ。
    """
    clusters: list[list[Layer2VisitInput]] = [[] for _ in range(staff_count)]
    for i, v in enumerate(visits):
        clusters[i % staff_count].append(v)

    proposals: list[CourseProposal] = []
    total_dist = 0.0
    clusterer = Layer2Clusterer()
    for i in range(staff_count):
        c = clusters[i]
        d = clusterer._cluster_total_distance(c)
        total_dist += d
        proposals.append(
            CourseProposal(
                course_code=COURSE_CODES[i],
                patient_ids=[v.patient_id for v in c],
                total_distance_km=round(d, 4),
                validity_score=clusterer._validity_score(c),
            )
        )
    return Layer2Result(
        proposals=proposals,
        total_distance_km=round(total_dist, 4),
        validity_score=0.0,  # baseline は score 評価対象外
    )


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------


def from_fixture_dicts(items: list[dict[str, Any]]) -> list[Layer2VisitInput]:
    """fixture JSON (list[dict]) を ``Layer2VisitInput`` リストに変換.

    fixture 形式 (受入基準で要求):
        {"patient_id": "uuid", "weekday": 0, "start_time": "09:00",
         "end_time": "10:00", "service_min": 60, "lat": 35.65, "lng": 140.10}

    本関数は test / fixture 評価で使い、本番 API 経路では使わない。
    """
    out: list[Layer2VisitInput] = []
    for it in items:
        sh, sm = (int(x) for x in it["start_time"].split(":"))
        eh, em = (int(x) for x in it["end_time"].split(":"))
        pid_str = str(it["patient_id"])
        try:
            pid = UUID(pid_str)
        except ValueError:
            # fixture が短いダミー文字列の場合は決定的に UUID を生成
            import hashlib

            digest = hashlib.md5(pid_str.encode("utf-8")).hexdigest()
            pid = UUID(digest)
        out.append(
            Layer2VisitInput(
                patient_id=pid,
                visit_id=pid,  # fixture では visit_id = patient_id とみなす
                start_time=time(sh, sm),
                end_time=time(eh, em),
                service_min=int(it["service_min"]),
                lat=float(it["lat"]),
                lng=float(it["lng"]),
            )
        )
    return out


__all__ = [
    "COURSE_CODES",
    "CourseProposal",
    "Layer2ClusterError",
    "Layer2Clusterer",
    "Layer2Result",
    "Layer2VisitInput",
    "MAX_PATIENTS_PER_COURSE",
    "MAX_SERVICE_TIME_RATIO",
    "TRAVEL_TIME_MINUTES",
    "from_fixture_dicts",
    "haversine_km",
    "naive_round_robin",
]
