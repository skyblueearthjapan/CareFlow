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
from collections import Counter
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

_WEEKDAY_CODE_TO_INT: dict[str, int] = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


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
) -> list[tuple[int, time, int, str | None]]:
    """patient.weekly_pattern から (weekday, start_time, service_minutes, time_type) を取り出す.

    リスト形式 (`entries: [{weekday, preferred_start, ...}]`) と
    サマリ形式 (`preferred_weekdays + preferred_start`) の両方をサポート.
    """
    pattern = patient.weekly_pattern
    if not isinstance(pattern, dict):
        return []
    out: list[tuple[int, time, int, str | None]] = []

    entries = pattern.get("entries")
    base_time_type = pattern.get("time_type")
    if isinstance(entries, list) and entries:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            wd = _resolve_weekday(entry.get("weekday"))
            if wd is None:
                continue
            st = _parse_hhmm(entry.get("preferred_start"))
            tt = entry.get("time_type") or base_time_type
            sm = entry.get("service_minutes")
            if not isinstance(sm, int) or sm <= 0:
                sm_value = pattern.get("service_minutes")
                sm = int(sm_value) if isinstance(sm_value, int) and sm_value > 0 else 30
            if st is None:
                # 時刻なしでも午前/午後判定はできるが、提案では仮 9:30 開始にする.
                st = AM_BLOCK_START
            out.append((wd, st, sm, tt if isinstance(tt, str) else None))
        return out

    # サマリ形式: preferred_weekdays + preferred_start を展開
    weekdays_raw = pattern.get("preferred_weekdays")
    base_start = _parse_hhmm(pattern.get("preferred_start"))
    base_sm_raw = pattern.get("service_minutes")
    base_sm = int(base_sm_raw) if isinstance(base_sm_raw, int) and base_sm_raw > 0 else 30
    if isinstance(weekdays_raw, list):
        for wd_raw in weekdays_raw:
            wd = _resolve_weekday(wd_raw)
            if wd is None:
                continue
            st = base_start if base_start is not None else AM_BLOCK_START
            out.append(
                (wd, st, base_sm, base_time_type if isinstance(base_time_type, str) else None)
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
# Stage 1+2: プール → V2Visit 展開
# ---------------------------------------------------------------------------


def build_visits_for_pool(
    patients: list[Patient],
    *,
    fixed_by_patient: dict[UUID, list[PatientFixedVisit]] | None = None,
    use_fixed_as_source: bool = False,
) -> list[V2Visit]:
    """段階 1〜2 中間: 各患者の希望を V2Visit に展開する.

    ``use_fixed_as_source=True`` の場合は ``fixed_by_patient`` を優先し、
    weekly_pattern より固定枠のスケジュールを使う (機能 D の再生成).
    """
    visits: list[V2Visit] = []
    for patient in patients:
        if patient.lat is None or patient.lng is None or patient.primary_office_id is None:
            continue
        addr = patient.address
        area = _extract_area_label(addr)
        sex_r = patient.sex_restriction
        used_fixed = False
        if use_fixed_as_source and fixed_by_patient is not None:
            fixed_rows = fixed_by_patient.get(patient.id) or []
            entries_fixed = _extract_fixed_visits_for_patient(fixed_rows)
            if entries_fixed:
                used_fixed = True
                for wd, st, sm in entries_fixed:
                    end_t = _add_minutes(st, sm)
                    am_pm = determine_am_pm(time_type="固定", preferred_start=st)
                    visits.append(
                        V2Visit(
                            patient_id=patient.id,
                            patient_name=patient.name,
                            patient_code=patient.code,
                            weekday=wd,
                            start_time=st,
                            end_time=end_t,
                            service_minutes=sm,
                            lat=float(patient.lat),
                            lng=float(patient.lng),
                            office_id=patient.primary_office_id,
                            am_pm=am_pm,
                            source_kind="fixed",
                            address=addr,
                            area_label=area,
                            time_type="固定",
                            sex_restriction=sex_r,
                        )
                    )
        if not used_fixed:
            entries = _extract_weekly_entries(patient)
            for wd, st, sm, tt in entries:
                end_t = _add_minutes(st, sm)
                am_pm = determine_am_pm(time_type=tt, preferred_start=st)
                visits.append(
                    V2Visit(
                        patient_id=patient.id,
                        patient_name=patient.name,
                        patient_code=patient.code,
                        weekday=wd,
                        start_time=st,
                        end_time=end_t,
                        service_minutes=sm,
                        lat=float(patient.lat),
                        lng=float(patient.lng),
                        office_id=patient.primary_office_id,
                        am_pm=am_pm,
                        source_kind="pool",
                        address=addr,
                        area_label=area,
                        time_type=tt,
                        sex_restriction=sex_r,
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
) -> list[V2Set]:
    """各バケット内で距離が近い 2-3 人を 1 セットにする (グリーディ).

    アルゴリズム:
      1. 全 visits ペアの Haversine 距離を計算
      2. 最も近いペアを 1 セット作る
      3. そのセットに「重心から最も近い visit」を greedy に追加 (max ``max_per_cluster``)
      4. 残りの visits で再帰 (空になるまで)

    H2 (同住所ペアリング 最大 2 人): 同住所の visits は 2 件までは同セットを優先,
    3 件以上は警告として呼び出し側で扱う.
    """
    sets: list[V2Set] = []
    remaining = list(visits)
    # 同 patient_id 同 start_time は本来 H1 で 1 件のはず. 同 patient_id の重複は除外.
    seen_keys: set[tuple[UUID, time]] = set()
    unique_remaining: list[V2Visit] = []
    for v in remaining:
        key = (v.patient_id, v.start_time)
        if key in seen_keys:
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


def _enforce_h2_same_address(sets: list[V2Set], warnings: list[str]) -> None:
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
                    f"H2 same-address group at ({key[0]:.4f},{key[1]:.4f}) "
                    f"has 3+ visits; remaining stayed in separate set"
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
    warnings: list[str],
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
                warnings.append(
                    f"H2: 同住所 3 名以上検出 → 1 名を別 set に分散移動 "
                    f"(office={visit_to_move.office_id}, wd={visit_to_move.weekday}, "
                    f"address≈({key[3][0]:.4f},{key[3][1]:.4f}))"
                )
            else:
                warnings.append(
                    f"H2: 同住所 3 名以上、移動先 set 見つからず "
                    f"(patient={visit_to_move.patient_code or visit_to_move.patient_name}, "
                    f"address≈({key[3][0]:.4f},{key[3][1]:.4f}))"
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


def enforce_course_count_constraint(
    sets_by_bucket: dict[tuple[UUID, int, Literal["am", "pm"]], list[V2Set]],
    *,
    staff_count_by_weekday: dict[tuple[UUID, int], int],
    warnings: list[str],
) -> dict[tuple[UUID, int, Literal["am", "pm"]], list[V2Set]]:
    """段階 4: バケットのセット数がコース数 (= スタッフ数) を超えた場合に警告.

    Q5 確定: マネージャー補充は自動化しない (警告ベース). 超過セットは
    マネージャー候補としてそのまま残し、警告に追加する.
    """
    for (office_id, weekday, am_pm), sets in sets_by_bucket.items():
        n = staff_count_by_weekday.get((office_id, weekday), 0)
        if n == 0:
            warnings.append(
                f"weekday={weekday} office={office_id} {am_pm}: スタッフ不在のため"
                f" {len(sets)} セットを配置できません (マネージャー補充候補)"
            )
            continue
        if len(sets) > n:
            warnings.append(
                f"weekday={weekday} office={office_id} {am_pm}: "
                f"セット数 {len(sets)} がスタッフ数 {n} を超過 "
                f"(マネージャー補充候補 {len(sets) - n} 件)"
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
    warnings: list[str],
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
        warnings.append(
            f"勤務可能スタッフ 0 名: {len(am_sets)} 午前 / {len(pm_sets)} 午後 セットは"
            f"マネージャー補充が必要 (course_code='M')"
        )

    # H9: コース容量 6 名以内 (午前 + 午後合計)
    # ここでは "セット単位" で組み合わせ、後で合計人数を check.

    courses: list[tuple[V2Set | None, V2Set | None]] = []
    am_remaining = list(am_sets)
    pm_remaining = list(pm_sets)

    while am_remaining and pm_remaining:
        if len(courses) >= staff_count and staff_count > 0:
            # スタッフ数を超えた分はマネージャー枠 (警告は段階 4 で出済)
            pass
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
            warnings.append(
                f"course pairing: am↔pm distance {best_d:.1f}km exceeds 5km (移動余裕注意)"
            )
        courses.append((am_chosen, pm_chosen))

    # 残った am は午前のみコース
    for a in am_remaining:
        courses.append((a, None))
    # 残った pm は午後のみコース
    for p in pm_remaining:
        courses.append((None, p))

    if staff_count > 0 and len(courses) > staff_count:
        warnings.append(
            f"course count {len(courses)} exceeds staff count {staff_count}"
            f" (マネージャー補充候補 {len(courses) - staff_count} 件)"
        )
    return courses


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
    warnings: list[str],
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
                warnings.append(
                    f"patient {v.patient_code} weekday={v.weekday} {v.start_time}"
                    f" blocked by acceptance_calendar"
                )
                continue
        if _is_in_lunch_break(v.start_time, v.end_time):
            warnings.append(
                f"patient {v.patient_code} weekday={v.weekday} {v.start_time}-{v.end_time}"
                f" overlaps lunch break (H10)"
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
) -> list[V2Visit]:
    """Before スナップショット: 既存 patient_fixed_visits (mode='normal') から構築."""
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

    out: list[V2Visit] = []
    for pfv in pfv_rows:
        patient = patients_by_id.get(pfv.patient_id)
        if patient is None or patient.lat is None or patient.lng is None:
            continue
        if patient.primary_office_id is None:
            continue
        end_t = _add_minutes(pfv.start_time, pfv.duration_min)
        am_pm = "am" if pfv.start_time.hour < NOON_HOUR else "pm"
        course_code = ct_label_by_id.get(pfv.course_template_id) if pfv.course_template_id else None
        addr = patient.address
        tt = _extract_time_type_for_weekday(patient, pfv.weekday)
        out.append(
            V2Visit(
                patient_id=patient.id,
                patient_name=patient.name,
                patient_code=patient.code,
                weekday=pfv.weekday,
                start_time=pfv.start_time,
                end_time=end_t,
                service_minutes=pfv.duration_min,
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
            )
        )
    return out


# ---------------------------------------------------------------------------
# Unassigned patients identification (W41 v2 Mode 2 UI 拡張)
# ---------------------------------------------------------------------------


def _identify_unassigned_patients(
    pool_patients: list[Patient],
    after_visits: list[V2Visit],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Mode 2 (full_optimize) で after_visits に出てこなかった患者と理由を抽出する.

    Returns:
        ``[{"patient_id": UUID, "patient_name": str, "patient_code": str | None,
        "reason": str}, ...]``
    """
    after_pids = {v.patient_id for v in after_visits}
    out: list[dict[str, Any]] = []
    for p in pool_patients:
        if p.id in after_pids:
            continue
        # 主要理由を決定: 最初に該当する warning から推測.
        reason = "原因不明 (受入カレンダー× / 容量超過 / 座標未設定 のいずれか)"
        if p.lat is None or p.lng is None:
            reason = "座標未設定 (住所のジオコーディングが未完了)"
        elif p.primary_office_id is None:
            reason = "拠点未設定 (primary_office_id が None)"
        else:
            for w in warnings:
                if p.code and p.code in w:
                    if "acceptance_calendar" in w:
                        reason = "受入カレンダー× (希望時間が受入不可)"
                    elif "lunch break" in w or "H10" in w:
                        reason = "希望時間が昼休憩 (12:00-13:00) と重複"
                    elif "座標" in w:
                        reason = "座標未設定"
                    elif "拠点" in w:
                        reason = "拠点未設定"
                    else:
                        reason = w[:120]
                    break
        out.append(
            {
                "patient_id": p.id,
                "patient_name": p.name,
                "patient_code": p.code,
                "reason": reason,
            }
        )
    return out


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
) -> dict[str, Any]:
    """5 段階を順に実行する.

    Returns:
        {
            "proposal_batch_id": UUID,
            "before_visits": [V2Visit, ...],   # 既存 PFV 由来
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

    warnings: list[str] = []
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
                "warnings": ["対象拠点が登録されていません"],
                "staff_count_by_weekday": {},
                "unassigned_patients": [],
            }

    # Stage 1: プール作成
    patients_by_id = await _load_active_patients(db, office_ids=office_ids)
    patients_with_fixed = await _load_patients_with_fixed(
        db, patient_ids=list(patients_by_id.keys())
    )

    if mode == "diff_add":
        # プール = active かつ固定枠無し
        pool_patients = [p for p in patients_by_id.values() if p.id not in patients_with_fixed]
    else:
        # full_optimize: 全 active 患者
        pool_patients = list(patients_by_id.values())

    # Before スナップショット
    before_visits = await _load_before_visits_from_pfv(db, patients_by_id=patients_by_id)

    # Stage 1+2 中間: pool_patients を V2Visit に展開
    pool_visits = build_visits_for_pool(pool_patients)

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
        before_copies = [replace(v) for v in before_visits]
        filtered_before = _filter_unavailable_and_lunch(
            before_copies, unavailable_slots=unavailable, warnings=warnings
        )
        after_visits = filtered_before + list(pool_visits)
    else:
        after_visits = list(pool_visits)

    # Stage 2: バケット
    buckets = split_into_buckets(after_visits)

    # Stage 3: 距離グリーディクラスタリング (バケットごと)
    sets_by_bucket: dict[tuple[UUID, int, Literal["am", "pm"]], list[V2Set]] = {}
    for key, bucket in buckets.items():
        sets = cluster_by_distance_greedy(bucket.visits)
        _enforce_h2_same_address(sets, warnings)
        # W41 v2 (H2 強化): 同住所 3 名以上を別 set に強制分散
        _enforce_h2_split_overflow(sets, warnings)
        sets_by_bucket[key] = sets

    # Stage 4: コース数制約
    staff_count_by_weekday = await count_active_staff_per_weekday(
        db, office_ids=office_ids, iso_year=iso_year, iso_week=iso_week
    )
    enforce_course_count_constraint(
        sets_by_bucket,
        staff_count_by_weekday=staff_count_by_weekday,
        warnings=warnings,
    )

    # Stage 5: 午前 ↔ 午後 組み合わせ + course_code 割当
    # (office_id, weekday) ごとに am と pm の sets を組み合わせる
    by_office_weekday: dict[tuple[UUID, int], dict[str, list[V2Set]]] = {}
    for (office_id, weekday, am_pm), sets in sets_by_bucket.items():
        by_office_weekday.setdefault((office_id, weekday), {"am": [], "pm": []})[am_pm] = sets
    for (office_id, weekday), am_pm_sets in by_office_weekday.items():
        am_sets = am_pm_sets.get("am") or []
        pm_sets = am_pm_sets.get("pm") or []
        staff_count = staff_count_by_weekday.get((office_id, weekday), 0)
        combined = combine_am_pm_sets(am_sets, pm_sets, staff_count=staff_count, warnings=warnings)
        # course_code を割り振る (A/B/C/D/E).
        # H4: staff_count == 0 のときは全コースを "M" (manager-required) にする.
        #     A/B/... を出すと UI 上「採用可能」と誤認させるため.
        for idx, (am_set, pm_set) in enumerate(combined):
            if staff_count == 0:
                code = "M"
            elif idx >= _COURSE_CODES_MAX:
                code = "M"  # スタッフ数を超えた分はマネージャー枠
            else:
                code = _COURSE_CODES[idx]
            for v in am_set.visits if am_set else []:
                v.course_code = code
            for v in pm_set.visits if pm_set else []:
                v.course_code = code

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
                f"office_id={office_id} に有効な course_template が無いため "
                f"course_id を解決できません (patient_id={pfv.patient_id})"
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
        2. 無ければ拠点の最初の有効 template を template_id にして新規 INSERT
        3. course_cache でメモ化 (同 (office_id, weekday, code) は 1 回だけ DB 引き)

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

    # 拠点の最初の有効 template を使う
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
            f"office_id={office_id} に有効な course_template が無いため "
            f"course_id を解決できません (weekday={weekday} code={code})"
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

    # 1) 対象週の active visits を soft-delete (reset と同じ source/status 保護方針).
    from datetime import UTC as _UTC  # noqa: N814
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
        from sqlalchemy import delete as sa_delete

        await db.execute(
            sa_delete(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
        )
        for v in visits_to_delete:
            v.deleted_at = now
            soft_deleted_count += 1
        await db.flush()

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
            warnings.append(f"patient_id={patient_id} は対象拠点の active 患者では無いためスキップ")
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
            # H10: 昼休憩枠と重なる visit はスキップ
            if _is_in_lunch_break(st, et):
                warnings.append(
                    f"patient_id={patient_id} weekday={wd} {st}-{et} "
                    f"overlaps lunch break (H10) — skipped"
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
    "LUNCH_END",
    "LUNCH_START",
    "MAX_PATIENTS_PER_COURSE",
    "MAX_PATIENTS_PER_SET",
    "NOON_HOUR",
    "PM_BLOCK_END",
    "PM_BLOCK_START",
    "SAME_ADDRESS_TOLERANCE",
    "V2Bucket",
    "V2Set",
    "V2Visit",
    "apply_individual_proposal",
    "apply_week_only",
    "build_visits_for_pool",
    "calc_h_violations",
    "calc_total_distance",
    "cluster_by_distance_greedy",
    "combine_am_pm_sets",
    "count_active_staff_per_weekday",
    "determine_am_pm",
    "enforce_course_count_constraint",
    "haversine_km",
    "reset_visits_to_fixed",
    "run_v2_pipeline",
    "split_into_buckets",
]
