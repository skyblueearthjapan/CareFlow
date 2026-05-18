"""AutoAllocator v2.0 — Wave 41 auto-schedule v2.

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)

責務 (5 段階, §3.3 / §4.3):
    - 段階 1: プール作成 (全 active 患者 or 固定枠未登録のみ)
    - 段階 2: (曜日 × 時間帯) バケットへ振り分け
    - 段階 3: 各バケット内で距離グリーディクラスタリング (2-3 人/セット)
    - 段階 4: コース数制約 (= スタッフ数) を適用、超過は警告
    - 段階 5: 午前 ↔ 午後 の組み合わせ (同エリア優先)

v1 (``auto_allocator.py``) との違い:
    - K-Means を捨て、純粋な距離グリーディクラスタリングへ
    - コース = 「1 スタッフ × 1 日 (午前 + 午後)」
    - クラスタリング軸 = 拠点 × 曜日 × 午前/午後
    - DB への副作用なし (純粋に提案を返すのみ; apply は個別 endpoint で実施)

ハード制約 H1-H10 (§12.1):
    - H1: 週次統一 — 同 patient_id は週通して同 start_time
    - H2: 同住所ペアリング (最大 2 人) — _enforce_h2_same_address
    - H3: 同住所連続性 — グリーディが自然に satisfy
    - H4: 全訪問同スタッフ禁止 — 段階 5 後に対応
    - H5: 受入カレンダー × 回避 — _filter_unavailable_and_lunch
          (Mode 1 / diff_add では enforce; Mode 2 / full_optimize では無視.
           受入カレンダー × は既存スケジュール枠の混雑度を表す動的データであり、
           既存固定枠ごと再配置する全面最適化では制約として意味を持たないため.)
    - H6: 実出勤枠遵守 — _count_active_staff_per_weekday
    - H7: 性別制限遵守 — 採用時に呼び出し側で check (本サービスは候補までで止める)
    - H8: 新人単独訪問禁止 — is_trainee=false のみカウント
    - H9 (新): コース容量 6 名以内 (午前 + 午後の合計) — _enforce_course_capacity_v2
    - H10 (新): 昼休憩 12:00-13:00 に visit を入れない — _filter_lunch_break

トランザクション:
    本サービスは ``db.commit()`` / ``db.rollback()`` を **呼ばない**.
    pure read-only にすることで提案算出は冪等. apply は別 endpoint.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, time
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift, StaffWeeklyOverride
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# H9: 1 コース (午前 + 午後 合計) の上限人数 (§12.1).
MAX_PATIENTS_PER_COURSE: int = 6

# 1 セット (= バケット内の距離クラスタ) 上限人数.
MAX_PATIENTS_PER_SET: int = 3

# W41 v2 拡張 (移動時間の time 化): Haversine 距離を時間 (分) に変換するための
# 平均速度. 都市部の安全側仮定 (信号・混雑考慮). 直線距離 × 60 / 速度 (km/h)
# で分換算する.
TRAVEL_SPEED_KMH: float = 20.0

# W41 v2 拡張 (コース容量 duration 化): 1 コース (1 スタッフ × 1 日, 昼休憩除く)
# の所要時間上限 (分). 9:00-12:00 + 13:00-18:00 = 8 時間 = 480 分.
COURSE_MAX_MINUTES: int = 480

# 訪問間バッファー (書類記入・移動準備・次患者対応準備等の余裕時間).
# 全面最適化 (mode="full_optimize") と差分追加 (mode="diff_add") の両方で
# ``_apply_travel_time_to_courses`` 内の earliest_start 計算と
# ``calc_course_total_minutes`` のコース容量計算に加算される.
#
# **同住所バケット判定 (バッファー 0 の条件)**:
#     ``_address_bucket`` (= ``SAME_ADDRESS_TOLERANCE = 0.001`` ≒ 緯度経度 100m 角)
#     で同一バケットに入る lat/lng のペア = 同住所扱い.
#     同一施設 (= マンション・グループホーム等) 内の連続 2 名訪問だけでなく、
#     **約 100m 以内の近接住所** も同住所扱いになる. これらの遷移では
#     「移動なし = 記入も次室への移動準備も最小限」とみなしバッファー 0 で
#     そのまま続行する.
#
# 異住所への遷移ではバッファー (8 分) を加算する.
# 加えて非固定 visit の actual_start は ``_round_up_to_5min`` で 5 分刻みに切り上げる
# (最大 4 分加算) ため、UI 上の実質バッファーは 8-12 分.
VISIT_BUFFER_MINUTES: int = 8

# 物理不可能配置の判定閾値 (分).
# 固定時刻 visit が前 visit + 移動 + バッファーで earliest_start に間に合わない場合、
# 不足が ``SHORTAGE_THRESHOLD_MIN`` 分以上なら「物理的に不可能」として配置を拒否し
# (course_code を None にして) unassigned に流す. 未満なら従来通り警告のみで配置.
# 5 分未満の微小不足は運用 (前 visit の早期終了 等) で吸収可能とみなす.
SHORTAGE_THRESHOLD_MIN: int = 5

# H2: 同住所判定の許容誤差 (緯度経度の絶対差 ≒ 100m).
SAME_ADDRESS_TOLERANCE: float = 0.001

# 午前/午後の境界 (Q1 確定: 12:00 未満=午前, 12:00 以降=午後).
NOON_HOUR: int = 12

# H10: 昼休憩 (12:00-13:00) は visit 禁止.
LUNCH_START: time = time(12, 0)
LUNCH_END: time = time(13, 0)

# 午前ブロック / 午後ブロック範囲 (§1 用語).
AM_BLOCK_START: time = time(9, 30)
AM_BLOCK_END: time = time(12, 0)
PM_BLOCK_START: time = time(13, 0)
PM_BLOCK_END: time = time(18, 0)

# Course code (午前/午後同一スタッフが担当) — 1 拠点で最大 5 スタッフ.
_COURSE_CODES: tuple[str, ...] = ("A", "B", "C", "D", "E")
_COURSE_CODES_MAX: int = len(_COURSE_CODES)

# CareFlow Wave Next 2 cross-review [H1]: M overflow を 1 つの巨大コースに
# 集約せず M / M2 / M3 ... に分散する. これにより
# ``_apply_travel_time_to_courses`` / ``_check_course_capacity_minutes`` /
# capacity 判定 (MAX_PATIENTS_PER_COURSE=6) が overflow set ごとに独立して効く.
# 上限は 0022 の seed (M..M9) に合わせる.
_M_OVERFLOW_CODES: tuple[str, ...] = ("M", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9")
_M_OVERFLOW_CODES_MAX: int = len(_M_OVERFLOW_CODES)


def _is_m_course_code(code: str | None) -> bool:
    """``code`` が M overflow code ("M", "M2", ... "M9") のいずれかなら True."""
    if code is None:
        return False
    return code in _M_OVERFLOW_CODES


def _find_next_available_code(
    assigned: set[str],
    *,
    normal_max: int,
    m_max: int,
) -> str | None:
    """assigned に含まれない未使用 course_code を返す.

    優先順位: A/B/C/D/E (normal_max 個まで) → M/M2.../M9 (m_max 個まで) → None.

    CareFlow バグ修正 (#102 Fix B 漏れ): Stage 5 で **異なる set が同じ
    course_code を持つ** ことを防ぐためのヘルパー. 既存固定コース
    (``existing_codes``) を尊重する際に他 set と衝突したら、ここで次の空き
    コードへフォールバックする.

    Args:
        assigned: 当該 (office_id, weekday) で既に割り当て済みの code 集合.
        normal_max: 通常コース (A-E) の発行上限 (= ``staff_count`` 上限).
        m_max: M overflow (M/M2/.../M9) の発行上限 (= ``manager_count`` 上限).

    Returns:
        次に発行可能な code. すべて埋まっていれば None (= 未割当扱い).
    """
    for c in _COURSE_CODES[:normal_max]:
        if c not in assigned:
            return c
    for i in range(min(m_max, _M_OVERFLOW_CODES_MAX)):
        c = _M_OVERFLOW_CODES[i]
        if c not in assigned:
            return c
    return None


_WEEKDAY_CODE_TO_INT: dict[str, int] = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}

# W41 v2 (警告日本語化): 警告メッセージで weekday / am_pm を日本語表記する.
_WEEKDAY_JP: tuple[str, ...] = ("月曜", "火曜", "水曜", "木曜", "金曜", "土曜", "日曜")
_AM_PM_JP: dict[str, str] = {"am": "午前", "pm": "午後", "any": "終日"}


def _weekday_jp(weekday: int) -> str:
    """0=月..6=日 を日本語ラベル ("月曜" 等) に変換. 範囲外は ``weekday=N`` を返す."""
    if 0 <= weekday <= 6:
        return _WEEKDAY_JP[weekday]
    return f"weekday={weekday}"


def _am_pm_jp(am_pm: str) -> str:
    """am/pm/any を日本語 ("午前"/"午後"/"終日") に変換. 不明値はそのまま返す."""
    return _AM_PM_JP.get(am_pm, am_pm)


def _fmt_hhmm(t: time) -> str:
    """time を "HH:MM" 文字列にする (警告で秒を出さないため)."""
    return t.strftime("%H:%M")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


# W41 v2 拡張 (構造化警告): 警告の type / actionable / 関連 patient / 提案時刻などを
# 構造化して返す. UI 側で曜日タブに振り分け + 「⏰ 固定時間を変更」アクションを
# 出すために使う.
V2WarningType = Literal[
    "same_address_consolidation",
    "course_capacity",
    "course_long_distance",
    "course_count",
    "acceptance_blocked",
    # W41 v2 拡張 (警告 type の分離): UI 分類のため細分化.
    # - travel_time_shortage: 固定時刻 / 時間帯 / 午前 / 午後 visit が
    #   前 visit からの移動時間で希望時刻に間に合わない or 昼休憩重複でバンプ.
    # - two_staff_shortage : 二人組訪問必須だが (office, weekday) スタッフ < 2 名.
    # course_long_distance は累積 30 分超 (= 長距離コース) 用途のみに残す.
    "travel_time_shortage",
    "two_staff_shortage",
    # W41 v2 (クロスレビュー修正): diff_add の衝突回避警告を一般 ("general") から
    # 切り出す. 既存 PFV 由来 visit と pool visit の (patient_id, weekday) 重複、
    # および pool 内同 (patient_id, weekday) 重複の 2 ケースで使用.
    "diff_add_conflict",
    # CareFlow Wave Next 2 cross-review [H2]: staff_shifts が未投入で
    # active staff いるのに staff_count=0 になる data-health 警告.
    "data_health_staff_shifts_missing",
    "general",
]


# P2: 未割当患者の構造化理由 (UI 分類 + 詳細表示用).
# 「原因不明 (受入カレンダー× / 容量超過 / 座標未設定 のいずれか)」のような曖昧文言を
# 撤去し、warning.affected_patient_ids との照合で patient_id 単位で確定させる.
UnassignedReason = Literal[
    "no_coordinates",  # 座標未設定 (lat/lng=None)
    "no_primary_office",  # 拠点未設定 (primary_office_id=None)
    "no_weekly_pattern",  # weekly_pattern 未設定 (PFV のみ / 完全未設定)
    "acceptance_calendar",  # 受入カレンダー × で拒否
    "course_capacity",  # コース容量超過 (480 分 or 6 名)
    "course_overflow",  # コース数超過 (Stage 5 で未割当)
    "manager_short",  # マネージャー不足 (M course 不足)
    "same_address_split",  # 同住所 3 名以上で別 set へ動かしたが配置できず
    "fixed_time_conflict",  # 固定時刻衝突 (travel_time_shortage 等)
    "lunch_break",  # 昼休憩 (12:00-13:00) と重なるため除外
    "unknown",  # 上記いずれにも一致しない fallback
]


# P2: 未割当が確定した stage. UI で「どの段階で外れたか」を案内するため.
UnassignedStage = Literal[
    "stage3_set",  # 距離クラスタリングで除外
    "stage4_capacity",  # コース容量制約 (Stage 4 / _check_course_capacity_minutes)
    "stage5_course",  # コース数 / マネージャー不足 (Stage 5)
    "apply",  # apply 段階で除外
    "general",  # 一般 (座標未設定など stage 前段)
]


@dataclass
class V2Warning:
    """構造化された警告 1 件分.

    Fields:
        type: 警告種別 ("same_address_consolidation" など)
        weekday: 関連曜日 (0=月..6=日) / 曜日不問は None
        message: 既存の日本語メッセージ (UI で表示)
        actionable: True なら UI で「固定時間を変更」ボタンを出す
        patient_id / patient_name: 関連患者 (任意 — 主にメイン患者 1 名)
        affected_patient_ids: P2 追加 — 警告に影響を受ける patient_id のリスト
            (例: 容量超過コース内の全 patient, マネージャー不足で未割当の全 patient).
            ``_identify_unassigned_patients`` が text 含み判定ではなく
            ``patient_id in w.affected_patient_ids`` で堅牢に照合するために使う.
        visit_id: 該当の提案 visit ID (週限定変更 API で使用)
        current_time / suggested_time: "HH:MM" 文字列 (集約したかった時刻 vs 現状)
        time_type: "固定" / "時間帯" / "午前" / "午後" / "終日" / None
        preferred_start / preferred_end: 患者の希望時間帯 (HH:MM 文字列)
    """

    type: V2WarningType
    message: str
    weekday: int | None = None
    actionable: bool = False
    patient_id: UUID | None = None
    patient_name: str | None = None
    visit_id: UUID | None = None
    current_time: str | None = None
    suggested_time: str | None = None
    time_type: str | None = None
    preferred_start: str | None = None
    preferred_end: str | None = None
    # P2 追加: 構造化照合用. _identify_unassigned_patients で text 含み判定の
    # 代わりに patient_id 集合で照合する. 既存 emit 箇所は徐々に埋めていく.
    affected_patient_ids: list[UUID] = field(default_factory=list)


@dataclass
class V2Visit:
    """1 件の提案 visit (in-memory, 段階 1〜5 を貫通する中間表現)."""

    patient_id: UUID
    patient_name: str
    patient_code: str | None
    weekday: int  # 0=Mon..6=Sun
    start_time: time
    end_time: time
    service_minutes: int
    lat: float
    lng: float
    office_id: UUID
    am_pm: Literal["am", "pm", "any"]
    source_kind: Literal["fixed", "pool"]
    # 段階 3 で確定するセット id (バケット内ユニーク).
    set_id: int | None = None
    # 段階 5 で確定するコース code (拠点 × 曜日内ユニーク).
    course_code: str | None = None
    # 段階 5 後に判明する担当スタッフ.
    assigned_staff_id: UUID | None = None
    # W41 v2 (Mode 2 UI 拡張): 住所文字列 + エリアラベル (町レベル).
    # Patient.address から build 時に流し込み, UI でエリア偏在を可視化する.
    address: str | None = None
    area_label: str | None = None
    # W41 v2 (Mode 2 UI 拡張 / Before/After 表示拡張):
    # patient.weekly_pattern.entries[].time_type と patient.sex_restriction を
    # UI に持ち回す. 例: time_type="午前"/"午後"/"終日"/"固定"/"時間帯",
    # sex_restriction="female_only"/"male_only"/None.
    time_type: str | None = None
    sex_restriction: str | None = None
    # W41 v2 (H2 視覚化): 同住所グループ id. UI で「📍 同住所 (N 名)」表示用.
    # API レスポンス構築時に _assign_same_address_groups で割当てる.
    same_address_group_id: str | None = None
    # W41 v2 (UI 時間詳細表示): 患者の希望時間帯 (HH:MM 文字列).
    # patient.weekly_pattern.entries[].preferred_start / preferred_end から流す.
    # ``time_type='時間帯'`` のとき範囲 (start-end), ``'固定'`` のとき開始時刻のみ.
    preferred_start: str | None = None
    preferred_end: str | None = None
    # W41 v2 拡張 (訪問間距離): 同コース内で次の patient までの直線距離 (km).
    # コース内 start_time 昇順で隣接ペアの Haversine 距離を計算し、最後の visit は None.
    # `_assign_distance_to_next` (api/v1/schedule_v2.py) で書き込まれる.
    distance_to_next_km: float | None = None
    # W41 v2 拡張 (二人組訪問): Patient.requires_multiple_staff を per-visit に流す.
    # True のとき、この visit は **同時刻** に **スタッフ 2 名** が同行する運用.
    # (同住所複数 patient は 1 スタッフが連続して回る "concurrent visits" であり、
    # こちらは 1 patient × 2 staff の "co-visit" で別概念. コース容量計算でも
    # patient 数としては 1 件としてカウントする.)
    requires_multiple_staff: bool = False


@dataclass
class V2Bucket:
    """段階 2 のバケット (office_id × weekday × am_pm)."""

    office_id: UUID
    weekday: int
    am_pm: Literal["am", "pm"]
    visits: list[V2Visit] = field(default_factory=list)


@dataclass
class V2Set:
    """段階 3 で作られる距離セット (2-3 人 / 同バケット)."""

    visits: list[V2Visit] = field(default_factory=list)

    @property
    def centroid(self) -> tuple[float, float] | None:
        if not self.visits:
            return None
        return (
            sum(v.lat for v in self.visits) / len(self.visits),
            sum(v.lng for v in self.visits) / len(self.visits),
        )


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2 点間の Haversine 距離 (km). 地球半径 6371 km."""
    r_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r_km * c


def haversine_minutes(distance_km: float) -> int:
    """W41 v2 拡張 (移動時間の time 化): 直線距離 (km) から移動時間 (分) を概算する.

    Rules:
        - ``distance_km <= 0`` (同住所 / 数値誤差): 0 分
        - それ以外: ``distance_km / TRAVEL_SPEED_KMH * 60`` を整数丸めし、最低 1 分.

    都市部の安全側仮定として ``TRAVEL_SPEED_KMH = 20`` km/h (信号・混雑考慮).
    """
    if distance_km <= 0:
        return 0
    return max(1, int(round(distance_km / TRAVEL_SPEED_KMH * 60)))


def _address_bucket(lat: float, lng: float) -> tuple[float, float]:
    """H2/H3 で同住所判定するためのバケットキー."""
    lat_b = round(lat / SAME_ADDRESS_TOLERANCE) * SAME_ADDRESS_TOLERANCE
    lng_b = round(lng / SAME_ADDRESS_TOLERANCE) * SAME_ADDRESS_TOLERANCE
    return (lat_b, lng_b)


# ---------------------------------------------------------------------------
# Area label extraction (W41 v2 Mode 2 UI 拡張)
# ---------------------------------------------------------------------------

# 千葉県千葉市XX区YY... — 区が含まれる住所
_AREA_PATTERN_WITH_WARD = re.compile(r"千葉県?千葉市?(?P<ward>[^区]+区)(?P<town>[^0-9０-９\s\-]+)")
# 千葉県四街道市XX... — 区が無い市住所
_AREA_PATTERN_CITY_ONLY = re.compile(r"(?P<city>[^市県\s]+市)(?P<town>[^0-9０-９\s\-]+)")
# 末尾の「町」「丁目」「番地」等を除去するための正規表現.
_TOWN_TRAILING_RE = re.compile(r"(町|丁目|番地|番).*$")


def _extract_area_label(address: str | None) -> str | None:
    """住所文字列から「町」レベルのエリアラベルを抽出する.

    例:
      "千葉県千葉市稲毛区宮野木町818-2"       → "宮野木"
      "千葉県千葉市花見川区幕張本郷3-21-29"   → "幕張本郷"
      "千葉県千葉市美浜区磯辺4-175棟402"       → "磯辺"
      "千葉県四街道市大日27-18"                → "大日"
      None / 空文字                            → None

    取得できない場合は None を返す.
    """
    if not address:
        return None
    m = _AREA_PATTERN_WITH_WARD.search(address)
    if m:
        town = m.group("town")
        stripped = _TOWN_TRAILING_RE.sub("", town)
        return stripped or town[:6]
    m2 = _AREA_PATTERN_CITY_ONLY.search(address)
    if m2:
        town = m2.group("town")
        stripped = _TOWN_TRAILING_RE.sub("", town)
        return stripped or town[:8]
    return None


# ---------------------------------------------------------------------------
# Helpers — weekday / time parsing
# ---------------------------------------------------------------------------


def _resolve_weekday(value: Any) -> int | None:
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str):
        return _WEEKDAY_CODE_TO_INT.get(value)
    return None


def _parse_hhmm(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return time(h, m)


def _add_minutes(t: time, minutes: int) -> time:
    total = t.hour * 60 + t.minute + minutes
    if total >= 24 * 60:
        return time(23, 59)
    if total < 0:
        return time(0, 0)
    return time(total // 60, total % 60)


def _round_up_to_5min(t: time) -> time:
    """``time`` を 5 分刻みに切り上げ.

    UI 上の時刻を整然と並べる + 実質バッファーを 8-12 分に揃えるため、
    ``_apply_travel_time_to_courses`` の非固定 visit ``actual_start`` 確定後に
    呼び出す. 固定枠 (``time_type='固定'``) は希望時刻を強制するため対象外.

    例:
        10:31 → 10:35
        09:03 → 09:05
        10:00 → 10:00 (既に 5 分刻み)
        09:59 → 10:00
        23:58 → 23:59 (24:00 を超える場合は _add_minutes と同じく 23:59 で頭打ち)
    """
    total_min = t.hour * 60 + t.minute
    remainder = total_min % 5
    if remainder == 0:
        return time(t.hour, t.minute)
    rounded = total_min + (5 - remainder)
    if rounded >= 24 * 60:
        return time(23, 59)
    return time(rounded // 60, rounded % 60)


# ---------------------------------------------------------------------------
# Stage helpers — am/pm decision (Q1)
# ---------------------------------------------------------------------------


def determine_am_pm(
    *,
    time_type: str | None,
    preferred_start: time | None,
) -> Literal["am", "pm", "any"]:
    """time_type に応じて午前/午後を柔軟判定する (Q1 確定 / §13).

    - 午前 → am
    - 午後 → pm
    - 終日 → any (好きな方を選べる)
    - 固定 → preferred_start 時刻が 12:00 未満なら am, 12:00 以降なら pm
    - 時間帯 → preferred_start を基準に同じ判定
    - None / 不明 → any
    """
    if time_type == "午前":
        return "am"
    if time_type == "午後":
        return "pm"
    if time_type == "終日":
        return "any"
    if time_type in ("固定", "時間帯") and preferred_start is not None:
        return "am" if preferred_start.hour < NOON_HOUR else "pm"
    return "any"


def _is_in_lunch_break(start: time, end: time) -> bool:
    """H10: visit が昼休憩枠 (12:00-13:00) と重なるか判定."""
    if start >= LUNCH_END:
        return False
    if end <= LUNCH_START:
        return False
    return True


# ---------------------------------------------------------------------------
# Stage 1: プール作成
# ---------------------------------------------------------------------------


async def _load_active_patients(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
) -> dict[UUID, Patient]:
    """対象拠点の active 患者を一括ロードする."""
    if not office_ids:
        return {}
    rows = await db.scalars(
        select(Patient).where(
            Patient.status == "active",
            Patient.deleted_at.is_(None),
            Patient.primary_office_id.in_(office_ids),
        )
    )
    return {p.id: p for p in rows.all()}


async def _load_patients_with_fixed(
    db: AsyncSession,
    *,
    patient_ids: list[UUID],
) -> set[UUID]:
    """``patient_fixed_visits`` (mode='normal') を持つ patient_id 集合を返す."""
    if not patient_ids:
        return set()
    rows = await db.scalars(
        select(PatientFixedVisit.patient_id).where(
            PatientFixedVisit.patient_id.in_(patient_ids),
            PatientFixedVisit.mode == "normal",
        )
    )
    return set(rows.all())


def _extract_weekly_entries(
    patient: Patient,
) -> list[tuple[int, time, int, str | None, str | None, str | None]]:
    """patient.weekly_pattern から ``(weekday, start_time, service_minutes,
    time_type, preferred_start_str, preferred_end_str)`` を取り出す.

    リスト形式 (`entries: [{weekday, preferred_start, ...}]`) と
    サマリ形式 (`preferred_weekdays + preferred_start`) の両方をサポート.

    W41 v2 (UI 時間詳細表示): ``preferred_start`` / ``preferred_end`` の元文字列も
    そのまま返して V2Visit に積む.
    """
    pattern = patient.weekly_pattern
    if not isinstance(pattern, dict):
        return []
    out: list[tuple[int, time, int, str | None, str | None, str | None]] = []

    entries = pattern.get("entries")
    base_time_type = pattern.get("time_type")
    if isinstance(entries, list) and entries:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            wd = _resolve_weekday(entry.get("weekday"))
            if wd is None:
                continue
            ps_raw = entry.get("preferred_start")
            pe_raw = entry.get("preferred_end")
            st = _parse_hhmm(ps_raw)
            tt = entry.get("time_type") or base_time_type
            sm = entry.get("service_minutes")
            if not isinstance(sm, int) or sm <= 0:
                sm_value = pattern.get("service_minutes")
                sm = int(sm_value) if isinstance(sm_value, int) and sm_value > 0 else 30
            if st is None:
                # 時刻なしでも午前/午後判定はできるが、提案では仮 9:30 開始にする.
                st = AM_BLOCK_START
            out.append(
                (
                    wd,
                    st,
                    sm,
                    tt if isinstance(tt, str) else None,
                    ps_raw if isinstance(ps_raw, str) else None,
                    pe_raw if isinstance(pe_raw, str) else None,
                )
            )
        return out

    # サマリ形式: preferred_weekdays + preferred_start を展開
    weekdays_raw = pattern.get("preferred_weekdays")
    base_ps_raw = pattern.get("preferred_start")
    base_pe_raw = pattern.get("preferred_end")
    base_start = _parse_hhmm(base_ps_raw)
    base_sm_raw = pattern.get("service_minutes")
    base_sm = int(base_sm_raw) if isinstance(base_sm_raw, int) and base_sm_raw > 0 else 30
    if isinstance(weekdays_raw, list):
        for wd_raw in weekdays_raw:
            wd = _resolve_weekday(wd_raw)
            if wd is None:
                continue
            st = base_start if base_start is not None else AM_BLOCK_START
            out.append(
                (
                    wd,
                    st,
                    base_sm,
                    base_time_type if isinstance(base_time_type, str) else None,
                    base_ps_raw if isinstance(base_ps_raw, str) else None,
                    base_pe_raw if isinstance(base_pe_raw, str) else None,
                )
            )
    return out


_WEEKDAY_INT_TO_CODE: dict[int, str] = {v: k for k, v in _WEEKDAY_CODE_TO_INT.items()}


def _weekday_int_to_code(weekday: int) -> str | None:
    return _WEEKDAY_INT_TO_CODE.get(weekday)


def _extract_time_type_for_weekday(patient: Patient, weekday: int) -> str | None:
    """W41 v2 (Mode 2 UI 拡張 / Before/After):
    patient.weekly_pattern.entries[weekday] の time_type を取り出す.

    entries 形式 (リスト) を優先し、サマリ形式の base time_type にフォールバック.
    どちらにも無ければ None.
    """
    pattern = patient.weekly_pattern
    if not isinstance(pattern, dict):
        return None
    base_tt = pattern.get("time_type")
    base_tt_s = base_tt if isinstance(base_tt, str) else None
    entries = pattern.get("entries")
    if isinstance(entries, list):
        wd_code = _weekday_int_to_code(weekday)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_wd = entry.get("weekday")
            if entry_wd == wd_code or entry_wd == weekday:
                tt = entry.get("time_type") or base_tt_s
                return tt if isinstance(tt, str) else None
    return base_tt_s


def _extract_preferred_window_for_weekday(
    patient: Patient, weekday: int
) -> tuple[str | None, str | None]:
    """W41 v2 (UI 時間詳細表示):
    patient.weekly_pattern.entries[weekday] の (preferred_start, preferred_end) を取り出す.

    entries 形式 (リスト) を優先し、サマリ形式の base preferred_start/end にフォールバック.
    どちらにも無ければ (None, None).
    """
    pattern = patient.weekly_pattern
    if not isinstance(pattern, dict):
        return (None, None)
    base_ps = pattern.get("preferred_start")
    base_pe = pattern.get("preferred_end")
    base_ps_s = base_ps if isinstance(base_ps, str) else None
    base_pe_s = base_pe if isinstance(base_pe, str) else None
    entries = pattern.get("entries")
    if isinstance(entries, list):
        wd_code = _weekday_int_to_code(weekday)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_wd = entry.get("weekday")
            if entry_wd == wd_code or entry_wd == weekday:
                ps = entry.get("preferred_start") or base_ps_s
                pe = entry.get("preferred_end") or base_pe_s
                return (
                    ps if isinstance(ps, str) else None,
                    pe if isinstance(pe, str) else None,
                )
    return (base_ps_s, base_pe_s)


def _extract_fixed_visits_for_patient(
    fixed_rows: list[PatientFixedVisit],
) -> list[tuple[int, time, int]]:
    """patient_fixed_visits (mode='normal', slot_index=0) を (weekday, start, duration) に変換."""
    out: list[tuple[int, time, int]] = []
    for pfv in fixed_rows:
        if pfv.mode != "normal" or pfv.slot_index != 0:
            continue
        out.append((pfv.weekday, pfv.start_time, pfv.duration_min))
    return out


# ---------------------------------------------------------------------------
# W41 v2 拡張 (今週限定オーバーレイ): pending_edits を (patient_id, weekday) → 編集
# Map に変換し、PFV / weekly_pattern を「読み込み時だけ」上書きする.
# DB / SQLAlchemy セッションには絶対に触らない.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingEditOverlay:
    """1 件の今週限定オーバーレイ.

    Fields:
        patient_id / weekday: 対象キー.
        new_start: ``time`` 形式の開始時刻.
        new_end: ``time`` 形式の終了時刻 (任意).
        new_time_type: "固定" / "時間帯" / "午前" / "午後" / "終日" / None.
        new_start_str / new_end_str: UI 表示用に元の "HH:MM[:SS]" 文字列も保持.
    """

    patient_id: UUID
    weekday: int
    new_start: time
    new_end: time | None
    new_time_type: str | None
    new_start_str: str
    new_end_str: str | None


def _build_pending_edit_overlay(
    pending_edits: list[Any] | None,
    *,
    warnings: list[V2Warning] | None = None,
) -> dict[tuple[UUID, int], PendingEditOverlay]:
    """``pending_edits: list[PendingFixedTimeEdit | dict]`` を
    ``{(patient_id, weekday): PendingEditOverlay}`` に変換する.

    同じ (patient_id, weekday) が複数あれば **最後のもの** で上書きする
    (UI 仕様: 最新編集を採用).

    時刻文字列のパース失敗や weekday 範囲外は warnings に記録してスキップ.
    """
    overlay: dict[tuple[UUID, int], PendingEditOverlay] = {}
    if not pending_edits:
        return overlay
    for raw in pending_edits:
        # Pydantic model または dict のどちらでも受け取れるように.
        if hasattr(raw, "model_dump"):
            data = raw.model_dump()
        elif isinstance(raw, dict):
            data = raw
        else:
            continue
        pid_raw = data.get("patient_id")
        if isinstance(pid_raw, UUID):
            patient_id = pid_raw
        else:
            try:
                patient_id = UUID(str(pid_raw))
            except (ValueError, AttributeError):
                continue
        wd = data.get("weekday")
        if not isinstance(wd, int) or not (0 <= wd <= 6):
            continue
        st = _parse_hhmm(data.get("new_start"))
        if st is None:
            if warnings is not None:
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"今週限定変更: new_start のパースに失敗したためスキップ "
                            f"(patient_id={patient_id}, weekday={wd}, value={data.get('new_start')!r})"
                        ),
                        actionable=False,
                        patient_id=patient_id,
                        weekday=wd,
                    )
                )
            continue
        end_raw = data.get("new_end")
        et: time | None = None
        if end_raw is not None and end_raw != "":
            et = _parse_hhmm(end_raw)
            if et is None:
                if warnings is not None:
                    warnings.append(
                        V2Warning(
                            type="general",
                            message=(
                                f"今週限定変更: new_end のパースに失敗したため new_end を無視 "
                                f"(patient_id={patient_id}, weekday={wd}, value={end_raw!r})"
                            ),
                            actionable=False,
                            patient_id=patient_id,
                            weekday=wd,
                        )
                    )
        new_tt = data.get("new_time_type")
        if not isinstance(new_tt, str) or new_tt == "":
            new_tt = None
        overlay[(patient_id, wd)] = PendingEditOverlay(
            patient_id=patient_id,
            weekday=wd,
            new_start=st,
            new_end=et,
            new_time_type=new_tt,
            new_start_str=data.get("new_start")
            if isinstance(data.get("new_start"), str)
            else _fmt_hhmm(st),
            new_end_str=end_raw if isinstance(end_raw, str) and et is not None else None,
        )
    return overlay


def _compute_overlay_duration(
    overlay: PendingEditOverlay,
    *,
    existing_duration: int,
) -> int:
    """オーバーレイ 1 件の duration_min を確定する.

    Rules:
        - time_type='固定' or None: new_end があれば new_end - new_start を実訪問時間とみなす.
          無ければ existing_duration を保持.
        - time_type='時間帯' / '午前' / '午後' / '終日': new_start..new_end は希望レンジで
          あって実訪問時間ではないため、existing_duration を保持する (= マスター更新と同じ方針).
    """
    is_range_type = overlay.new_time_type in ("時間帯", "午前", "午後", "終日")
    if is_range_type:
        return existing_duration
    if overlay.new_end is not None:
        dur = (overlay.new_end.hour * 60 + overlay.new_end.minute) - (
            overlay.new_start.hour * 60 + overlay.new_start.minute
        )
        if dur > 0:
            # W41 v2 cross-review (M-Codex-3): 上限 8 時間 (480 分) 超は異常値.
            # V2VisitPlan.duration_min も Field(ge=1, le=480) で 480 を上限としている.
            if dur > 480:
                return existing_duration
            return dur
    return existing_duration


# ---------------------------------------------------------------------------
# Stage 1+2: プール → V2Visit 展開
# ---------------------------------------------------------------------------


def build_visits_for_pool(
    patients: list[Patient],
    *,
    fixed_by_patient: dict[UUID, list[PatientFixedVisit]] | None = None,
    use_fixed_as_source: bool = False,
    pending_overlay: dict[tuple[UUID, int], PendingEditOverlay] | None = None,
    course_code_by_template_id: dict[UUID, str] | None = None,
) -> list[V2Visit]:
    """段階 1〜2 中間: 各患者の希望を V2Visit に展開する.

    ``use_fixed_as_source=True`` の場合は ``fixed_by_patient`` を優先し、
    weekly_pattern より固定枠のスケジュールを使う (機能 D の再生成).

    ``pending_overlay`` が渡された場合、(patient_id, weekday) が一致する希望時刻を
    オーバーレイ値で上書きする (DB は変更しない). Patient.weekly_pattern オブジェクト
    自体には触らない.

    CareFlow #102 Fix A: ``course_code_by_template_id`` が渡された場合、
    fixed source 分岐で ``PatientFixedVisit.course_template_id`` から
    course label (例 "B") を引いて V2Visit.course_code に埋める. これにより
    orphan PFV (= 旧版で常に course_code=None だった visit) が後段 Stage 5 の
    機械的付番で別 course に上書きされず、PFV で指定された course を尊重できる.
    """
    overlay = pending_overlay or {}
    visits: list[V2Visit] = []
    for patient in patients:
        if patient.lat is None or patient.lng is None or patient.primary_office_id is None:
            continue
        addr = patient.address
        area = _extract_area_label(addr)
        sex_r = patient.sex_restriction
        # W41 v2 拡張 (二人組訪問): patient.requires_multiple_staff を per-visit に
        # 流す. 旧 DB 状態 (フィールド存在しない場合) は False にフォールバック.
        req_multi = bool(getattr(patient, "requires_multiple_staff", False) or False)
        used_fixed = False
        if use_fixed_as_source and fixed_by_patient is not None:
            fixed_rows = fixed_by_patient.get(patient.id) or []
            # CareFlow #102 Fix A: weekday -> course_code map を patient ごとに構築.
            # 同 patient で複数曜日に異なる course_template_id が指定されている
            # ケースを尊重するため (実運用ではほぼ単一だが、データ的に許容される).
            wd_to_course_code: dict[int, str] = {}
            if course_code_by_template_id:
                for _row in fixed_rows:
                    if _row.course_template_id is not None:
                        _label = course_code_by_template_id.get(_row.course_template_id)
                        if _label is not None:
                            wd_to_course_code[_row.weekday] = _label
            entries_fixed = _extract_fixed_visits_for_patient(fixed_rows)
            if entries_fixed:
                used_fixed = True
                for wd, st, sm in entries_fixed:
                    ov = overlay.get((patient.id, wd))
                    if ov is not None:
                        st_eff = ov.new_start
                        sm_eff = _compute_overlay_duration(ov, existing_duration=sm)
                        tt_eff = ov.new_time_type or "固定"
                        ps_eff = ov.new_start_str
                        pe_eff = ov.new_end_str
                    else:
                        st_eff = st
                        sm_eff = sm
                        tt_eff = "固定"
                        ps_eff = _fmt_hhmm(st)
                        pe_eff = None
                    end_t = _add_minutes(st_eff, sm_eff)
                    am_pm = determine_am_pm(time_type=tt_eff, preferred_start=st_eff)
                    # CareFlow #102 Fix A: PFV.course_template_id から引いた
                    # course_code を埋める. Stage 5 (#102 Fix B) はこの既存
                    # course_code を尊重するため、orphan PFV visit は PFV で
                    # 指定された course にそのまま配置される.
                    cc_eff = wd_to_course_code.get(wd)
                    visits.append(
                        V2Visit(
                            patient_id=patient.id,
                            patient_name=patient.name,
                            patient_code=patient.code,
                            weekday=wd,
                            start_time=st_eff,
                            end_time=end_t,
                            service_minutes=sm_eff,
                            lat=float(patient.lat),
                            lng=float(patient.lng),
                            office_id=patient.primary_office_id,
                            am_pm=am_pm,
                            source_kind="fixed",
                            course_code=cc_eff,
                            address=addr,
                            area_label=area,
                            time_type=tt_eff,
                            preferred_start=ps_eff,
                            preferred_end=pe_eff,
                            sex_restriction=sex_r,
                            requires_multiple_staff=req_multi,
                        )
                    )
        if not used_fixed:
            entries = _extract_weekly_entries(patient)
            for wd, st, sm, tt, ps_str, pe_str in entries:
                ov = overlay.get((patient.id, wd))
                if ov is not None:
                    st_eff = ov.new_start
                    sm_eff = _compute_overlay_duration(ov, existing_duration=sm)
                    tt_eff = ov.new_time_type or tt
                    ps_eff = ov.new_start_str
                    pe_eff = ov.new_end_str if ov.new_end_str is not None else pe_str
                else:
                    st_eff = st
                    sm_eff = sm
                    tt_eff = tt
                    ps_eff = ps_str
                    pe_eff = pe_str
                end_t = _add_minutes(st_eff, sm_eff)
                am_pm = determine_am_pm(time_type=tt_eff, preferred_start=st_eff)
                visits.append(
                    V2Visit(
                        patient_id=patient.id,
                        patient_name=patient.name,
                        patient_code=patient.code,
                        weekday=wd,
                        start_time=st_eff,
                        end_time=end_t,
                        service_minutes=sm_eff,
                        lat=float(patient.lat),
                        lng=float(patient.lng),
                        office_id=patient.primary_office_id,
                        am_pm=am_pm,
                        source_kind="pool",
                        address=addr,
                        area_label=area,
                        time_type=tt_eff,
                        sex_restriction=sex_r,
                        preferred_start=ps_eff,
                        preferred_end=pe_eff,
                        requires_multiple_staff=req_multi,
                    )
                )
    return visits


# ---------------------------------------------------------------------------
# Stage 2: バケット振り分け
# ---------------------------------------------------------------------------


def split_into_buckets(
    visits: list[V2Visit],
) -> dict[tuple[UUID, int, Literal["am", "pm"]], V2Bucket]:
    """段階 2: (office_id × weekday × am/pm) バケットに振り分ける.

    am_pm='any' (終日) の visit は preferred_start を見て am/pm を最終決定する.
    時刻が <12 なら am, それ以外なら am 寄せ (デフォルト) する.
    """
    buckets: dict[tuple[UUID, int, Literal["am", "pm"]], V2Bucket] = {}
    for v in visits:
        # any (終日) は am にデフォルト寄せ (start_time が 12 以降なら pm).
        if v.am_pm == "any":
            final_ap: Literal["am", "pm"] = "pm" if v.start_time.hour >= NOON_HOUR else "am"
        else:
            final_ap = v.am_pm  # type: ignore[assignment]
        key = (v.office_id, v.weekday, final_ap)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = V2Bucket(office_id=v.office_id, weekday=v.weekday, am_pm=final_ap)
            buckets[key] = bucket
        bucket.visits.append(v)
    return buckets


# ---------------------------------------------------------------------------
# Stage 3: 距離グリーディクラスタリング
# ---------------------------------------------------------------------------


def cluster_by_distance_greedy(
    visits: list[V2Visit],
    *,
    max_per_cluster: int = MAX_PATIENTS_PER_SET,
    warnings: list[V2Warning] | None = None,
) -> list[V2Set]:
    """各バケット内で距離が近い 2-3 人を 1 セットにする (グリーディ).

    アルゴリズム:
      1. 全 visits ペアの Haversine 距離を計算
      2. 最も近いペアを 1 セット作る
      3. そのセットに「重心から最も近い visit」を greedy に追加 (max ``max_per_cluster``)
      4. 残りの visits で再帰 (空になるまで)

    H2 (同住所ペアリング 最大 2 人): 同住所の visits は 2 件までは同セットを優先,
    3 件以上は警告として呼び出し側で扱う.

    silent drop fix: ``warnings`` が渡された場合、同 ``(patient_id, start_time)``
    の重複 visit を skip する際に warning を emit する. これにより
    ``_identify_unassigned_patients`` が patient_id 照合で reason 分類できる.
    後方互換のため ``warnings=None`` (デフォルト) では warning emit を行わない.
    """
    sets: list[V2Set] = []
    remaining = list(visits)
    # 同 patient_id 同 start_time は本来 H1 で 1 件のはず. 同 patient_id の重複は除外.
    seen_keys: set[tuple[UUID, time]] = set()
    unique_remaining: list[V2Visit] = []
    for v in remaining:
        key = (v.patient_id, v.start_time)
        if key in seen_keys:
            if warnings is not None:
                name = v.patient_name or (v.patient_code or "不明")
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"{name} 様: 同時刻 ({_fmt_hhmm(v.start_time)}) の "
                            "重複 visit を 1 件 skip"
                        ),
                        actionable=False,
                        patient_id=v.patient_id,
                        patient_name=v.patient_name,
                        weekday=v.weekday,
                        affected_patient_ids=[v.patient_id],
                    )
                )
            continue
        seen_keys.add(key)
        unique_remaining.append(v)
    remaining = unique_remaining

    while remaining:
        if len(remaining) == 1:
            sets.append(V2Set(visits=[remaining[0]]))
            remaining = []
            break
        # 最も近いペアを見つける
        best_i, best_j = 0, 1
        best_dist = float("inf")
        for i in range(len(remaining)):
            for j in range(i + 1, len(remaining)):
                d = haversine_km(
                    remaining[i].lat,
                    remaining[i].lng,
                    remaining[j].lat,
                    remaining[j].lng,
                )
                if d < best_dist:
                    best_dist = d
                    best_i, best_j = i, j
        seed_a = remaining[best_i]
        seed_b = remaining[best_j]
        # 大きい index から pop しないと前の index がずれる.
        if best_j > best_i:
            remaining.pop(best_j)
            remaining.pop(best_i)
        else:
            remaining.pop(best_i)
            remaining.pop(best_j)
        new_set = V2Set(visits=[seed_a, seed_b])
        # 重心から近い visit を greedy に追加
        while len(new_set.visits) < max_per_cluster and remaining:
            cen = new_set.centroid
            if cen is None:
                break
            best_k = 0
            best_k_dist = float("inf")
            for k, v in enumerate(remaining):
                d = haversine_km(cen[0], cen[1], v.lat, v.lng)
                if d < best_k_dist:
                    best_k_dist = d
                    best_k = k
            # クラスタの最大半径を考慮: 重心から 5km 以上離れていれば打切り
            if best_k_dist > 5.0:
                break
            new_set.visits.append(remaining.pop(best_k))
        sets.append(new_set)
    return sets


def _can_move_to_time(visit: V2Visit, target_time: time) -> bool:
    """visit を target_time に移動可能か判定する (ソフト制約).

    time_type 別の判定:
      - "固定": 集約不可 (時刻固定なので動かせない)
      - "時間帯": preferred_start ≤ target_time ≤ preferred_end なら可
      - "午前": target_time.hour < 12 (NOON_HOUR) なら可
      - "午後": target_time.hour >= 12 なら可
      - "終日": 常に可
      - その他 / None: 常に可 (制約なし)
    """
    tt = visit.time_type
    if tt == "固定":
        return False
    if tt == "時間帯":
        ps = _parse_hhmm(visit.preferred_start)
        pe = _parse_hhmm(visit.preferred_end)
        if ps is not None and target_time < ps:
            return False
        if pe is not None and target_time > pe:
            return False
        return True
    if tt == "午前":
        return target_time.hour < NOON_HOUR
    if tt == "午後":
        return target_time.hour >= NOON_HOUR
    # "終日" / None / 不明 → 制約なし
    return True


def _consolidate_same_address_time(
    visits: list[V2Visit],
    warnings: list[V2Warning],
) -> None:
    """W41 v2 (同住所同時刻集約 ソフト制約):
    同住所 (= 同 ``(office, weekday, address_bucket)``) の patient 群が
    異なる ``start_time`` にいる場合、最多 ``start_time`` (mode) に集約する.

    時間制約 (午前/午後/固定/時間帯) を尊重し、動かせない visit は warning に
    詳細を出して放置する.

    アルゴリズム:
      1. (office_id, weekday, address_bucket) ごとに visits を集計
      2. 同住所が 2 名以上 かつ 異なる start_time にいる場合:
         - 最多 start_time を target_time とする (タイブレーク: 早い時刻優先)
         - 他の visits の time_type を見て _can_move_to_time で判定
         - 集約可能なら visit.start_time / end_time を書き換え
         - 集約不可なら warning ("固定" / "時間帯外" など)

    Args:
        visits: in-place で書き換える対象 visits.
        warnings: 集約できなかった visit を追記する.
    """
    from collections import defaultdict

    # (office_id, weekday, address_bucket) → list[V2Visit]
    groups: dict[tuple[UUID, int, tuple[float, float]], list[V2Visit]] = defaultdict(list)
    for v in visits:
        if v.lat is None or v.lng is None:
            continue
        key = (v.office_id, v.weekday, _address_bucket(v.lat, v.lng))
        groups[key].append(v)

    for _key, group_visits in groups.items():
        if len(group_visits) < 2:
            continue
        start_times = [v.start_time for v in group_visits]
        # 既に全員同じ start_time なら何もしない
        if len(set(start_times)) == 1:
            continue
        # 最多 start_time を決定 (mode). タイは早い時刻を選ぶ.
        counter: Counter[time] = Counter(start_times)
        max_count = max(counter.values())
        # タイブレーク: 出現回数が同じなら時刻が早いほうを優先
        target_time = min(t for t, c in counter.items() if c == max_count)

        for v in group_visits:
            if v.start_time == target_time:
                continue
            if _can_move_to_time(v, target_time):
                # service_minutes を保ったまま start/end を書き換える.
                v.start_time = target_time
                v.end_time = _add_minutes(target_time, v.service_minutes)
            else:
                # 集約不可: 詳細な warning を残す.
                name = v.patient_name or (v.patient_code or "不明")
                wd_jp = _weekday_jp(v.weekday)
                if v.time_type == "固定":
                    reason = "希望時刻が固定のため動かせない"
                elif v.time_type == "時間帯":
                    pe = v.preferred_end or "-"
                    ps = v.preferred_start or "-"
                    reason = f"希望時間帯 ({ps}-{pe}) 外のため動かせない"
                elif v.time_type in ("午前", "午後"):
                    reason = f"希望が {v.time_type} のため別時間帯への集約不可"
                else:
                    reason = "時間制約により集約不可"
                warnings.append(
                    V2Warning(
                        type="same_address_consolidation",
                        message=(
                            f"同住所集約: {name} 様: {wd_jp} は {_fmt_hhmm(v.start_time)} のまま "
                            f"({_fmt_hhmm(target_time)} へ集約したかったが {reason})"
                        ),
                        weekday=v.weekday,
                        actionable=True,
                        patient_id=v.patient_id,
                        patient_name=v.patient_name,
                        current_time=_fmt_hhmm(v.start_time),
                        suggested_time=_fmt_hhmm(target_time),
                        time_type=v.time_type,
                        preferred_start=v.preferred_start,
                        preferred_end=v.preferred_end,
                        # P2: 単一 patient warning でも affected_patient_ids を埋める.
                        affected_patient_ids=[v.patient_id],
                    )
                )


def _enforce_h2_same_address(sets: list[V2Set], warnings: list[V2Warning]) -> None:
    """H2: 同住所 visits は同セット, ただし 1 セット 2 人まで.

    distance_greedy で多くは満たされるが、距離が近接しているのに別セットに
    分かれてしまった同住所ペアを 1 つに統合する.
    """
    # (address_bucket) → [(set_index, visit_index)] を作る
    address_locations: dict[tuple[float, float], list[tuple[int, int]]] = {}
    for si, st in enumerate(sets):
        for vi, v in enumerate(st.visits):
            key = _address_bucket(v.lat, v.lng)
            address_locations.setdefault(key, []).append((si, vi))
    for key, locs in address_locations.items():
        if len(locs) <= 1:
            continue
        # 既に同セットなら OK. 2 件目以降は最初の set へ移動 (max 2 人まで).
        target_si = locs[0][0]
        target_set = sets[target_si]
        moved = 0
        for si, vi in locs[1:]:
            if si == target_si:
                continue
            # 既に target_set に同住所 2 人いるなら警告のみ
            # H2: 同一ループ内で他の locs を target に移し、元のスロットを None
            #     マークしているため、target_set.visits にも None が紛れ込む.
            #     None visit を除外しないと .lat / .lng が AttributeError になる.
            same_addr_in_target = sum(
                1
                for v in target_set.visits
                if v is not None and _address_bucket(v.lat, v.lng) == key
            )
            if same_addr_in_target >= 2:
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"H2 同住所制約: 同住所 ({key[0]:.4f},{key[1]:.4f}) に 3 名以上 "
                            f"検出 — 3 名目以降は別コースのまま残置"
                        ),
                        actionable=False,
                    )
                )
                continue
            # 移動
            v_to_move = sets[si].visits[vi]
            target_set.visits.append(v_to_move)
            sets[si].visits[vi] = None  # type: ignore[assignment]  # 後でフィルタ
            moved += 1
        if moved > 0:
            # None を除去
            for s in sets:
                s.visits = [v for v in s.visits if v is not None]


def _enforce_h2_split_overflow(
    sets: list[V2Set],
    warnings: list[V2Warning],
) -> None:
    """H2 強化: 同住所 3 名以上を強制的に別 set へ分散する.

    既存 ``_enforce_h2_same_address`` の補完として呼ぶ. 同 ``(office, weekday,
    am_pm, address_bucket)`` で 3 名以上を検出したら, 3 件目以降を同
    ``(office, weekday, am_pm)`` 内の容量に余裕がある別 set に移動する.

    移動先候補条件:
      - 同 (office, weekday, am_pm) 内の別 set
      - 移動後の set サイズが ``MAX_PATIENTS_PER_SET`` 以下
      - 移動先に同住所がまだ 2 件未満
    移動先が見つからない場合は warning に詳細記録 (移動できなかった旨明示).
    """
    from collections import defaultdict

    # (office_id, weekday, am_pm, address_bucket) → [(si, vi)] 集計
    groups: dict[
        tuple[UUID, int, Literal["am", "pm", "any"], tuple[float, float]],
        list[tuple[int, int]],
    ] = defaultdict(list)
    for si, st in enumerate(sets):
        for vi, v in enumerate(st.visits):
            if v is None or v.lat is None or v.lng is None:
                continue
            key = (v.office_id, v.weekday, v.am_pm, _address_bucket(v.lat, v.lng))
            groups[key].append((si, vi))

    for key, locs in groups.items():
        if len(locs) < 3:
            continue
        # set ごとに 2 件まで OK、超過分を移動候補リストに
        same_set_count: dict[int, int] = defaultdict(int)
        for si, _vi in locs:
            same_set_count[si] += 1

        overflow: list[tuple[int, int]] = []
        for si, vi in locs:
            if same_set_count[si] > 2:
                overflow.append((si, vi))
                same_set_count[si] -= 1

        # 移動先候補: 同 (office, weekday, am_pm) で容量余裕 & 同住所 2 件未満
        for src_si, src_vi in overflow:
            visit_to_move = sets[src_si].visits[src_vi]
            if visit_to_move is None:
                continue
            target_si: int | None = None
            for ti, t_set in enumerate(sets):
                if ti == src_si:
                    continue
                # 同 (office, weekday, am_pm) か?
                first_v = next((v for v in t_set.visits if v is not None), None)
                if first_v is None:
                    continue
                if (
                    first_v.office_id != visit_to_move.office_id
                    or first_v.weekday != visit_to_move.weekday
                    or first_v.am_pm != visit_to_move.am_pm
                ):
                    continue
                # 容量
                valid_count = sum(1 for v in t_set.visits if v is not None)
                if valid_count >= MAX_PATIENTS_PER_SET:
                    continue
                # 同住所が既に 2 件以上いない
                same_in_target = sum(
                    1
                    for v in t_set.visits
                    if v is not None
                    and v.lat is not None
                    and v.lng is not None
                    and _address_bucket(v.lat, v.lng) == key[3]
                )
                if same_in_target >= 2:
                    continue
                target_si = ti
                break

            if target_si is not None:
                sets[target_si].visits.append(visit_to_move)
                # 後でフィルタ. 既存 `_enforce_h2_same_address` と同じパターン.
                sets[src_si].visits[src_vi] = None  # type: ignore[call-overload]
                wd_jp = _weekday_jp(visit_to_move.weekday)
                name = visit_to_move.patient_name or (visit_to_move.patient_code or "不明")
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"H2 同住所制約: 同住所 3 名以上検出 → 1 名を他コースに分散 "
                            f"({wd_jp} {name} 様)"
                        ),
                        weekday=visit_to_move.weekday,
                        actionable=False,
                        patient_id=visit_to_move.patient_id,
                        patient_name=visit_to_move.patient_name,
                    )
                )
            else:
                name = visit_to_move.patient_name or (visit_to_move.patient_code or "不明")
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"H2 同住所制約: 同住所 3 名以上だが他コースに移動先なし "
                            f"(patient: {name} 様)"
                        ),
                        weekday=visit_to_move.weekday,
                        actionable=False,
                        patient_id=visit_to_move.patient_id,
                        patient_name=visit_to_move.patient_name,
                    )
                )

    # None を除去
    for s in sets:
        s.visits = [v for v in s.visits if v is not None]


# ---------------------------------------------------------------------------
# Stage 4: コース数制約 (= スタッフ数)
# ---------------------------------------------------------------------------


async def count_active_staff_per_weekday(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
    iso_year: int,
    iso_week: int,
) -> dict[tuple[UUID, int], int]:
    """H6/H8: (office_id × weekday) ごとの稼働可能スタッフ数を返す.

    対象: is_trainee=false, role='staff', primary_office_id in office_ids
    かつ StaffShift.is_on=True (当該曜日). weekly override で off になっていれば除外.
    """
    if not office_ids:
        return {}
    staff_rows = await db.scalars(
        select(Staff).where(
            Staff.status == "active",
            Staff.deleted_at.is_(None),
            Staff.role == "staff",
            Staff.is_trainee.is_(False),
            Staff.primary_office_id.in_(office_ids),
        )
    )
    staff_list = list(staff_rows.all())
    if not staff_list:
        return {}
    staff_ids = [s.id for s in staff_list]
    shifts_rows = await db.scalars(
        select(StaffShift).where(
            StaffShift.staff_id.in_(staff_ids),
            StaffShift.is_on.is_(True),
        )
    )
    shifts_by_staff: dict[UUID, set[int]] = {}
    for sh in shifts_rows.all():
        shifts_by_staff.setdefault(sh.staff_id, set()).add(sh.weekday)
    # 当該週の off override を取得
    overrides_rows = await db.scalars(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id.in_(staff_ids),
            StaffWeeklyOverride.iso_year == iso_year,
            StaffWeeklyOverride.iso_week == iso_week,
            StaffWeeklyOverride.override_type == "off",
        )
    )
    off_overrides: set[tuple[UUID, int]] = {
        (ov.staff_id, ov.weekday) for ov in overrides_rows.all()
    }
    counter: Counter[tuple[UUID, int]] = Counter()
    for s in staff_list:
        if s.primary_office_id is None:
            continue
        for wd in shifts_by_staff.get(s.id, set()):
            if (s.id, wd) in off_overrides:
                continue
            counter[(s.primary_office_id, wd)] += 1
    return dict(counter)


async def count_active_managers_per_weekday(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
    iso_year: int,
    iso_week: int,
) -> dict[tuple[UUID, int], int]:
    """(office_id × weekday) ごとの稼働可能マネージャー数を返す.

    対象: role='manager', is_trainee=False, status='active', deleted_at IS NULL,
    primary_office_id in office_ids, かつ StaffShift.is_on=True (当該曜日).
    weekly override で off になっていれば除外.

    CareFlow Wave Next 3: M course (= マネージャー枠) の発行数を当該曜日の
    出勤マネージャー数で動的に絞るために使用する.「マネージャー 1 名に対して
    1 つのコースのみ」ルールの実装. 超過セットは ``run_v2_pipeline`` Stage 5
    で ``unassigned_patients`` に流す.

    ``count_active_staff_per_weekday`` と全く同じシグネチャ / 動作だが
    ``Staff.role`` が ``'manager'`` か ``'staff'`` かだけが違う.
    """
    if not office_ids:
        return {}
    staff_rows = await db.scalars(
        select(Staff).where(
            Staff.status == "active",
            Staff.deleted_at.is_(None),
            Staff.role == "manager",
            Staff.is_trainee.is_(False),
            Staff.primary_office_id.in_(office_ids),
        )
    )
    staff_list = list(staff_rows.all())
    if not staff_list:
        return {}
    staff_ids = [s.id for s in staff_list]
    shifts_rows = await db.scalars(
        select(StaffShift).where(
            StaffShift.staff_id.in_(staff_ids),
            StaffShift.is_on.is_(True),
        )
    )
    shifts_by_staff: dict[UUID, set[int]] = {}
    for sh in shifts_rows.all():
        shifts_by_staff.setdefault(sh.staff_id, set()).add(sh.weekday)
    # 当該週の off override を取得
    overrides_rows = await db.scalars(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id.in_(staff_ids),
            StaffWeeklyOverride.iso_year == iso_year,
            StaffWeeklyOverride.iso_week == iso_week,
            StaffWeeklyOverride.override_type == "off",
        )
    )
    off_overrides: set[tuple[UUID, int]] = {
        (ov.staff_id, ov.weekday) for ov in overrides_rows.all()
    }
    counter: Counter[tuple[UUID, int]] = Counter()
    for s in staff_list:
        if s.primary_office_id is None:
            continue
        for wd in shifts_by_staff.get(s.id, set()):
            if (s.id, wd) in off_overrides:
                continue
            counter[(s.primary_office_id, wd)] += 1
    return dict(counter)


async def _emit_staff_shifts_data_health_warning(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
) -> None:
    """CareFlow Wave Next 2 cross-review [H2]: staff_shifts 未投入時の警告.

    active staff (role='staff', not trainee, status='active') が居るのに
    StaffShift.is_on=True の行が **拠点全体で 0 件** の場合、
    ``load_staff_shifts_from_sheet.py`` が未実行な data-health 問題の可能性が
    高い. apply は block しないが、強い warning として運用者に伝える.

    (休業日と区別できないため、判定は「拠点単位で is_on=True が全曜日 0」のみ.
    一部曜日だけ 0 のケースは正規な休業日として false-positive を避ける.)
    """
    if not office_ids:
        return
    # 拠点ごとに active staff 数 + is_on=True shift 数を集計.
    staff_rows = await db.scalars(
        select(Staff).where(
            Staff.status == "active",
            Staff.deleted_at.is_(None),
            Staff.role == "staff",
            Staff.is_trainee.is_(False),
            Staff.primary_office_id.in_(office_ids),
        )
    )
    staff_list = list(staff_rows.all())
    if not staff_list:
        return  # active staff いない拠点は false-positive を避ける.
    staff_ids_by_office: dict[UUID, list[UUID]] = {}
    for s in staff_list:
        if s.primary_office_id is None:
            continue
        staff_ids_by_office.setdefault(s.primary_office_id, []).append(s.id)
    if not staff_ids_by_office:
        return
    all_staff_ids = [sid for sids in staff_ids_by_office.values() for sid in sids]
    shifts_rows = await db.scalars(
        select(StaffShift).where(
            StaffShift.staff_id.in_(all_staff_ids),
            StaffShift.is_on.is_(True),
        )
    )
    on_shift_count_by_office: dict[UUID, int] = {}
    staff_office: dict[UUID, UUID] = {
        s.id: s.primary_office_id for s in staff_list if s.primary_office_id is not None
    }
    for sh in shifts_rows.all():
        oid = staff_office.get(sh.staff_id)
        if oid is None:
            continue
        on_shift_count_by_office[oid] = on_shift_count_by_office.get(oid, 0) + 1
    for oid, staff_ids in staff_ids_by_office.items():
        if on_shift_count_by_office.get(oid, 0) == 0:
            office_name = (office_name_by_id or {}).get(oid) or str(oid)
            warnings.append(
                V2Warning(
                    type="data_health_staff_shifts_missing",
                    message=(
                        f"{office_name}: active スタッフ {len(staff_ids)} 名いますが "
                        "全曜日で staff_shifts (出勤フラグ) が未登録です. "
                        "(load_staff_shifts_from_sheet.py 未実行の可能性). "
                        "このままだと全 set が M (マネージャー枠) に流れます."
                    ),
                    weekday=None,
                    actionable=True,
                )
            )


def enforce_course_count_constraint(
    sets_by_bucket: dict[tuple[UUID, int, Literal["am", "pm"]], list[V2Set]],
    *,
    staff_count_by_weekday: dict[tuple[UUID, int], int],
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
) -> dict[tuple[UUID, int, Literal["am", "pm"]], list[V2Set]]:
    """段階 4: バケットのセット数がコース数 (= スタッフ数) を超えた場合に警告.

    Q5 確定: マネージャー補充は自動化しない (警告ベース). 超過セットは
    マネージャー候補としてそのまま残し、警告に追加する.

    W41 v2 (警告日本語化): weekday は日本語, office は名前で表示する.

    CareFlow Wave Next 2 cross-review [M1]: warning は raw staff_count ではなく
    ``effective_max = min(staff_count, _COURSE_CODES_MAX)`` を基準にする.
    Stage 5 の code 割り振りは ``A/B/C/D/E`` (= 上限 5) でしか通常コードを発行
    しないため、staff_count=6 / 必要セット 6 のときに 5 set 通常 + 1 set M に
    なる事実を warning が反映していなかった (= 「6=6 なので M 0 件」と誤表示).
    また ``staff_count > _COURSE_CODES_MAX`` のときは余剰スタッフがいて
    cap が効くことを別途案内する.
    """
    for (office_id, weekday, am_pm), sets in sets_by_bucket.items():
        n = staff_count_by_weekday.get((office_id, weekday), 0)
        office_name = (office_name_by_id or {}).get(office_id) or str(office_id)
        wd_jp = _weekday_jp(weekday)
        ap_jp = _am_pm_jp(am_pm)
        # P2: 全 set 内 patient_id (影響受ける可能性のある patient).
        all_pids = list({v.patient_id for s in sets for v in s.visits})
        if n == 0:
            warnings.append(
                V2Warning(
                    type="course_capacity",
                    message=(
                        f"{wd_jp} {office_name} {ap_jp}: スタッフ不在のため "
                        f"{len(sets)} グループを配置不可 (マネージャー補充候補)"
                    ),
                    weekday=weekday,
                    actionable=False,
                    affected_patient_ids=all_pids,
                )
            )
            continue
        # 通常コードは A/B/C/D/E (= _COURSE_CODES_MAX 個) までしか発行されない.
        # staff_count > _COURSE_CODES_MAX のときも余剰は M に流れる旨を案内する.
        if n > _COURSE_CODES_MAX:
            warnings.append(
                V2Warning(
                    type="course_count",
                    message=(
                        f"{wd_jp} {office_name} {ap_jp}: "
                        f"出勤スタッフ {n} 名いますがコース数上限 {_COURSE_CODES_MAX} "
                        "のため余剰スタッフはマネージャー枠 (M) に流れます"
                    ),
                    weekday=weekday,
                    actionable=True,
                )
            )
        effective_max = min(n, _COURSE_CODES_MAX)
        if len(sets) > effective_max:
            # P2: 効率超過分の set 内 patient_id (set 末尾から overflow 順).
            overflow_sets = sets[effective_max:]
            overflow_pids = list({v.patient_id for s in overflow_sets for v in s.visits})
            warnings.append(
                V2Warning(
                    type="course_capacity",
                    message=(
                        f"{wd_jp} {office_name} {ap_jp}: "
                        f"{len(sets)} グループ必要だが対応可能コース数 {effective_max} "
                        f"(対応可能スタッフ {n} 名, コース上限 {_COURSE_CODES_MAX}) "
                        f"(マネージャー補充候補 {len(sets) - effective_max} 件)"
                    ),
                    weekday=weekday,
                    actionable=False,
                    affected_patient_ids=overflow_pids,
                )
            )
    return sets_by_bucket


# ---------------------------------------------------------------------------
# Stage 5: 午前 ↔ 午後 の組み合わせ
# ---------------------------------------------------------------------------


def _set_centroid(v_set: V2Set) -> tuple[float, float] | None:
    return v_set.centroid


def _set_distance(a: V2Set, b: V2Set) -> float:
    ca = _set_centroid(a)
    cb = _set_centroid(b)
    if ca is None or cb is None:
        return 0.0
    return haversine_km(ca[0], ca[1], cb[0], cb[1])


def combine_am_pm_sets(
    am_sets: list[V2Set],
    pm_sets: list[V2Set],
    *,
    staff_count: int,
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
) -> list[tuple[V2Set | None, V2Set | None]]:
    """段階 5: 各スタッフ 1 日 = 午前セット + 午後セットを組み合わせる.

    手順:
      1. am_sets / pm_sets を staff_count 個に丸める (超過は warnings).
      2. am と pm を greedy に「最も近い」ペアでマッチング.
      3. 同エリア優先 (5km 以内なら OK). 5km 以上離れていれば警告.
      4. 余った片方は単独コース (午前のみ or 午後のみ).
    """
    if not am_sets and not pm_sets:
        return []

    # H4: staff_count == 0 (= 勤務可能スタッフ 0 名) の場合は
    #     UI 上 "通常コース (A/B/C/D/E)" を出すと「採用可能」と誤認させるため,
    #     呼び出し側 (run_v2_pipeline) で course_code="M" (manager-required) を
    #     付与する. ここでは早期に警告を出して UX を明示する.
    if staff_count == 0 and (am_sets or pm_sets):
        # 代表的な weekday を am/pm セットから拾う (警告に曜日を埋める)
        sample_wd: int | None = None
        for s in am_sets + pm_sets:
            if s.visits:
                sample_wd = s.visits[0].weekday
                break
        # P2: 全 set 内 patient_id (マネージャー補充候補となる patient).
        affected_pids_zero_staff = list(
            {v.patient_id for s in (am_sets + pm_sets) for v in s.visits}
        )
        warnings.append(
            V2Warning(
                type="course_count",
                message=(
                    f"勤務可能スタッフ 0 名: 午前 {len(am_sets)} グループ / "
                    f"午後 {len(pm_sets)} グループ はマネージャー補充が必要 "
                    f"(course_code='M')"
                ),
                weekday=sample_wd,
                actionable=False,
                affected_patient_ids=affected_pids_zero_staff,
            )
        )

    # H9: コース容量 6 名以内 (午前 + 午後合計)
    # ここでは "セット単位" で組み合わせ、後で合計人数を check.

    courses: list[tuple[V2Set | None, V2Set | None]] = []
    am_remaining = list(am_sets)
    pm_remaining = list(pm_sets)

    while am_remaining and pm_remaining:
        # 注: スタッフ数を超えた分は後段 (run_v2_pipeline Stage 5) で M / M2 / M3
        # にコード割り当てされる. ここではセット数を維持して全ペアを生成する.
        # 最も近い am/pm ペアを greedy に取り出す
        best_i, best_j = 0, 0
        best_d = float("inf")
        for i, a in enumerate(am_remaining):
            for j, p in enumerate(pm_remaining):
                # 容量 check
                total = len(a.visits) + len(p.visits)
                if total > MAX_PATIENTS_PER_COURSE:
                    # 容量超過は除外
                    continue
                d = _set_distance(a, p)
                if d < best_d:
                    best_d = d
                    best_i, best_j = i, j
        if best_d == float("inf"):
            # 全組み合わせが容量超過 → 単独コース化
            break
        am_chosen = am_remaining.pop(best_i)
        pm_chosen = pm_remaining.pop(best_j)
        if best_d > 5.0:
            # 警告に曜日を埋める (am_chosen から拾う)
            sample_v_d: V2Visit | None = None
            for vv in am_chosen.visits + pm_chosen.visits:
                sample_v_d = vv
                break
            warnings.append(
                V2Warning(
                    type="course_long_distance",
                    message=(
                        f"1 コース内 午前→午後 の移動距離 {best_d:.1f}km "
                        f"(推奨 5km 以内、移動時間に余裕を持たせる必要)"
                    ),
                    weekday=sample_v_d.weekday if sample_v_d is not None else None,
                    actionable=False,
                )
            )
        courses.append((am_chosen, pm_chosen))

    # 残った am は午前のみコース
    for a in am_remaining:
        courses.append((a, None))
    # 残った pm は午後のみコース
    for p in pm_remaining:
        courses.append((None, p))

    if staff_count > 0 and len(courses) > staff_count:
        # 拠点名と曜日を warning に含めるため、最初の visit から拾う.
        sample_v: V2Visit | None = None
        for am_c, pm_c in courses:
            if am_c is not None and am_c.visits:
                sample_v = am_c.visits[0]
                break
            if pm_c is not None and pm_c.visits:
                sample_v = pm_c.visits[0]
                break
        # P2: 超過分 (= 末尾の course から staff_count を超えた分) の patient_id.
        overflow_courses = courses[staff_count:]
        overflow_pids: list[UUID] = []
        seen_pids: set[UUID] = set()
        for am_c, pm_c in overflow_courses:
            for s in (am_c, pm_c):
                if s is None:
                    continue
                for v in s.visits:
                    if v.patient_id not in seen_pids:
                        seen_pids.add(v.patient_id)
                        overflow_pids.append(v.patient_id)
        if sample_v is not None:
            office_name = (office_name_by_id or {}).get(sample_v.office_id) or str(
                sample_v.office_id
            )
            wd_jp = _weekday_jp(sample_v.weekday)
            warnings.append(
                V2Warning(
                    type="course_count",
                    message=(
                        f"{wd_jp} 拠点 {office_name}: 必要コース数 {len(courses)} > "
                        f"対応可能スタッフ {staff_count} 名 "
                        f"(マネージャー補充候補 {len(courses) - staff_count} 件)"
                    ),
                    weekday=sample_v.weekday,
                    actionable=False,
                    affected_patient_ids=overflow_pids,
                )
            )
        else:
            warnings.append(
                V2Warning(
                    type="course_count",
                    message=(
                        f"必要コース数 {len(courses)} > 対応可能スタッフ {staff_count} 名 "
                        f"(マネージャー補充候補 {len(courses) - staff_count} 件)"
                    ),
                    actionable=False,
                    affected_patient_ids=overflow_pids,
                )
            )
    return courses


# ---------------------------------------------------------------------------
# W41 v2 拡張: 移動時間の time 化 + コース容量 duration 化
# ---------------------------------------------------------------------------


def calc_course_total_minutes(visits: list[V2Visit]) -> int:
    """コース内訪問の合計所要時間 (分) = visit duration + 隣接移動時間 + バッファー.

    W41 v2 拡張 (コース容量 duration 化): ``len(visits)`` だけでは長時間訪問や
    長距離移動を含むコースを過小評価するため、duration + Haversine 移動時間 +
    訪問間バッファー で計算する.

    HIGH #1 (Codex クロスレビュー): ``_apply_travel_time_to_courses`` は
    earliest_start 計算時に ``VISIT_BUFFER_MINUTES`` (= 8 分) を加算するため、
    実 timeline は ``sum(duration) + sum(travel)`` より長くなる.
    本関数も同じ補正を入れないと容量判定 (480 分) が漏れる
    (例: 6 visit 異住所連続 = 5 × 8 = 40 分の差).

    Notes:
        - ``visits`` は同コース (同 ``(office_id, weekday, course_code)``) 内.
        - 同住所 (= ``_address_bucket`` 一致) は **移動時間 0 + バッファー 0**.
          (同アパート/施設内の連続訪問は記入・次室移動が最小限のため.)
        - 異住所への遷移は ``travel_min + VISIT_BUFFER_MINUTES`` を加算.
        - 二人組訪問 ``requires_multiple_staff=True`` の duration はそのまま 1 回分
          (時間軸上の所要時間はスタッフ数に依存しない).
    """
    if not visits:
        return 0
    sv = sorted(visits, key=lambda v: v.start_time)
    total = sum(int(v.service_minutes) for v in sv)
    for i in range(1, len(sv)):
        prev = sv[i - 1]
        cur = sv[i]
        # 同住所は移動時間 0 + バッファー 0
        if _address_bucket(prev.lat, prev.lng) == _address_bucket(cur.lat, cur.lng):
            continue
        travel_min = haversine_minutes(haversine_km(prev.lat, prev.lng, cur.lat, cur.lng))
        # _apply_travel_time_to_courses と同じく異住所はバッファー加算.
        total += travel_min + VISIT_BUFFER_MINUTES
    return total


def _reorder_same_address_consecutive(
    visits: list[V2Visit],
    *,
    warnings: list[V2Warning] | None = None,
) -> list[V2Visit]:
    """同住所ペアを連続位置 (配列上で隣接) に並べ替える.

    **ユーザー要望 (最重要)**: 同住所患者は必ず連番に配置する. 間に別住所患者が
    挟まると同住所メリット (移動 0 + バッファー 0) が消えるため、
    ``_apply_travel_time_to_courses`` で start_time 順にソートした直後に
    本関数を呼んで同住所連番を強制する.

    Algorithm:
        1. 入力は ``start_time`` 昇順を想定 (順序は基本的に維持).
        2. ``lat`` / ``lng`` が None の visit は対象外 (元の位置を維持).
        3. 同住所バケットごとに位置をグルーピング.
        4. 同住所が 2 件かつ既に隣接していない場合のみ並べ替え:
           - 両者非固定: 後ろ側の visit を、前側の visit の隣 (直後) に移動.
           - 一方が固定 (``time_type='固定'``) で他方が非固定:
             非固定側を固定側の隣に寄せる. 固定側の元の位置 (= 配列インデックス)
             は不変. 非固定側を固定側の元 start_time に対し前後関係を維持して挿入.
           - 両者固定: 並べ替え不可 (start_time が動かせず、位置を入れ替えると
             配列順 = 時刻順 不整合になる). そのまま放置.
        5. 同住所 3 件以上が同一コース内に残っている場合: 2 件のみペア化し
           3 件目以降は warning を出す (``H2 enforce`` で別コース化済みの想定).

    Returns:
        並べ替え後の新しい list. 入力 list の要素順は変更しない
        (shallow copy を返す). 要素 (V2Visit) インスタンス自体は同一
        (同じオブジェクトを参照する shallow copy).

    Notes:
        - 並べ替え後に ``start_time`` 単調性が一時的に崩れる可能性があるが、
          ``_apply_travel_time_to_courses`` が後段で earliest_start を再計算する
          ため OK (例: [A 9:00, B 9:30 同住所, C 9:15] → リオーダーで
          [A 9:00, B 9:30, C 9:15] になっても、後段で C を 9:30→10:00 等に調整).
        - 固定時刻 patient の ``start_time`` は不変 (既存仕様).
          ただし配列位置は同住所相手と隣接するよう調整可能.

    Side effects on other visits' times (intentional — ユーザー方針):
        同住所連番を最優先するため、間に挟まれた / 隣接した別住所 visit や
        非固定 first の start_time が後段 (``_apply_travel_time_to_courses``)
        で動くことを **明示的に許容** している.

        - 同住所ペア (固定 first + 非固定 second) + 間に別住所 visit:
          例 input ``[A 09:00 固定 addr1, C 09:15 addr2, B 11:00 非固定 addr1]``
          → reorder 後 ``[A, B, C]``. 後段で C の earliest が
          ``B.end + travel + buffer`` まで押し下げられる場合がある.
          (= reorder で挟まれた別住所 visit が後段で押し下げられる場合がある.)
        - 同住所ペア (非固定 first + 固定 second) + 間に別住所 visit:
          例 input ``[A 09:00 非固定 addr1, C 09:15 addr2, B 10:00 固定 addr1]``
          → reorder 後 ``[C, A, B]``. 後段で A (非固定 first) の earliest が
          ``C.end + travel + buffer`` (= ~10:00) まで繰り下げられる場合がある.
          (= 非固定 first の start_time が後段で繰り下げられる可能性がある.
          本来 09:00 で配置できた visit が同住所優先のため遅延される.)

        これらは「同住所メリット (移動 0 + バッファー 0) を確保するためなら
        他 visit の時刻が動くのは許容」とのユーザー方針に基づく仕様であり、
        バグではない. behavior pin テスト
        (``test_same_address_pair_pushes_back_subsequent_other_address_visit``,
        ``test_same_address_pair_non_fixed_first_with_fixed_second_can_push_first_later``)
        で動作を固定している.
    """
    if not visits or len(visits) < 2:
        return list(visits)

    # 同住所バケットごとに index を収集 (lat/lng=None は除外).
    address_to_indices: dict[tuple[float, float], list[int]] = {}
    for i, v in enumerate(visits):
        if v.lat is None or v.lng is None:
            continue
        key = _address_bucket(v.lat, v.lng)
        address_to_indices.setdefault(key, []).append(i)

    # 並べ替え対象 (address_bucket, [indices]) のみ抽出.
    # 既に隣接していたら skip.
    result = list(visits)
    for _addr_key, indices in address_to_indices.items():
        if len(indices) < 2:
            continue

        # current_indices を毎回再計算 (前のループで result を組み替えた可能性).
        current_indices = [
            i
            for i, v in enumerate(result)
            if (
                v.lat is not None
                and v.lng is not None
                and _address_bucket(v.lat, v.lng) == _addr_key
            )
        ]
        if len(current_indices) < 2:
            continue

        # M1: warning 発火は current_indices 再計算後に判定する (reorder 後の
        # 実際の同住所件数で判断). 3+ は H2 enforce で別コース化されている想定だが、
        # 残っていたら 2 件のみペア化 + warning. sample は reorder 後の先頭 visit.
        if len(current_indices) >= 3 and warnings is not None:
            sample = result[current_indices[0]]
            name = sample.patient_name or (sample.patient_code or "不明")
            # L1: warning メッセージに曜日 + course_code (+ 可能なら address) を含める
            # (UI 識別性向上). V2Visit.address があれば併記, なければ最低限の lat/lng.
            weekday_label = (
                _weekday_jp(sample.weekday) if sample.weekday is not None else "曜日不明"
            )
            course_label = sample.course_code or "?"
            address_suffix = (
                f"{sample.address} ({_addr_key[0]:.4f},{_addr_key[1]:.4f})"
                if sample.address
                else f"({_addr_key[0]:.4f},{_addr_key[1]:.4f})"
            )
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"H2 同住所連番: {weekday_label}・コース {course_label} の "
                        f"同住所 {address_suffix} に 3 名以上が同コース内に残存 — "
                        f"2 名のみ連番化 "
                        f"(該当 patient 例: {name} 様, manual review needed)"
                    ),
                    weekday=sample.weekday,
                    actionable=False,
                    patient_id=sample.patient_id,
                    patient_name=sample.patient_name,
                )
            )

        first_idx = current_indices[0]
        second_idx = current_indices[1]
        if second_idx - first_idx == 1:
            continue  # 既に隣接

        first = result[first_idx]
        second = result[second_idx]
        first_fixed = first.time_type == "固定"
        second_fixed = second.time_type == "固定"

        if first_fixed and second_fixed:
            # 両者固定: 配列位置を動かすと start_time 単調性が崩れたまま
            # earliest 計算に影響. 動かさない (隣接していないことを許容).
            continue

        # second を first の直後に移動するのが基本.
        # ただし second が固定 / first が非固定なら、first を second の直前に
        # 移動する (= 固定側を動かさない).
        if second_fixed and not first_fixed:
            # 非固定 first を固定 second の直前に移動.
            # pop first, then insert at (second_idx - 1) since first removed shifted.
            v_to_move = result.pop(first_idx)
            insert_at = second_idx - 1  # 元 second_idx の位置 (= 直前).
            result.insert(insert_at, v_to_move)
        else:
            # first が固定 (second 非固定) または両者非固定:
            # second を first の直後 (first_idx + 1) に移動.
            v_to_move = result.pop(second_idx)
            insert_at = first_idx + 1
            result.insert(insert_at, v_to_move)

    return result


def _apply_travel_time_to_courses(
    visits: list[V2Visit],
    *,
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
) -> set[int]:
    """W41 v2 拡張 (動的 start_time): 同コース連続訪問に移動時間を反映する.

    Algorithm:
        1. ``(office_id, weekday, course_code)`` ごとに visits を集計
        2. 各コース内で ``start_time`` 昇順にソートし、隣接ペアの移動時間を計算
        3. 各 visit の ``actual_start = max(desired_start, prev_end + travel_min)``
           を ``time_type`` ごとに分岐して決定:
             - "固定": 移動時間が不足する場合、不足量で分岐する.
                 * ``shortage < SHORTAGE_THRESHOLD_MIN`` (= 5 分未満): 微小不足は
                   運用で吸収可能とみなし、warning だけ出して固定時刻に配置.
                 * ``shortage >= SHORTAGE_THRESHOLD_MIN``: 物理的に配置不可と判定し
                   ``course_code = None`` に書き換え、戻り値 ``set[int]`` (id(v))
                   に visit を含めて呼び出し側に通知する. 呼び出し側はこの ID 集合を
                   使って ``after_visits`` から取り除き ``unassigned_patients`` に
                   流す (理由は ``fixed_time_conflict``).
             - "時間帯": ``preferred_start ≤ actual ≤ preferred_end`` 内なら earliest
               を採用. 範囲超過なら **earliest を採用** + warning (window_upper に
               クランプすると infeasible timeline になるため).
             - "午前": ``AM_BLOCK_END`` (12:00) 未満で earliest を採用.
               12:00 以降になる場合は ``LUNCH_END`` (13:00) にバンプ可能なら
               午後扱いで配置, 不可なら earliest 維持 + warning.
             - "午後": ``PM_BLOCK_END`` 以前で earliest を採用 (>=13:00 制約).
               18:00 超なら earliest 維持 + actionable warning.
             - "終日" / None: 営業時間内なら制約なく earliest を採用.
        4. 各 visit の actual_start が確定したら **昼休憩 (12:00-13:00) との重なりを
           再検証** (CRITICAL #1): 重なる場合は ``time_type`` に応じて 13:00 にバンプ
           or warning を出す. (_filter_unavailable_and_lunch は既に実行済みのため
           ここで再チェックしないと H10 が破られる可能性がある.)
        5. 30 分以上の移動が連続するコースは長距離 warning を別途出す
           (``course_long_distance``).

    In-place: visits の ``start_time`` / ``end_time`` を書き換える.

    Returns:
        物理不可能と判定して ``course_code = None`` にした visit の ``id(v)`` 集合.
        呼び出し側はこの集合を使って ``after_visits`` から除去すること
        (除去しないと ``_identify_unassigned_patients`` の after_pids 判定に
        引っかからず未割当扱いされない).

    Notes:
        ``course_code`` が None の visit はスキップ (Stage 5 がコース割当てを
        行わなかったもの, 例: スタッフ不在). 同住所連続は移動 0 分.

        warning type は **travel_time_shortage** (移動時間で時刻調整) と
        **course_long_distance** (累積 30 分超) を分けて出力する.
    """
    # 1) コードごとに集計
    groups: dict[tuple[UUID, int, str | None], list[V2Visit]] = {}
    for v in visits:
        if v.course_code is None:
            continue
        groups.setdefault((v.office_id, v.weekday, v.course_code), []).append(v)

    lunch_start_min = LUNCH_START.hour * 60 + LUNCH_START.minute  # 720
    lunch_end_min = LUNCH_END.hour * 60 + LUNCH_END.minute  # 780
    pm_block_end_min = PM_BLOCK_END.hour * 60 + PM_BLOCK_END.minute  # 1080

    # 物理不可能配置として course から外した visit の id(v) 集合.
    # 呼び出し側 (run_v2_pipeline) が after_visits からの除去に使う.
    unassigned_visit_ids: set[int] = set()

    for (office_id, weekday, course_code), gv in groups.items():
        if len(gv) < 2:
            continue
        sv = sorted(gv, key=lambda x: x.start_time)
        # 同住所連番強制 (ユーザー要望 最重要): 同住所ペアは配列上で必ず隣接させる.
        # 間に別住所が挟まると同住所メリット (移動 0 + バッファー 0) が消えるため、
        # earliest_start 再計算の前にリオーダーする.
        sv = _reorder_same_address_consecutive(sv, warnings=warnings)
        cumulative_travel_min = 0
        wd_jp = _weekday_jp(weekday)
        office_name = (office_name_by_id or {}).get(office_id) or str(office_id)
        for i in range(1, len(sv)):
            prev = sv[i - 1]
            cur = sv[i]
            # 同住所は移動 0 (バッファーも不要 — 同アパート内の連続訪問は次室移動が最小限).
            if _address_bucket(prev.lat, prev.lng) == _address_bucket(cur.lat, cur.lng):
                travel_min = 0
                buffer_min = 0
            else:
                travel_min = haversine_minutes(haversine_km(prev.lat, prev.lng, cur.lat, cur.lng))
                buffer_min = VISIT_BUFFER_MINUTES
            cumulative_travel_min += travel_min

            desired_start = cur.start_time
            earliest_start = _add_minutes(prev.end_time, travel_min + buffer_min)

            tt = cur.time_type
            actual_start: time
            cur_name = cur.patient_name or (cur.patient_code or "不明")
            prev_name = prev.patient_name or (prev.patient_code or "不明")

            if tt == "固定":
                # 固定は時刻を動かさない. 移動時間が不足する場合は不足量で分岐.
                #   * shortage < SHORTAGE_THRESHOLD_MIN (= 5 分未満): 微小不足は
                #     運用 (前 visit の早期終了) で吸収可能とみなし、warning だけ
                #     出して固定時刻に配置する.
                #   * shortage >= SHORTAGE_THRESHOLD_MIN: 物理的に配置不可と判定し
                #     ``course_code = None`` に書き換え、戻り値 ``set[int]`` に
                #     id(v) を追加して呼び出し側へ通知する. 呼び出し側はこの集合
                #     を使って ``after_visits`` から除去し ``unassigned_patients``
                #     (reason=fixed_time_conflict) へ流す.
                actual_start = desired_start
                if earliest_start > desired_start:
                    shortage = (earliest_start.hour * 60 + earliest_start.minute) - (
                        desired_start.hour * 60 + desired_start.minute
                    )
                    if shortage >= SHORTAGE_THRESHOLD_MIN:
                        # 物理不可能 → コースから外す + 未割当通知集合に追加.
                        cur.course_code = None
                        unassigned_visit_ids.add(id(cur))
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{prev_name} 様 ({_fmt_hhmm(prev.end_time)} 終了) → "
                                    f"{cur_name} 様 ({_fmt_hhmm(desired_start)} 固定開始) "
                                    f"への必要 {travel_min + buffer_min} 分 "
                                    f"(移動 {travel_min} 分 + バッファー {buffer_min} 分) "
                                    f"が {shortage} 分不足 — 物理的に配置不可のため "
                                    "未割当に移動 (固定時刻の見直し要)"
                                ),
                                weekday=weekday,
                                actionable=True,
                                patient_id=cur.patient_id,
                                patient_name=cur.patient_name,
                                current_time=_fmt_hhmm(desired_start),
                                suggested_time=_fmt_hhmm(earliest_start),
                                time_type=tt,
                                preferred_start=cur.preferred_start,
                                preferred_end=cur.preferred_end,
                                # P2: 固定時刻衝突 — fixed_time_conflict にマップ.
                                affected_patient_ids=[cur.patient_id],
                            )
                        )
                        # 物理不可能 visit は course 上でこれ以上後続に影響させない.
                        # 後続 pair (i+1) は cur をスキップして prev=prev のまま回したい
                        # が、ループ構造を大きく崩さないため cur.end_time も
                        # actual_start (= desired_start) ベースで残し、警告のみで進める.
                        # (後続が cur.end_time をベースに earliest を計算すると
                        # 削除済 visit の影響が残るが、cur 自身は後段で
                        # ``after_visits`` から除去されるため UI には出ない.)
                    else:
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{prev_name} 様 ({_fmt_hhmm(prev.end_time)} 終了) → "
                                    f"{cur_name} 様 ({_fmt_hhmm(desired_start)} 固定開始) "
                                    f"への必要 {travel_min + buffer_min} 分 "
                                    f"(移動 {travel_min} 分 + バッファー {buffer_min} 分) "
                                    f"が {shortage} 分不足 "
                                    f"(< {SHORTAGE_THRESHOLD_MIN} 分は許容: "
                                    "固定時刻のまま配置)"
                                ),
                                weekday=weekday,
                                actionable=True,
                                patient_id=cur.patient_id,
                                patient_name=cur.patient_name,
                                current_time=_fmt_hhmm(desired_start),
                                suggested_time=_fmt_hhmm(earliest_start),
                                time_type=tt,
                                preferred_start=cur.preferred_start,
                                preferred_end=cur.preferred_end,
                                # P2: 固定時刻衝突 — fixed_time_conflict にマップ.
                                affected_patient_ids=[cur.patient_id],
                            )
                        )
            elif tt == "時間帯":
                ps = _parse_hhmm(cur.preferred_start)
                pe = _parse_hhmm(cur.preferred_end)
                window_lower = ps if ps is not None else desired_start
                window_upper = pe if pe is not None else PM_BLOCK_END
                candidate = max(desired_start, earliest_start, window_lower)
                if candidate > window_upper:
                    # HIGH #2: 範囲超過: window_upper にクランプすると
                    # earliest_start > window_upper のため physically infeasible.
                    # earliest_start を採用 + 警告 (時間帯外で開始) し、
                    # 後続 visit に正しいタイムラインを伝播させる.
                    actual_start = earliest_start
                    overage_min = (earliest_start.hour * 60 + earliest_start.minute) - (
                        window_upper.hour * 60 + window_upper.minute
                    )
                    warnings.append(
                        V2Warning(
                            type="travel_time_shortage",
                            message=(
                                f"{office_name} {course_code} {wd_jp}: {cur_name} 様 の "
                                f"希望時間帯 ({cur.preferred_start or '-'}-{cur.preferred_end or '-'}) "
                                f"を移動時間で超過、{_fmt_hhmm(earliest_start)} 開始 "
                                f"(約 {overage_min} 分遅れ)"
                            ),
                            weekday=weekday,
                            actionable=True,
                            patient_id=cur.patient_id,
                            patient_name=cur.patient_name,
                            current_time=_fmt_hhmm(actual_start),
                            suggested_time=_fmt_hhmm(earliest_start),
                            time_type=tt,
                            preferred_start=cur.preferred_start,
                            preferred_end=cur.preferred_end,
                            affected_patient_ids=[cur.patient_id],
                        )
                    )
                else:
                    actual_start = candidate
            elif tt == "午前":
                # HIGH #1: 午前 (AM_BLOCK_END=12:00 未満) の制約内で earliest を取る.
                # 12:00 を超える場合は LUNCH_END (13:00) にバンプ (午後扱い) を試す.
                # それでも収まらない (18:00 超) なら earliest 維持 + warning.
                candidate = max(desired_start, earliest_start)
                if candidate >= AM_BLOCK_END:
                    bumped = LUNCH_END  # 13:00
                    bumped_end_min = bumped.hour * 60 + bumped.minute + cur.service_minutes
                    if bumped_end_min <= pm_block_end_min:
                        # 13:00 開始 + service が 18:00 内 → 午後にバンプ.
                        actual_start = bumped
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{cur_name} 様 (午前希望) が移動時間で "
                                    f"earliest {_fmt_hhmm(earliest_start)} で 12:00 超過、"
                                    "13:00 (午後) に繰り下げ"
                                ),
                                weekday=weekday,
                                actionable=True,
                                patient_id=cur.patient_id,
                                patient_name=cur.patient_name,
                                current_time=_fmt_hhmm(bumped),
                                suggested_time=_fmt_hhmm(bumped),
                                time_type=tt,
                                preferred_start=cur.preferred_start,
                                preferred_end=cur.preferred_end,
                                affected_patient_ids=[cur.patient_id],
                            )
                        )
                    else:
                        # 18:00 超えで配置不可 → earliest 維持 + 警告 (運用者要確認).
                        actual_start = candidate
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{cur_name} 様 (午前希望) が earliest "
                                    f"{_fmt_hhmm(earliest_start)} で 12:00 を超過、"
                                    "13:00 にバンプしても 18:00 を超えるため配置不可"
                                ),
                                weekday=weekday,
                                actionable=True,
                                patient_id=cur.patient_id,
                                patient_name=cur.patient_name,
                                current_time=_fmt_hhmm(actual_start),
                                time_type=tt,
                                preferred_start=cur.preferred_start,
                                preferred_end=cur.preferred_end,
                                affected_patient_ids=[cur.patient_id],
                            )
                        )
                else:
                    actual_start = candidate
            elif tt == "午後":
                # 午後 (>= 13:00) の制約内で earliest を取る. 13:00 未満なら 13:00 に揃える.
                # earliest + service が 18:00 超えなら earliest 維持 + actionable warning.
                pm_start = PM_BLOCK_START
                candidate = max(desired_start, earliest_start, pm_start)
                candidate_end_min = candidate.hour * 60 + candidate.minute + cur.service_minutes
                if candidate_end_min > pm_block_end_min:
                    # 18:00 超: 動かさず警告のみ. 後段で重複検出/UI 対応.
                    actual_start = candidate
                    warnings.append(
                        V2Warning(
                            type="travel_time_shortage",
                            message=(
                                f"{office_name} {course_code} {wd_jp}: "
                                f"{cur_name} 様 (午後希望) が earliest "
                                f"{_fmt_hhmm(earliest_start)} で "
                                f"{_fmt_hhmm(PM_BLOCK_END)} を超過 "
                                "(運用者要確認)"
                            ),
                            weekday=weekday,
                            actionable=True,
                            patient_id=cur.patient_id,
                            patient_name=cur.patient_name,
                            current_time=_fmt_hhmm(actual_start),
                            time_type=tt,
                            preferred_start=cur.preferred_start,
                            preferred_end=cur.preferred_end,
                            affected_patient_ids=[cur.patient_id],
                        )
                    )
                else:
                    actual_start = candidate
            else:
                # "終日" / None / 不明: 営業時間内なら earliest を採用.
                actual_start = max(desired_start, earliest_start)

            # CareFlow v2 拡張 (5 分刻み切り上げ): 非固定 visit の actual_start を
            # 5 分刻みに切り上げる (UI 上の時刻整列 + 実質バッファー 8-12 分).
            # 固定枠 (``time_type='固定'``) は希望時刻を強制するため対象外.
            # 切り上げにより AM/PM 境界 / 昼休憩 / 18:00 を超える可能性があるが、
            # AM→12:00→13:00 バンプは直後の lunch 再検証で吸収される. 午後 18:00
            # 超過は本ブロックで再判定して actionable warning を追加する.
            if tt != "固定":
                actual_start = _round_up_to_5min(actual_start)
                if tt == "午後":
                    rounded_end_min = (
                        actual_start.hour * 60 + actual_start.minute + cur.service_minutes
                    )
                    if rounded_end_min > pm_block_end_min:
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{cur_name} 様 (午後希望) が 5 分刻み切り上げで "
                                    f"{_fmt_hhmm(actual_start)} 開始となり "
                                    f"{_fmt_hhmm(PM_BLOCK_END)} を超過 "
                                    "(運用者要確認)"
                                ),
                                weekday=weekday,
                                actionable=True,
                                patient_id=cur.patient_id,
                                patient_name=cur.patient_name,
                                current_time=_fmt_hhmm(actual_start),
                                time_type=tt,
                                preferred_start=cur.preferred_start,
                                preferred_end=cur.preferred_end,
                                affected_patient_ids=[cur.patient_id],
                            )
                        )

            # CRITICAL #1: 昼休憩 (12:00-13:00) 再検証.
            # _filter_unavailable_and_lunch は既に実行済みのため、
            # 動的調整後に lunch break と重なるかを再チェックする.
            actual_start_min = actual_start.hour * 60 + actual_start.minute
            actual_end_min = actual_start_min + cur.service_minutes
            if actual_start_min < lunch_end_min and actual_end_min > lunch_start_min:
                if tt == "固定":
                    # 固定時刻は動かさない: 警告のみ.
                    warnings.append(
                        V2Warning(
                            type="travel_time_shortage",
                            message=(
                                f"{office_name} {course_code} {wd_jp}: "
                                f"{cur_name} 様 (固定 {_fmt_hhmm(actual_start)}) が "
                                "昼休憩 12:00-13:00 に重なる "
                                "(固定時刻のため動かせず — 運用者要確認)"
                            ),
                            weekday=weekday,
                            actionable=True,
                            patient_id=cur.patient_id,
                            patient_name=cur.patient_name,
                            current_time=_fmt_hhmm(actual_start),
                            time_type=tt,
                            preferred_start=cur.preferred_start,
                            preferred_end=cur.preferred_end,
                            affected_patient_ids=[cur.patient_id],
                        )
                    )
                else:
                    # 固定以外: 13:00 にバンプして再評価.
                    bumped_start_min = lunch_end_min  # 13:00
                    bumped_end_min_v = bumped_start_min + cur.service_minutes
                    can_bump = True
                    # time_type 別に bump 可否を判定.
                    if tt == "午前":
                        # 午前希望を 13:00 にバンプ — 18:00 内なら可.
                        can_bump = bumped_end_min_v <= pm_block_end_min
                    elif tt == "時間帯":
                        # 時間帯 window 内 (= window_upper 以下) なら可.
                        pe_v = _parse_hhmm(cur.preferred_end)
                        window_upper_v = pe_v if pe_v is not None else PM_BLOCK_END
                        window_upper_min = window_upper_v.hour * 60 + window_upper_v.minute
                        can_bump = bumped_start_min <= window_upper_min
                    elif tt == "午後":
                        # 午後 visit が 12-13 に被るのは earliest < 13:00 のとき.
                        # 13:00 バンプ + service が 18:00 内なら OK.
                        can_bump = bumped_end_min_v <= pm_block_end_min
                    else:
                        # 終日 / None: 18:00 内ならバンプ.
                        can_bump = bumped_end_min_v <= pm_block_end_min

                    if can_bump:
                        actual_start = LUNCH_END
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{cur_name} 様 が移動時間で昼休憩 12:00-13:00 に "
                                    "重なるため 13:00 に繰り下げ"
                                ),
                                weekday=weekday,
                                actionable=False,
                                patient_id=cur.patient_id,
                                patient_name=cur.patient_name,
                                current_time=_fmt_hhmm(actual_start),
                                suggested_time=_fmt_hhmm(actual_start),
                                time_type=tt,
                                preferred_start=cur.preferred_start,
                                preferred_end=cur.preferred_end,
                                affected_patient_ids=[cur.patient_id],
                            )
                        )
                    else:
                        # バンプ不可: 動かさず actionable warning.
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{cur_name} 様 が移動時間で昼休憩 12:00-13:00 に "
                                    f"重なる (time_type={tt or '不明'}, "
                                    "13:00 バンプも不可)"
                                ),
                                weekday=weekday,
                                actionable=True,
                                patient_id=cur.patient_id,
                                patient_name=cur.patient_name,
                                current_time=_fmt_hhmm(actual_start),
                                time_type=tt,
                                preferred_start=cur.preferred_start,
                                preferred_end=cur.preferred_end,
                                affected_patient_ids=[cur.patient_id],
                            )
                        )

            cur.start_time = actual_start
            cur.end_time = _add_minutes(actual_start, cur.service_minutes)

        # コース全体で連続移動が 30 分超なら長距離コース warning.
        if cumulative_travel_min > 30:
            # P2: コース内全 patient_id (長距離コースは全員に影響).
            ld_pids = list({v.patient_id for v in sv})
            warnings.append(
                V2Warning(
                    type="course_long_distance",
                    message=(
                        f"{office_name} {course_code} コース {wd_jp}: "
                        f"連続移動時間合計 {cumulative_travel_min} 分 (30 分超 — "
                        "長距離コースとして要注意)"
                    ),
                    weekday=weekday,
                    actionable=False,
                    affected_patient_ids=ld_pids,
                )
            )

    return unassigned_visit_ids


def _check_course_capacity_minutes(
    visits: list[V2Visit],
    *,
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
) -> None:
    """W41 v2 拡張 (コース容量 duration 化): コース総所要時間が ``COURSE_MAX_MINUTES``
    (= 480 分 = 8 時間, 昼休憩除く) を超えていれば warning を追加する.

    既存の人数制約 (``MAX_PATIENTS_PER_COURSE=6``) と併用する独立 check.
    人数 ≤ 6 でも duration が長すぎるコースを検出する.

    TODO(future): コース再分配 (visits を他コースに移送) は次回 iteration.
    現状は ``actionable=True`` で UI 通知し、運用者の手動介入を促す.
    """
    groups: dict[tuple[UUID, int, str | None], list[V2Visit]] = {}
    for v in visits:
        if v.course_code is None:
            continue
        groups.setdefault((v.office_id, v.weekday, v.course_code), []).append(v)

    for (office_id, weekday, course_code), gv in groups.items():
        total_min = calc_course_total_minutes(gv)
        if total_min > COURSE_MAX_MINUTES:
            office_name = (office_name_by_id or {}).get(office_id) or str(office_id)
            wd_jp = _weekday_jp(weekday)
            # P2: コース内全 patient_id (容量超過コースは全員に影響可能性).
            affected_pids = list({v.patient_id for v in gv})
            warnings.append(
                V2Warning(
                    type="course_capacity",
                    message=(
                        f"{office_name} {course_code} コース {wd_jp}: "
                        f"コース総所要時間 {total_min} 分 > 上限 {COURSE_MAX_MINUTES} 分 "
                        "(訪問時間 + 移動時間合計)"
                    ),
                    weekday=weekday,
                    # HIGH #3: 自動再分配は未実装 — 運用者の手動再分配を促すため
                    # actionable=True にする (UI で「コース変更」アクションを出す).
                    actionable=True,
                    affected_patient_ids=affected_pids,
                )
            )


def _check_two_staff_availability(
    visits: list[V2Visit],
    *,
    staff_count_by_weekday: dict[tuple[UUID, int], int],
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
) -> None:
    """W41 v2 拡張 (二人組訪問): ``requires_multiple_staff=True`` の visit に
    対し、同 (office_id, weekday) の対応可能スタッフが 2 名以上いるか check する.

    スタッフ枠が 2 名未満なら、当該 visit ごとに warning を追加する.
    実際の visit_staff_assignments への 2 行登録は ``apply_*`` (DB 書き込み)
    側の責務. 本サービスは提案段階の整合性チェックに留める.

    Scope (MEDIUM #2):
        現状の判定は (office_id, weekday) 粒度の first-pass heuristic.
        スタッフが午前/午後どちらに対応可能か等の時間帯までは未考慮.

        TODO(future): スタッフの shift 時間帯まで考慮した可用性 check に拡張
        (visit.start_time が staff_shift の勤務時間内かを 2 名分突き合わせ).
    """
    if not any(v.requires_multiple_staff for v in visits):
        return
    for v in visits:
        if not v.requires_multiple_staff:
            continue
        n_staff = staff_count_by_weekday.get((v.office_id, v.weekday), 0)
        if n_staff < 2:
            office_name = (office_name_by_id or {}).get(v.office_id) or str(v.office_id)
            wd_jp = _weekday_jp(v.weekday)
            name = v.patient_name or (v.patient_code or "不明")
            warnings.append(
                V2Warning(
                    # MEDIUM #1: 二人組訪問は専用 type に分離 (UI 分類のため).
                    type="two_staff_shortage",
                    message=(
                        f"{office_name} {wd_jp}: {name} 様 は二人組訪問必須だが "
                        f"対応可能スタッフ {n_staff} 名 (2 名必要)"
                    ),
                    weekday=v.weekday,
                    actionable=True,
                    patient_id=v.patient_id,
                    patient_name=v.patient_name,
                    # P2: 単一 patient warning でも affected_patient_ids を埋めて
                    # _identify_unassigned_patients が一貫した検索ロジックで照合できるようにする.
                    affected_patient_ids=[v.patient_id],
                )
            )


# ---------------------------------------------------------------------------
# Acceptance calendar (H5)
# ---------------------------------------------------------------------------


async def _load_unavailable_slots(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
) -> dict[tuple[UUID, int], set[time]]:
    """H5: acceptance_calendar から × 時刻を取得."""
    if not office_ids:
        return {}
    rows = await db.scalars(
        select(AcceptanceCalendar).where(
            AcceptanceCalendar.office_id.in_(office_ids),
            AcceptanceCalendar.status == "unavailable",
        )
    )
    out: dict[tuple[UUID, int], set[time]] = {}
    for row in rows.all():
        out.setdefault((row.office_id, row.weekday), set()).add(row.time_slot)
    return out


def _filter_unavailable_and_lunch(
    visits: list[V2Visit],
    *,
    unavailable_slots: dict[tuple[UUID, int], set[time]],
    warnings: list[V2Warning],
    skip_acceptance: bool = False,
) -> list[V2Visit]:
    """H5 + H10: 受入 × 時刻 + 昼休憩枠を除外.

    Args:
        skip_acceptance: True なら H5 (acceptance_calendar ×) フィルタをスキップ.
            Mode 2 (full_optimize) で使用. 受入カレンダー × は既存スケジュールの
            混雑度を表す動的データであり、既存固定枠ごと再配置する全面最適化では
            制約として意味を持たないため. 昼休憩 (H10) は常に enforce.
    """
    out: list[V2Visit] = []
    for v in visits:
        if not skip_acceptance:
            blocked = unavailable_slots.get((v.office_id, v.weekday), set())
            if v.start_time in blocked:
                code = v.patient_code or "-"
                name = v.patient_name or "-"
                warnings.append(
                    V2Warning(
                        type="acceptance_blocked",
                        message=(
                            f"{code} {name} 様: {_weekday_jp(v.weekday)} "
                            f"{_fmt_hhmm(v.start_time)} は受入カレンダーで「×」設定のため配置不可"
                        ),
                        weekday=v.weekday,
                        actionable=False,
                        patient_id=v.patient_id,
                        patient_name=v.patient_name,
                        current_time=_fmt_hhmm(v.start_time),
                        time_type=v.time_type,
                        preferred_start=v.preferred_start,
                        preferred_end=v.preferred_end,
                    )
                )
                continue
        if _is_in_lunch_break(v.start_time, v.end_time):
            code = v.patient_code or "-"
            name = v.patient_name or "-"
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"{code} {name} 様: {_weekday_jp(v.weekday)} "
                        f"{_fmt_hhmm(v.start_time)} は昼休憩 (12:00-13:00) に重なるため配置不可"
                    ),
                    weekday=v.weekday,
                    actionable=False,
                    patient_id=v.patient_id,
                    patient_name=v.patient_name,
                    current_time=_fmt_hhmm(v.start_time),
                )
            )
            continue
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# KPI calculation
# ---------------------------------------------------------------------------


def calc_total_distance(visits: list[V2Visit]) -> float:
    """同 (office × weekday × course_code) 内で start_time 順に隣接距離合算."""
    if not visits:
        return 0.0
    groups: dict[tuple[UUID, int, str | None], list[V2Visit]] = {}
    for v in visits:
        groups.setdefault((v.office_id, v.weekday, v.course_code), []).append(v)
    total = 0.0
    for gv in groups.values():
        sv = sorted(gv, key=lambda x: x.start_time)
        for i in range(1, len(sv)):
            total += haversine_km(sv[i - 1].lat, sv[i - 1].lng, sv[i].lat, sv[i].lng)
    return total


def calc_h_violations(visits: list[V2Visit]) -> dict[str, int]:
    """H1-H10 の違反件数を集計."""
    # H1: 同 patient_id の start_time が複数
    by_patient: dict[UUID, set[time]] = {}
    for v in visits:
        by_patient.setdefault(v.patient_id, set()).add(v.start_time)
    h1 = sum(1 for ts in by_patient.values() if len(ts) > 1)

    # H2: 同住所 3 人以上
    groups2: dict[tuple[int, time, tuple[float, float]], int] = {}
    for v in visits:
        key = (v.weekday, v.start_time, _address_bucket(v.lat, v.lng))
        groups2[key] = groups2.get(key, 0) + 1
    h2 = sum(1 for c in groups2.values() if c > 2)

    # H9: コース容量 6 名超過
    by_course: dict[tuple[UUID, int, str | None], int] = {}
    for v in visits:
        by_course[(v.office_id, v.weekday, v.course_code)] = (
            by_course.get((v.office_id, v.weekday, v.course_code), 0) + 1
        )
    h9 = sum(1 for c in by_course.values() if c > MAX_PATIENTS_PER_COURSE)

    # H10: 昼休憩枠と重複
    h10 = sum(1 for v in visits if _is_in_lunch_break(v.start_time, v.end_time))

    # H4: 全訪問同スタッフ禁止 — 週 2 回以上訪問なのに同 staff のみのケース
    by_patient_assigned: dict[UUID, list[UUID]] = {}
    for v in visits:
        if v.assigned_staff_id is not None:
            by_patient_assigned.setdefault(v.patient_id, []).append(v.assigned_staff_id)
    h4 = 0
    for assigned in by_patient_assigned.values():
        if len(assigned) >= 2 and len(set(assigned)) == 1:
            h4 += 1

    return {
        "H1": h1,
        "H2": h2,
        "H3": 0,
        "H4": h4,
        "H5": 0,
        "H6": 0,
        "H7": 0,
        "H8": 0,
        "H9": h9,
        "H10": h10,
    }


# ---------------------------------------------------------------------------
# Before snapshot — existing patient_fixed_visits 由来
# ---------------------------------------------------------------------------


async def _load_before_visits_from_pfv(
    db: AsyncSession,
    *,
    patients_by_id: dict[UUID, Patient],
    pending_overlay: dict[tuple[UUID, int], PendingEditOverlay] | None = None,
    warnings: list[V2Warning] | None = None,
) -> list[V2Visit]:
    """Before スナップショット: 既存 patient_fixed_visits (mode='normal') から構築.

    ``pending_overlay`` が渡された場合は、PFV 値を Python オブジェクトレベルで上書きする
    (DB / SQLAlchemy セッションには触らない). マスターは絶対に変更しない.
    overlay に該当するキーがあるのに PFV が見つからない場合は warning に記録.
    """
    if not patients_by_id:
        return []
    pfv_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id.in_(list(patients_by_id.keys())),
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.slot_index == 0,
            )
        )
    ).all()

    # course_template_id → CourseTemplate.label の map を事前構築 (N+1 回避)
    ct_ids = {pfv.course_template_id for pfv in pfv_rows if pfv.course_template_id is not None}
    ct_label_by_id: dict[UUID, str] = {}
    if ct_ids:
        ct_rows = await db.scalars(
            select(CourseTemplate).where(
                CourseTemplate.id.in_(ct_ids),
                CourseTemplate.deleted_at.is_(None),
            )
        )
        for ct in ct_rows.all():
            ct_label_by_id[ct.id] = ct.label

    pending_overlay = pending_overlay or {}
    pfv_keys_seen: set[tuple[UUID, int]] = set()

    out: list[V2Visit] = []
    for pfv in pfv_rows:
        patient = patients_by_id.get(pfv.patient_id)
        if patient is None or patient.lat is None or patient.lng is None:
            continue
        if patient.primary_office_id is None:
            continue
        overlay_key = (pfv.patient_id, pfv.weekday)
        pfv_keys_seen.add(overlay_key)
        overlay = pending_overlay.get(overlay_key)
        # overlay の new_start_time / duration_min を採用 (PFV モデルは書き換えない)
        if overlay is not None:
            start_time_v = overlay.new_start
            duration_v = _compute_overlay_duration(overlay, existing_duration=pfv.duration_min)
        else:
            start_time_v = pfv.start_time
            duration_v = pfv.duration_min
        end_t = _add_minutes(start_time_v, duration_v)
        am_pm = "am" if start_time_v.hour < NOON_HOUR else "pm"
        course_code = ct_label_by_id.get(pfv.course_template_id) if pfv.course_template_id else None
        addr = patient.address
        # time_type / preferred_start / preferred_end も overlay を優先する.
        if overlay is not None:
            tt = overlay.new_time_type or _extract_time_type_for_weekday(patient, pfv.weekday)
            ps_str = overlay.new_start_str
            pe_str = (
                overlay.new_end_str
                or _extract_preferred_window_for_weekday(patient, pfv.weekday)[1]
            )
        else:
            tt = _extract_time_type_for_weekday(patient, pfv.weekday)
            ps_str, pe_str = _extract_preferred_window_for_weekday(patient, pfv.weekday)
        out.append(
            V2Visit(
                patient_id=patient.id,
                patient_name=patient.name,
                patient_code=patient.code,
                weekday=pfv.weekday,
                start_time=start_time_v,
                end_time=end_t,
                service_minutes=duration_v,
                lat=float(patient.lat),
                lng=float(patient.lng),
                office_id=patient.primary_office_id,
                am_pm=am_pm,  # type: ignore[arg-type]
                course_code=course_code,  # PFV.course_template_id 由来
                source_kind="fixed",
                address=addr,
                area_label=_extract_area_label(addr),
                time_type=tt,
                sex_restriction=patient.sex_restriction,
                preferred_start=ps_str,
                preferred_end=pe_str,
                # W41 v2 拡張 (二人組訪問): patient.requires_multiple_staff を流す.
                requires_multiple_staff=bool(
                    getattr(patient, "requires_multiple_staff", False) or False
                ),
            )
        )

    # overlay に該当するが PFV が見つからない (= 新規患者 or 別曜日) は warning.
    if warnings is not None:
        for key in pending_overlay.keys():
            if key not in pfv_keys_seen:
                patient = patients_by_id.get(key[0])
                pname = patient.name if patient is not None else None
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"今週限定変更: (patient_id={key[0]}, weekday={key[1]}) に対応する "
                            f"固定枠が存在しないためオーバーレイをスキップしました"
                        ),
                        actionable=False,
                        patient_id=key[0],
                        patient_name=pname,
                        weekday=key[1],
                    )
                )

    return out


# ---------------------------------------------------------------------------
# Unassigned patients identification (W41 v2 Mode 2 UI 拡張)
# ---------------------------------------------------------------------------


def _classify_warning_reason(
    warning: V2Warning,
) -> tuple[UnassignedReason, UnassignedStage] | None:
    """P2: 1 件の warning から (reason, stage) を分類する.

    マネージャー不足 / 容量超過 / コース超過 / 昼休憩 / 受入カレンダー / 固定時刻衝突 に
    対応. 一致しない warning は None を返す (呼び出し側で他 warning を試す).
    """
    msg = warning.message
    wtype = warning.type
    # マネージャー不足 (M course 不足) 判定: course_capacity type で
    # "manager 不足" を含むメッセージ. Stage 5 で emit される.
    if wtype == "course_capacity" and "manager 不足" in msg:
        return ("manager_short", "stage5_course")
    # コース容量 (480 分 / 6 名超過 / マネージャー補充候補).
    if wtype == "course_capacity":
        return ("course_capacity", "stage4_capacity")
    # コース数超過 (Stage 5 で staff_count を超えるコース).
    if wtype == "course_count":
        return ("course_overflow", "stage5_course")
    # 受入カレンダー × (acceptance_blocked) — H5.
    if wtype == "acceptance_blocked":
        return ("acceptance_calendar", "general")
    # 固定時刻衝突 / 時間帯外 / 昼休憩重複 etc.
    if wtype == "travel_time_shortage":
        if "昼休憩" in msg:
            return ("lunch_break", "general")
        return ("fixed_time_conflict", "general")
    # silent drop fix: diff_add で既存固定枠 / pool 内重複によりスキップされた visit.
    # _filter_conflicting_pool_visits / _filter_pool_internal_conflicts が emit する.
    if wtype == "diff_add_conflict":
        return ("fixed_time_conflict", "general")
    # 一般 warning の中で「昼休憩」"H10" 含む → lunch_break.
    if wtype == "general" and ("昼休憩" in msg or "H10" in msg):
        return ("lunch_break", "general")
    # H2 同住所 3 名以上で別 set に動かしたが配置先なし.
    if wtype == "general" and "同住所" in msg:
        return ("same_address_split", "stage3_set")
    return None


def _identify_unassigned_patients(
    pool_patients: list[Patient],
    after_visits: list[V2Visit],
    warnings: list[V2Warning],
) -> list[dict[str, Any]]:
    """Mode 2 (full_optimize) で after_visits に出てこなかった患者と理由を抽出する.

    P2: text 含み判定を撤去し、warning.affected_patient_ids での patient_id 照合に
    切り替えた. fallback で patient.code を message に含む warning も探すが、
    最終 fallback は ``reason="unknown"`` で固定 (旧「原因不明 (...のいずれか)」
    のような曖昧文言は撤去).

    Returns:
        ``[{"patient_id": UUID, "patient_name": str, "patient_code": str | None,
        "reason": UnassignedReason, "reason_detail": str | None,
        "dropped_at_stage": UnassignedStage | None}, ...]``
    """
    after_pids = {v.patient_id for v in after_visits}
    out: list[dict[str, Any]] = []
    for p in pool_patients:
        if p.id in after_pids:
            continue
        reason: UnassignedReason
        stage: UnassignedStage | None
        reason_detail: str | None = None
        # Stage 前段 (build_visits_for_pool で skip される) の判定を優先する.
        # build_visits_for_pool は lat/lng/primary_office_id のいずれかが None なら skip.
        if p.lat is None or p.lng is None:
            reason = "no_coordinates"
            stage = "general"
            reason_detail = "住所のジオコーディングが未完了 (lat/lng が None)"
        elif p.primary_office_id is None:
            reason = "no_primary_office"
            stage = "general"
            reason_detail = "primary_office_id が None"
        else:
            reason = "unknown"
            stage = None
            # P2: warning.affected_patient_ids での照合を最優先.
            matched_by_id = False
            for w in warnings:
                if p.id in (w.affected_patient_ids or []):
                    classified = _classify_warning_reason(w)
                    if classified is not None:
                        reason, stage = classified
                        reason_detail = w.message[:200] if w.message else None
                        matched_by_id = True
                        break
            # 後方互換 fallback: affected_patient_ids が未埋めの古い warning や
            # 単一 patient warning (patient_id フィールドだけ) を探す.
            matched_by_patient = False
            if not matched_by_id:
                for w in warnings:
                    if w.patient_id == p.id:
                        classified = _classify_warning_reason(w)
                        if classified is not None:
                            reason, stage = classified
                            reason_detail = w.message[:200] if w.message else None
                            matched_by_patient = True
                            break
            # silent drop fix (#2): どの warning にも一致せず reason=unknown 確定する
            # ところで weekly_pattern 未設定 (PFV のみ等) を明示する. build_visits_for_pool
            # は weekly_pattern が dict でない患者の visit を生成しないため、
            # 「unknown」より具体的な理由を返せる.
            if (
                not matched_by_id
                and not matched_by_patient
                and not isinstance(p.weekly_pattern, dict)
            ):
                reason = "no_weekly_pattern"
                stage = "general"
                reason_detail = "weekly_pattern が未設定 (PFV のみ存在の可能性)"
        out.append(
            {
                "patient_id": p.id,
                "patient_name": p.name,
                "patient_code": p.code,
                "reason": reason,
                "reason_detail": reason_detail,
                "dropped_at_stage": stage,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Conflict avoidance — diff_add で既存 visit と時間重複する pool visit を除外
# ---------------------------------------------------------------------------


def _filter_conflicting_pool_visits(
    existing: list[V2Visit],
    pool: list[V2Visit],
    warnings: list[V2Warning],
) -> list[V2Visit]:
    """diff_add: 既存 visit と時間重複する pool visit を除外し warning を出す.

    既存 visit (= filtered_before, 主に PFV 由来の固定枠) と新規 pool visit
    が同 ``(patient_id, weekday)`` で時間帯重複する場合、その pool visit を
    取り除いて warning に記録する.

    重複判定:
        ``start_time < other.end_time AND end_time > other.start_time``
        (= 半開区間 [start, end) で 1 分でも被ったら重複)
        ``end_time == other.start_time`` (touching) は重複扱いしない.

    Scope (HIGH #2 クロスレビュー指摘):
        判定は ``(patient_id, weekday)`` 粒度. 現状の ``run_v2_pipeline`` は
        ``_load_patients_with_fixed`` が **patient_id 粒度** (PFV を 1 行でも
        持つ患者は全曜日 pool 外) のため、本 helper が実 pipeline で発火する
        ケースは限定的. ただし以下を予防的にカバーする:
          1. 将来 ``pool_patients`` の判定を patient+weekday 粒度に拡張した場合
          2. ``pending_edits`` overlay 等で同 patient_id × 同 weekday の visit が
             before / pool 両方に出現する非典型シナリオ

        異なる患者間の衝突 (= スタッフ二重予約) は ``_apply_travel_time_to_courses``
        とコース容量 check が間接的にカバーするため、本 helper の対象外.
        pool 内の同 (patient_id, weekday) 重複は ``_filter_pool_internal_conflicts``
        で別途検出する.

    Returns:
        ``pool`` のうち衝突しなかった V2Visit のみのリスト.
    """
    if not existing or not pool:
        return list(pool)

    existing_by_pid_wd: dict[tuple[UUID, int], list[V2Visit]] = defaultdict(list)
    for v in existing:
        existing_by_pid_wd[(v.patient_id, v.weekday)].append(v)

    kept: list[V2Visit] = []
    for pv in pool:
        conflicts = existing_by_pid_wd.get((pv.patient_id, pv.weekday), [])
        conflict_ex: V2Visit | None = None
        for ex in conflicts:
            if pv.start_time < ex.end_time and pv.end_time > ex.start_time:
                conflict_ex = ex
                break
        if conflict_ex is None:
            kept.append(pv)
            continue

        name = pv.patient_name or (pv.patient_code or "不明")
        warnings.append(
            V2Warning(
                type="diff_add_conflict",
                message=(
                    f"{name} 様: {_weekday_jp(pv.weekday)} "
                    f"{_fmt_hhmm(pv.start_time)}-{_fmt_hhmm(pv.end_time)} は既存訪問 "
                    f"({_fmt_hhmm(conflict_ex.start_time)}-{_fmt_hhmm(conflict_ex.end_time)}) "
                    "と重複のためスキップ"
                ),
                weekday=pv.weekday,
                actionable=True,
                patient_id=pv.patient_id,
                patient_name=pv.patient_name,
                # silent drop fix: _identify_unassigned_patients が patient_id 照合で
                # reason 分類できるよう構造化照合用フィールドを埋める.
                affected_patient_ids=[pv.patient_id],
            )
        )
    return kept


def _filter_pool_internal_conflicts(
    pool: list[V2Visit],
    warnings: list[V2Warning],
) -> list[V2Visit]:
    """pool 内で同 ``(patient_id, weekday)`` で時間重複する visit を間引く.

    HIGH #2 (Codex クロスレビュー) + Opus MEDIUM #1:
        ``patient.weekly_pattern.entries`` に同曜日 2 件以上の entry がある場合、
        ``build_visits_for_pool`` がその数だけ pool visit を生成する.
        ``_filter_conflicting_pool_visits`` は existing × pool の重複しか見ないため、
        pool 同士の重複は別途検出する必要がある.

    重複判定:
        ``_filter_conflicting_pool_visits`` と同じ半開区間 [start, end).
        ``end_time == other.start_time`` (touching) は重複扱いしない.

    Strategy:
        同 ``(patient_id, weekday)`` グループ内で ``start_time`` 昇順に並べ、
        最初の visit を keep / 以降は前の keep 済 visit と重複する場合のみ除外.
        (= 重複した 2 件のうち先頭時刻のものを優先採用)

    Returns:
        ``pool`` のうち衝突しなかった V2Visit のみのリスト.
    """
    if not pool:
        return list(pool)

    grouped: dict[tuple[UUID, int], list[V2Visit]] = defaultdict(list)
    for v in pool:
        grouped[(v.patient_id, v.weekday)].append(v)

    dropped_ids: set[int] = set()  # id(v) で同一性判定
    for group in grouped.values():
        if len(group) < 2:
            continue
        sorted_g = sorted(group, key=lambda v: v.start_time)
        kept_in_group: list[V2Visit] = [sorted_g[0]]
        for pv in sorted_g[1:]:
            conflict_with: V2Visit | None = None
            for kv in kept_in_group:
                if pv.start_time < kv.end_time and pv.end_time > kv.start_time:
                    conflict_with = kv
                    break
            if conflict_with is None:
                kept_in_group.append(pv)
                continue

            dropped_ids.add(id(pv))
            name = pv.patient_name or (pv.patient_code or "不明")
            warnings.append(
                V2Warning(
                    type="diff_add_conflict",
                    message=(
                        f"{name} 様: {_weekday_jp(pv.weekday)} "
                        f"{_fmt_hhmm(pv.start_time)}-{_fmt_hhmm(pv.end_time)} は同患者の別提案 "
                        f"({_fmt_hhmm(conflict_with.start_time)}-{_fmt_hhmm(conflict_with.end_time)}) "
                        "と重複のためスキップ"
                    ),
                    weekday=pv.weekday,
                    actionable=True,
                    patient_id=pv.patient_id,
                    patient_name=pv.patient_name,
                    # silent drop fix: _identify_unassigned_patients が patient_id
                    # 照合で reason 分類できるよう構造化照合用フィールドを埋める.
                    affected_patient_ids=[pv.patient_id],
                )
            )

    return [v for v in pool if id(v) not in dropped_ids]


# ---------------------------------------------------------------------------
# Main pipeline — runs all 5 stages
# ---------------------------------------------------------------------------


async def run_v2_pipeline(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
    mode: Literal["diff_add", "full_optimize"],
    pending_edits: list[Any] | None = None,
) -> dict[str, Any]:
    """5 段階を順に実行する.

    Args:
        pending_edits: ``list[PendingFixedTimeEdit | dict]``. 与えられた場合、
            PFV / weekly_pattern を読み込み時のみ一時オーバーレイする (DB は変更しない).
            (patient_id, weekday) が重複する場合は **最後のもの** を採用.

    Returns:
        {
            "proposal_batch_id": UUID,
            "before_visits": [V2Visit, ...],   # 既存 PFV 由来 (overlay 適用済)
            "after_visits":  [V2Visit, ...],   # 提案結果 (course_code 確定済)
            "pool_visits":   [V2Visit, ...],   # 機能 A のときのみ非空
            "warnings": [...],
            "staff_count_by_weekday": {...},
            "unassigned_patients": [...]  # Mode 2 のみ非空; UI 表示用.
        }
    """
    if iso_year < 2000 or iso_year > 2100:
        raise ValueError(f"iso_year out of range: {iso_year}")
    if iso_week < 1 or iso_week > 53:
        raise ValueError(f"iso_week out of range: {iso_week}")
    if mode not in ("diff_add", "full_optimize"):
        raise ValueError(f"unsupported mode: {mode!r}")

    warnings: list[V2Warning] = []
    proposal_batch_id = uuid.uuid4()

    # 拠点未指定 = 全拠点
    if not office_ids:
        rows = await db.scalars(select(Office.id).where(Office.deleted_at.is_(None)))
        office_ids = list(rows.all())
        if not office_ids:
            return {
                "proposal_batch_id": proposal_batch_id,
                "before_visits": [],
                "after_visits": [],
                "pool_visits": [],
                "warnings": [
                    V2Warning(
                        type="general",
                        message="対象拠点が登録されていません",
                        actionable=False,
                    )
                ],
                "staff_count_by_weekday": {},
                "unassigned_patients": [],
            }

    # W41 v2 (警告日本語化): 警告メッセージで office.name を表示するための lookup.
    office_rows = await db.scalars(
        select(Office).where(Office.id.in_(office_ids), Office.deleted_at.is_(None))
    )
    office_name_by_id: dict[UUID, str] = {o.id: o.name for o in office_rows.all()}

    # Stage 1: プール作成
    patients_by_id = await _load_active_patients(db, office_ids=office_ids)
    patients_with_fixed = await _load_patients_with_fixed(
        db, patient_ids=list(patients_by_id.keys())
    )

    # W41 v2.8 hotfix#3 (孤児 patient 救済の完全版):
    # 旧仕様では diff_add 時 pool_patients = 「PFV 無し」のみ → P060 のように
    # PFV あるが weekly_pattern=null + 今週 visit ない patient は完全孤児に.
    # hotfix#2 で pool_patients に「PFV あるが今週 visit 無し」を含めたが、
    # build_visits_for_pool は weekly_pattern ベースなので weekly_pattern=null
    # では visit が 0 件 → pool_visits に出ない → 候補に出ない問題が残った.
    # 本 hotfix では「PFV ある孤児 patient」は **PFV ベースで visit 展開** する.
    from datetime import date as _date_cls

    orphan_fixed_by_patient: dict[UUID, list[PatientFixedVisit]] = {}
    if mode == "diff_add":
        try:
            week_monday = _date_cls.fromisocalendar(iso_year, iso_week, 1)
            week_sunday = _date_cls.fromisocalendar(iso_year, iso_week, 7)
        except ValueError:
            week_monday = None
            week_sunday = None

        patients_with_week_visit: set[UUID] = set()
        if week_monday is not None and week_sunday is not None:
            visit_rows = await db.scalars(
                select(Visit.patient_id)
                .where(
                    Visit.patient_id.in_(list(patients_by_id.keys())),
                    Visit.visit_date.between(week_monday, week_sunday),
                    Visit.deleted_at.is_(None),
                )
                .distinct()
            )
            patients_with_week_visit = {pid for pid in visit_rows.all() if pid is not None}

        # 通常 pool (PFV 無し): weekly_pattern ベース展開
        pool_patients_no_fixed = [
            p for p in patients_by_id.values() if p.id not in patients_with_fixed
        ]
        # 孤児 pool (PFV あり + 今週 visit 無し): PFV ベース展開
        pool_patients_orphan_fixed = [
            p
            for p in patients_by_id.values()
            if p.id in patients_with_fixed and p.id not in patients_with_week_visit
        ]
        pool_patients = pool_patients_no_fixed + pool_patients_orphan_fixed

        # 孤児 patient の PFV を取得 (PFV ベース展開用)
        # PatientFixedVisit は soft-delete を持たない (固定枠は物理削除)
        if pool_patients_orphan_fixed:
            orphan_pfv_rows = await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id.in_([p.id for p in pool_patients_orphan_fixed]),
                    PatientFixedVisit.mode == "normal",
                )
            )
            for pfv in orphan_pfv_rows.all():
                orphan_fixed_by_patient.setdefault(pfv.patient_id, []).append(pfv)
    else:
        # full_optimize: 全 active 患者.
        # silent drop fix (#1, 最重要・根治): orphan PFV を持つ患者 (= weekly_pattern が
        # dict でない + PFV あり) は ``build_visits_for_pool`` が weekly_pattern ベース
        # では visit を 0 件しか生成しない. 全面最適化でもこの種の患者を取りこぼさない
        # よう、diff_add と同様 PFV ベース展開する経路を作る.
        pool_patients = list(patients_by_id.values())
        pool_patients_no_fixed = [
            p
            for p in pool_patients
            if not (p.id in patients_with_fixed and not isinstance(p.weekly_pattern, dict))
        ]
        pool_patients_orphan_fixed = [
            p
            for p in pool_patients
            if p.id in patients_with_fixed and not isinstance(p.weekly_pattern, dict)
        ]
        # 孤児 patient の PFV を取得 (PFV ベース展開用)
        # PatientFixedVisit は soft-delete を持たない (固定枠は物理削除)
        if pool_patients_orphan_fixed:
            orphan_pfv_rows = await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id.in_([p.id for p in pool_patients_orphan_fixed]),
                    PatientFixedVisit.mode == "normal",
                )
            )
            for pfv in orphan_pfv_rows.all():
                orphan_fixed_by_patient.setdefault(pfv.patient_id, []).append(pfv)

    # W41 v2 拡張 (今週限定オーバーレイ): pending_edits を (patient_id, weekday) → Overlay の
    # マップに変換. PFV / weekly_pattern の読み込み時に Python オブジェクトレベルで上書きする.
    # DB / SQLAlchemy セッションには触らない.
    pending_overlay = _build_pending_edit_overlay(pending_edits, warnings=warnings)

    # Before スナップショット
    before_visits = await _load_before_visits_from_pfv(
        db,
        patients_by_id=patients_by_id,
        pending_overlay=pending_overlay,
        warnings=warnings,
    )

    # Stage 1+2 中間: pool_patients を V2Visit に展開
    # W41 v2 (警告日本語化): 緯度経度 / 拠点 未設定の患者を明示的に warning に出す.
    for _p in pool_patients:
        if _p.lat is None or _p.lng is None:
            warnings.append(
                V2Warning(
                    type="general",
                    message=f"{_p.name} 様: 緯度経度が未登録のためスケジュール対象外",
                    actionable=False,
                    patient_id=_p.id,
                    patient_name=_p.name,
                )
            )
        elif _p.primary_office_id is None:
            warnings.append(
                V2Warning(
                    type="general",
                    message=f"{_p.name} 様: 拠点が未設定のためスケジュール対象外",
                    actionable=False,
                    patient_id=_p.id,
                    patient_name=_p.name,
                )
            )
    # silent drop fix (#1): 通常 pool (weekly_pattern ベース) + 孤児 pool (PFV ベース) を統合.
    # diff_add / full_optimize の両モードで orphan PFV 患者を救済する.
    # ``pool_patients_orphan_fixed`` の判定基準が mode によって異なる:
    #   - diff_add     : PFV あり + 今週 visit 無し (= 孤児)
    #   - full_optimize: PFV あり + weekly_pattern が dict でない (= 完全孤児)
    pool_visits = build_visits_for_pool(pool_patients_no_fixed, pending_overlay=pending_overlay)
    if pool_patients_orphan_fixed and orphan_fixed_by_patient:
        # CareFlow #102 Fix A: orphan PFV の course_template_id -> course label
        # map を事前構築 (N+1 回避). build_visits_for_pool が PFV.course_template_id
        # から V2Visit.course_code を埋められるようにする.
        orphan_template_ids: set[UUID] = {
            pfv.course_template_id
            for _pfvs in orphan_fixed_by_patient.values()
            for pfv in _pfvs
            if pfv.course_template_id is not None
        }
        orphan_course_code_by_template_id: dict[UUID, str] = {}
        if orphan_template_ids:
            ct_rows = await db.scalars(
                select(CourseTemplate).where(
                    CourseTemplate.id.in_(orphan_template_ids),
                    CourseTemplate.deleted_at.is_(None),
                )
            )
            for ct in ct_rows.all():
                orphan_course_code_by_template_id[ct.id] = ct.label
        pool_visits_orphan = build_visits_for_pool(
            pool_patients_orphan_fixed,
            fixed_by_patient=orphan_fixed_by_patient,
            use_fixed_as_source=True,
            pending_overlay=pending_overlay,
            course_code_by_template_id=orphan_course_code_by_template_id,
        )
        pool_visits = pool_visits + pool_visits_orphan

    # H5 + H10: 受入カレンダー × + 昼休憩を除外
    # Mode 2 (full_optimize) は H5 をスキップ — 受入カレンダー × は既存スケジュール
    # 枠の混雑度を表すため、全面再配置時には制約として意味を持たない. H10 (昼休憩)
    # は両モードとも常に enforce.
    unavailable = await _load_unavailable_slots(db, office_ids=office_ids)
    skip_acceptance = mode == "full_optimize"
    pool_visits = _filter_unavailable_and_lunch(
        pool_visits,
        unavailable_slots=unavailable,
        warnings=warnings,
        skip_acceptance=skip_acceptance,
    )

    # 機能 A: pool_visits 単独で配置 (既存 PFV はそのまま)
    # 機能 B: pool_visits = 全 active patient ベースで再配置
    #
    # C1: V2Visit を共有すると stage 3-5 の course_code 等の mutation が
    #     before_visits にも波及して Before/After 比較が壊れる. 必ず after 用に
    #     新規オブジェクト (replace=shallow copy) を作る.
    # H3: H5/H10 フィルタは after に流す before 由来 visit にも適用する.
    #     before_visits 自身 (KPI 用の参照) はフィルタしない.
    if mode == "diff_add":
        # W41 v2.8 hotfix#4: orphan patient (PFV あるが今週 visit ない) は
        # before_visits にも PFV 由来として既に含まれている. それを filtered_before
        # にそのまま流すと _filter_conflicting_pool_visits で「自分自身との衝突」
        # 判定が出て pool から除外される. 結果として候補に出ない.
        # 解決: orphan patient の visit は filtered_before から除外し、
        # pool 経由でのみ after に流入させる.
        orphan_patient_ids: set[UUID] = {p.id for p in pool_patients_orphan_fixed}
        before_copies = [
            replace(v) for v in before_visits if v.patient_id not in orphan_patient_ids
        ]
        filtered_before = _filter_unavailable_and_lunch(
            before_copies, unavailable_slots=unavailable, warnings=warnings
        )
        # 既存 visit (filtered_before) と時間重複する pool visit を除外.
        # 同 (patient_id, weekday) で時間帯が被るものを取り除き、warning を出す.
        pool_visits = _filter_conflicting_pool_visits(filtered_before, pool_visits, warnings)
        # pool 内の同 (patient_id, weekday) 重複も検出
        # (weekly_pattern.entries が同曜日 2 件以上ある稀ケース対策).
        pool_visits = _filter_pool_internal_conflicts(pool_visits, warnings)
        after_visits = filtered_before + list(pool_visits)
    else:
        after_visits = list(pool_visits)

    # W41 v2 (同住所同時刻集約 ソフト制約): _enforce_h2_same_address の前に呼ぶ.
    # 同住所 patient が異なる start_time に分散している場合、最多 start_time に
    # 寄せる. 時間制約 (固定/午前/午後/時間帯) を尊重し、動かせない場合は warning.
    _consolidate_same_address_time(after_visits, warnings)

    # Stage 2: バケット
    buckets = split_into_buckets(after_visits)

    # Stage 3: 距離グリーディクラスタリング (バケットごと)
    sets_by_bucket: dict[tuple[UUID, int, Literal["am", "pm"]], list[V2Set]] = {}
    for key, bucket in buckets.items():
        # silent drop fix: 重複 visit skip を warning に出す.
        sets = cluster_by_distance_greedy(bucket.visits, warnings=warnings)
        _enforce_h2_same_address(sets, warnings)
        # W41 v2 (H2 強化): 同住所 3 名以上を別 set に強制分散
        _enforce_h2_split_overflow(sets, warnings)
        sets_by_bucket[key] = sets

    # Stage 4: コース数制約
    staff_count_by_weekday = await count_active_staff_per_weekday(
        db, office_ids=office_ids, iso_year=iso_year, iso_week=iso_week
    )
    # CareFlow Wave Next 2 cross-review [H2]: staff_shifts 未投入で staff_count=0
    # になる data-health 警告 (active staff いるのに全曜日で shift 未登録).
    await _emit_staff_shifts_data_health_warning(
        db,
        office_ids=office_ids,
        warnings=warnings,
        office_name_by_id=office_name_by_id,
    )
    enforce_course_count_constraint(
        sets_by_bucket,
        staff_count_by_weekday=staff_count_by_weekday,
        warnings=warnings,
        office_name_by_id=office_name_by_id,
    )

    # CareFlow Wave Next 3: 当該曜日の出勤マネージャー数を取得.
    # M / M2 / M3 ... overflow code の発行数をマネージャー数で動的に絞り、
    # 超過セットの patient は ``unassigned_patients`` に流す
    # (「マネージャー 1 名に対して 1 コース」ルールの実装).
    manager_count_by_weekday = await count_active_managers_per_weekday(
        db, office_ids=office_ids, iso_year=iso_year, iso_week=iso_week
    )

    # Stage 5: 午前 ↔ 午後 組み合わせ + course_code 割当
    # (office_id, weekday) ごとに am と pm の sets を組み合わせる
    by_office_weekday: dict[tuple[UUID, int], dict[str, list[V2Set]]] = {}
    for (office_id, weekday, am_pm), sets in sets_by_bucket.items():
        by_office_weekday.setdefault((office_id, weekday), {"am": [], "pm": []})[am_pm] = sets

    # CareFlow Wave Next 3: マネージャー不足で course code を発行できなかった
    # visit を集める. これらは ``after_visits`` から取り除いて
    # ``_identify_unassigned_patients`` で未割当患者として扱う.
    unassigned_visit_ids: set[int] = set()

    for (office_id, weekday), am_pm_sets in by_office_weekday.items():
        am_sets = am_pm_sets.get("am") or []
        pm_sets = am_pm_sets.get("pm") or []
        staff_count = staff_count_by_weekday.get((office_id, weekday), 0)
        manager_count = manager_count_by_weekday.get((office_id, weekday), 0)
        combined = combine_am_pm_sets(
            am_sets,
            pm_sets,
            staff_count=staff_count,
            warnings=warnings,
            office_name_by_id=office_name_by_id,
        )
        # course_code を割り振る (A/B/C/D/E).
        # H4: staff_count == 0 のときは全コースを "M" (manager-required) にする.
        #     A/B/... を出すと UI 上「採用可能」と誤認させるため.
        #
        # W41 v2.6 (動的コース絞り込み): 通常コース A/B/C/D/E の発行上限を
        # ``staff_count`` (= role='staff' の当該曜日出勤人数) で動的に絞る.
        # 例: 拠点 X の月曜出勤スタッフ 4 名なら A/B/C/D の 4 コースまで、
        # 5 番目以降のセットは M (マネージャー補充枠) に押し付ける.
        # _COURSE_CODES_MAX (=5) は配列範囲ガードとして残す.
        #
        # CareFlow Wave Next 2 cross-review [H1]: overflow set を全て "M" に
        # 集約すると ``_apply_travel_time_to_courses`` / ``_check_course_capacity_minutes``
        # / H9 capacity 判定が ``(office, weekday, code)`` で grouping するため
        # 6 患者 × N set = 6N 患者を 1 ルートとして扱ってしまう. M / M2 / M3 ...
        # に index を付けて分散することで、各 overflow set が独立した物理ルートと
        # して計算される.
        #
        # CareFlow Wave Next 3 (M course manager 制限): M / M2 / ... の発行は
        # 「マネージャー 1 名に対して 1 コース」ルールにより、当該曜日の出勤
        # マネージャー数 (``manager_count``) で制限する. それも超えた set の
        # visit は course_code を割り当てず ``after_visits`` から取り除き、
        # ``unassigned_patients`` に流す.
        normal_course_limit = min(staff_count, _COURSE_CODES_MAX)
        m_overflow_limit = min(manager_count, _M_OVERFLOW_CODES_MAX)
        office_name = (office_name_by_id or {}).get(office_id) or str(office_id)
        wd_jp = _weekday_jp(weekday)
        m_overflow_idx = 0
        # CareFlow バグ修正 (#102 Fix B 漏れ): 当該 (office_id, weekday) で
        # 既に発行済みの code を追跡する. 既存固定コース (existing_codes) を
        # 採用する際、他 set と衝突したら fallback して別コードに変更する.
        # これにより「同 (office, weekday, course_code, start_time) で異住所
        # 2 名同時刻配置」を防ぐ.
        assigned_codes: set[str] = set()
        for idx, (am_set, pm_set) in enumerate(combined):
            code: str | None
            # CareFlow #102 Fix B: am_set / pm_set 内に既に course_code が
            # 埋まっている visit (= orphan PFV の build_visits_for_pool 時に
            # PFV.course_template_id から復元) があれば、その course_code を
            # 尊重して上書きを避ける. 異なる existing コードが混在した場合は
            # alphabetical 先頭を採用 + warning を出す.
            existing_codes: set[str] = set()
            for v in am_set.visits if am_set else []:
                if v.course_code is not None:
                    existing_codes.add(v.course_code)
            for v in pm_set.visits if pm_set else []:
                if v.course_code is not None:
                    existing_codes.add(v.course_code)
            if existing_codes:
                if len(existing_codes) == 1:
                    candidate = next(iter(existing_codes))
                else:
                    # 異なる既存コードが混在 → alphabetical で先頭を採用.
                    candidate = sorted(existing_codes)[0]
                    warnings.append(
                        V2Warning(
                            type="general",
                            message=(
                                f"{wd_jp} {office_name}: 異なる固定コース "
                                f"({sorted(existing_codes)}) が同一 set に混在 — "
                                f"先頭 ({candidate}) を採用 (clustering で混ざった可能性 — "
                                "PFV course 配置を見直してください)"
                            ),
                            weekday=weekday,
                            actionable=True,
                            affected_patient_ids=list(
                                {
                                    v.patient_id
                                    for v in (
                                        (am_set.visits if am_set else [])
                                        + (pm_set.visits if pm_set else [])
                                    )
                                    if v.course_code is not None
                                }
                            ),
                        )
                    )
                # CareFlow バグ修正 (#102 Fix B 漏れ): existing_codes 採用時に
                # 他 set と衝突する場合は fallback で別コードに変更する.
                # これを怠ると 2 set が同じ course_code で同時刻配置され、
                # FE に「同コース同時刻 2 名」が降りる本質バグになる.
                if candidate in assigned_codes:
                    affected_patient_ids_conflict = list(
                        {
                            v.patient_id
                            for v in (
                                (am_set.visits if am_set else [])
                                + (pm_set.visits if pm_set else [])
                            )
                        }
                    )
                    fallback = _find_next_available_code(
                        assigned_codes,
                        normal_max=normal_course_limit,
                        m_max=m_overflow_limit,
                    )
                    if fallback is None:
                        # 空きコードがない → 未割当扱い
                        affected_visits_nocode: list[V2Visit] = []
                        if am_set is not None:
                            affected_visits_nocode.extend(am_set.visits)
                        if pm_set is not None:
                            affected_visits_nocode.extend(pm_set.visits)
                        for v in affected_visits_nocode:
                            v.course_code = None
                            unassigned_visit_ids.add(id(v))
                        warnings.append(
                            V2Warning(
                                type="course_capacity",
                                message=(
                                    f"{wd_jp} {office_name}: 既存固定コース "
                                    f"{candidate} が他 set で既に使用中、かつ "
                                    "代替コードに空きがないため "
                                    f"{len(affected_patient_ids_conflict)} 名の "
                                    "患者が未割当 (course code 不足)."
                                ),
                                weekday=weekday,
                                actionable=True,
                                affected_patient_ids=affected_patient_ids_conflict,
                            )
                        )
                        continue
                    warnings.append(
                        V2Warning(
                            type="general",
                            message=(
                                f"{wd_jp} {office_name}: 既存固定コース "
                                f"{candidate} が他 set で既に使用中、"
                                f"別コード ({fallback}) に変更"
                            ),
                            weekday=weekday,
                            actionable=True,
                            affected_patient_ids=affected_patient_ids_conflict,
                        )
                    )
                    code = fallback
                    # fallback が M 系なら m_overflow_idx を消費したと記録する
                    if _is_m_course_code(fallback):
                        try:
                            new_idx = _M_OVERFLOW_CODES.index(fallback) + 1
                            if new_idx > m_overflow_idx:
                                m_overflow_idx = new_idx
                        except ValueError:
                            pass
                else:
                    code = candidate
                # 既存コードを採用したので、idx ベース順位は m_overflow に
                # 影響させない (空きがある場合は idx ベース付番は次の set で
                # 再開). 既存コードが M 系の場合は m_overflow_idx を進める方が
                # 自然だが、ここでは「既存尊重 = 上書き禁止」のみを保証する.
            elif idx < normal_course_limit and _COURSE_CODES[idx] not in assigned_codes:
                code = _COURSE_CODES[idx]
            elif m_overflow_idx < m_overflow_limit and (
                _M_OVERFLOW_CODES[m_overflow_idx] not in assigned_codes
            ):
                # マネージャー数まで M / M2 / ... 発番
                code = _M_OVERFLOW_CODES[m_overflow_idx]
                m_overflow_idx += 1
            else:
                # idx ベース付番が assigned_codes と衝突 or 上限超過 →
                # 残った空きコードへ fallback. それも無ければ未割当.
                fallback_default = _find_next_available_code(
                    assigned_codes,
                    normal_max=normal_course_limit,
                    m_max=m_overflow_limit,
                )
                if fallback_default is not None:
                    code = fallback_default
                    if _is_m_course_code(fallback_default):
                        try:
                            new_idx = _M_OVERFLOW_CODES.index(fallback_default) + 1
                            if new_idx > m_overflow_idx:
                                m_overflow_idx = new_idx
                        except ValueError:
                            pass
                else:
                    # スタッフ + マネージャー数を超過 → 未割当
                    code = None
                    affected_visits: list[V2Visit] = []
                    if am_set is not None:
                        affected_visits.extend(am_set.visits)
                    if pm_set is not None:
                        affected_visits.extend(pm_set.visits)
                    affected_patient_ids_overflow = list({v.patient_id for v in affected_visits})
                    affected_patient_count = len(affected_patient_ids_overflow)
                    for v in affected_visits:
                        unassigned_visit_ids.add(id(v))
                    warnings.append(
                        V2Warning(
                            type="course_capacity",
                            message=(
                                f"{wd_jp} {office_name}: 通常コース "
                                f"{normal_course_limit} + M (マネージャー枠) "
                                f"{m_overflow_limit} を超えるセットがあり、"
                                f"{affected_patient_count} 名の患者が未割当 "
                                "(manager 不足のため). マネージャー補充 or "
                                "曜日変更を検討してください."
                            ),
                            weekday=weekday,
                            actionable=True,
                            # P2: 未割当 patient_id を構造化照合用に格納.
                            # メッセージに "manager 不足" を含むので
                            # reason="manager_short" にマップされる.
                            affected_patient_ids=affected_patient_ids_overflow,
                        )
                    )
                    continue
            assigned_codes.add(code)
            for v in am_set.visits if am_set else []:
                v.course_code = code
            for v in pm_set.visits if pm_set else []:
                v.course_code = code

    # マネージャー不足で未割当になった visit を after_visits から取り除く.
    # _identify_unassigned_patients は after_visits.patient_id にいない pool patient を
    # 未割当扱いするため、ここで物理的に取り除く必要がある.
    if unassigned_visit_ids:
        after_visits = [v for v in after_visits if id(v) not in unassigned_visit_ids]

    # W41 v2 拡張 (移動時間の time 化): コース内連続訪問に対し、移動時間を
    # start_time に反映する. course_code 確定後に呼ぶ. time_type ごとに
    # 挙動分岐 (固定/時間帯/午前/午後/終日) し、不整合は warning に出す.
    #
    # CareFlow #101: 固定 visit が shortage>=SHORTAGE_THRESHOLD_MIN の場合は
    # ``course_code=None`` + 戻り値 set に id(v) が入る. 既存の
    # ``unassigned_visit_ids`` (Stage 5 overflow) と union し、まとめて
    # ``after_visits`` から除去する.
    travel_unassigned_ids = _apply_travel_time_to_courses(
        after_visits, warnings=warnings, office_name_by_id=office_name_by_id
    )
    if travel_unassigned_ids:
        after_visits = [v for v in after_visits if id(v) not in travel_unassigned_ids]

    # W41 v2 拡張 (コース容量 duration 化): 既存の人数制約 (MAX_PATIENTS_PER_COURSE=6)
    # と独立して、コース総所要時間 (visit duration + 移動時間) が 480 分を超えていないか check.
    _check_course_capacity_minutes(
        after_visits, warnings=warnings, office_name_by_id=office_name_by_id
    )

    # W41 v2 拡張 (二人組訪問): requires_multiple_staff=True 患者の visit に
    # 対し、同 (office, weekday) の対応可能スタッフが 2 名以上いるか check.
    _check_two_staff_availability(
        after_visits,
        staff_count_by_weekday=staff_count_by_weekday,
        warnings=warnings,
        office_name_by_id=office_name_by_id,
    )

    # W41 v2 (Mode 2 UI 拡張): 未割当患者リストを抽出.
    # full_optimize モードのときのみ意味を持つ (after_visits = pool_visits 由来).
    # diff_add モードでは after_visits に before 由来 visit も含まれるため、
    # pool_patients (= 固定枠なし患者) のうち after に出ない者は依然として未割当扱い.
    unassigned = _identify_unassigned_patients(
        pool_patients=pool_patients,
        after_visits=after_visits,
        warnings=warnings,
    )

    return {
        "proposal_batch_id": proposal_batch_id,
        "before_visits": before_visits,
        "after_visits": after_visits,
        "pool_visits": pool_visits,
        "warnings": warnings,
        "staff_count_by_weekday": staff_count_by_weekday,
        "unassigned_patients": unassigned,
    }


# ---------------------------------------------------------------------------
# Reset-to-fixed (機能 D)
# ---------------------------------------------------------------------------

# W41 v2 final cross-review (C-Codex-2): reset で soft-delete してよい source 集合.
# - 手動 D&D / 手動作成 (``manual``) や AI 経路 / インポート系は **保護**.
# - 自動生成由来のみ削除して再生成する.
# layer1_expander.py の ``LAYER1_VISIT_SOURCE='auto'``, auto_allocator.py の
# ``AUTO_ALLOC_VISIT_SOURCE='auto_alloc'``, 本ファイル自身の ``'reset_v2'`` と
# 旧コード経由の ``'auto_alloc_v2'`` / ``'pfv'`` / ``'fixed'`` を含める.
_RESET_DELETABLE_SOURCES: tuple[str, ...] = (
    "auto",
    "auto_alloc",
    "auto_alloc_v2",
    "auto_alloc_v2w",
    "pfv",
    "fixed",
    "reset_v2",
)

# W41 v2 final cross-review (C-Codex-2): 完了済み・実績入力済みなど運用上
# 削除してはならない status は保護する. ``planned`` (および legacy: ``proposed``)
# のみ削除候補.
_RESET_DELETABLE_STATUSES: tuple[str, ...] = ("planned", "proposed")


async def _resolve_course_for_pfv(
    db: AsyncSession,
    *,
    pfv: PatientFixedVisit,
    office_id: UUID,
    iso_year: int,
    iso_week: int,
    weekday: int,
    course_cache: dict[tuple[UUID, int, str], Course],
    warnings: list[str],
) -> Course | None:
    """``patient_fixed_visit`` に対応する Course を解決 (無ければ新規作成).

    W41 v2 final cross-review (C-Codex-1): 旧実装は Visit.course_id を
    セットしていなかったため Frontend の CourseDayTablePanel から visit が
    除外され、reset 後に「全部消えた」ように見えた. PFV → Course を解決し
    course_id を埋める.

    解決順序 (``app.api.v1.schedule._get_or_create_course_for_template_week``
    に倣う):
        1. PFV.course_template_id がある場合: (template_id, year, week, weekday)
           で SELECT.
        2. miss / template_id=NULL の場合: (office_id, code, year, week, weekday)
           で SELECT (template の label 先頭 1 文字を code とする).
        3. それでも無ければ新規 Course を ``staff_assigned`` で INSERT.
        4. PFV.course_template_id が NULL の場合は当該拠点の最初の有効
           template を使う (warning を残す).

    course_cache は同 (office_id, weekday, code) を 1 回だけ解決するためのもの.
    """
    # template 解決
    template: CourseTemplate | None = None
    if pfv.course_template_id is not None:
        template = await db.scalar(
            select(CourseTemplate).where(
                CourseTemplate.id == pfv.course_template_id,
                CourseTemplate.deleted_at.is_(None),
            )
        )

    if template is None:
        # フォールバック: 拠点の最初の有効 template を使う
        template = await db.scalar(
            select(CourseTemplate)
            .where(
                CourseTemplate.office_id == office_id,
                CourseTemplate.deleted_at.is_(None),
            )
            .order_by(CourseTemplate.label)
            .limit(1)
        )
        if template is None:
            warnings.append(
                f"拠点 {office_id} に有効なコーステンプレートが無いため "
                f"コース解決不可 (患者ID: {pfv.patient_id})"
            )
            return None
        if pfv.course_template_id is not None:
            warnings.append(
                f"PatientFixedVisit.course_template_id={pfv.course_template_id} "
                f"が見つからないため、拠点デフォルト template={template.id} で代替しました "
                f"(patient_id={pfv.patient_id})"
            )

    label_first = (template.label or "").strip()[:1].upper()
    code = label_first if label_first in ("A", "B", "C", "D", "E", "M") else "M"

    cache_key = (office_id, weekday, code)
    cached = course_cache.get(cache_key)
    if cached is not None:
        return cached

    # 1st try: (template_id, year, week, weekday)
    course = await db.scalar(
        select(Course).where(
            Course.template_id == template.id,
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.weekday == weekday,
            Course.deleted_at.is_(None),
        )
    )
    if course is None:
        # 2nd try: UNIQUE 制約と同じ key (office_id, code, year, week, weekday)
        course = await db.scalar(
            select(Course).where(
                Course.office_id == office_id,
                Course.code == code,
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.weekday == weekday,
                Course.deleted_at.is_(None),
            )
        )

    if course is None:
        # 新規作成 — reset は確定操作なので ``staff_assigned`` で生成する.
        course = Course(
            iso_year=iso_year,
            iso_week=iso_week,
            weekday=weekday,
            code=code,
            course_status=COURSE_STATUS_STAFF_ASSIGNED,
            template_id=template.id,
            office_id=office_id,
        )
        db.add(course)
        await db.flush()

    course_cache[cache_key] = course
    return course


async def _resolve_course_for_code(
    db: AsyncSession,
    *,
    office_id: UUID,
    iso_year: int,
    iso_week: int,
    weekday: int,
    code: str,
    course_cache: dict[tuple[UUID, int, str], Course],
    courses_created_counter: list[int],
    warnings: list[str],
) -> Course | None:
    """``(office_id, weekday, code)`` から Course を解決 (無ければ新規作成).

    ``_resolve_course_for_pfv`` から派生. PFV ではなく visit_plan の
    ``course_code`` を直接受け取る ``apply_week_only`` 用ヘルパー.

    解決順序:
        1. (office_id, code, iso_year, iso_week, weekday) で既存 Course を探す
        2. 無ければ ``code`` に一致する template を選んで template_id を確定し新規 INSERT
        3. course_cache でメモ化 (同 (office_id, weekday, code) は 1 回だけ DB 引き)

    CareFlow Wave Next 2 cross-review [C1]: 旧実装は「拠点の最初の有効 template」
    を無条件に template_id に充てており、``code="M"`` でも A や B template を
    指してしまう不整合 (FE は template.label と code を照合するため UI 上に
    出ない) が発生していた. 本実装では:
        - ``code`` が ``A/B/C/D/E`` の通常 code → ``label`` 先頭文字一致 (大文字)
          の template を選択
        - ``code`` が ``M / M2 / M3...`` (M overflow) → 完全一致 label → "M" label
          → 先頭 'M' label の順で template を選択
        - template が見つからなければ ``warnings`` に明示的に追記し ``None``
          を返す (= 呼び出し側で Course 作成失敗を検知可能)

    ``courses_created_counter`` は新規作成件数を呼び出し側へ返すためのワンスロット
    int list (mutate して使う).
    """
    cache_key = (office_id, weekday, code)
    cached = course_cache.get(cache_key)
    if cached is not None:
        return cached

    # 1st try: UNIQUE 制約と同じ key (office_id, code, year, week, weekday)
    course = await db.scalar(
        select(Course).where(
            Course.office_id == office_id,
            Course.code == code,
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.weekday == weekday,
            Course.deleted_at.is_(None),
        )
    )
    if course is not None:
        course_cache[cache_key] = course
        return course

    # CareFlow Wave Next 2 cross-review [C1]: code に一致する template を明示選択.
    # 旧実装の「先頭 template (label 昇順)」では code="M" でも A/B template を
    # 引いてしまい FE 表示と Course.template_id が不整合になっていた.
    code_first = code[:1].upper()
    templates = (
        await db.scalars(
            select(CourseTemplate)
            .where(
                CourseTemplate.office_id == office_id,
                CourseTemplate.deleted_at.is_(None),
            )
            .order_by(CourseTemplate.label)
        )
    ).all()

    template: CourseTemplate | None = None
    if _is_m_course_code(code):
        # M / M2 / M3 ... の場合: 完全一致 label → "M" label → 先頭 'M' label の順.
        exact = next((t for t in templates if (t.label or "").strip().upper() == code), None)
        if exact is not None:
            template = exact
        else:
            base_m = next((t for t in templates if (t.label or "").strip().upper() == "M"), None)
            if base_m is not None:
                template = base_m
            else:
                template = next(
                    (t for t in templates if (t.label or "").strip()[:1].upper() == "M"),
                    None,
                )
    else:
        # 通常 code (A/B/C/D/E): label 先頭文字一致.
        template = next(
            (t for t in templates if (t.label or "").strip()[:1].upper() == code_first),
            None,
        )

    if template is None:
        warnings.append(
            f"コース解決不可 ({_weekday_jp(weekday)} {code} コース): "
            f"拠点に code={code!r} に対応する template が見つかりません"
        )
        return None

    course = Course(
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        template_id=template.id,
        office_id=office_id,
    )
    db.add(course)
    await db.flush()
    course_cache[cache_key] = course
    courses_created_counter[0] += 1
    return course


async def apply_week_only(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
    patient_visit_plans: list[dict[str, Any]],
    pending_edits: list[Any] | None = None,
) -> dict[str, Any]:
    """全面最適化結果を visits のみに反映 (patient_fixed_visits は更新しない).

    Atomic flow:
      1. 対象週・対象拠点の active visits を soft-delete
         (uq_visits_pds_group_active 衝突回避; W41 v2 reset と同じ source/status 保護)
      2. 必要な courses を確保 (既存利用 or 新規作成 staff_assigned)
      3. visits を INSERT (course_id 紐付け, source="auto_alloc_v2w")
      4. assigned_staff_id がある visit_plan は visit_staff_assignments を INSERT

    Args:
        patient_visit_plans: ``[{"patient_id": UUID, "visit_plans": list[dict]}, ...]``.
            各 visit_plans 要素は ``weekday, start_time, end_time, duration_min,
            course_code, office_id, am_pm, assigned_staff_id?`` を持つ.
        pending_edits: 今週限定オーバーレイ. ``patient_visit_plans`` の各 visit_plan を
            ``(patient_id, weekday)`` キーで上書きする (新時刻 / 終了時刻 / duration).
            通常 FE 側は ``patient_visit_plans`` に既にオーバーレイを反映済みで送ってくるが、
            念のため backend 側でも再適用する (defensive). 同じキーが重複する場合は
            **最後のもの** を採用.

    Returns:
        ``{visits_created, visits_soft_deleted, courses_created,
           visit_staff_assignments_created, warnings}``.

    本関数は ``await db.flush()`` のみ呼ぶ. commit / rollback は呼び出し側.
    """
    if iso_year < 2000 or iso_year > 2100:
        raise ValueError(f"iso_year out of range: {iso_year}")
    if iso_week < 1 or iso_week > 53:
        raise ValueError(f"iso_week out of range: {iso_week}")

    warnings: list[str] = []
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: year={iso_year} week={iso_week}") from exc

    # 全拠点指定対応
    if not office_ids:
        rows = await db.scalars(select(Office.id).where(Office.deleted_at.is_(None)))
        office_ids = list(rows.all())
        if not office_ids:
            return {
                "visits_created": 0,
                "visits_soft_deleted": 0,
                "courses_created": 0,
                "visit_staff_assignments_created": 0,
                "warnings": ["対象拠点が登録されていません"],
            }

    patients_by_id = await _load_active_patients(db, office_ids=office_ids)
    patient_ids = list(patients_by_id.keys())

    # P1 (本質バグ修正): DELETE 対象を FE から渡された patient_visit_plans に含まれる
    # patient_id のみに限定する. 旧実装は active 全患者の旧 visit を soft-delete し、
    # INSERT は patient_visit_plans 分のみだったため、unassigned 患者の旧 visit が
    # 「消失 + 新規 INSERT なし」状態になっていた (= 14 名分の visit が消える事故).
    #
    # 修正: visit_plans に含まれる patient のみ DELETE 対象とし、unassigned patient の
    # 旧 visit は保護する. 「旧予定 + 新提案」が混在する状態を許容するため、
    # 別途 warning を出して運用者に通知する.
    plan_patient_ids_set: set[UUID] = set()
    for entry in patient_visit_plans:
        pid_raw = entry.get("patient_id")
        if pid_raw is None:
            continue
        if isinstance(pid_raw, UUID):
            plan_patient_ids_set.add(pid_raw)
        else:
            try:
                plan_patient_ids_set.add(UUID(str(pid_raw)))
            except (ValueError, AttributeError):
                continue
    plan_patient_ids = list(plan_patient_ids_set)

    # 1) 対象週の active visits を soft-delete (reset と同じ source/status 保護方針).
    from datetime import UTC as _UTC  # noqa: N814
    from datetime import datetime as _dt

    visits_to_delete: list[Visit] = []
    if plan_patient_ids:
        week_sunday = date.fromordinal(week_monday.toordinal() + 6)
        stmt = (
            select(Visit)
            .where(
                # P1: DELETE 対象を plan_patient_ids に限定. 旧実装は patient_ids
                # (= active 全員) を指定していたため、unassigned 患者の visit も
                # 一緒に soft-delete される本質バグがあった.
                Visit.patient_id.in_(plan_patient_ids),
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
                Visit.status.in_(_RESET_DELETABLE_STATUSES),
                Visit.source.in_(_RESET_DELETABLE_SOURCES),
            )
            .with_for_update()
        )
        rows = await db.scalars(stmt)
        visits_to_delete = list(rows.all())  # type: ignore[arg-type]

    now = _dt.now(tz=_UTC)
    soft_deleted_count = 0
    if visits_to_delete:
        visit_ids = [v.id for v in visits_to_delete]
        from sqlalchemy import delete as sa_delete

        await db.execute(
            sa_delete(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
        )
        for v in visits_to_delete:
            v.deleted_at = now
            soft_deleted_count += 1
        await db.flush()

    # P1: unassigned 患者 (= active だが visit_plans に含まれない) の旧 visit が
    # 保護されたことを warning に出す. 「旧予定 + 新提案」混在状態を明示し、
    # 運用者が必要に応じて手動で旧 visit を整理できるようにする.
    unassigned_patient_ids = [pid for pid in patient_ids if pid not in plan_patient_ids_set]
    if unassigned_patient_ids:
        week_sunday_chk = date.fromordinal(week_monday.toordinal() + 6)
        preserved_rows = await db.scalars(
            select(Visit).where(
                Visit.patient_id.in_(unassigned_patient_ids),
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday_chk,
                Visit.status.in_(_RESET_DELETABLE_STATUSES),
                Visit.source.in_(_RESET_DELETABLE_SOURCES),
            )
        )
        preserved_visits = list(preserved_rows.all())
        if preserved_visits:
            preserved_pids = {v.patient_id for v in preserved_visits}
            preserved_names = sorted(
                patients_by_id[pid].name
                for pid in preserved_pids
                if pid in patients_by_id and patients_by_id[pid].name
            )
            sample_names = "、".join(preserved_names[:5])
            extra_count = len(preserved_names) - 5
            extra_suffix = f" 他 {extra_count} 名" if extra_count > 0 else ""
            warnings.append(
                f"未割当 {len(preserved_pids)} 名 ({sample_names}{extra_suffix}) の旧 visit "
                f"{len(preserved_visits)} 件を保持しました "
                "(visit_plans に含まれなかったため、削除せず維持). "
                "必要に応じて手動で整理してください."
            )

    # W41 v2 拡張 (今週限定オーバーレイ): pending_edits を defensive に再適用.
    # FE 側は通常 patient_visit_plans に既にオーバーレイを反映済みで送ってくるが、
    # backend 側でも (patient_id, weekday) ベースで上書きする.
    apply_overlay = _build_pending_edit_overlay(pending_edits)

    # 2-3) visit_plans を visits に変換して INSERT (course 解決込み)
    course_cache: dict[tuple[UUID, int, str], Course] = {}
    courses_created_counter: list[int] = [0]
    inserted_visits = 0
    new_visits_with_staff: list[tuple[Visit, UUID]] = []

    for entry in patient_visit_plans:
        patient_id_raw = entry.get("patient_id")
        if patient_id_raw is None:
            continue
        # UUID へ正規化 (FastAPI 経由なら既に UUID だが防御的).
        if isinstance(patient_id_raw, UUID):
            patient_id = patient_id_raw
        else:
            try:
                patient_id = UUID(str(patient_id_raw))
            except (ValueError, AttributeError):
                continue
        patient = patients_by_id.get(patient_id)
        if patient is None:
            warnings.append(f"対象拠点の active 患者ではないためスキップ (患者ID: {patient_id})")
            continue
        visit_plans_raw = entry.get("visit_plans") or []
        for plan in visit_plans_raw:
            wd = plan.get("weekday")
            st = plan.get("start_time")
            et = plan.get("end_time")
            dur = plan.get("duration_min")
            code = plan.get("course_code") or "M"
            office_id_raw = plan.get("office_id")
            if not isinstance(wd, int) or not (0 <= wd <= 6):
                continue
            if isinstance(st, str):
                parsed_st = _parse_hhmm(st)
                if parsed_st is None:
                    continue
                st = parsed_st
            if isinstance(et, str):
                parsed_et = _parse_hhmm(et)
                if parsed_et is None:
                    continue
                et = parsed_et
            if not isinstance(st, time) or not isinstance(et, time):
                continue
            if et <= st:
                continue
            if not isinstance(dur, int) or dur <= 0:
                dur = 30
            # W41 v2 拡張: 今週限定オーバーレイの再適用 (defensive).
            ov = apply_overlay.get((patient_id, wd))
            if ov is not None:
                st = ov.new_start
                dur = _compute_overlay_duration(ov, existing_duration=dur)
                if ov.new_end is not None and ov.new_time_type not in (
                    "時間帯",
                    "午前",
                    "午後",
                    "終日",
                ):
                    et = ov.new_end
                else:
                    et = _add_minutes(st, dur)
                # Overlay 適用後の end_time > start_time 再検証.
                # クライアントが new_end <= new_start な pending_edit を送っても
                # 不正な visit (end <= start) を DB に insert しないためのガード.
                if et <= st:
                    warnings.append(
                        f"patient_id={patient_id}: {_weekday_jp(wd)} overlay 適用後の "
                        f"end_time ({_fmt_hhmm(et)}) <= start_time ({_fmt_hhmm(st)}) "
                        "のためスキップ"
                    )
                    continue
            # H10: 昼休憩枠と重なる visit はスキップ
            if _is_in_lunch_break(st, et):
                warnings.append(
                    f"patient_id={patient_id}: {_weekday_jp(wd)} {_fmt_hhmm(st)}-"
                    f"{_fmt_hhmm(et)} は昼休憩 (12:00-13:00) に重なるため配置不可"
                )
                continue
            if isinstance(office_id_raw, UUID):
                office_id = office_id_raw
            else:
                try:
                    office_id = UUID(str(office_id_raw))
                except (ValueError, AttributeError):
                    # office_id が無ければ patient.primary_office_id にフォールバック
                    if patient.primary_office_id is None:
                        continue
                    office_id = patient.primary_office_id
            visit_date = date.fromordinal(week_monday.toordinal() + wd)
            course = await _resolve_course_for_code(
                db,
                office_id=office_id,
                iso_year=iso_year,
                iso_week=iso_week,
                weekday=wd,
                code=str(code),
                course_cache=course_cache,
                courses_created_counter=courses_created_counter,
                warnings=warnings,
            )
            new_visit = Visit(
                patient_id=patient.id,
                visit_date=visit_date,
                start_time=st,
                end_time=et,
                type="regular",
                status="planned",
                source="auto_alloc_v2w",
                required_staff_count=1,
                course_id=(course.id if course is not None else None),
                note=f"apply_week_only_v2 iso_year={iso_year} iso_week={iso_week}",
            )
            db.add(new_visit)
            inserted_visits += 1
            staff_raw = plan.get("assigned_staff_id")
            if staff_raw is not None:
                if isinstance(staff_raw, UUID):
                    staff_id = staff_raw
                else:
                    try:
                        staff_id = UUID(str(staff_raw))
                    except (ValueError, AttributeError):
                        staff_id = None  # type: ignore[assignment]
                if staff_id is not None:
                    new_visits_with_staff.append((new_visit, staff_id))

    await db.flush()

    # 4) visit_staff_assignments を一括 INSERT
    assignments_created = 0
    for visit, sid in new_visits_with_staff:
        db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=sid))
        assignments_created += 1
    await db.flush()

    return {
        "visits_created": inserted_visits,
        "visits_soft_deleted": soft_deleted_count,
        "courses_created": courses_created_counter[0],
        "visit_staff_assignments_created": assignments_created,
        "warnings": warnings,
    }


async def reset_visits_to_fixed(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
) -> dict[str, Any]:
    """機能 D: 対象週の visits を soft-delete → patient_fixed_visits から再生成.

    手順:
      1. (iso_year, iso_week) の active visits を patient 範囲で soft-delete
         (W41 v2 final cross-review C-Codex-2: source / status で絞り、
         手動作成・完了済み visit を保護する)
      2. patient_fixed_visits (mode='normal') から visits を INSERT
         (W41 v2 final cross-review C-Codex-1: 対応する Course を解決して
         visit.course_id をセット — UI 側コース表で表示されるようにする)
      3. スタッフ割当はローテーション (簡易): is_trainee=false の active staff を
         (office_id, weekday) ごとに循環割当.

    本関数は ``await db.flush()`` のみ呼ぶ. commit は呼び出し側.
    """
    if iso_year < 2000 or iso_year > 2100:
        raise ValueError(f"iso_year out of range: {iso_year}")
    if iso_week < 1 or iso_week > 53:
        raise ValueError(f"iso_week out of range: {iso_week}")

    warnings: list[str] = []
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: year={iso_year} week={iso_week}") from exc

    # 全拠点指定対応
    if not office_ids:
        rows = await db.scalars(select(Office.id).where(Office.deleted_at.is_(None)))
        office_ids = list(rows.all())
        if not office_ids:
            return {
                "visits_regenerated": 0,
                "visits_soft_deleted": 0,
                "courses_used": 0,
                "warnings": ["対象拠点が登録されていません"],
            }

    patients_by_id = await _load_active_patients(db, office_ids=office_ids)
    patient_ids = list(patients_by_id.keys())

    # 1) 対象週の active visits を取得 (該当 patient のみ).
    # W41 v2 final cross-review (C-Codex-2): source / status で絞り、
    # 手動作成 (source != auto-generated) / 完了済み (status != planned)
    # / キャンセル済み visit は保護する.
    # C-Claude-1: with_for_update() で行ロックを取得し、同じ週に対する
    # 並行 reset / apply を直列化する.
    from datetime import UTC as _UTC  # noqa: N814  (UTC alias for clarity)
    from datetime import datetime as _dt

    visits_to_delete: list[Visit] = []
    if patient_ids:
        week_sunday = date.fromordinal(week_monday.toordinal() + 6)
        stmt = (
            select(Visit)
            .where(
                Visit.patient_id.in_(patient_ids),
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday,
                Visit.status.in_(_RESET_DELETABLE_STATUSES),
                Visit.source.in_(_RESET_DELETABLE_SOURCES),
            )
            .with_for_update()
        )
        rows = await db.scalars(stmt)
        visits_to_delete = list(rows.all())  # type: ignore[arg-type]

    now = _dt.now(tz=_UTC)
    soft_deleted_count = 0
    if visits_to_delete:
        visit_ids = [v.id for v in visits_to_delete]
        # 関連 VisitStaffAssignment を物理削除 (deleted_at を持たないため).
        from sqlalchemy import delete as sa_delete

        await db.execute(
            sa_delete(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
        )
        for v in visits_to_delete:
            v.deleted_at = now
            soft_deleted_count += 1
        await db.flush()

    # 2) patient_fixed_visits から visits を再生成
    pfv_rows = await db.scalars(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id.in_(patient_ids),
            PatientFixedVisit.mode == "normal",
        )
    )
    pfv_list = list(pfv_rows.all())

    # 3) スタッフ割当はローテーション: (office_id, weekday) ごとに staff_pool を構築
    # staff list を (office_id, weekday) ごとに取得
    staff_rows = await db.scalars(
        select(Staff).where(
            Staff.status == "active",
            Staff.deleted_at.is_(None),
            Staff.role == "staff",
            Staff.is_trainee.is_(False),
            Staff.primary_office_id.in_(office_ids),
        )
    )
    staff_list = list(staff_rows.all())
    staff_ids = [s.id for s in staff_list]
    shifts_rows = await db.scalars(
        select(StaffShift).where(StaffShift.staff_id.in_(staff_ids), StaffShift.is_on.is_(True))
    )
    shifts_by_staff: dict[UUID, set[int]] = {}
    for sh in shifts_rows.all():
        shifts_by_staff.setdefault(sh.staff_id, set()).add(sh.weekday)
    overrides_rows = await db.scalars(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id.in_(staff_ids),
            StaffWeeklyOverride.iso_year == iso_year,
            StaffWeeklyOverride.iso_week == iso_week,
            StaffWeeklyOverride.override_type == "off",
        )
    )
    off_overrides: set[tuple[UUID, int]] = {
        (ov.staff_id, ov.weekday) for ov in overrides_rows.all()
    }

    staff_by_office_weekday: dict[tuple[UUID, int], list[UUID]] = {}
    for s in staff_list:
        if s.primary_office_id is None:
            continue
        for wd in shifts_by_staff.get(s.id, set()):
            if (s.id, wd) in off_overrides:
                continue
            staff_by_office_weekday.setdefault((s.primary_office_id, wd), []).append(s.id)

    # ローテーション用カウンタ
    rotation_idx: dict[tuple[UUID, int], int] = {}
    courses_used_keys: set[tuple[UUID, int, UUID]] = set()

    # W41 v2 final cross-review (C-Codex-1): PFV → Course を解決するための cache.
    # 同 (office_id, weekday, code) は 1 回だけ DB 引きする.
    course_cache: dict[tuple[UUID, int, str], Course] = {}

    # H1: 1 PFV ごとに await db.flush() を呼ぶと O(N) DB roundtrip になる.
    #     visits は一括で add → 1 回 flush → assignments を一括 add → 1 回 flush.
    inserted_visits = 0
    new_visits_with_staff: list[tuple[Visit, UUID]] = []
    for pfv in pfv_list:
        patient = patients_by_id.get(pfv.patient_id)
        if patient is None or patient.primary_office_id is None:
            continue
        end_t = _add_minutes(pfv.start_time, pfv.duration_min)
        if end_t <= pfv.start_time:
            continue
        visit_date = date.fromordinal(week_monday.toordinal() + pfv.weekday)
        office_id = patient.primary_office_id
        # W41 v2 final cross-review (C-Codex-1): 対応する Course を解決して
        # visit.course_id をセットする. これを抜くと Frontend の CourseDayTablePanel
        # でコース表から除外されてしまう.
        course = await _resolve_course_for_pfv(
            db,
            pfv=pfv,
            office_id=office_id,
            iso_year=iso_year,
            iso_week=iso_week,
            weekday=pfv.weekday,
            course_cache=course_cache,
            warnings=warnings,
        )
        # ローテーションで staff_id を選ぶ
        pool = staff_by_office_weekday.get((office_id, pfv.weekday), [])
        staff_id: UUID | None = None
        if pool:
            idx = rotation_idx.get((office_id, pfv.weekday), 0)
            staff_id = pool[idx % len(pool)]
            rotation_idx[(office_id, pfv.weekday)] = idx + 1
            courses_used_keys.add((office_id, pfv.weekday, staff_id))
        new_visit = Visit(
            patient_id=patient.id,
            visit_date=visit_date,
            start_time=pfv.start_time,
            end_time=end_t,
            type="regular",
            status="planned",
            source="reset_v2",
            required_staff_count=1,
            course_id=(course.id if course is not None else None),
            note=f"reset_to_fixed_v2 iso_year={iso_year} iso_week={iso_week}",
        )
        db.add(new_visit)
        inserted_visits += 1
        if staff_id is not None:
            new_visits_with_staff.append((new_visit, staff_id))

    # 1) visits を一括 INSERT (visit.id を解決)
    await db.flush()

    # 2) staff assignments を一括 INSERT
    for visit, sid in new_visits_with_staff:
        db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=sid))
    await db.flush()

    return {
        "visits_regenerated": inserted_visits,
        "visits_soft_deleted": soft_deleted_count,
        "courses_used": len(courses_used_keys),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Apply individual (機能 A/B 共通; 1 件採用)
# ---------------------------------------------------------------------------


async def apply_individual_proposal(
    db: AsyncSession,
    *,
    patient_id: UUID,
    visit_plans: list[dict[str, Any]],
) -> dict[str, Any]:
    """1 患者の固定枠 (patient_fixed_visits mode='normal') を提案で上書きする.

    Args:
        patient_id: 対象患者.
        visit_plans: V2VisitPlan の dict 表現リスト. 各要素は少なくとも
            ``weekday`` (int), ``start_time`` (time), ``duration_min`` (int)
            を持つ.

    Idempotent: 既に同じ内容の PFV があれば変更ゼロで ``idempotent=true`` を返す.
    """
    warnings: list[str] = []
    # 既存 PFV (mode='normal') を取得.
    # C2: 同一患者を 2 セッションが同時に採用すると、SELECT → INSERT の
    #     between で race が起きて重複 PFV ができる. with_for_update() で
    #     対象患者の PFV 行に排他ロックを掛け、commit までシリアライズする.
    existing_rows = await db.scalars(
        select(PatientFixedVisit)
        .where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.slot_index == 0,
        )
        .with_for_update()
    )
    existing = list(existing_rows.all())
    existing_by_wd: dict[int, PatientFixedVisit] = {p.weekday: p for p in existing}

    # 提案を {weekday: (start_time, duration_min)} に正規化
    proposed_by_wd: dict[int, tuple[time, int]] = {}
    for plan in visit_plans:
        wd = plan.get("weekday")
        st = plan.get("start_time")
        dur = plan.get("duration_min")
        if not isinstance(wd, int) or not (0 <= wd <= 6):
            continue
        if isinstance(st, str):
            parsed = _parse_hhmm(st)
            if parsed is None:
                continue
            st = parsed
        if not isinstance(st, time):
            continue
        if not isinstance(dur, int) or dur <= 0:
            dur = 30
        proposed_by_wd[wd] = (st, dur)

    if not proposed_by_wd:
        return {
            "patient_id": patient_id,
            "applied": False,
            "fixed_visit_ids": [],
            "idempotent": False,
            "warnings": ["visit_plans が空または不正です"],
        }

    # 差分検出 (idempotent 判定)
    is_same = True
    if set(existing_by_wd.keys()) != set(proposed_by_wd.keys()):
        is_same = False
    else:
        for wd, (st, dur) in proposed_by_wd.items():
            ex = existing_by_wd[wd]
            if ex.start_time != st or ex.duration_min != dur:
                is_same = False
                break
    if is_same and existing_by_wd:
        return {
            "patient_id": patient_id,
            "applied": True,
            "fixed_visit_ids": [ex.id for ex in existing],
            "idempotent": True,
            "warnings": warnings,
        }

    # 差分適用: 既存 PFV (mode='normal', slot_index=0) を提案にあわせて UPSERT
    fixed_visit_ids: list[UUID] = []
    # 削除対象: 既存 wd が提案に無い
    for wd, ex in list(existing_by_wd.items()):
        if wd not in proposed_by_wd:
            await db.delete(ex)
    # 追加/更新
    for wd, (st, dur) in proposed_by_wd.items():
        if wd in existing_by_wd:
            ex = existing_by_wd[wd]
            ex.start_time = st
            ex.duration_min = dur
            fixed_visit_ids.append(ex.id)
        else:
            new_pfv = PatientFixedVisit(
                patient_id=patient_id,
                mode="normal",
                weekday=wd,
                start_time=st,
                duration_min=dur,
                slot_index=0,
            )
            db.add(new_pfv)
            await db.flush()
            fixed_visit_ids.append(new_pfv.id)
    await db.flush()
    return {
        "patient_id": patient_id,
        "applied": True,
        "fixed_visit_ids": fixed_visit_ids,
        "idempotent": False,
        "warnings": warnings,
    }


__all__ = [
    "AM_BLOCK_END",
    "AM_BLOCK_START",
    "COURSE_MAX_MINUTES",
    "LUNCH_END",
    "LUNCH_START",
    "MAX_PATIENTS_PER_COURSE",
    "MAX_PATIENTS_PER_SET",
    "NOON_HOUR",
    "PM_BLOCK_END",
    "PM_BLOCK_START",
    "SAME_ADDRESS_TOLERANCE",
    "TRAVEL_SPEED_KMH",
    "V2Bucket",
    "V2Set",
    "V2Visit",
    "_consolidate_same_address_time",
    "apply_individual_proposal",
    "apply_week_only",
    "build_visits_for_pool",
    "calc_course_total_minutes",
    "calc_h_violations",
    "calc_total_distance",
    "cluster_by_distance_greedy",
    "combine_am_pm_sets",
    "count_active_staff_per_weekday",
    "determine_am_pm",
    "enforce_course_count_constraint",
    "haversine_km",
    "haversine_minutes",
    "reset_visits_to_fixed",
    "run_v2_pipeline",
    "split_into_buckets",
]
