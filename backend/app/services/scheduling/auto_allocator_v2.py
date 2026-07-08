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
    - H10 (新): 昼休憩枠に visit を入れない. Wave 3 で「11:30-13:30 内に 45-60 分
      の連続空きをコース別に確保」する動的方式に変更 (``compute_lunch_window``).
      古典的な「12:00-13:00 固定」は API 境界や Stage 1〜2 の仮定値としてのみ残る.

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
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_same_address_link import PatientSameAddressLink
from app.models.staff import Staff, StaffSecondaryOffice, StaffShift, StaffWeeklyOverride
from app.models.visit import (
    VISIT_SOURCE_MANUAL_WEEK,
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling.config import (
    DEFAULT_SCHEDULING_CONFIG,
    SchedulingConfig,
)
from app.services.scheduling.constants import (
    COURSE_MAX_MINUTES,
    DEFAULT_OFFICE_OPERATING_WEEKDAYS,
    MAX_PATIENTS_PER_COURSE,
    SAME_ADDRESS_TOLERANCE,
)

# Phase G-21: feature flag canary 切替キー.
# OfficeFeatureFlag.feature_key が ``g21_new_algorithm`` で enabled_at IS NOT NULL の
# office のみ新アルゴリズム (pinned/非 pinned 2 経路化 + 4 経路 union before) を使う.
G21_NEW_ALGORITHM_FEATURE_KEY: str = "g21_new_algorithm"

# Phase G-45: 拠点稼働曜日 (= operating_weekdays) のデフォルト値 (= 月-土).
# DB カラム NULL / 不正値 (= リスト以外 or 範囲外要素を含む) の場合に
# ``_load_office_operating_weekdays`` がフォールバックする.
# Phase G-88: 正準値は ``app.services.scheduling.constants`` に単一ソース化.
# 上部 import 済みの ``DEFAULT_OFFICE_OPERATING_WEEKDAYS`` を再エクスポート維持.

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# H9: 1 コース (午前 + 午後 合計) の上限人数 (§12.1).
# Phase G-88: 正準値は ``constants`` に単一ソース化 (上部 import 済み). 値 = 6.
# 下流 (``from auto_allocator_v2 import MAX_PATIENTS_PER_COURSE``) のため再エクスポート維持.

# 1 セット (= バケット内の距離クラスタ) 上限人数.
MAX_PATIENTS_PER_SET: int = 3

# W41 v2 拡張 (移動時間の time 化): Haversine 距離を時間 (分) に変換するための
# 平均速度. 都市部の安全側仮定 (信号・混雑考慮). 直線距離 × 60 / 速度 (km/h)
# で分換算する.
TRAVEL_SPEED_KMH: float = 20.0

# W41 v2 拡張 (コース容量 duration 化): 1 コース (1 スタッフ × 1 日, 昼休憩除く)
# の所要時間上限 (分). 9:00-12:00 + 13:00-18:00 = 8 時間 = 480 分.
# Phase G-88: 正準値は ``constants`` に単一ソース化 (上部 import 済み). 値 = 480.

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
#
# **Wave 4 Phase C** で「移動時間で間に合わない物理不可能判定」専用に意味を再整理.
# 希望時刻からの「乖離 (= 配置時刻が前後にズレた幅)」は別概念で、下の
# ``CARE_ALARM_*_THRESHOLD_MIN`` で扱う:
#   - ``SHORTAGE_THRESHOLD_MIN`` : 「前 visit からの移動 + バッファーが間に合わず
#       earliest_start > desired_start」となる物理不可能判定. 5 分以上の不足を拒否.
#   - ``CARE_ALARM_WARNING_THRESHOLD_MIN`` / ``CARE_ALARM_UNASSIGNED_THRESHOLD_MIN``:
#       移動可能なケースでも、希望時刻から大きくズレて配置された場合に warning /
#       unassigned に流すケアアラーム閾値. 物理可否ではなくケアの観点で判定.
SHORTAGE_THRESHOLD_MIN: int = 5

# Wave 4 (Phase C): ケアアラーム閾値.
# 固定/時間帯 patient の希望時刻からの乖離を 3 段階で扱う:
#   - 0-CARE_ALARM_WARNING_THRESHOLD_MIN (= 30): 黙黕 shift (warning なし)
#   - CARE_ALARM_WARNING_THRESHOLD_MIN-CARE_ALARM_UNASSIGNED_THRESHOLD_MIN (= 60):
#       warning emit (V2WarningType="care_alarm_deviation") + 配置は維持
#   - CARE_ALARM_UNASSIGNED_THRESHOLD_MIN 超: unassigned (course_code=None) +
#       UnassignedReason="care_alarm_exceeded"
# SHORTAGE_THRESHOLD_MIN (= 5) は「移動時間で間に合わない物理不可能判定」専用で
# 意味を維持. 希望時刻からの乖離はあくまでケアの観点 (患者が想定外の時刻に来訪を
# 受ける) で評価する別の指標.
#
# 適用対象 (Wave 4 Phase C 確定仕様):
#   - ``time_type='固定'``  : ``|actual_start - preferred_start|`` で評価
#   - ``time_type='時間帯'``: ``[preferred_start, preferred_end]`` 範囲外なら、
#                            範囲端からの距離で評価. 範囲内なら 0 (乖離なし).
#   - ``time_type='午前'/'午後'/'終日'`` or preferred 不在 : 対象外 (= 0).
CARE_ALARM_WARNING_THRESHOLD_MIN: int = 30
CARE_ALARM_UNASSIGNED_THRESHOLD_MIN: int = 60

# H2: 同住所判定の許容誤差 (緯度経度の絶対差 ≒ 100m).
# Phase G-88: 正準値は ``constants`` に単一ソース化 (上部 import 済み). 値 = 0.001.

# 午前/午後の境界 (Q1 確定: 12:00 未満=午前, 12:00 以降=午後).
NOON_HOUR: int = 12

# H10: 昼休憩 — Wave 3 lunch フレキシブル化 (#WAVE3):
#   開始は ``LUNCH_EARLIEST_START`` (11:30) ～ ``LUNCH_LATEST_START`` (12:30) の範囲、
#   長さは ``LUNCH_DURATION_PREFERRED`` (60 分) を基本に、密集時は
#   ``LUNCH_DURATION_FALLBACK`` (45 分) まで短縮可能。終了上限は
#   ``LUNCH_LATEST_END`` (13:30).
#
#   旧 ``LUNCH_START`` / ``LUNCH_END`` (12:00-13:00 固定) は動的化し、後方互換
#   alias として ``LUNCH_DEFAULT_START`` / ``LUNCH_DEFAULT_END`` (12:00-13:00)
#   に書き換え (下記 L139-143). API 境界やコース確定前ステージ (Stage 1〜2) 等で
#   「仮の昼休憩枠」が必要な箇所では ``LUNCH_DEFAULT_START`` /
#   ``LUNCH_DEFAULT_END`` を使う.
LUNCH_EARLIEST_START: time = time(11, 30)
LUNCH_LATEST_START: time = time(12, 30)
LUNCH_LATEST_END: time = time(13, 30)
LUNCH_DURATION_PREFERRED: int = 60
LUNCH_DURATION_FALLBACK: int = 45
# Phase E-3 改修 (2): フレキシブルランチ 3 段階 fallback.
# 「11:30-13:30 の 2 時間枠内のどこかで必ず 45-60 分の休憩を確保。最悪、なんとなく
# スペース確保」(User 確定仕様) のため、60 分 → 45 分 → 30 分 の 3 段階で空きを探す.
# 30 分 fallback が採用された場合は ``compute_lunch_window`` が warning を出す.
LUNCH_DURATION_MIN: int = 30
# 候補刻み (5 分): 11:30, 11:35, ..., 12:30.
LUNCH_CANDIDATE_STEP_MIN: int = 5

# Phase E-3 改修 (3): 同住所ペアの最低占有 (分).
# 同住所ペアは「最大 2 名」で揃え + ペア合算 duration で占有させるが、
# 合算が 90 分未満 (= 35+35=70, 30+30=60, etc.) でも最低 90 分占有を確保する.
# User 確定仕様: ``max(service 合計, 90)``.
SAME_ADDRESS_PAIR_MIN_OCCUPANCY: int = 90

# API 境界 / 仮定値で使う「標準昼休憩枠」(動的計算が走らないステージ用).
LUNCH_DEFAULT_START: time = time(12, 0)
LUNCH_DEFAULT_END: time = time(13, 0)

# 旧 ``LUNCH_START`` / ``LUNCH_END`` (12:00-13:00 固定) は ``LUNCH_DEFAULT_START`` /
# ``LUNCH_DEFAULT_END`` への alias として後方互換維持. 新規コードでは
# ``LUNCH_DEFAULT_*`` を使うこと.
LUNCH_START: time = LUNCH_DEFAULT_START
LUNCH_END: time = LUNCH_DEFAULT_END

# 午前ブロック / 午後ブロック範囲 (§1 用語).
# Wave 3 では AM/PM の境界は lunch window と動的連動するのが理想だが、
# Stage 1〜2 では lunch がまだ確定しないため、暫定的に標準枠 (12:00 / 13:00)
# を用いる. Stage 6 (``_apply_travel_time_to_courses``) で lunch が確定した後の
# 再判定は別 phase (Wave 3.5) で対応.
#
# TODO (Wave 3.5): AM/PM 境界も lunch 動的連動.
#   - 現状: AM_BLOCK_END=12:00 / PM_BLOCK_START=13:00 で固定.
#   - 問題: lunch_end_t=13:30 にバンプされた AM 希望 visit が
#     13:00-13:30 のグレーゾーンで AM/PM 判定 flaky になる
#     (PM_BLOCK_START=13:00 以降のため PM 扱い).
#   - 計画: コース別 lunch 確定後、PM_BLOCK_START を ``max(13:00, lunch_end_t)``
#     に置き換え、13:00-13:30 を「lunch にバンプされた AM 希望の余地」として残す.
AM_BLOCK_START: time = time(9, 30)
AM_BLOCK_END: time = time(12, 0)
PM_BLOCK_START: time = time(13, 0)
PM_BLOCK_END: time = time(18, 0)

# Course code (午前/午後同一スタッフが担当) — 1 拠点で最大 5 スタッフ.
# ⚠️ これは v2 全面最適化 (full_optimize) のコース上限 (A-E = 5). 別機能である
#    ``layer2_clustering.COURSE_CODES`` (A-D = /courses/generate 旧 Layer2 案生成) とは
#    意図的に別物であり、互いに統一しないこと.
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
    # Fix E (CareFlow): 同コース異住所同時刻 2 名以上が発生した場合に、
    # 後者の時刻を「前者の end + travel + buffer (8 分) → 5 分刻み切り上げ」で
    # 自動シフトする際の通知. 固定時刻 visit でも例外的に時刻を動かす.
    # severity 的には "info" 相当 (運用者通知; actionable=False で自動解決済み).
    "auto_time_shift_for_conflict",
    # Wave 4 (Phase C): ケアアラーム閾値による「希望時刻からの大乖離」警告.
    # 固定/時間帯 patient が希望時刻から 30 分超 (60 分以内) で配置された場合に
    # emit. 60 分超は unassigned + reason="care_alarm_exceeded" として外す
    # (= 警告でなく未割当扱い).
    "care_alarm_deviation",
    # Phase G-45: 拠点稼働曜日 (= operating_weekdays) に含まれない曜日に
    # patient の visit がスケジュールされそうになった場合に emit.
    # 同 (patient_id, weekday) で 1 件のみ重複排除.
    "office_closed",
    "general",
]


# Wave 4 (Phase C): 警告カテゴリ集約 (11→6 種).
# UI 側でフィルタ / 集計を簡略化するため、既存 11 種の V2WarningType を 6 つの
# カテゴリに束ねる. ``V2Warning.code`` (= 既存 type) は後方互換維持. UI が
# category だけ見れば「時刻乖離系」「容量系」「データ品質系」のような大分類で
# 振り分けられる.
class V2WarningCategory(StrEnum):
    """V2Warning の集約カテゴリ (Wave 4 Phase C; 11 種 type を 6 カテゴリに集約)."""

    time_deviation = "time_deviation"  # travel_time_shortage + care_alarm_deviation
    capacity = (
        "capacity"  # course_capacity + course_count + course_long_distance + two_staff_shortage
    )
    acceptance = "acceptance"  # acceptance_blocked
    data_quality = "data_quality"  # data_health_staff_shifts_missing
    placement_info = "placement_info"  # same_address_consolidation + auto_time_shift_for_conflict
    conflict = "conflict"  # diff_add_conflict + general


# Wave 4 (Phase C): V2WarningType → V2WarningCategory mapping.
# 未登録 code は ``conflict`` (= general カテゴリ) にフォールバック.
_WARNING_CODE_TO_CATEGORY: dict[str, V2WarningCategory] = {
    "travel_time_shortage": V2WarningCategory.time_deviation,
    "care_alarm_deviation": V2WarningCategory.time_deviation,
    "course_capacity": V2WarningCategory.capacity,
    "course_count": V2WarningCategory.capacity,
    "course_long_distance": V2WarningCategory.capacity,
    "two_staff_shortage": V2WarningCategory.capacity,
    "acceptance_blocked": V2WarningCategory.acceptance,
    "data_health_staff_shifts_missing": V2WarningCategory.data_quality,
    "same_address_consolidation": V2WarningCategory.placement_info,
    "auto_time_shift_for_conflict": V2WarningCategory.placement_info,
    "diff_add_conflict": V2WarningCategory.conflict,
    "office_closed": V2WarningCategory.conflict,
    "general": V2WarningCategory.conflict,
}


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
    # Phase E-3 改修 (4): 同住所 3 名以上が _align_same_address_pair_to_same_time
    # に到達した時 (= H2 enforce で別 set へ動かしきれなかった残存) に 3 名目以降を
    # 自動別コース化のため unassigned に流す.
    "same_address_three_or_more",
    "fixed_time_conflict",  # 固定時刻衝突 (travel_time_shortage 等)
    "lunch_break",  # 昼休憩 (12:00-13:00) と重なるため除外
    # Wave 4 (Phase C): 希望時刻から CARE_ALARM_UNASSIGNED_THRESHOLD_MIN (=60) 分超で
    # 配置された固定/時間帯 patient. ケアアラーム閾値超過のため unassigned 扱い.
    "care_alarm_exceeded",
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
    # Wave 4 (Phase C): 警告 type 集約カテゴリ (6 種). ``type`` から
    # ``_WARNING_CODE_TO_CATEGORY`` で自動解決される (__post_init__).
    # 明示的に渡された場合はそちらを優先 (= テスト等で override 可能).
    category: V2WarningCategory | None = None

    def __post_init__(self) -> None:
        # Wave 4 (Phase C): code (= type) から category を自動解決.
        # 未登録 type は V2WarningCategory.conflict にフォールバック.
        if self.category is None:
            self.category = _WARNING_CODE_TO_CATEGORY.get(self.type, V2WarningCategory.conflict)


@dataclass
class V2Visit:
    """1 件の提案 visit (in-memory, 段階 1〜5 を貫通する中間表現).

    Note (Wave 2 以降の不変量例外):
        通常 ``end_time = start_time + service_minutes`` だが、
        ``_align_same_address_pair_to_same_time`` で処理される同住所ペアの 2 人目 (B)
        は **例外**: ``B.end_time = aligned_start + a.service_minutes + b.service_minutes``
        となりペア合算 60 分を占有する. ``B.service_minutes`` 自体は不変なので、
        集計系 (``calc_course_total_minutes`` 等) は影響なし.
        UI / 後段 (earliest_start 伝播) は ``end_time`` を信頼すること.
    """

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
    # T-4 (2026-07-08): 患者性別 (male/female/unknown)。提案系タイムラインの性別
    # ウォッシュ表示用。現状 populate するのは propose_slots_service のみ (他経路は
    # None のまま = FE が中立色で描く)。
    sex: str | None = None
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
    # Phase G-21 T3 / Wave 1: pinned PFV 由来の visit は時刻補正対象外.
    # ``_apply_corrections_to_visits`` (W1 4 経路共通 helper) は本フラグを見て、
    # pinned visit の start_time / end_time / course_code を一切動かさない.
    is_pinned: bool = False
    # Phase G-92 (プール投入 固定優先→希望フォールバック): diff_add でこの pool
    # visit がどの希望ソースから展開されたかを示す.
    #   - "fixed"                   : 固定訪問スケジュール (PFV mode='normal') 由来.
    #   - "preferred"               : 希望訪問パターン (patient.weekly_pattern) 由来.
    #   - "fixed_fallback_preferred": 固定枠が 3 条件 (時間不適合 / 定員オーバー /
    #     時間衝突) で入らず、 希望訪問パターンへフォールバックした候補.
    # diff_add の PFV 患者は固定枠と希望の両ソースを候補展開し、固定が 3 条件を
    # クリアできない場合に希望側へ差し替える. 既定 ``"preferred"`` で、 PFV 非対象 /
    # full_optimize 等の既存経路は不変.
    pool_origin: Literal["fixed", "preferred", "fixed_fallback_preferred"] = "preferred"


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


def haversine_minutes(distance_km: float, *, speed_kmh: float = TRAVEL_SPEED_KMH) -> int:
    """W41 v2 拡張 (移動時間の time 化): 直線距離 (km) から移動時間 (分) を概算する.

    Rules:
        - ``distance_km <= 0`` (同住所 / 数値誤差): 0 分
        - それ以外: ``distance_km / speed_kmh * 60`` を整数丸めし、最低 1 分.

    都市部の安全側仮定として ``TRAVEL_SPEED_KMH = 20`` km/h (信号・混雑考慮).

    Phase G-88 Step3: ``speed_kmh`` 引数で移動速度を注入可能にする. 既定は module
    定数 ``TRAVEL_SPEED_KMH`` (= 20) なので、config を渡さない既存呼出は挙動不変.
    最適化経路では ``config.travel_speed_kmh`` を渡す.
    """
    if distance_km <= 0:
        return 0
    return max(1, int(round(distance_km / speed_kmh * 60)))


def _address_bucket(lat: float, lng: float) -> tuple[float, float]:
    """H2/H3 で同住所判定するためのバケットキー."""
    lat_b = round(lat / SAME_ADDRESS_TOLERANCE) * SAME_ADDRESS_TOLERANCE
    lng_b = round(lng / SAME_ADDRESS_TOLERANCE) * SAME_ADDRESS_TOLERANCE
    return (lat_b, lng_b)


# ---------------------------------------------------------------------------
# Wave 1 #115 (旧 Fix D / CareFlow #103): 同時刻 2 名配置の境界防御.
#
# 旧仕様: 「異住所同時刻ペア」を検出して 422 で拒否.
# Wave 1 後 (本実装): 通常の「異住所同時刻ペア」は ``apply_travel_corrections``
# の ``_auto_shift_same_time_conflicts`` が後段で解消するため境界では拒否しない.
# 「物理不可能 (= auto_shift 不能) なケース」のみ raise する. 具体的には:
#   - 同枠に **座標 None / office 未解決の patient** が混入している場合.
#     auto_shift は haversine 距離で位置を決めるため、座標が無いと正しく動けず
#     データ不備として扱う.
#
# 経路 (拒否ロジックを残す箇所):
#   1. apply_individual_proposal — 提案 PFV と他患者既存 PFV を突合.
#   2. reset_visits_to_fixed — 監視用途で warning log のみ.
#   3. bulk_apply_week_only_visit_changes — visit 上書き境界.
# ---------------------------------------------------------------------------


class CrossAddressTimeConflictError(Exception):
    """同コース同時刻に **物理不可能 (auto_shift 不能)** な配置を検出した時のエラー.

    Wave 1 (#115) で意味変更: 旧来の「異住所同時刻 = 即拒否」ではなく、
    ``_detect_cross_address_time_conflicts`` が data integrity 不備 (座標 None /
    office 未解決) のみ拾うようになった結果、本エラーも「物理的に解消不能な配置」
    のみ raise されるようになった. 呼び出し側 (endpoint) で catch して 422 +
    構造化 detail に変換する.

    Attributes:
        conflicts: 衝突詳細のリスト. 各要素は ``office_id`` (str), ``weekday`` (int),
            ``start_time`` (str), ``patient_ids`` (list[str]), ``reason`` (str) を持つ.
    """

    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        self.conflicts = conflicts
        super().__init__(
            f"{len(conflicts)} 件の物理不可能な同時刻衝突を検出 (Wave 1: missing coordinates)"
        )


class PinnedVisitMovedError(Exception):
    """Phase G-21 T3-6: D&D で pinned PFV と異なる start_time の visit_plan が来た時のエラー.

    ``apply_week_only`` の境界検証で raise する. endpoint は 422 + warning 返却する.

    Attributes:
        violations: ``[{"patient_id": str, "weekday": int, "pfv_start": str,
            "plan_start": str, "patient_name": str | None}, ...]``.
    """

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        super().__init__(
            f"{len(violations)} 件の pinned PFV を D&D で動かそうとしました (Phase G-21 T3-6)"
        )


def _detect_cross_address_time_conflicts(
    items: list[Any],
    patients_by_id: dict[UUID, Patient],
    *,
    office_id_getter: Any = None,
    course_key_getter: Any = None,
    weekday_getter: Any = None,
    start_time_getter: Any = None,
    patient_id_getter: Any = None,
) -> list[dict[str, Any]]:
    """同 (office, weekday, course_key, start_time) で異住所な複数 patient を検出する.

    本 helper は ``PatientFixedVisit`` / ``Visit`` / V2VisitPlan dict などの
    異なる「時間枠を持つレコード」を共通インターフェースで検査する.

    Args:
        items: 検査対象のレコードリスト.
        patients_by_id: ``patient_id → Patient`` の辞書 (lat/lng 解決用).
        office_id_getter: ``item → UUID | None`` を返す callable. 既定は
            ``getattr(item, 'office_id', None)``. None を返した場合は
            patient.primary_office_id にフォールバックする.
        course_key_getter: ``item → Hashable`` を返す callable. 既定は
            ``course_template_id`` 属性 (= PatientFixedVisit / Visit ともに使える).
        weekday_getter: ``item → int`` を返す callable. 既定は ``weekday`` 属性.
        start_time_getter: ``item → time | str`` を返す callable. 既定は
            ``start_time`` 属性.
        patient_id_getter: ``item → UUID`` を返す callable. 既定は ``patient_id`` 属性.

    Returns:
        衝突 dict のリスト. 衝突なしなら空リスト. 各 dict は:
          ``office_id`` (str), ``weekday`` (int), ``course_key`` (str | None),
          ``start_time`` (str), ``patient_ids`` (list[str]).

    Notes:
        - 同住所 (= ``_address_bucket`` で同じバケットに入る) ペアは衝突としない.
        - lat / lng が None の patient は座標不明扱いで「住所不明」バケットに
          まとめる. 同じ「住所不明」患者だけのグループは衝突なし.
        - patient_id 重複 (= 同一患者が同枠に複数枠) は呼び出し側で別途検出する.
    """

    def _default_office(item: Any) -> UUID | None:
        return getattr(item, "office_id", None)

    def _default_course(item: Any) -> Any:
        return getattr(item, "course_template_id", None)

    def _default_weekday(item: Any) -> int:
        return int(item.weekday)

    def _default_start(item: Any) -> Any:
        return item.start_time

    def _default_patient_id(item: Any) -> UUID:
        return item.patient_id

    off_fn = office_id_getter or _default_office
    course_fn = course_key_getter or _default_course
    wd_fn = weekday_getter or _default_weekday
    st_fn = start_time_getter or _default_start
    pid_fn = patient_id_getter or _default_patient_id

    by_key: dict[tuple[Any, int, Any, Any], list[Any]] = defaultdict(list)
    for it in items:
        try:
            pid = pid_fn(it)
            wd = wd_fn(it)
            st = st_fn(it)
        except (AttributeError, TypeError, ValueError):
            continue
        patient = patients_by_id.get(pid)
        # office_id は item から優先, 無ければ patient.primary_office_id.
        office_id = off_fn(it)
        if office_id is None and patient is not None:
            office_id = patient.primary_office_id
        if office_id is None:
            continue
        course_k = course_fn(it)
        by_key[(office_id, wd, course_k, st)].append(it)

    conflicts: list[dict[str, Any]] = []
    for (office_id, wd, course_k, st), bucket in by_key.items():
        # 異 patient_id が 2 つ以上含まれるかを確認.
        pids: set[UUID] = set()
        for it in bucket:
            try:
                pids.add(pid_fn(it))
            except (AttributeError, TypeError):
                continue
        if len(pids) < 2:
            continue
        # Wave 1 (#115): 意味変更 — 通常の異住所同時刻ペアは
        # ``apply_travel_corrections`` の auto_shift が後段で解消するため、本 helper
        # では **物理不可能 (= auto_shift 不能) なケースのみ** conflict として残す.
        # 物理不可能ケース:
        #   - 同枠に **座標 None patient が混じっている** → auto_shift は距離計算で
        #     位置を決めるため、座標が無いと正しくシフトできない (= データ不備).
        # 同座標 (家族・施設) ペアは Wave 2 で正しく扱われるので除外、
        # 完全に異住所だが両者座標がある場合は Wave 1 の auto_shift が解消する.
        has_unknown_coord = False
        addr_buckets: set[Any] = set()
        for pid in pids:
            p = patients_by_id.get(pid)
            if p is None or p.lat is None or p.lng is None:
                has_unknown_coord = True
                continue
            addr_buckets.add(_address_bucket(float(p.lat), float(p.lng)))
        # 座標 None patient が混在する複数患者同枠 = データ不備.
        # それ以外 (全員座標あり) は Wave 1 auto_shift で解消可能と判断し conflict
        # としない (= 異住所だろうが同住所だろうが補正側に任せる).
        if not has_unknown_coord:
            continue
        # 注: 上の ``len(pids) < 2`` ガード (l.556) で patient 単独枠は既に弾いてあるため、
        # ここに到達した時点で常に「unknown patient + 他 patient (座標あり/なし問わず)」
        # の同枠 = 物理不可能ケースとなる. 旧 dead code の二重 ``len(pids) < 2`` チェック
        # は削除済み (Phase A reviewer MEDIUM #2).
        start_str = st.isoformat(timespec="minutes") if isinstance(st, time) else str(st)
        course_repr: str | None
        if course_k is None:
            course_repr = None
        else:
            course_repr = str(course_k)
        conflicts.append(
            {
                "office_id": str(office_id),
                "weekday": int(wd),
                "course_key": course_repr,
                "start_time": start_str,
                "patient_ids": sorted(str(p) for p in pids),
                "reason": "missing_coordinates",
            }
        )
    return conflicts


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


def _compute_preferred_time_deviation(
    *,
    actual_start: time,
    time_type: str | None,
    preferred_start: time | None,
    preferred_end: time | None,
) -> int:
    """Wave 4 (Phase C): time_type=固定 / 時間帯 patient の希望時刻からの乖離 (分単位).

    Rules:
        - ``time_type='固定'``: ``|actual_start - preferred_start|``.
        - ``time_type='時間帯'``:
            * ``actual_start < preferred_start`` → ``preferred_start - actual_start``
            * ``actual_start > preferred_end``   → ``actual_start - preferred_end``
            * 範囲内 (``preferred_start <= actual_start <= preferred_end``) → 0
        - ``time_type='午前'/'午後'/'終日'`` / None / preferred 不在 : 0 (対象外).

    Args:
        actual_start: 配置確定後の開始時刻.
        time_type: ``V2Visit.time_type``.
        preferred_start: 患者の希望開始時刻 (HH:MM パース後 time / None).
        preferred_end: 患者の希望終了時刻 (HH:MM パース後 time / None).

    Returns:
        乖離分数 (常に >= 0). 対象外 / 評価不能なら 0.
    """
    if time_type == "固定":
        if preferred_start is None:
            return 0
        actual_min = actual_start.hour * 60 + actual_start.minute
        preferred_min = preferred_start.hour * 60 + preferred_start.minute
        return abs(actual_min - preferred_min)
    if time_type == "時間帯":
        if preferred_start is None or preferred_end is None:
            return 0
        actual_min = actual_start.hour * 60 + actual_start.minute
        lower_min = preferred_start.hour * 60 + preferred_start.minute
        upper_min = preferred_end.hour * 60 + preferred_end.minute
        if actual_min < lower_min:
            return lower_min - actual_min
        if actual_min > upper_min:
            return actual_min - upper_min
        return 0
    # 午前 / 午後 / 終日 / None: ケアアラーム対象外.
    return 0


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


def _same_address_pair_members(
    busy: V2Visit,
    candidates: list[V2Visit],
    *,
    exclude_ids: frozenset[int] = frozenset(),
) -> list[V2Visit]:
    """Phase G-95/G-96: ``busy`` が同住所「既存ペア」の一員かを判定しペア相手を返す.

    同住所ペア (= ``_align_same_address_pair_to_same_time`` で 1 スタッフが連続訪問
    する最大 2 名) は本来ペア合算で 90 分占有するが、 既に placed 済 / auto_shift で
    de-align された場合 ``busy.end_time`` が実 service 長 (例: 35 分) のまま残る.

    ``busy`` と「ペア関係」にある同住所・別患者の visit を ``candidates`` から拾う.
    「ペア関係」= 次のいずれか (= 同時刻配置 / 端点連続配置):
      (a) 同 ``start_time`` (= align 済 / 既存同時刻ペア), または
      (b) 連続 (一方の ``end == 他方の start``; 同住所は travel 0 + buffer 0 で隙間
          なく並ぶため、 auto_shift で de-align された 2 人目は contiguous).
    「同建物だが別時刻 (間に隙間)」の単なる連続訪問は **ペアではない** ため除外する
    (= 90 分占有を誤適用しない).

    Phase G-96 (修正1): ``exclude_ids`` に挙げた ``id(visit)`` は候補から除外する.
    段 a (= プール投入の diff-add) では「いま動かそうとしている pool 提案 pv」自身が
    ``candidates`` (= group) に含まれるため、 相手 ``ov`` の占有終端計算で pv を ov の
    同住所ペア相手と誤認し、 ov が単独でも 90 分占有へ過大底上げされる事故があった.
    呼び出し側で ``exclude_ids={id(pv)}`` を渡すことで pv をペア候補から外す.

    read-only (in-memory; visit は一切書き換えない). 決定性は呼び出し側の入力順に従う.
    """
    busy_bucket = _address_bucket(busy.lat, busy.lng)
    return [
        ov
        for ov in candidates
        if ov is not busy
        and id(ov) not in exclude_ids
        and ov.patient_id != busy.patient_id
        and _address_bucket(ov.lat, ov.lng) == busy_bucket
        and (
            ov.start_time == busy.start_time  # (a) 同時刻ペア.
            or ov.start_time == busy.end_time  # (b) busy の直後に連続.
            or ov.end_time == busy.start_time  # (b) busy の直前に連続.
        )
    ]


def _same_address_pair_occupancy_end(
    busy: V2Visit,
    candidates: list[V2Visit],
    *,
    exclude_ids: frozenset[int] = frozenset(),
) -> time:
    """Phase G-95/G-96: ``busy`` の占有終端を同住所 2 名 90 分占有込みで返す.

    ``busy`` が同住所「既存ペア」の一員なら占有終端を
    ``max(busy.end_time, pair_anchor_start + SAME_ADDRESS_PAIR_MIN_OCCUPANCY)``
    に底上げする. ``pair_anchor_start`` = 当該ペアクラスタの最早 ``start_time``
    (= de-align 前のペア起点; auto_shift は 2 人目を後ろへずらすため 1 人目の
    start が anchor として残る). ペアでなければ ``busy.end_time`` をそのまま返す.

    Phase G-96 (修正1): ``exclude_ids`` は ``_same_address_pair_members`` へ素通しし、
    指定 ``id(visit)`` をペア候補から外す. 段 a で移動対象 pool 提案 pv 自身を
    相手 ov のペア相手と誤認しないようにするため.

    read-only (in-memory; visit は書き換えない).
    """
    members = _same_address_pair_members(busy, candidates, exclude_ids=exclude_ids)
    if not members:
        return busy.end_time
    pair_anchor_start = min([busy.start_time] + [ov.start_time for ov in members])
    pair_floor_end = _add_minutes(pair_anchor_start, SAME_ADDRESS_PAIR_MIN_OCCUPANCY)
    return max(busy.end_time, pair_floor_end)


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


def _is_in_lunch_break(
    start: time,
    end: time,
    *,
    window_start: time = LUNCH_EARLIEST_START,
    window_end: time = LUNCH_LATEST_END,
) -> bool:
    """H10: visit が「動的 lunch window (11:30-13:30)」と物理的に重なるか判定.

    Phase G-88 Step3: ``window_start`` / ``window_end`` 引数で昼休み取得時間帯を
    注入可能にする. 既定は module 定数 (``LUNCH_EARLIEST_START`` 11:30 /
    ``LUNCH_LATEST_END`` 13:30) なので、引数を渡さない既存呼出・テストは挙動不変.
    最適化経路では ``config.lunch_window_start`` / ``config.lunch_window_end`` を渡す.

    Phase E-3 改修 (2): フレキシブルランチ 3 段階 fallback (60→45→30 分) に
    合わせて緩和. ``LUNCH_DURATION_MIN`` (= 30 分) の最低 lunch でも避けられない
    場合のみ True を返す.

    Wave 3 で lunch がコース別動的になったため、コース確定前ステージ (Stage 1〜2)
    や API 境界では「visit が **どの最小 30 分 lunch 配置でも避けられない区間** に
    入っているか」を判定する.

    Lunch 配置の制約 (Phase E-3 仕様):
        - ``lunch_start`` ∈ [11:30, 13:00]  (= LUNCH_LATEST_END - LUNCH_DURATION_MIN)
        - ``lunch_end`` ∈ [12:00, 13:30]    (= LUNCH_EARLIEST_START + LUNCH_DURATION_MIN)
        - ``lunch_end - lunch_start`` ∈ [30 分, 60 分]

    Lunch が visit と重ならないためには以下のいずれか:
        - **AM 側回避**: lunch_end ≤ visit_start. 最小 30 分 lunch (11:30-12:00) で
          成立するには ``visit_start ≥ 12:00`` が必要.
        - **PM 側回避**: lunch_start ≥ visit_end. PM 側 30 分 lunch (13:00-13:30) で
          成立するには ``visit_end ≤ 13:00`` が必要.

    上記いずれも成立しない (= ``visit_start < 12:00`` かつ ``visit_end > 13:00``)
    場合に True (= 物理的に lunch を取れない) を返す.

    例 (Phase E-3 で挙動が変わる代表例):
        - 12:00-12:35 (35 分 visit, 12:00 開始): AM 側回避可 (start=12:00) → False.
          旧仕様では True (= 拒否) だった.
        - 12:10-12:45: AM 側 start=12:10 < 12:00 NG, PM 側 end=12:45 > 13:00 NG → True.

    早期 escape:
        - ``end <= 11:30`` (visit が lunch window より前): False
        - ``start >= 13:30`` (visit が lunch window より後): False
    """
    if end <= window_start:
        return False
    if start >= window_end:
        return False
    # AM 側回避: 11:30-12:00 (30 分 lunch) → visit_start >= 12:00 で OK.
    # PM 側回避: 13:00-13:30 (30 分 lunch) → visit_end <= 13:00 で OK.
    am_avoidable_visit_start = _add_minutes(window_start, LUNCH_DURATION_MIN)  # 12:00
    pm_avoidable_visit_end = _add_minutes(window_end, -LUNCH_DURATION_MIN)  # 13:00
    if start >= am_avoidable_visit_start:
        # AM 側 lunch (11:30-12:00 30 分) で重複回避可.
        return False
    if end <= pm_avoidable_visit_end:
        # PM 側 lunch (13:00-13:30 30 分) で重複回避可.
        return False
    return True


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _min_to_time(total_min: int) -> time:
    if total_min >= 24 * 60:
        return time(23, 59)
    if total_min < 0:
        return time(0, 0)
    return time(total_min // 60, total_min % 60)


def compute_lunch_window(
    visits_in_course: list[V2Visit],
    *,
    warnings: list[V2Warning] | None = None,
    weekday: int = -1,
    course_code: str | None = None,
    office_name: str = "",
    duration: int = LUNCH_DURATION_PREFERRED,
    window_start: time = LUNCH_EARLIEST_START,
    window_end: time = LUNCH_LATEST_END,
) -> tuple[time, time] | None:
    """Wave 3 (#WAVE3): コース内 visit リストから最適 lunch slot を動的に決定する.

    Phase E-3 改修 (2): フレキシブルランチ 3 段階 fallback (60→45→30 分).
    「11:30-13:30 の 2 時間枠内のどこかで必ず 45-60 分の休憩を確保。最悪、
    なんとなくスペース確保」(User 確定仕様) のため、60 分が取れなければ 45 分、
    45 分も取れなければ 30 分の最終 fallback を試す. 30 分採用時は warning 発火.

    Phase G-88 Step3 (設定注入):
        ``duration`` / ``window_start`` / ``window_end`` を引数化し、事業所別設定
        (``SchedulingConfig.lunch_duration_min`` / ``lunch_window_start`` /
        ``lunch_window_end``) を注入できるようにする. 既定は module 定数
        (``LUNCH_DURATION_PREFERRED`` (60) / ``LUNCH_EARLIEST_START`` (11:30) /
        ``LUNCH_LATEST_END`` (13:30)) なので、config を渡さない既存呼出・テストは
        挙動不変.

        **config 化するのは「標準 (= 最優先) の長さと取得時間帯」のみ**. 内部 fallback
        (45 分 / 最低 30 分 / 5 分刻み候補走査) は労基・人道的下限として **固定**
        のまま残す (``LUNCH_DURATION_FALLBACK`` / ``LUNCH_DURATION_MIN`` /
        ``LUNCH_CANDIDATE_STEP_MIN``). ただし fallback 長は標準長より短いものだけ
        を使う (例: 標準 30 分なら 45 分 fallback は不使用). 正午 (``NOON_HOUR``)
        中心の採用優先も固定.

    Algorithm:
      1. visit を ``start_time`` 昇順 sort し、占有区間 [start, end) のリストを作る.
      2. 候補 ``lunch_start`` を ``LUNCH_EARLIEST_START`` (11:30) から
         ``LUNCH_LATEST_START`` (12:30) まで ``LUNCH_CANDIDATE_STEP_MIN``
         (5 分) 刻みで走査する.
      3. 各候補で 60→45→30 分の順に空きをチェックし、見つかった duration の
         best 候補として保持する.
      4. 60 分が取れる候補があれば 12:00 中心に最も近いものを採用.
         無ければ 45 分 best、それも無ければ 30 分 best.
      5. すべての候補で 30 分も取れなければ ``None`` を返し warning を出す.
      6. 30 分 fallback 採用時は「30 分しか取れないコース」運用者通知の warning を出す.

    Args:
        visits_in_course: 同じコース (= 1 staff × 1 day) に属する visit のリスト.
        warnings: None 時は warning を出さない. リスト渡しなら
            30 分も取れない場合 + 30 分 fallback 採用時に ``V2Warning(type="general")``
            を append する.
        weekday: warning メッセージ用 (0=月..6=日 / -1 なら省略).
        course_code: warning メッセージ用 (None 可).
        office_name: warning メッセージ用 (空文字可).

    Returns:
        ``(lunch_start, lunch_end)``. lunch を取れない場合は ``None``.
    """
    # 占有区間を分単位に変換 (start_min, end_min). 同住所ペアの 2 人目は
    # end_time が aligned + pair 合算 になっている点に注意 (V2Visit docstring).
    occupied: list[tuple[int, int]] = []
    for v in visits_in_course:
        s = _time_to_min(v.start_time)
        e = _time_to_min(v.end_time)
        if e <= s:
            continue
        occupied.append((s, e))
    occupied.sort()

    # Phase G-88 Step3: window/duration を引数 (= config 値 or module 定数既定) から
    # 解決する. 既定 (11:30 / 13:30 / 60) では従来の固定値と一致する.
    earliest_min = _time_to_min(window_start)  # 既定 11:30 = 690
    latest_end_min = _time_to_min(window_end)  # 既定 13:30 = 810
    # 標準 (= 最優先) 長の cand_start 上限 (= window_end - duration).
    # 既定では 12:30 (= 13:30 - 60) で旧 LUNCH_LATEST_START と一致する.
    latest_start_min = latest_end_min - duration  # 既定 12:30 = 750
    # Phase E-3 改修 (2): 30 分 fallback では cand_start を 13:00 (=
    # window_end - LUNCH_DURATION_MIN) まで広げて探索する.
    # 30 分 lunch を 13:00-13:30 に配置するケース (= PM 側ギリギリ) を許容するため.
    latest_start_min_30 = latest_end_min - LUNCH_DURATION_MIN  # 13:00 = 780
    noon_min = NOON_HOUR * 60  # 12:00 = 720 (午前/午後境界は固定)
    # 標準長より短い fallback 長のみ採用する (= 標準 30 分時に 45 分 fallback を
    # 使わない). 既定 (標準 60) では [45, 30] で従来と一致する.
    _fallback_durations = [d for d in (LUNCH_DURATION_FALLBACK, LUNCH_DURATION_MIN) if d < duration]
    _dur_45 = LUNCH_DURATION_FALLBACK if LUNCH_DURATION_FALLBACK in _fallback_durations else None
    _dur_30 = LUNCH_DURATION_MIN if LUNCH_DURATION_MIN in _fallback_durations else None

    def _has_free_window(slot_start: int, slot_end: int) -> bool:
        """[slot_start, slot_end) が占有区間と一切重ならないか."""
        if slot_end > latest_end_min:
            return False
        for s, e in occupied:
            # 重なり条件: slot_start < e AND s < slot_end
            if slot_start < e and s < slot_end:
                return False
        return True

    # best_60 = 標準長 (= duration; 既定 60) の最良枠. best_45 / best_30 は固定
    # fallback 長 (45 / 30 分) の最良枠. 変数名は既定値由来で歴史的に 60/45/30.
    best_60: tuple[int, int] | None = None
    best_60_dist: int = 10**9
    best_45: tuple[int, int] | None = None
    best_45_dist: int = 10**9
    best_30: tuple[int, int] | None = None
    best_30_dist: int = 10**9

    # Phase E-3 改修 (2): 3 段階 fallback. 各 cand_start で 標準長→45→30 を順に試す.
    # 標準長が取れた候補で 45/30 は試さない (= 標準長優先). 標準長が取れない候補のみ
    # 45 を試し、45 も取れなければ 30 を試す.
    # Phase G-88 Step3: 標準長 (= duration) は config 値. fallback 長 (45/30) は固定.
    #   標準長より長い fallback (= duration < 45 のときの 45) は使わない
    #   (``_fallback_durations`` で除外済み).
    # cand_start の上限は duration ごとに異なる:
    #   - 標準長 lunch: cand_start <= latest_start_min (= window_end - duration).
    #   - 45 分 lunch: cand_start <= window_end - 45.
    #   - 30 分 lunch: cand_start <= window_end - 30.
    # 統一して latest_start_min_30 (= window_end - 30) まで走査する.
    for cand_start in range(earliest_min, latest_start_min_30 + 1, LUNCH_CANDIDATE_STEP_MIN):
        end_pref = cand_start + duration
        if (
            cand_start <= latest_start_min
            and end_pref <= latest_end_min
            and _has_free_window(cand_start, end_pref)
        ):
            dist = abs(cand_start - noon_min)
            if dist < best_60_dist or (
                dist == best_60_dist and best_60 is not None and cand_start < best_60[0]
            ):
                best_60 = (cand_start, end_pref)
                best_60_dist = dist
            continue
        if _dur_45 is not None:
            end_45 = cand_start + _dur_45
            if end_45 <= latest_end_min and _has_free_window(cand_start, end_45):
                dist = abs(cand_start - noon_min)
                if dist < best_45_dist or (
                    dist == best_45_dist and best_45 is not None and cand_start < best_45[0]
                ):
                    best_45 = (cand_start, end_45)
                    best_45_dist = dist
                continue
        # 45 分も取れない候補のみ 30 分 (= LUNCH_DURATION_MIN) を試す.
        if _dur_30 is not None:
            end_30 = cand_start + _dur_30
            if end_30 <= latest_end_min and _has_free_window(cand_start, end_30):
                dist = abs(cand_start - noon_min)
                if dist < best_30_dist or (
                    dist == best_30_dist and best_30 is not None and cand_start < best_30[0]
                ):
                    best_30 = (cand_start, end_30)
                    best_30_dist = dist

    if best_60 is not None:
        return (_min_to_time(best_60[0]), _min_to_time(best_60[1]))
    if best_45 is not None:
        return (_min_to_time(best_45[0]), _min_to_time(best_45[1]))
    if best_30 is not None:
        # Phase E-3 改修 (2): 30 分 fallback 採用時は warning 発火.
        # 「45 分も取れず 30 分しか確保できないコース」を運用者に通知する.
        if warnings is not None:
            wd_jp = _weekday_jp(weekday) if weekday >= 0 else ""
            prefix = " ".join(filter(None, [office_name, course_code or "", wd_jp])).strip()
            if prefix:
                prefix = f"{prefix}: "
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"{prefix}スケジュールが密集しているため "
                        f"昼休憩を {LUNCH_DURATION_MIN} 分しか確保できません "
                        f"({_fmt_hhmm(_min_to_time(best_30[0]))}-"
                        f"{_fmt_hhmm(_min_to_time(best_30[1]))}; 運用者要確認)"
                    ),
                    weekday=weekday if weekday >= 0 else None,
                    actionable=True,
                )
            )
        return (_min_to_time(best_30[0]), _min_to_time(best_30[1]))

    # 30 分も取れない: warning + None.
    if warnings is not None:
        wd_jp = _weekday_jp(weekday) if weekday >= 0 else ""
        prefix = " ".join(filter(None, [office_name, course_code or "", wd_jp])).strip()
        if prefix:
            prefix = f"{prefix}: "
        warnings.append(
            V2Warning(
                type="general",
                message=(
                    f"{prefix}スケジュールが密集しているため "
                    f"昼休憩 (最低 {LUNCH_DURATION_MIN} 分) を確保できません "
                    "(運用者要確認)"
                ),
                weekday=weekday if weekday >= 0 else None,
                actionable=True,
            )
        )
    return None


def _lunch_window_overlaps(start: time, end: time, lunch: tuple[time, time] | None) -> bool:
    """visit 区間 [start, end) が lunch slot と重なるか判定 (lunch=None なら常に False)."""
    if lunch is None:
        return False
    ls, le = lunch
    if start >= le:
        return False
    if end <= ls:
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


async def _load_active_patients_via_sub_office(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
    excluded_patient_ids: set[UUID],
) -> tuple[dict[UUID, Patient], dict[UUID, UUID]]:
    """Phase E-5 (項目 ⑥B): PFV.sub_office_id 経由でフォロー対象患者を引き込む.

    主担当拠点 (Patient.primary_office_id) が ``office_ids`` に含まれていなくても、
    ``PatientFixedVisit.sub_office_id`` が ``office_ids`` に含まれる patient を
    pool 候補化するためにロードする.

    Args:
        office_ids: 対象拠点 ID リスト.
        excluded_patient_ids: 既に ``_load_active_patients`` で取得済みの patient_id
            集合. ここに含まれている患者は重複ロードしない (= 主担当拠点が既に
            scope 内なので別経路で扱われる).

    Returns:
        (extra_patients, sub_office_by_patient_id) tuple.
        - extra_patients: PFV.sub_office_id 経由でのみ scope に入る patient のマップ.
        - sub_office_by_patient_id: 対象 patient ごとの sub_office_id (代表 1 件).
          PFV 単位ではなく patient 単位の代表値を返す (1 patient に複数 sub_office を
          持たせる運用は今回想定しない).
    """
    if not office_ids:
        return {}, {}
    pfv_rows = (
        await db.execute(
            select(PatientFixedVisit.patient_id, PatientFixedVisit.sub_office_id)
            .where(
                PatientFixedVisit.sub_office_id.in_(office_ids),
                PatientFixedVisit.mode == "normal",
            )
            .distinct()
        )
    ).all()
    if not pfv_rows:
        return {}, {}
    sub_office_by_patient: dict[UUID, UUID] = {}
    for pid, sub_oid in pfv_rows:
        if pid in excluded_patient_ids:
            continue
        if pid not in sub_office_by_patient:
            sub_office_by_patient[pid] = sub_oid
    if not sub_office_by_patient:
        return {}, {}
    patient_rows = await db.scalars(
        select(Patient).where(
            Patient.id.in_(list(sub_office_by_patient.keys())),
            Patient.status == "active",
            Patient.deleted_at.is_(None),
        )
    )
    extras = {p.id: p for p in patient_rows.all()}
    # 万が一 patient が active でない / deleted の場合は sub_office マップからも除外
    sub_office_by_patient = {
        pid: oid for pid, oid in sub_office_by_patient.items() if pid in extras
    }
    return extras, sub_office_by_patient


async def _load_visit_delete_target_patient_ids(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
) -> set[UUID]:
    """visit 削除対象とすべき patient_id 集合を返す (status 不問).

    CareFlow 本番バグ (Bug A) 修正:
        ``reset_visits_to_fixed`` の step1 削除は ``_load_active_patients`` の
        active 患者のみを対象にしていたため、 患者が一時休止 (status='inactive')
        / suspended / pending 等に変わると、 過去に再生成系 (source=auto / reset_v2
        等, status='planned') で作られた旧 visit が削除されず週ビューに残り続けた
        (= ゴースト). 削除対象 patient 範囲を「status 不問 + 対象 office 範囲」に
        広げ、 非稼働患者の旧 visit も掃除する. (再生成 step2 は従来通り active
        患者の PFV のみ → 非稼働患者は消えて再生成されない = 正しい挙動.)

    office 範囲の解釈は **再生成スコープと対称** にする (誤削除防止):
        - ``Patient.primary_office_id`` ∈ office_ids のみ

        再生成 step2 (``_load_active_patients`` / PFV クエリ) は primary_office_id
        基準で患者を集める. もし削除側に ``PatientFixedVisit.sub_office_id`` ∈
        office_ids の arm を残すと、 「主拠点が office_ids 範囲外・sub_office PFV が
        範囲内」の cross-office 患者 (例: 都賀 primary / INAGE serving) が、 単一
        office reset の step1 で削除されながら step2 の再生成スコープに入らず、
        消失 (visit が削除されっぱなし) する. 削除対象 = 再生成対象 = primary-office
        範囲に揃えることで対称性を保ち、 cross-office 患者の誤削除を防ぐ.

    source / status による保護フィルタ (= manual / completed / cancelled 保護) は
    呼び出し側の DELETE SQL 側で従来通り適用するため、 本 helper は patient 範囲
    のみを担う.
    """
    if not office_ids:
        return set()
    primary_rows = await db.scalars(
        select(Patient.id).where(
            Patient.deleted_at.is_(None),
            Patient.primary_office_id.in_(office_ids),
        )
    )
    return set(primary_rows.all())


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


async def _load_g21_enabled_offices(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
) -> set[UUID]:
    """Phase G-21: ``g21_new_algorithm`` feature flag が enabled な拠点集合を返す.

    canary 切替: ``OfficeFeatureFlag.enabled_at IS NOT NULL`` の office のみ新ロジック
    (pinned/非 pinned 2 経路 + 4 経路 union before) を使う.
    enabled_at IS NULL は「未有効化」として旧経路を維持.
    """
    if not office_ids:
        return set()
    rows = await db.scalars(
        select(OfficeFeatureFlag.office_id).where(
            OfficeFeatureFlag.office_id.in_(office_ids),
            OfficeFeatureFlag.feature_key == G21_NEW_ALGORITHM_FEATURE_KEY,
            OfficeFeatureFlag.enabled_at.is_not(None),
        )
    )
    return set(rows.all())


def _coerce_operating_weekdays(raw: object) -> set[int]:
    """``Office.operating_weekdays`` (JSONB) を ``set[int]`` (0..6) に正規化する.

    不正値 (= list でない / 要素が int でない / 範囲外 / 重複) を含む場合は
    デフォルト (= 月-土 = {0..5}) にフォールバックする. 後方互換のため、
    本番運用で万一壊れたデータがあってもパイプライン全停止を防ぐ.
    """
    if not isinstance(raw, list) or not raw:
        return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    out: set[int] = set()
    for v in raw:
        # bool は int subclass だが weekday としては不適切.
        if isinstance(v, bool) or not isinstance(v, int):
            return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
        if v < 0 or v > 6:
            return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
        out.add(v)
    if not out:
        return set(DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    return out


def _emit_office_closed_warning(
    *,
    warnings: list[V2Warning] | None,
    closed_warned: set[tuple[UUID, int]],
    patient_id: UUID,
    patient_name: str | None,
    weekday: int,
    office_id: UUID,
    office_name_by_id: dict[UUID, str] | None,
) -> None:
    """Phase G-45: 拠点休業日 skip 時の構造化警告を emit する (= 重複防止 helper).

    同 ``(patient_id, weekday)`` で複数の経路 (= PFV / weekly / orphan etc) が
    重複して emit しないよう、 ``closed_warned`` set で dedupe する.
    ``warnings`` が None の場合は何もしない.
    """
    if warnings is None:
        return
    key = (patient_id, weekday)
    if key in closed_warned:
        return
    closed_warned.add(key)
    name = patient_name or "不明"
    office_name = (
        (office_name_by_id or {}).get(office_id) if office_name_by_id else None
    ) or "拠点"
    msg = (
        f"{_weekday_jp(weekday)}: 拠点 {office_name} は休業日のため "
        f"{name} 様の visit をスケジュールしません. "
        "サブ拠点で受ける場合は患者マスタを編集してください"
    )
    warnings.append(
        V2Warning(
            type="office_closed",
            message=msg,
            weekday=weekday,
            actionable=True,
            patient_id=patient_id,
            patient_name=patient_name,
            affected_patient_ids=[patient_id],
        )
    )


async def _load_office_operating_weekdays(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
) -> dict[UUID, set[int]]:
    """Phase G-45: 拠点ごとの稼働曜日集合を返す.

    DB の ``offices.operating_weekdays`` カラム (JSONB int 配列) を読み出し、
    ``{office_id: {weekday, ...}}`` の dict にする. NULL / 不正値はデフォルト
    (= 月-土 = {0..5}) にフォールバックする.

    パイプライン (Stage 1) で V2Visit 生成時に休業曜日を skip するために使用する.
    呼び出し側 (= ``build_visits_for_pool`` / ``_load_before_visits_*`` /
    ``reset_visits_to_fixed``) は ``(office_id, weekday)`` 単位で
    ``weekday in op_weekdays_by_office[office_id]`` をチェックする.
    """
    if not office_ids:
        return {}
    rows = (
        await db.execute(
            select(Office.id, Office.operating_weekdays).where(
                Office.id.in_(office_ids),
                Office.deleted_at.is_(None),
            )
        )
    ).all()
    out: dict[UUID, set[int]] = {}
    for oid, raw in rows:
        out[oid] = _coerce_operating_weekdays(raw)
    return out


async def _load_same_address_pair_modes(
    db: AsyncSession,
    *,
    patient_ids: list[UUID],
) -> dict[tuple[UUID, UUID], str]:
    """Phase G-21 T3-3: ``PatientSameAddressLink`` を ``(a, b) -> pair_mode`` map で返す.

    キーは常に ``patient_a_id < patient_b_id`` で正規化済 (DB の CHECK 制約).
    ``preferred`` は DB 行を持たない運用なので、本 map に存在しない pair は
    暗黙的に ``preferred`` として扱う.
    """
    if not patient_ids:
        return {}
    rows = (
        await db.execute(
            select(
                PatientSameAddressLink.patient_a_id,
                PatientSameAddressLink.patient_b_id,
                PatientSameAddressLink.pair_mode,
            ).where(
                PatientSameAddressLink.patient_a_id.in_(patient_ids),
                PatientSameAddressLink.patient_b_id.in_(patient_ids),
            )
        )
    ).all()
    out: dict[tuple[UUID, UUID], str] = {}
    for a, b, mode in rows:
        out[(a, b)] = mode
    return out


def _extract_weekly_entries(
    patient: Patient,
    *,
    config: SchedulingConfig | None = None,
) -> list[tuple[int, time, int, str | None, str | None, str | None]]:
    """patient.weekly_pattern から ``(weekday, start_time, service_minutes,
    time_type, preferred_start_str, preferred_end_str)`` を取り出す.

    リスト形式 (`entries: [{weekday, preferred_start, ...}]`) と
    サマリ形式 (`preferred_weekdays + preferred_start`) の両方をサポート.

    W41 v2 (UI 時間詳細表示): ``preferred_start`` / ``preferred_end`` の元文字列も
    そのまま返して V2Visit に積む.

    Phase G-88 Step3: ``preferred_start`` 不在時の仮開始時刻を ``config.business_start``
    で注入可能にする. ``config=None`` は module 定数 ``AM_BLOCK_START`` (09:30) を
    使い挙動不変.
    """
    # Phase G-88 Step3: 仮開始時刻 (preferred_start 不在時) を config 化.
    _business_start = config.business_start if config is not None else AM_BLOCK_START
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
                # Phase E-3 改修 (1): デフォルト service_minutes を 30 → 35 に変更.
                # 患者マスタに service_minutes が未設定の場合、新規追加患者でも 35 分
                # 訪問が標準となる. 既存 DB レコードの 30 分は別途 bulk update SQL で対応.
                sm = int(sm_value) if isinstance(sm_value, int) and sm_value > 0 else 35
            if st is None:
                # 時刻なしでも午前/午後判定はできるが、提案では仮 9:30 開始にする.
                st = _business_start
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
    # Phase E-3 改修 (1): デフォルト service_minutes を 30 → 35 に変更 (サマリ形式).
    base_sm = int(base_sm_raw) if isinstance(base_sm_raw, int) and base_sm_raw > 0 else 35
    if isinstance(weekdays_raw, list):
        for wd_raw in weekdays_raw:
            wd = _resolve_weekday(wd_raw)
            if wd is None:
                continue
            st = base_start if base_start is not None else _business_start
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


def _g93_desired_weekdays(
    patient: Patient,
    fixed_rows: list[PatientFixedVisit] | None,
    *,
    config: SchedulingConfig | None = None,
) -> set[int]:
    """Phase G-93 (部分不足プール): 患者の「希望週内スロット曜日」集合を返す.

    フロント ``CourseDayTablePanel.tsx`` の poolPatients (= 希望回数 > 実績) 判定と
    方向性を合わせるため、 以下 2 ソースの和集合 (曜日粒度) を取る. ただし
    「配置済み (= 実績)」の status 集合はフロントと BE で意図的に揃えていない:
    BE 側では status ∈ {planned, in_progress, completed} を配置済みとみなし、
    cancelled は患者が実際には訪問されておらず再訪問が必要なため未配置 (=
    再提案対象) として除外する (詳細は ``run_auto_allocation`` の placed_statuses
    定義を参照). フロントは任意 status をカウントするが、 cancelled の再提案要否は
    BE 判定を正とする:
      - PFV (mode='normal', slot_index=0) の weekday — 固定枠.
      - weekly_pattern の preferred (entries 形式 / サマリ形式) の weekday — 希望.

    本集合は「この患者が 1 週間で訪問を希望している曜日」の上限であり、
    今週すでに配置済みの visit でカバーされていない曜日を差し引くことで
    「不足している曜日 (= 部分不足)」を判定する材料となる. 時刻情報は持たず、
    曜日のみを返す (不足スロットの時刻展開は build_visits_for_pool 側で行う).
    """
    wds: set[int] = set()
    for wd, _st, _sm in _extract_fixed_visits_for_patient(fixed_rows or []):
        wds.add(wd)
    for wd, _st, _sm, _tt, _ps, _pe in _extract_weekly_entries(patient, config=config):
        wds.add(wd)
    return wds


def _g94_desired_count(
    patient: Patient,
    fixed_rows: list[PatientFixedVisit] | None,
    *,
    config: SchedulingConfig | None = None,
) -> int | None:
    """Phase G-94 (修正1 過剰提案): 患者の「今週訪問を希望している回数」を返す.

    G-93 の partial_short 判定は曜日ベース (希望曜日のうち未配置があれば不足) で
    あるため、 固定枠曜日と weekly 希望曜日がズレた患者 (例: 固定 水木金 / 希望 火)
    は、 希望回数 (frequency_per_week) を実 visit 件数で満たしていても「希望曜日
    (火) が未配置」と判定され過剰提案されてしまう. これを防ぐため、 曜日判定の
    前段で回数充足チェックに使う希望回数を求める.

    決定順:
      1. ``weekly_pattern.frequency_per_week`` (1〜7 の int) があればそれを採用
         (= 希望回数の権威ソース. 小宮啓子 / 小湊愛莉 はこれが設定される).
      2. 無ければ「希望週内スロット曜日 (PFV ∪ weekly preferred) の異曜日数」に
         フォールバックする. PFV スロット数のみだと preferred 曜日が多い患者
         (植田弥生: PFV 2 / 希望週3) を過小評価し、 本当に不足している患者を
         誤って除外してしまうため、 両ソースの和集合曜日数を採る (= 最も寛容な
         見積りで、 真に不足な患者を取りこぼさない).
      3. どちらも得られない (frequency 未設定 かつ 希望曜日 0) なら ``None`` を
         返し、 呼び出し側は回数チェックを skip して従来の曜日判定に委ねる.

    Returns:
        希望回数 (>= 1) または ``None`` (回数を確定できない場合).
    """
    pattern = patient.weekly_pattern
    if isinstance(pattern, dict):
        raw = pattern.get("frequency_per_week")
        if isinstance(raw, int) and 1 <= raw <= 7:
            return raw
    desired_wds = _g93_desired_weekdays(patient, fixed_rows, config=config)
    if desired_wds:
        return len(desired_wds)
    return None


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
    ct_office_by_id: dict[UUID, UUID] | None = None,
    sub_office_scope: set[UUID] | None = None,
    op_weekdays_by_office: dict[UUID, set[int]] | None = None,
    office_name_by_id: dict[UUID, str] | None = None,
    warnings: list[V2Warning] | None = None,
    config: SchedulingConfig | None = None,
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

    Phase G-33: ``ct_office_by_id`` (= course_template_id → office_id の map) が
    渡された場合、 PFV.course_template_id を持つ V2Visit の ``office_id`` を
    その template の ``office_id`` に差し替える (= cross-office PFV 対応). 例えば
    patient.primary_office_id = 稲毛 だが PFV.course_template_id が都賀A を
    指す場合、 V2Visit.office_id を都賀にする. これにより After (= Stage 4 以降)
    の Course グループが (template_office, weekday, course_code) で集計され、
    Before 側 (Phase G-24 で適用済) と同じ規約になる. ``sub_office_scope`` 側の
    差し替え (PFV.sub_office_id) が優先で、 cross-office 差し替えはその次.
    map が None または該当 entry 無しなら patient.primary_office_id を使う (=
    既存挙動). G-21 経路 (= build_visits_for_pool_v2) は本変更対象外.

    Phase E-5 (項目 ⑥B): ``sub_office_scope`` が渡された場合、fixed source 分岐で
    PFV.sub_office_id が set 内に含まれる行は V2Visit.office_id を sub_office_id に
    差し替える (= サブ拠点経由のフォロー配置を pool 候補化). 自動算出本体 (mode 2 /
    full_optimize) は ``sub_office_scope=None`` で呼ぶため挙動は不変.

    Phase G-45 (拠点稼働曜日): ``op_weekdays_by_office`` が渡された場合、
    最終 ``office_id_eff`` (= sub_office / cross-office 差し替え後の office) が
    当該 weekday に休業の場合 V2Visit を emit せず skip する.
    同 ``(patient_id, weekday)`` で重複しないよう ``warnings`` に 1 件のみ emit
    (= ``V2Warning(type="office_closed")``). ``op_weekdays_by_office=None`` は
    旧挙動互換 (= skip しない).
    """
    overlay = pending_overlay or {}
    sub_scope = sub_office_scope or set()
    visits: list[V2Visit] = []
    # Phase G-45: 拠点休業日 skip の重複 emit 抑止. (patient_id, weekday) ごと 1 件.
    closed_warned: set[tuple[UUID, int]] = set()
    for patient in patients:
        if patient.lat is None or patient.lng is None or patient.primary_office_id is None:
            continue
        addr = patient.address
        area = _extract_area_label(addr)
        sex_r = patient.sex_restriction
        # W41 v2 拡張 (二人組訪問): patient.requires_multiple_staff を per-visit に
        # 流す. 旧 DB 状態 (フィールド存在しない場合) は False にフォールバック.
        req_multi = bool(getattr(patient, "requires_multiple_staff", False) or False)
        # Phase G-30: weekday -> pinned PFV (slot_index=0, mode='normal',
        # is_pinned=True) のマップを患者ごとに構築. legacy 経路 (= G-21 OFF) でも
        # ``is_pinned=True`` を V2Visit に流して、 後段
        # ``_apply_corrections_to_visits`` の pinned fence (= snapshot
        # post-restore) で時刻が動かないようにする. weekly_pattern 分岐でも参照
        # するため if 文の外で構築する.
        pinned_pfv_by_wd: dict[int, PatientFixedVisit] = {}
        if fixed_by_patient is not None:
            for _row in fixed_by_patient.get(patient.id) or []:
                if _row.mode != "normal" or _row.slot_index != 0:
                    continue
                if _row.is_pinned:
                    pinned_pfv_by_wd[_row.weekday] = _row
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
            # Phase E-5: weekday -> office_id 差し替えマップ.
            # PFV.sub_office_id が sub_scope に含まれていれば、その weekday の
            # V2Visit.office_id を sub_office_id に差し替える.
            wd_to_office_id: dict[int, UUID] = {}
            if sub_scope:
                for _row in fixed_rows:
                    if _row.sub_office_id is not None and _row.sub_office_id in sub_scope:
                        wd_to_office_id[_row.weekday] = _row.sub_office_id
            # Phase G-33: cross-office PFV 対応. PFV.course_template_id が
            # patient.primary_office_id と異なる office を指す場合、 その weekday
            # の V2Visit.office_id を template の office_id に差し替える. Before 側
            # (Phase G-24) と同じ規約を After 側にも適用する. sub_office_id 差し替え
            # (Phase E-5) は precedence が上 (= cross-office より優先).
            wd_to_course_office_id: dict[int, UUID] = {}
            if ct_office_by_id:
                for _row in fixed_rows:
                    if _row.course_template_id is not None:
                        _co = ct_office_by_id.get(_row.course_template_id)
                        if _co is not None:
                            wd_to_course_office_id[_row.weekday] = _co
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
                    # Phase E-5 / G-33: office_id 差し替え (該当 weekday のみ).
                    # 優先順位: sub_office_id (Phase E-5) > course_template.office_id
                    # (Phase G-33 cross-office PFV) > patient.primary_office_id.
                    if wd in wd_to_office_id:
                        office_id_eff = wd_to_office_id[wd]
                    elif wd in wd_to_course_office_id:
                        office_id_eff = wd_to_course_office_id[wd]
                    else:
                        office_id_eff = patient.primary_office_id
                    # Phase G-30: 該当 weekday に pinned PFV があれば
                    # ``is_pinned=True``. legacy 経路でも pinned visit の時刻が
                    # ``apply_travel_corrections`` で動かないようにする.
                    is_pinned_eff = wd in pinned_pfv_by_wd
                    # Phase G-45: 最終 office_id_eff (sub_office / cross-office 差し替え後)
                    # が当該 weekday に休業の場合は emit せず skip + warning.
                    if op_weekdays_by_office is not None:
                        _op_wd = op_weekdays_by_office.get(office_id_eff)
                        if _op_wd is not None and wd not in _op_wd:
                            _emit_office_closed_warning(
                                warnings=warnings,
                                closed_warned=closed_warned,
                                patient_id=patient.id,
                                patient_name=patient.name,
                                weekday=wd,
                                office_id=office_id_eff,
                                office_name_by_id=office_name_by_id,
                            )
                            continue
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
                            office_id=office_id_eff,
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
                            is_pinned=is_pinned_eff,
                        )
                    )
        if not used_fixed:
            entries = _extract_weekly_entries(patient, config=config)
            # Phase G-30.1 HIGH-1: 同 weekday に複数 entry (= AM/PM split 構成)
            # + pinned PFV があると、 同じ pinned visit が複数回 emit される.
            # 既に pinned visit を emit した weekday を記録し、 同 weekday の
            # 2 件目以降の entry を skip する.
            emitted_pinned_wds: set[int] = set()
            for wd, st, sm, tt, ps_str, pe_str in entries:
                # Phase G-30.1: weekly_pattern 由来でも、 同 (patient_id, weekday) に
                # pinned PFV があれば PFV ベースに切替える (= start_time /
                # duration / time_type を PFV から取る). G-30 で is_pinned=True の
                # propagate は実装済だが、 start_time が weekly_pattern.preferred_start
                # (例 09:00) のままだと PFV.start_time (例 09:30) と divergent.
                # ``apply_travel_corrections`` の post-restore は snapshot 時点の
                # 値に戻すので、 snapshot 時点で既に 09:00 になっていたら 09:00 に
                # 復元される (= PFV の 09:30 に戻らない). 対策として weekly_pattern
                # entry は完全に skip し、 PFV ベース 1 件のみ emit する.
                # overlay (pending edit) があれば overlay 値が PFV 値より優先される
                # (= 既存 fixed-source 分岐 / G-21 と同規約).
                pinned_pfv = pinned_pfv_by_wd.get(wd)
                if pinned_pfv is not None:
                    if wd in emitted_pinned_wds:
                        # Phase G-30.1 HIGH-1: 同 weekday に複数 entry がある場合、
                        # pinned visit は 1 件しか emit しない (重複防止).
                        continue
                    emitted_pinned_wds.add(wd)
                    ov = overlay.get((patient.id, wd))
                    if ov is not None:
                        st_eff = ov.new_start
                        sm_eff = _compute_overlay_duration(
                            ov, existing_duration=pinned_pfv.duration_min
                        )
                        tt_eff = ov.new_time_type or "固定"
                        ps_eff = ov.new_start_str
                        pe_eff = ov.new_end_str
                    else:
                        st_eff = pinned_pfv.start_time
                        sm_eff = pinned_pfv.duration_min
                        # PFV.time_type カラムは存在しないため固定文字列 "固定" を
                        # セット (= fixed-source 分岐と同じ規約).
                        tt_eff = "固定"
                        ps_eff = _fmt_hhmm(pinned_pfv.start_time)
                        pe_eff = None
                    end_t = _add_minutes(st_eff, sm_eff)
                    am_pm = determine_am_pm(time_type=tt_eff, preferred_start=st_eff)
                    # Phase G-31: PFV.course_template_id を course_code に流して
                    # Stage 4 振り分けで別 course に動かないよう fence する.
                    # G-30.1 で時刻 (start_time) は pinned 固定したが、course_code
                    # を未指定 (None) のまま emit していたため Stage 4 が別 course
                    # にアサインし、 BEFORE A → AFTER M 等 course が動く事象が
                    # VPS で再現していた. fixed-source 分岐 (L1716-1721) と同じ
                    # 規約で course_code_by_template_id から引き当てる.
                    cc_eff = (
                        course_code_by_template_id.get(pinned_pfv.course_template_id)
                        if course_code_by_template_id is not None
                        and pinned_pfv.course_template_id is not None
                        else None
                    )
                    # Phase G-33: cross-office pinned PFV 対応. PFV.course_template_id
                    # が patient.primary_office_id と異なる office を指す場合、
                    # V2Visit.office_id を template の office_id に差し替える.
                    # Before 側 (Phase G-24) と同じ規約. weekly_pattern 分岐 pinned
                    # emit は sub_office_scope 経路を使わないため、 cross-office
                    # 差し替えのみで OK.
                    pinned_course_office_id = (
                        ct_office_by_id.get(pinned_pfv.course_template_id)
                        if ct_office_by_id is not None and pinned_pfv.course_template_id is not None
                        else None
                    )
                    office_id_eff = pinned_course_office_id or patient.primary_office_id
                    # Phase G-45: 拠点休業日 skip (weekly_pattern + pinned PFV 経路).
                    if op_weekdays_by_office is not None:
                        _op_wd = op_weekdays_by_office.get(office_id_eff)
                        if _op_wd is not None and wd not in _op_wd:
                            _emit_office_closed_warning(
                                warnings=warnings,
                                closed_warned=closed_warned,
                                patient_id=patient.id,
                                patient_name=patient.name,
                                weekday=wd,
                                office_id=office_id_eff,
                                office_name_by_id=office_name_by_id,
                            )
                            continue
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
                            office_id=office_id_eff,
                            am_pm=am_pm,
                            source_kind="pool",
                            course_code=cc_eff,
                            address=addr,
                            area_label=area,
                            time_type=tt_eff,
                            sex_restriction=sex_r,
                            preferred_start=ps_eff,
                            preferred_end=pe_eff,
                            requires_multiple_staff=req_multi,
                            is_pinned=True,
                        )
                    )
                    continue
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
                # Phase G-45: 拠点休業日 skip (weekly_pattern 非 pinned 経路).
                # 非 pinned weekly entry は patient.primary_office_id を office として使う.
                if op_weekdays_by_office is not None:
                    _op_wd = op_weekdays_by_office.get(patient.primary_office_id)
                    if _op_wd is not None and wd not in _op_wd:
                        _emit_office_closed_warning(
                            warnings=warnings,
                            closed_warned=closed_warned,
                            patient_id=patient.id,
                            patient_name=patient.name,
                            weekday=wd,
                            office_id=patient.primary_office_id,
                            office_name_by_id=office_name_by_id,
                        )
                        continue
                # Phase G-30 / G-30.1: pinned PFV があれば上の continue で処理済.
                # ここに到達する weekly_pattern entry は必ず非 pinned.
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
                        is_pinned=False,
                    )
                )
    return visits


# ---------------------------------------------------------------------------
# Phase G-21 T3-2: build_visits_for_pool_v2 (pinned / 非 pinned 2 経路化)
# ---------------------------------------------------------------------------


def build_visits_for_pool_v2(
    patients: list[Patient],
    *,
    fixed_by_patient: dict[UUID, list[PatientFixedVisit]] | None = None,
    pending_overlay: dict[tuple[UUID, int], PendingEditOverlay] | None = None,
    course_code_by_template_id: dict[UUID, str] | None = None,
    sub_office_scope: set[UUID] | None = None,
    op_weekdays_by_office: dict[UUID, set[int]] | None = None,
    office_name_by_id: dict[UUID, str] | None = None,
    warnings: list[V2Warning] | None = None,
    config: SchedulingConfig | None = None,
) -> list[V2Visit]:
    """Phase G-21 T3-2: pinned / 非 pinned 患者で 2 経路に分けて V2Visit に展開する.

    ``build_visits_for_pool`` の置き換え (= G21 feature flag enabled 拠点で使用):

    * **pinned PFV** (``is_pinned=True``): time_type='固定', PFV.start_time 厳守.
      Phase G-10/G-11 と同じ「動かさない」配置 (= 現在の挙動 = ``time_type='固定'``).
    * **非 pinned 患者** (PFV.is_pinned=False または PFV なし):
      ``weekly_pattern.entries[].time_type / preferred_start / preferred_end`` を採用.
      時間帯範囲内で ``apply_travel_corrections`` に時刻 shift を任せる.

    **Invariant G21-A**: 同 patient × 同 weekday に pinned PFV が存在する場合は
    weekly_pattern entry を **skip + warning**. 同曜日で pinned と非 pinned が
    混在することはあり得ないが、データ不整合の防衛として明示的に skip する.

    Args:
        patients: pool 候補患者リスト.
        fixed_by_patient: ``{patient_id: list[PatientFixedVisit]}``. PFV を patient 別に
            事前にロードしたマップ. ``None`` の場合は全 patient で weekly_pattern 経路.
        pending_overlay: 今週限定オーバーレイ (``run_v2_pipeline`` から渡される).
        course_code_by_template_id: PFV.course_template_id → course label の map
            (pinned PFV 経路で V2Visit.course_code に流す).
        sub_office_scope: Phase E-5 sub_office 経由配置の scope.
        warnings: Invariant G21-A 違反時に warning を追記するためのリスト.

    Returns:
        ``list[V2Visit]``: 全患者 × 全 weekday の V2Visit を展開したリスト.
    """
    overlay = pending_overlay or {}
    sub_scope = sub_office_scope or set()
    fixed_map = fixed_by_patient or {}
    visits: list[V2Visit] = []
    # Phase G-45: 拠点休業日 skip の重複 emit 抑止. (patient_id, weekday) ごと 1 件.
    closed_warned: set[tuple[UUID, int]] = set()

    for patient in patients:
        if patient.lat is None or patient.lng is None or patient.primary_office_id is None:
            continue
        addr = patient.address
        area = _extract_area_label(addr)
        sex_r = patient.sex_restriction
        req_multi = bool(getattr(patient, "requires_multiple_staff", False) or False)

        pfv_rows = fixed_map.get(patient.id) or []
        # weekday -> pinned PFV (slot_index=0, mode='normal', is_pinned=True).
        # Invariant G21-A: pinned PFV があれば該当 weekday の weekly_pattern entry を skip.
        pinned_by_wd: dict[int, PatientFixedVisit] = {}
        for pfv in pfv_rows:
            if pfv.mode != "normal" or pfv.slot_index != 0:
                continue
            if pfv.is_pinned:
                pinned_by_wd[pfv.weekday] = pfv

        # course_template_id → label map / sub_office_id 差し替えマップ.
        wd_to_course_code: dict[int, str] = {}
        if course_code_by_template_id:
            for _row in pfv_rows:
                if _row.course_template_id is not None:
                    _label = course_code_by_template_id.get(_row.course_template_id)
                    if _label is not None:
                        wd_to_course_code[_row.weekday] = _label
        wd_to_office_id: dict[int, UUID] = {}
        if sub_scope:
            for _row in pfv_rows:
                if _row.sub_office_id is not None and _row.sub_office_id in sub_scope:
                    wd_to_office_id[_row.weekday] = _row.sub_office_id

        # 1) pinned PFV 経路: time_type='固定', PFV.start_time 厳守.
        for wd, pfv in pinned_by_wd.items():
            ov = overlay.get((patient.id, wd))
            if ov is not None:
                st_eff = ov.new_start
                sm_eff = _compute_overlay_duration(ov, existing_duration=pfv.duration_min)
                tt_eff = ov.new_time_type or "固定"
                ps_eff = ov.new_start_str
                pe_eff = ov.new_end_str
            else:
                st_eff = pfv.start_time
                sm_eff = pfv.duration_min
                tt_eff = "固定"
                ps_eff = _fmt_hhmm(pfv.start_time)
                pe_eff = None
            end_t = _add_minutes(st_eff, sm_eff)
            am_pm = determine_am_pm(time_type=tt_eff, preferred_start=st_eff)
            cc_eff = wd_to_course_code.get(wd)
            office_id_eff = wd_to_office_id.get(wd, patient.primary_office_id)
            # Phase G-45: 拠点休業日 skip (G-21 経路 pinned PFV).
            if op_weekdays_by_office is not None:
                _op_wd = op_weekdays_by_office.get(office_id_eff)
                if _op_wd is not None and wd not in _op_wd:
                    _emit_office_closed_warning(
                        warnings=warnings,
                        closed_warned=closed_warned,
                        patient_id=patient.id,
                        patient_name=patient.name,
                        weekday=wd,
                        office_id=office_id_eff,
                        office_name_by_id=office_name_by_id,
                    )
                    continue
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
                    office_id=office_id_eff,
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
                    is_pinned=True,
                )
            )

        # 2) 非 pinned 経路: weekly_pattern.entries の time_type / preferred_start / preferred_end
        #    を採用. apply_travel_corrections が時間帯範囲内で時刻 shift する.
        entries = _extract_weekly_entries(patient, config=config)
        for wd, st, sm, tt, ps_str, pe_str in entries:
            # Invariant G21-A: 同 weekday に pinned PFV があれば weekly_pattern entry skip + warning.
            if wd in pinned_by_wd:
                if warnings is not None:
                    pinned = pinned_by_wd[wd]
                    warnings.append(
                        V2Warning(
                            type="general",
                            message=(
                                f"Invariant G21-A: 患者 {patient.name} 様 ({_weekday_jp(wd)}) "
                                f"に pinned PFV (start_time={_fmt_hhmm(pinned.start_time)}) と "
                                f"weekly_pattern entry が同時に存在 — "
                                f"weekly_pattern entry を skip し pinned を採用"
                            ),
                            weekday=wd,
                            actionable=False,
                            patient_id=patient.id,
                            patient_name=patient.name,
                        )
                    )
                continue
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
            # Phase G-45: 拠点休業日 skip (G-21 経路 非 pinned weekly_pattern).
            if op_weekdays_by_office is not None:
                _op_wd = op_weekdays_by_office.get(patient.primary_office_id)
                if _op_wd is not None and wd not in _op_wd:
                    _emit_office_closed_warning(
                        warnings=warnings,
                        closed_warned=closed_warned,
                        patient_id=patient.id,
                        patient_name=patient.name,
                        weekday=wd,
                        office_id=patient.primary_office_id,
                        office_name_by_id=office_name_by_id,
                    )
                    continue
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
    pair_modes: dict[tuple[UUID, UUID], str] | None = None,
) -> None:
    """H2 強化: 同住所 3 名以上を強制的に別 set へ分散する.

    既存 ``_enforce_h2_same_address`` の補完として呼ぶ. 同 ``(office, weekday,
    am_pm, address_bucket)`` で 3 名以上を検出したら, 3 件目以降を同
    ``(office, weekday, am_pm)`` 内の容量に余裕がある別 set に移動する.

    移動先候補条件:
      - 同 (office, weekday, am_pm) 内の別 set
      - 移動後の set サイズが ``MAX_PATIENTS_PER_SET`` 以下
      - 移動先に同住所がまだ 2 件未満

    Phase G-21 final H4: ``pair_modes`` を受け取り、 ``blocked`` 関係にあるペアを
    同一 set に再 merge しないよう尊重する. 旧実装は ``_enforce_same_address_pair_mode``
    で blocked ペアを分離した後、 本 helper の overflow 移動で blocked 相手の set
    に re-merge する競合があった.

    移動先が見つからない場合は warning に詳細記録 (移動できなかった旨明示).
    """
    from collections import defaultdict

    pair_modes = pair_modes or {}

    def _is_blocked_with_set(visit: V2Visit, target_set: V2Set) -> bool:
        """visit を target_set に移すと blocked ペアが同 set に同居するか.

        ``pair_modes`` のキーは ``(a, b)`` 正規化済 (a < b 文字列比較) なので、
        順序両方向で lookup する.
        """
        if not pair_modes:
            return False
        for other_v in target_set.visits:
            if other_v is None:
                continue
            if other_v.patient_id == visit.patient_id:
                continue
            pid_a = visit.patient_id
            pid_b = other_v.patient_id
            mode = pair_modes.get((pid_a, pid_b)) or pair_modes.get((pid_b, pid_a))
            if mode == "blocked":
                return True
        return False

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
        # Phase G-21 T3-4: overflow patient 選出を決定論化.
        # 同 input で常に同じ overflow patient を選ぶよう、
        # (patient_id 文字列, set_index, visit_index) で昇順ソート.
        # 旧実装は `_enforce_h2_same_address` の処理順 (= bucket 内 visit 順)
        # に依存しており、 visit 並び替えによって overflow 対象が変動していた.
        locs.sort(key=lambda kv: (str(sets[kv[0]].visits[kv[1]].patient_id), kv[0], kv[1]))

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
                # Phase G-21 final H4: blocked 関係尊重 — 移動先候補に
                # 当該 visit と blocked のペアがいれば skip (再 merge 防止).
                if _is_blocked_with_set(visit_to_move, t_set):
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
# Phase G-21 T3-3: pair_mode 制約 (blocked / preferred / required)
# ---------------------------------------------------------------------------


def _enforce_same_address_pair_mode(
    sets: list[V2Set],
    pair_modes: dict[tuple[UUID, UUID], str],
    warnings: list[V2Warning],
) -> None:
    """Phase G-21 T3-3: ``PatientSameAddressLink.pair_mode`` を反映する.

    | pair_mode | 振る舞い |
    |-----------|----------|
    | blocked   | 同時刻 NG (hard): 同 set 入り禁止 — 同 set に居たら 2 人目を別 set に剥がす. |
    | preferred | デフォルト挙動 (既存 `_enforce_h2_same_address` で同住所同時刻 OK). |
    | required  | 強い同時刻優先: 同 set でなければ 2 人目を別 set から取って同 set へ移す. |

    ``_enforce_h2_same_address`` / ``_enforce_h2_split_overflow`` の **前段** に呼ぶ.
    本 helper は ``(office, weekday, am_pm)`` 内で実施する (= bucket 越え移動はしない).

    Args:
        sets: 距離グリーディクラスタリング後の V2Set リスト (in-place).
        pair_modes: ``{(patient_a_id, patient_b_id): pair_mode}`` map.
            キーは ``patient_a_id < patient_b_id`` 正規化済 (DB CHECK 制約).
        warnings: 制約衝突や移動できなかった旨を記録するリスト.

    実装メモ:
        - blocked: 同 set 内に該当ペアがいたら、 patient_b を別 set (容量空き) に剥がす.
          剥がせなければ warning.
        - required: 異 set に分かれていたら、 patient_b を patient_a の set に統合する.
          容量超過になる場合は warning のみで配置維持.
    """
    if not pair_modes:
        return

    def _pair_key(p1: UUID, p2: UUID) -> tuple[UUID, UUID]:
        if str(p1) < str(p2):
            return (p1, p2)
        return (p2, p1)

    # (patient_id) -> (set_index, visit_index) の lookup を毎回再構築する
    # (set の visit が移動するたびに index が変わるため).
    def _build_location_map() -> dict[UUID, tuple[int, int]]:
        loc: dict[UUID, tuple[int, int]] = {}
        for si, s in enumerate(sets):
            for vi, v in enumerate(s.visits):
                if v is None:
                    continue
                loc[v.patient_id] = (si, vi)
        return loc

    # --- blocked: 同 set 内のペアを別 set に分離 ---
    for (a, b), mode in pair_modes.items():
        if mode != "blocked":
            continue
        loc = _build_location_map()
        if a not in loc or b not in loc:
            continue
        a_si, a_vi = loc[a]
        b_si, b_vi = loc[b]
        if a_si != b_si:
            continue  # 既に別 set
        # 2 人目 (b) を別 set に剥がす. 同 (office, weekday, am_pm) 容量空き先を探す.
        visit_b = sets[b_si].visits[b_vi]
        if visit_b is None:
            continue
        # Phase G-21 T3 (Reviewer H4 fix): 移動先候補で「visit_b と同住所が既に
        # 2 件以上いる set」は除外する. これを抜くと _enforce_h2_same_address の
        # 2 名上限制約を後段で破ることになり、 同住所 3 名以上を再構成するリスクがある.
        visit_b_addr = (
            _address_bucket(visit_b.lat, visit_b.lng)
            if visit_b.lat is not None and visit_b.lng is not None
            else None
        )
        target_si: int | None = None
        for ti, t_set in enumerate(sets):
            if ti == b_si:
                continue
            first_v = next((v for v in t_set.visits if v is not None), None)
            if first_v is None:
                # 空 set は使える (bucket 制約は満たされる — 1 人目だから)
                target_si = ti
                break
            if (
                first_v.office_id != visit_b.office_id
                or first_v.weekday != visit_b.weekday
                or first_v.am_pm != visit_b.am_pm
            ):
                continue
            valid_count = sum(1 for v in t_set.visits if v is not None)
            if valid_count >= MAX_PATIENTS_PER_SET:
                continue
            # H4: target に visit_b と同住所が既に 2 件以上いるなら NG.
            if visit_b_addr is not None:
                same_addr_in_target = sum(
                    1
                    for v in t_set.visits
                    if v is not None
                    and v.lat is not None
                    and v.lng is not None
                    and _address_bucket(v.lat, v.lng) == visit_b_addr
                )
                if same_addr_in_target >= 2:
                    continue
            target_si = ti
            break
        if target_si is not None:
            sets[target_si].visits.append(visit_b)
            sets[b_si].visits[b_vi] = None  # type: ignore[call-overload]
            # 後段で None を除去
            for s in sets:
                s.visits = [v for v in s.visits if v is not None]
            warnings.append(
                V2Warning(
                    type="general",
                    message=(f"pair_mode=blocked: 患者 {visit_b.patient_name} 様 を別コースに分離"),
                    weekday=visit_b.weekday,
                    actionable=False,
                    patient_id=visit_b.patient_id,
                    patient_name=visit_b.patient_name,
                    affected_patient_ids=[a, b],
                )
            )
        else:
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"pair_mode=blocked: ペア ({a}, {b}) を別 set にできず (容量超過) "
                        "— 同コース配置のまま保持"
                    ),
                    weekday=visit_b.weekday,
                    actionable=True,
                    affected_patient_ids=[a, b],
                )
            )

    # --- required: 別 set のペアを同 set に統合 ---
    for (a, b), mode in pair_modes.items():
        if mode != "required":
            continue
        loc = _build_location_map()
        if a not in loc or b not in loc:
            continue
        a_si, a_vi = loc[a]
        b_si, b_vi = loc[b]
        if a_si == b_si:
            continue  # 既に同 set
        # 同 (office, weekday, am_pm) bucket 内かを確認.
        va = sets[a_si].visits[a_vi]
        vb = sets[b_si].visits[b_vi]
        if va is None or vb is None:
            continue
        if va.office_id != vb.office_id or va.weekday != vb.weekday or va.am_pm != vb.am_pm:
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"pair_mode=required: ペア ({va.patient_name}, {vb.patient_name}) は "
                        "bucket (office/weekday/am_pm) が異なるため同 set にできず"
                    ),
                    weekday=va.weekday,
                    actionable=True,
                    affected_patient_ids=[a, b],
                )
            )
            continue
        # b を a の set に移す. 容量超過時は warning のみ.
        valid_count_a = sum(1 for v in sets[a_si].visits if v is not None)
        if valid_count_a >= MAX_PATIENTS_PER_SET:
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"pair_mode=required: ペア ({va.patient_name}, {vb.patient_name}) を "
                        f"同 set にしたいが容量超過 (MAX={MAX_PATIENTS_PER_SET})"
                    ),
                    weekday=va.weekday,
                    actionable=True,
                    affected_patient_ids=[a, b],
                )
            )
            continue
        sets[a_si].visits.append(vb)
        sets[b_si].visits[b_vi] = None  # type: ignore[call-overload]
        for s in sets:
            s.visits = [v for v in s.visits if v is not None]
        warnings.append(
            V2Warning(
                type="general",
                message=(
                    f"pair_mode=required: 患者 {vb.patient_name} 様 を "
                    f"{va.patient_name} 様 と同コースに統合"
                ),
                weekday=va.weekday,
                actionable=False,
                patient_id=vb.patient_id,
                patient_name=vb.patient_name,
                affected_patient_ids=[a, b],
            )
        )

    # preferred は既存 _enforce_h2_same_address の挙動 = 同住所同時刻 OK で何もしない.
    _ = _pair_key  # 将来 reverse 検索が要れば使う


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

    Phase G-45: primary_office が当該 weekday に休業の場合、 staff の
    ``StaffSecondaryOffice`` を辿り、 稼働している secondary_office に転入して
    カウントする (= 「応援」). secondary は ``staff_id, office_id`` の挿入順
    (= row insert 順) で評価し、 最初に当該 weekday が稼働の secondary を採用する.
    secondary が無い / 全 secondary も休業の staff は当該 weekday カウント外.
    1 staff = 1 office カウント (= 重複排除).
    """
    return await _count_active_staff_per_weekday_for_role(
        db,
        office_ids=office_ids,
        iso_year=iso_year,
        iso_week=iso_week,
        role="staff",
    )


async def _count_active_staff_per_weekday_for_role(
    db: AsyncSession,
    *,
    office_ids: list[UUID],
    iso_year: int,
    iso_week: int,
    role: str,
) -> dict[tuple[UUID, int], int]:
    """Phase G-45: ``count_active_staff_per_weekday`` /
    ``count_active_managers_per_weekday`` の共通実装.

    role = 'staff' or 'manager' で切り替える.
    """
    if not office_ids:
        return {}
    # primary_office_id が office_ids に含まれる staff を取得.
    # Phase G-45: 加えて、 office_ids に primary が無くても secondary が
    # office_ids に含まれる staff も応援候補に含める. これを抜くと、
    # office_ids=[INAGE] のときに primary=TSUGA + secondary=INAGE の staff が
    # INAGE 応援としてカウントされない.
    primary_match_stmt = select(Staff.id).where(
        Staff.status == "active",
        Staff.deleted_at.is_(None),
        Staff.role == role,
        Staff.is_trainee.is_(False),
        Staff.primary_office_id.in_(office_ids),
    )
    secondary_match_stmt = (
        select(Staff.id)
        .join(StaffSecondaryOffice, StaffSecondaryOffice.staff_id == Staff.id)
        .where(
            Staff.status == "active",
            Staff.deleted_at.is_(None),
            Staff.role == role,
            Staff.is_trainee.is_(False),
            StaffSecondaryOffice.office_id.in_(office_ids),
        )
    )
    primary_ids = set((await db.scalars(primary_match_stmt)).all())
    secondary_ids = set((await db.scalars(secondary_match_stmt)).all())
    target_ids = primary_ids | secondary_ids
    if not target_ids:
        return {}
    staff_rows = await db.scalars(select(Staff).where(Staff.id.in_(target_ids)))
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

    # Phase G-45: 全 staff の secondary_office_id を staff_id ごとに先取り (1 query).
    # ORDER BY (staff_id, office_id) で deterministic 順序を保証する
    # (= fallback で先頭 secondary を選ぶため UUID 昇順で安定化).
    sec_rows = await db.scalars(
        select(StaffSecondaryOffice)
        .where(StaffSecondaryOffice.staff_id.in_(staff_ids))
        .order_by(
            StaffSecondaryOffice.staff_id,
            StaffSecondaryOffice.office_id,
        )
    )
    secondaries_by_staff: dict[UUID, list[UUID]] = {}
    for sec in sec_rows.all():
        secondaries_by_staff.setdefault(sec.staff_id, []).append(sec.office_id)

    # Phase G-45: 全候補 office (primary / secondary) の稼働曜日 map.
    # 1 staff = 1 office カウントを守るため、 primary が closed なら最初の
    # 稼働 secondary に転入する.
    candidate_office_ids: set[UUID] = set()
    for s in staff_list:
        if s.primary_office_id is not None:
            candidate_office_ids.add(s.primary_office_id)
        for sid in secondaries_by_staff.get(s.id, []):
            candidate_office_ids.add(sid)
    op_weekdays_by_office = await _load_office_operating_weekdays(
        db, office_ids=list(candidate_office_ids)
    )
    office_ids_set: set[UUID] = set(office_ids)

    counter: Counter[tuple[UUID, int]] = Counter()
    for s in staff_list:
        if s.primary_office_id is None:
            continue
        for wd in shifts_by_staff.get(s.id, set()):
            if (s.id, wd) in off_overrides:
                continue
            # 1) primary が当該曜日に稼働 + 範囲内 (= office_ids_set) ならそこにカウント.
            primary_op = op_weekdays_by_office.get(s.primary_office_id)
            if s.primary_office_id in office_ids_set and (primary_op is None or wd in primary_op):
                counter[(s.primary_office_id, wd)] += 1
                continue
            # 2) primary が休業 (or 範囲外) — secondary を順に評価し、 最初の
            # 稼働 secondary が office_ids 内なら転入してカウント. 1 staff = 1 office.
            chosen_secondary: UUID | None = None
            for sec_oid in secondaries_by_staff.get(s.id, []):
                sec_op = op_weekdays_by_office.get(sec_oid)
                if sec_oid in office_ids_set and (sec_op is None or wd in sec_op):
                    chosen_secondary = sec_oid
                    break
            if chosen_secondary is not None:
                counter[(chosen_secondary, wd)] += 1
                # else: primary 休業 + secondary も無効 — 当該 weekday カウント外.
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

    Phase G-45: ``count_active_staff_per_weekday`` と同じく、 primary_office が
    当該 weekday に休業の manager は ``StaffSecondaryOffice`` を辿って
    secondary に転入してカウントする (= 応援運用と整合).
    """
    return await _count_active_staff_per_weekday_for_role(
        db,
        office_ids=office_ids,
        iso_year=iso_year,
        iso_week=iso_week,
        role="manager",
    )


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
    config: SchedulingConfig | None = None,
) -> list[tuple[V2Set | None, V2Set | None]]:
    """段階 5: 各スタッフ 1 日 = 午前セット + 午後セットを組み合わせる.

    手順:
      1. am_sets / pm_sets を staff_count 個に丸める (超過は warnings).
      2. am と pm を greedy に「最も近い」ペアでマッチング.
      3. 同エリア優先 (5km 以内なら OK). 5km 以上離れていれば警告.
      4. 余った片方は単独コース (午前のみ or 午後のみ).

    Phase G-88 Step3: ``config`` 注入時のみ 1 コース最大人数を
    ``config.max_patients_per_course`` に差し替える. ``config=None`` は module 定数
    ``MAX_PATIENTS_PER_COURSE`` (= 6) を使い挙動不変.
    """
    if not am_sets and not pm_sets:
        return []
    _max_patients = (
        config.max_patients_per_course if config is not None else MAX_PATIENTS_PER_COURSE
    )

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
                if total > _max_patients:
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


def calc_course_total_minutes(
    visits: list[V2Visit],
    *,
    config: SchedulingConfig | None = None,
) -> int:
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
    # Phase G-88 Step3: config 注入時のみ buffer / 移動速度を差し替え. config=None
    # は module 定数を使い挙動不変.
    _buffer_min = config.visit_buffer_min if config is not None else VISIT_BUFFER_MINUTES
    _speed_kmh = config.travel_speed_kmh if config is not None else TRAVEL_SPEED_KMH
    sv = sorted(visits, key=lambda v: v.start_time)
    # Phase E-3 改修 (3): 同住所ペア (隣接 same_address) の service 合計が
    # ``SAME_ADDRESS_PAIR_MIN_OCCUPANCY`` (= 90) 未満なら 90 分に底上げして total に積む.
    # これで容量上限 480 分判定が ``_align_same_address_pair_to_same_time`` の実占有
    # (= max(a+b, 90)) と整合し、過小評価による容量オーバー漏れを解消する.
    #
    # 走査: sv は start_time 昇順. 隣接 same_address な (i-1, i) ペアを 1 つ進むごとに
    # 検出し、ペア外の visit は service_minutes そのまま、ペアの 2 件は合算占有を採用.
    # 3 名以上同住所同コースは「先頭 2 名のみペア化、3 名目以降は single」(
    # ``_align_same_address_pair_to_same_time`` 仕様) と整合させるため、ペア確定後は
    # ``i += 2`` で進める.
    total = 0
    n = len(sv)
    i = 0
    while i < n:
        cur = sv[i]
        if i + 1 < n:
            nxt = sv[i + 1]
            if (
                cur.lat is not None
                and cur.lng is not None
                and nxt.lat is not None
                and nxt.lng is not None
                and _address_bucket(cur.lat, cur.lng) == _address_bucket(nxt.lat, nxt.lng)
                and cur.patient_id != nxt.patient_id
            ):
                pair_occupancy = max(
                    int(cur.service_minutes) + int(nxt.service_minutes),
                    SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
                )
                total += pair_occupancy
                i += 2
                continue
        total += int(cur.service_minutes)
        i += 1

    for i in range(1, len(sv)):
        prev = sv[i - 1]
        cur = sv[i]
        # 同住所は移動時間 0 + バッファー 0
        if _address_bucket(prev.lat, prev.lng) == _address_bucket(cur.lat, cur.lng):
            continue
        travel_min = haversine_minutes(
            haversine_km(prev.lat, prev.lng, cur.lat, cur.lng), speed_kmh=_speed_kmh
        )
        # _apply_travel_time_to_courses と同じく異住所はバッファー加算.
        total += travel_min + _buffer_min
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


def _auto_shift_same_time_conflicts(
    sv: list[V2Visit],
    *,
    office_name: str,
    course_code: str | None,
    weekday: int,
    warnings: list[V2Warning],
    config: SchedulingConfig | None = None,
) -> list[V2Visit]:
    """Fix E (CareFlow): 同コース内の異住所同時刻 2 名以上を自動シフトする.

    ``_apply_travel_time_to_courses`` の各 ``(office, weekday, course_code)`` グループ
    内で ``start_time`` 順にソートした直後に呼び出す. 同じ ``start_time`` を持つ
    隣接 visit が **異住所** (``_address_bucket`` 比較) の場合:

    1. 順序決定 (距離最適化):
       前後 visit ``P`` (= sv[i-1] if exists) と ``Q`` (= sv[j+1] if exists) に対して、
       ペア順 ``[P, A, B, Q]`` と ``[P, B, A, Q]`` の総距離を比較し、最小の方を採用.
       (``dist(A,B)`` は両ケース共通なので、実質 ``dist(P,A)+dist(B,Q)`` vs
       ``dist(P,B)+dist(A,Q)`` の比較になる. P or Q が無い場合は片側のみ比較.)

    2. 後 patient の時刻シフト:
       - ``actual_start = max(desired, prev.end + travel + VISIT_BUFFER_MINUTES)``
       - 5 分刻み切り上げを適用 (``_round_up_to_5min``)
       - ``time_type='固定'`` でも **例外的に時刻を動かす** (= 同時刻配置回避優先).
       - ``end_time`` も ``service_minutes`` だけ後ろにずらす.
       - 後段の lunch / PM 制約検証は ``_apply_travel_time_to_courses`` 本体に任せる
         (cur.start_time が動いた後の earliest_start ベースで再評価される).

    3. 3 名以上同時刻:
       同 start_time を持つ visit が 3 名以上連続する場合は、距離最適化で並び順を
       決めた上で **順次** シフト (2 番目を 1 番目の後ろ、3 番目を 2 番目の後ろ).
       3 名同住所のケースは ``_reorder_same_address_consecutive`` 内 H2 enforce や
       Fix A (Stage 5 重複防止) の責務であり、本関数では「異住所成分が混じる場合のみ」
       シフトする (= 同住所のみは travel=0+buffer=0 で既存処理に任せる).

    4. warning emit:
       ``auto_time_shift_for_conflict`` type で「{B name} 様を {new_start} に変更」
       を出す. ``affected_patient_ids`` にシフトされた patient ID を入れる.
       severity 的には info 相当なので ``actionable=False``.

    5. 同住所同時刻 (家族・施設):
       ``_address_bucket`` 一致なら travel=0+buffer=0 で既存仕様通り同時刻維持
       (本関数では何もしない).

    Args:
        sv: 1 コース内の ``start_time`` 昇順 + 同住所連番リオーダー済み visit list.
        office_name: warning メッセージ用 (拠点名).
        course_code: warning メッセージ用 (コース code; None 可).
        weekday: warning の weekday フィールド.
        warnings: 共有 warnings list (in-place append).

    Returns:
        並べ替え (距離最適化) 済み visit list.
        各 visit の ``start_time`` / ``end_time`` は in-place で書き換わる.

    Notes:
        - 同 ``patient_id`` が同時刻になることは通常ありえない (1 patient 1 visit)
          が、念のため同 patient_id ペアはスキップする.
        - ``_reorder_same_address_consecutive`` の同住所連番強制を破壊しない:
          同住所 visit は ``_address_bucket`` が一致するので「異住所衝突」判定から
          除外される → 並び順を変えない.
        - 本関数は ``_apply_travel_time_to_courses`` 本体の earliest_start 再計算の
          **前段** で同時刻を解消することで、固定時刻でも shortage>=5 で物理不可能
          判定にされる前に「時刻自体を動かす」ルートを通す.
    """
    if len(sv) < 2:
        return sv

    # Phase G-88 Step3: config 注入時のみ buffer / 移動速度を差し替え. config=None
    # は module 定数 (VISIT_BUFFER_MINUTES=8 / TRAVEL_SPEED_KMH=20) を使い挙動不変.
    _buffer_min = config.visit_buffer_min if config is not None else VISIT_BUFFER_MINUTES
    _speed_kmh = config.travel_speed_kmh if config is not None else TRAVEL_SPEED_KMH

    def _dist(p: V2Visit | None, q: V2Visit | None) -> float:
        """2 visit 間の Haversine 距離 (km). 片方 None なら 0 (邪魔しない)."""
        if p is None or q is None:
            return 0.0
        if p.lat is None or p.lng is None or q.lat is None or q.lng is None:
            return 0.0
        return haversine_km(p.lat, p.lng, q.lat, q.lng)

    def _same_addr(p: V2Visit, q: V2Visit) -> bool:
        if p.lat is None or p.lng is None or q.lat is None or q.lng is None:
            return False
        return _address_bucket(p.lat, p.lng) == _address_bucket(q.lat, q.lng)

    wd_jp = _weekday_jp(weekday)
    result: list[V2Visit] = list(sv)
    i = 0
    while i < len(result) - 1:
        # 同 start_time を持つ連続区間 [i, j] を見つける.
        j = i
        while j + 1 < len(result) and result[j + 1].start_time == result[i].start_time:
            j += 1
        if j == i:
            i += 1
            continue
        # 区間 [i, j] に 2 名以上同時刻が存在.
        # 「異住所成分が混じるか」を判定: 区間内の少なくとも 1 ペアが異住所なら処理対象.
        segment = result[i : j + 1]
        has_diff_addr = False
        for a in range(len(segment)):
            for b in range(a + 1, len(segment)):
                if not _same_addr(segment[a], segment[b]):
                    has_diff_addr = True
                    break
            if has_diff_addr:
                break
        if not has_diff_addr:
            # 全員同住所同時刻: 家族・施設想定で既存仕様通り維持.
            i = j + 1
            continue

        # 2 名ケース (j == i + 1): 距離最適化で順序判定.
        # 3 名以上ケース: 区間内で順次貪欲に「P (prev) と Q (next) に対し
        # 最も近い visit を順次選ぶ」順序を決める (= insertion of best-first).
        prev_visit = result[i - 1] if i - 1 >= 0 else None
        next_visit = result[j + 1] if j + 1 < len(result) else None

        if len(segment) == 2:
            a_v, b_v = segment[0], segment[1]
            # 順序 [P, A, B, Q] vs [P, B, A, Q]:
            #   共通項: dist(A,B), 残り = dist(P,A) + dist(B,Q) vs dist(P,B) + dist(A,Q).
            cost_ab = _dist(prev_visit, a_v) + _dist(b_v, next_visit)
            cost_ba = _dist(prev_visit, b_v) + _dist(a_v, next_visit)
            if cost_ba < cost_ab:
                ordered = [b_v, a_v]
            else:
                ordered = [a_v, b_v]
        else:
            # 3 名以上: 貪欲. prev から始まり、各ステップで未配置の中から
            # 「直前 visit からの距離 + (最後の場合のみ) next visit までの距離」
            # が最小のものを選ぶ. 計算量は len(segment) <= 6 程度を想定 (1 コース上限).
            remaining = list(segment)
            ordered = []
            current = prev_visit
            while remaining:
                if len(remaining) == 1:
                    ordered.append(remaining.pop())
                    break
                best_idx = 0
                best_cost = float("inf")
                for k, cand in enumerate(remaining):
                    # 残りが cand 含めて len(remaining) 件のとき、
                    # cand を選んだら残り len(remaining)-1 件.
                    # last in segment なら next_visit との距離も加味.
                    cost = _dist(current, cand)
                    if len(remaining) == 1:
                        cost += _dist(cand, next_visit)
                    if cost < best_cost:
                        best_cost = cost
                        best_idx = k
                chosen = remaining.pop(best_idx)
                ordered.append(chosen)
                current = chosen

        # 配列を入れ替え.
        result[i : j + 1] = ordered

        # シフト: ordered[0] は元の start_time のまま (= 先頭).
        # ordered[1:] を順次 prev.end + travel + buffer → 5 分刻み切り上げで配置.
        for k in range(1, len(ordered)):
            prev = ordered[k - 1]
            cur = ordered[k]
            desired = cur.start_time
            if _same_addr(prev, cur):
                travel_min = 0
                buffer_min = 0
            else:
                travel_min = haversine_minutes(
                    haversine_km(prev.lat, prev.lng, cur.lat, cur.lng), speed_kmh=_speed_kmh
                )
                buffer_min = _buffer_min
            earliest = _add_minutes(prev.end_time, travel_min + buffer_min)
            # 5 分刻み切り上げ (固定でも例外的に動かすため一律適用).
            new_start = _round_up_to_5min(earliest)
            if new_start == cur.start_time:
                # 既に earliest 以降に置かれている (= シフト不要).
                continue
            old_start = cur.start_time
            cur.start_time = new_start
            cur.end_time = _add_minutes(new_start, cur.service_minutes)

            # warning emit.
            prev_name = prev.patient_name or (prev.patient_code or "不明")
            cur_name = cur.patient_name or (cur.patient_code or "不明")
            warnings.append(
                V2Warning(
                    type="auto_time_shift_for_conflict",
                    message=(
                        f"{office_name} {course_code or '?'} コース {wd_jp}: "
                        f"{prev_name} 様 ({_fmt_hhmm(desired)}) と "
                        f"{cur_name} 様 ({_fmt_hhmm(old_start)}) の同時刻衝突を"
                        f"自動調整、{cur_name} 様を {_fmt_hhmm(new_start)} に変更"
                    ),
                    weekday=weekday,
                    actionable=False,
                    patient_id=cur.patient_id,
                    patient_name=cur.patient_name,
                    current_time=_fmt_hhmm(new_start),
                    suggested_time=_fmt_hhmm(new_start),
                    time_type=cur.time_type,
                    preferred_start=cur.preferred_start,
                    preferred_end=cur.preferred_end,
                    affected_patient_ids=[cur.patient_id],
                )
            )

        # 区間処理後、シフトで end_time が動いた末尾の次に進む.
        i = j + 1

    return result


def _align_same_address_pair_to_same_time(
    sv: list[V2Visit],
    *,
    warnings: list[V2Warning],
    weekday: int,
    course_code: str | None,
    office_name: str,
    unassigned_visit_ids: set[int] | None = None,
    config: SchedulingConfig | None = None,
) -> list[V2Visit]:
    """Wave 2 (#115) + Phase E-3 改修 (4): 同住所ペアを **同 start_time** に揃え +
    duration を ``max(service 合計, SAME_ADDRESS_PAIR_MIN_OCCUPANCY=90)`` 占有させる.

    ``_apply_travel_time_to_courses`` 内、``_reorder_same_address_consecutive`` で
    同住所連番が確定し、``_auto_shift_same_time_conflicts`` で異住所同時刻が解消
    された **直後** に呼ぶ. 同住所ペア (= ``_address_bucket`` 一致の 2 患者) を
    検出し、両者の ``start_time`` を揃えた上で **2 人目の ``end_time`` をペア合算
    duration の終端 = 60 分占有等** に書き換える.

    Algorithm:
        1. 隣接ペアを走査 (sv は ``_reorder_same_address_consecutive`` 後なので
           同住所は隣接).
        2. ``_address_bucket`` 不一致 / patient_id 同一 / lat/lng None ならスキップ.
        3. time_type 別に揃え先を決める:
            - 両者「固定」かつ ``start_time`` 一致 → そのまま
            - 両者「固定」かつ ``start_time`` 不一致 → 揃えず warning
              (現場で再調整)
            - 片方固定 + 他方非固定 → 固定の time に揃える、他方 window 検証
            - 両者非固定 → 早い方 (= sort 済の前者 A) に揃える、他方 window 検証
        4. 揃え確定後:
            - A.start_time = B.start_time = aligned_start
            - A.end_time = aligned_start + A.service_minutes
            - **B.end_time = aligned_start + A.service_minutes + B.service_minutes**
              (= ペア合算 60 分占有). B.service_minutes 自体は変えない (UI 表示は
              本来の 30 分).
        5. 3 名以上同住所同コース (H2 enforce 漏れ): 先頭 2 名のみペア化、
           3 名目以降は不変 (single). 警告は
           ``_reorder_same_address_consecutive`` が既に出している.
        6. lunch 跨ぎ: 合算 end が lunch 12-13 と重なる場合は warning を追加
           (Wave 3 の lunch フレキシブル化までは固定 12-13 で OK).

    Args:
        sv: 1 コース内の visit list (``_reorder_same_address_consecutive`` +
            ``_auto_shift_same_time_conflicts`` 適用後).
        warnings: 共有 warnings list (in-place append).
        weekday: warning 用 (0=月..6=日).
        course_code: warning 用 (コース code; None 可).
        office_name: warning 用 (拠点名).

    Returns:
        同住所ペア処理後の visit list. ``sv`` の要素を in-place で書き換える
        (``start_time`` / ``end_time``). 戻り値は ``sv`` と同じインスタンス.

    Notes:
        - ``_address_bucket`` で同 bucket な隣接ペアのみ対象.
        - 3 名以上の同住所同コースは 「2 名 + 1 名」 として処理し、後段は通常通り
          earliest_start で配置される.

    Invariants (Wave 2 以降):
        - 同住所ペアの 2 人目 (B) は ``end_time`` が ``service_minutes`` より長くなる.
          具体的には ``B.end_time - B.start_time = a.service_minutes + b.service_minutes``
          (= ペア合算 60 分占有 / 例: 30+30=60 分).
        - ``B.service_minutes`` 自体は不変 (UI 表示用の本来 30 分は保持).
        - 集計関数 (``calc_course_total_minutes`` 等) は ``service_minutes`` ベースで
          計算するため、合算占有による影響を受けない.
        - A 側は ``A.end_time - A.start_time = a.service_minutes`` で通常通り.
        - ``end_time != start_time + service_minutes`` という不変量崩しは **B のみ** に
          限定される. ペア以外 visit は従来通り.
    """
    if len(sv) < 2:
        return sv

    wd_jp = _weekday_jp(weekday)

    # Phase G-88 Step3: config 注入時のみ昼休み取得時間帯を差し替え. config=None
    # は module 定数 (LUNCH_EARLIEST_START 11:30 / LUNCH_LATEST_END 13:30) を使い
    # 挙動不変. lunch 圧迫 warning 判定 (_is_in_lunch_break / 残空き計算) に効く.
    _lunch_window_start = config.lunch_window_start if config is not None else LUNCH_EARLIEST_START
    _lunch_window_end = config.lunch_window_end if config is not None else LUNCH_LATEST_END

    # Phase E-3 改修 (4): 同住所 3 名以上の自動別コース化.
    # H2 enforce (_enforce_h2_same_address / _enforce_h2_split_overflow) で
    # 同 (office, weekday, am_pm) 内の別 set に分散しきれなかった残存 3 名以降を
    # ここで unassigned に流す. 「auto_allocator で自動別コース化 + warning」
    # (User 確定仕様).
    #
    # 選定基準 (deterministic; Layer 3 Wave 5 と整合):
    #   1. 固定時刻 patient は守る (= 残す).
    #   2. 残りは preferred_start (= 希望時刻) 昇順 で評価し、2 名目までを残す.
    #   3. 3 名目以降 (= preferred_start が遅い側 or patient_id deterministic tie-break)
    #      を unassigned に流す.
    if unassigned_visit_ids is not None:
        # 同住所バケットごとに同コース内の visits を集約.
        addr_groups: dict[tuple[float, float], list[V2Visit]] = {}
        for v in sv:
            if v.lat is None or v.lng is None or v.course_code is None:
                continue
            key = _address_bucket(v.lat, v.lng)
            addr_groups.setdefault(key, []).append(v)
        for addr_key, group in addr_groups.items():
            if len(group) < 3:
                continue

            # 固定時刻を最優先で残し、残りは preferred_start (なければ start_time)
            # 昇順 → patient_id 文字列で tie-break (deterministic) で並べる.
            def _sort_key(v: V2Visit) -> tuple[int, str, str]:
                # 0 = 固定 (絶対残す), 1 = それ以外
                fixed_rank = 0 if v.time_type == "固定" else 1
                ps = _parse_hhmm(v.preferred_start)
                ps_str = _fmt_hhmm(ps) if ps is not None else _fmt_hhmm(v.start_time)
                return (fixed_rank, ps_str, str(v.patient_id))

            sorted_group = sorted(group, key=_sort_key)
            # 先頭 2 名は残す. 3 名目以降を unassigned に流す.
            for excess in sorted_group[2:]:
                # 固定時刻 patient は守る (= 動かさない). 残り 3 名すべてが固定の
                # ケースでは sorted_group[:2] に固定が 2 名入って残り 1 名 (固定) を
                # unassigned に出すことになるが、これは「固定時刻同住所 3 名以上」
                # という運用上稀なケースで manual review 必須.
                # User 確定仕様で「3 名重なる場合は別コース/別時間にずらす」のため
                # 固定 3 名でも warning + 配置維持ではなく unassigned に流す.
                excess.course_code = None
                unassigned_visit_ids.add(id(excess))
                excess_name = excess.patient_name or (excess.patient_code or "不明")
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"{office_name} {course_code or '?'} コース {wd_jp}: "
                            f"3 名以上の同住所患者 ({excess_name} 様 ほか, "
                            f"住所バケット {addr_key[0]:.4f},{addr_key[1]:.4f}) を "
                            "別コース移動推奨 — 3 名目以降を未割当に移動 "
                            "(同住所は最大 2 名でペア化)"
                        ),
                        weekday=weekday,
                        actionable=True,
                        patient_id=excess.patient_id,
                        patient_name=excess.patient_name,
                        affected_patient_ids=[excess.patient_id],
                    )
                )
            # 残した 2 名以外を sv から除外する (in-place モディファイ; 後続のペア走査が
            # 通常通り進む).
        # sv 自体から course_code=None になった excess visit を除く (返り値 list は
        # 同じインスタンスを保持しつつ in-place で更新するため、外側 list を編集).
        sv[:] = [v for v in sv if v.course_code is not None]
        if len(sv) < 2:
            return sv

    # 「同住所連番」前提なので隣接走査でペアを拾う.
    # 既に処理済みの index は skip (3 連続ケースで 2 名目以降を二重処理しないため).
    i = 0
    while i < len(sv) - 1:
        a = sv[i]
        b = sv[i + 1]
        if (
            a.lat is None
            or a.lng is None
            or b.lat is None
            or b.lng is None
            or _address_bucket(a.lat, a.lng) != _address_bucket(b.lat, b.lng)
            or a.patient_id == b.patient_id
        ):
            i += 1
            continue

        a_fixed = a.time_type == "固定"
        b_fixed = b.time_type == "固定"

        aligned_start: time
        skip_align = False
        if a_fixed and b_fixed:
            if a.start_time == b.start_time:
                aligned_start = a.start_time
            else:
                # 両者固定で時刻不一致: 揃えず warning.
                a_name = a.patient_name or (a.patient_code or "不明")
                b_name = b.patient_name or (b.patient_code or "不明")
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"{office_name} {course_code or '?'} コース {wd_jp}: "
                            f"{a_name} 様 ({_fmt_hhmm(a.start_time)} 固定) と "
                            f"{b_name} 様 ({_fmt_hhmm(b.start_time)} 固定) は "
                            f"同住所だが両者固定で時刻不一致のため揃えられません "
                            "(現場で再調整してください)"
                        ),
                        weekday=weekday,
                        actionable=True,
                        patient_id=b.patient_id,
                        patient_name=b.patient_name,
                        affected_patient_ids=[a.patient_id, b.patient_id],
                    )
                )
                skip_align = True
                # フォールバック値 (型システム用): align しないので未使用.
                aligned_start = a.start_time
        elif a_fixed and not b_fixed:
            aligned_start = a.start_time
        elif b_fixed and not a_fixed:
            aligned_start = b.start_time
        else:
            # 両者非固定: 早い方 (= sort 済の先頭 A) に揃える.
            aligned_start = a.start_time

        if skip_align:
            i += 1
            continue

        # 他方の window 検証 (片方固定 / 両者非固定): preferred_end を超えると warning.
        # 揃え先 = aligned_start が他方の preferred 範囲内かをチェック.
        def _check_window(v: V2Visit, target: time) -> None:
            if v.time_type in ("時間帯", "午前", "午後"):
                pe = _parse_hhmm(v.preferred_end)
                ps = _parse_hhmm(v.preferred_start)
                window_lower = ps if ps is not None else None
                window_upper = pe if pe is not None else None
                out_of_range = False
                if window_lower is not None and target < window_lower:
                    out_of_range = True
                if window_upper is not None and target > window_upper:
                    out_of_range = True
                if out_of_range:
                    v_name = v.patient_name or (v.patient_code or "不明")
                    warnings.append(
                        V2Warning(
                            type="general",
                            message=(
                                f"{office_name} {course_code or '?'} コース {wd_jp}: "
                                f"{v_name} 様 を同住所ペアと同時刻 "
                                f"{_fmt_hhmm(target)} に揃えたところ希望時間帯 "
                                f"({v.preferred_start or '-'}-{v.preferred_end or '-'}) を超過"
                            ),
                            weekday=weekday,
                            actionable=True,
                            patient_id=v.patient_id,
                            patient_name=v.patient_name,
                            current_time=_fmt_hhmm(target),
                            time_type=v.time_type,
                            preferred_start=v.preferred_start,
                            preferred_end=v.preferred_end,
                            affected_patient_ids=[v.patient_id],
                        )
                    )

        if a_fixed and not b_fixed:
            _check_window(b, aligned_start)
        elif b_fixed and not a_fixed:
            _check_window(a, aligned_start)
        elif not a_fixed and not b_fixed:
            # 早い方に揃えるので a 側は問題なし. b 側 window のみ検証.
            _check_window(b, aligned_start)

        # 揃え確定: A.start = B.start = aligned_start, A.end = aligned + A.service.
        # B.end = aligned + max(A.service + B.service, SAME_ADDRESS_PAIR_MIN_OCCUPANCY).
        a.start_time = aligned_start
        a.end_time = _add_minutes(aligned_start, a.service_minutes)
        b.start_time = aligned_start
        # Phase E-3 改修 (3): 同住所ペアの最低占有を 90 分に引き上げ.
        # User 確定仕様 = ``max(service 合計, 90)``. 例:
        #   - service 35+35=70 分 → 90 分占有 (ペア間スイッチ + 説明補助等の余裕)
        #   - service 50+50=100 分 → 100 分占有 (合計を採用)
        # 次 visit の earliest_start = b.end_time + travel + buffer なので
        # 自動で 90 分占有が伝播する.
        pair_occupancy = max(a.service_minutes + b.service_minutes, SAME_ADDRESS_PAIR_MIN_OCCUPANCY)
        b.end_time = _add_minutes(aligned_start, pair_occupancy)

        # lunch 跨ぎ判定 (Wave 3 #WAVE3: lunch コース別動的化).
        # ここでは同住所ペア align 直後 (= compute_lunch_window 前) なので、
        # 「ペアが lunch を取れる余地を残しているか」を ``_is_in_lunch_break``
        # (= 11:30-13:30 の最広範囲で 30 分 lunch も避けられない区間判定) で
        # ざっくり警告する.
        pair_start_t = aligned_start
        pair_end_t = _add_minutes(aligned_start, pair_occupancy)
        pair_blocks_lunch_physically = _is_in_lunch_break(
            pair_start_t,
            pair_end_t,
            window_start=_lunch_window_start,
            window_end=_lunch_window_end,
        )
        if pair_blocks_lunch_physically:
            a_name = a.patient_name or (a.patient_code or "不明")
            b_name = b.patient_name or (b.patient_code or "不明")
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"{office_name} {course_code or '?'} コース {wd_jp}: "
                        f"同住所ペア {a_name} 様 + {b_name} 様 "
                        f"({_fmt_hhmm(aligned_start)} 占有 "
                        f"{pair_occupancy} 分) が "
                        f"昼休憩 ({_fmt_hhmm(_lunch_window_start)}-"
                        f"{_fmt_hhmm(_lunch_window_end)} の動的枠) と重なります"
                    ),
                    weekday=weekday,
                    actionable=True,
                    patient_id=b.patient_id,
                    patient_name=b.patient_name,
                    affected_patient_ids=[a.patient_id, b.patient_id],
                )
            )
        else:
            # Wave 5 (Phase E-3 HIGH cleanup): 同住所ペアが lunch window
            # [11:30, 13:30] と重なるが ``_is_in_lunch_break`` では False
            # (= AM/PM どちらかに 30 分 lunch を寄せれば回避可能) のケースでも、
            # ``LUNCH_DURATION_FALLBACK`` (= 45 分) 以上の連続空きを残せないと
            # ``compute_lunch_window`` 側で 30 分 fallback warning が出る.
            # その diagnostic として「同住所ペアが原因」を明示する warning を
            # 別途 emit する. 物理占有不可能 (上記 if 側) と重複しないよう
            # else 分岐に置く. User 確定仕様 = 45-60 分の lunch 確保.
            pair_start_min_local = _time_to_min(pair_start_t)
            pair_end_min_local = _time_to_min(pair_end_t)
            lunch_start_min_local = _time_to_min(_lunch_window_start)  # 既定 11:30
            lunch_end_min_local = _time_to_min(_lunch_window_end)  # 既定 13:30
            overlap_start = max(pair_start_min_local, lunch_start_min_local)
            overlap_end = min(pair_end_min_local, lunch_end_min_local)
            if overlap_start < overlap_end:
                am_remaining = max(0, pair_start_min_local - lunch_start_min_local)
                pm_remaining = max(0, lunch_end_min_local - pair_end_min_local)
                max_continuous = max(am_remaining, pm_remaining)
                if max_continuous < LUNCH_DURATION_FALLBACK:
                    a_name = a.patient_name or (a.patient_code or "不明")
                    b_name = b.patient_name or (b.patient_code or "不明")
                    warnings.append(
                        V2Warning(
                            type="same_address_consolidation",
                            message=(
                                f"{office_name} {course_code or '?'} コース {wd_jp}: "
                                f"同住所ペア ({a_name} 様 + {b_name} 様) が "
                                f"{_fmt_hhmm(pair_start_t)}-{_fmt_hhmm(pair_end_t)} の "
                                f"{pair_occupancy} 分枠で lunch window を圧迫し、"
                                f"{LUNCH_DURATION_FALLBACK} 分以上の昼休憩が確保できません "
                                f"(残空き最大 {max_continuous} 分)"
                            ),
                            weekday=weekday,
                            actionable=True,
                            patient_id=b.patient_id,
                            patient_name=b.patient_name,
                            affected_patient_ids=[a.patient_id, b.patient_id],
                        )
                    )

        # 3 名連続同住所は H2 enforce 想定だが残存ケースに備えて、
        # ペア化済みの a/b は再走査しないよう i += 2 で進める.
        # 3 名目以降は single としてそのまま earliest_start で処理される.
        i += 2

    return sv


def _apply_corrections_to_visits(
    visits: list[V2Visit],
    *,
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
    config: SchedulingConfig | None = None,
) -> set[int]:
    """Phase G-21 W1: 4 経路統合のための共通補正 helper.

    Phase G-88 Step3: ``config`` を ``apply_travel_corrections`` へそのまま伝播する.
    ``config=None`` は module 定数を使い挙動不変.

    呼び出し側 4 経路:
        - ``run_v2_pipeline``        (全面最適化)
        - ``reset_visits_to_fixed``  (mode='auto' のみ; legacy はスキップ)
        - ``apply_week_only``        (週限定反映)
        - ``apply_individual_proposal`` (1 患者提案採用)

    Phase G-21 final C4: pinned visit を **制約計算 (=入力) には含める** が、
    補正後の output で ``start_time`` / ``end_time`` / ``course_code`` は元に
    戻す ("監視のみ"). 旧実装は pinned を 「除外」 していたため、 非 pinned visit
    が pinned 周辺の移動時間 / 同時刻衝突 / lunch 調整を見られなかった.
    新実装は全 visit を ``apply_travel_corrections`` に渡し、 pinned の値だけ
    snapshot から post-restore することで、 pinned は動かないまま、 周辺 visit
    が pinned を考慮した補正を受けられるようにする.

    Returns:
        ``apply_travel_corrections`` と同じ意味の集合: course_code=None に書き換え
        られた visit の ``id(v)`` 集合.
    """
    if not visits:
        return set()
    # pinned visit の start/end/course を snapshot. 補正後に id(v) で post-restore.
    # `id(v)` snapshot key は呼出し中の同一 Python オブジェクトに紐付くため
    # 安定して照合できる (apply_travel_corrections は in-place 編集で v を返す).
    pinned_visits = [v for v in visits if v.is_pinned]
    pinned_snapshot: dict[int, tuple[time, time, str | None]] = {
        id(v): (v.start_time, v.end_time, v.course_code) for v in pinned_visits
    }
    # Phase G-21 final C4: 入力には全 visit (pinned 含む) を渡す.
    unassigned = apply_travel_corrections(
        visits,
        warnings=warnings,
        office_name_by_id=office_name_by_id,
        config=config,
    )
    # pinned visit の値を snapshot から復元する ("監視のみ" — 制約計算には参加
    # するが、 自身の時刻 / コースは絶対に動かない).
    for v in pinned_visits:
        st, et, cc = pinned_snapshot[id(v)]
        v.start_time = st
        v.end_time = et
        v.course_code = cc
        # pinned visit が `course_code=None` に書き換えられた場合 (= 補正で
        # unassigned 扱いになった) でも post-restore で元 course を復元するため、
        # travel_unassigned_ids 集合からも除外して「物理不可能」扱いを取り消す.
        unassigned.discard(id(v))
    return unassigned


def apply_travel_corrections(
    visits: list[V2Visit],
    *,
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
    config: SchedulingConfig | None = None,
) -> set[int]:
    """Wave 1 (#115): visit list に対し時刻補正フル一式を適用する public helper.

    Phase G-88 Step3: ``config`` を ``_apply_travel_time_to_courses`` へ伝播する.
    ``config=None`` は module 定数を使い挙動不変.

    補正内容 (``_apply_travel_time_to_courses`` から切り出し / Wave 2 追加):
        1. ``(office_id, weekday, course_code)`` ごとに grouping.
        2. ``start_time`` 昇順ソート.
        3. ``_reorder_same_address_consecutive`` で同住所連番化.
        4. ``_auto_shift_same_time_conflicts`` で異住所同時刻を距離最適化 + 後者シフト
           (Fix E; 固定時刻も例外的に動く).
        5. ``_align_same_address_pair_to_same_time`` で同住所ペアを同時刻 + 倍 duration
           占有化 (Wave 2 / #115).
        6. earliest_start 再計算ループ (travel + buffer + 5 分切り上げ + lunch 再検証).
        7. shortage 判定 (固定で >= ``SHORTAGE_THRESHOLD_MIN`` 分不足 → 物理不可能).

    呼び出し経路:
        - ``run_v2_pipeline``: 全面最適化提案を返す前.
        - ``apply_week_only``: visit_plans を DB INSERT 前に補正.
        - ``reset_visits_to_fixed``: PFV から再生成した visit を DB INSERT 前に補正.
        - ``apply_individual_proposal``: 提案 PFV を DB UPDATE 前に補正.

    In-place: visits の ``start_time`` / ``end_time`` を書き換える.

    Returns:
        ``course_code = None`` に書き換えた visit の ``id(v)`` 集合.
        呼び出し側はこの ID 集合の visit を INSERT 対象から除外すること
        (= ``unassigned`` 扱いで物理不可能だったもの).
    """
    return _apply_travel_time_to_courses(
        visits, warnings=warnings, office_name_by_id=office_name_by_id, config=config
    )


def _evaluate_care_alarm_for_visits(
    visits: list[V2Visit],
    *,
    weekday: int,
    course_code: str | None,
    office_name: str,
    warnings: list[V2Warning],
    unassigned_visit_ids: set[int],
) -> None:
    """Wave 4 (Phase C): 1 コース内の visit リストに対しケアアラーム閾値判定を行う.

    各 visit (time_type=固定 / 時間帯 のみ対象) について、確定した ``start_time``
    が希望時刻からどれだけ乖離しているか (``_compute_preferred_time_deviation``)
    を評価し、3 段階で扱う:
        - dev <= ``CARE_ALARM_WARNING_THRESHOLD_MIN`` (= 30): silent.
        - 30 < dev <= ``CARE_ALARM_UNASSIGNED_THRESHOLD_MIN`` (= 60):
          warning emit (``V2WarningType="care_alarm_deviation"``), 配置は維持.
        - dev > 60: ``course_code=None`` + ``unassigned_visit_ids`` に追加,
          warning emit (``care_alarm_deviation``; 後段 ``_classify_warning_reason``
          で ``UnassignedReason="care_alarm_exceeded"`` に紐づく).

    既に ``course_code=None`` の visit (物理不可能で外された等) はスキップ.
    """
    for cur in visits:
        if cur.course_code is None:
            continue
        tt = cur.time_type
        if tt not in ("固定", "時間帯"):
            continue
        ps = _parse_hhmm(cur.preferred_start)
        pe = _parse_hhmm(cur.preferred_end)
        deviation_min = _compute_preferred_time_deviation(
            actual_start=cur.start_time,
            time_type=tt,
            preferred_start=ps,
            preferred_end=pe,
        )
        if deviation_min <= CARE_ALARM_WARNING_THRESHOLD_MIN:
            continue
        wd_jp = _weekday_jp(weekday)
        cur_name = cur.patient_name or (cur.patient_code or "不明")
        window_label = (
            f"{cur.preferred_start or '-'}-{cur.preferred_end or '-'}"
            if tt == "時間帯"
            else (cur.preferred_start or "-")
        )
        if deviation_min > CARE_ALARM_UNASSIGNED_THRESHOLD_MIN:
            # 60 分超: unassigned + reason=care_alarm_exceeded.
            cur.course_code = None
            unassigned_visit_ids.add(id(cur))
            warnings.append(
                V2Warning(
                    type="care_alarm_deviation",
                    message=(
                        f"{office_name} {course_code} {wd_jp}: "
                        f"{cur_name} 様 ({tt} 希望 {window_label}) が "
                        f"{_fmt_hhmm(cur.start_time)} 配置で "
                        f"{deviation_min} 分の乖離 "
                        f"({CARE_ALARM_UNASSIGNED_THRESHOLD_MIN} 分超) — "
                        "ケアアラーム閾値超過のため未割当に移動"
                    ),
                    weekday=weekday,
                    actionable=True,
                    patient_id=cur.patient_id,
                    patient_name=cur.patient_name,
                    current_time=_fmt_hhmm(cur.start_time),
                    time_type=tt,
                    preferred_start=cur.preferred_start,
                    preferred_end=cur.preferred_end,
                    affected_patient_ids=[cur.patient_id],
                )
            )
        else:
            # 30 < dev <= 60: warning + 配置維持.
            warnings.append(
                V2Warning(
                    type="care_alarm_deviation",
                    message=(
                        f"{office_name} {course_code} {wd_jp}: "
                        f"{cur_name} 様 ({tt} 希望 {window_label}) が "
                        f"{_fmt_hhmm(cur.start_time)} 配置で "
                        f"{deviation_min} 分の乖離 "
                        f"(>{CARE_ALARM_WARNING_THRESHOLD_MIN} 分; 配置は維持)"
                    ),
                    weekday=weekday,
                    actionable=True,
                    patient_id=cur.patient_id,
                    patient_name=cur.patient_name,
                    current_time=_fmt_hhmm(cur.start_time),
                    time_type=tt,
                    preferred_start=cur.preferred_start,
                    preferred_end=cur.preferred_end,
                    affected_patient_ids=[cur.patient_id],
                )
            )


def _apply_travel_time_to_courses(
    visits: list[V2Visit],
    *,
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
    config: SchedulingConfig | None = None,
) -> set[int]:
    """W41 v2 拡張 (動的 start_time): 同コース連続訪問に移動時間を反映する.

    Phase G-88 Step3: ``config`` 注入時のみ buffer (``config.visit_buffer_min``) /
    移動速度 (``config.travel_speed_kmh``) / 営業終了境界 (``config.business_end``;
    旧 ``PM_BLOCK_END``) / 昼休み (``config.lunch_*``; ``compute_lunch_window`` 経由)
    を差し替える. ``config=None`` は module 定数を使い挙動不変.
    **正午 12:00 境界 (AM_BLOCK_END / PM_BLOCK_START) の内部構造は今回据え置き**
    (営業の開始・終了境界のみ config 化).

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
               12:00 以降になる場合は ``lunch_end_t`` (動的) にバンプ可能なら
               午後扱いで配置, 不可なら earliest 維持 + warning.
             - "午後": ``PM_BLOCK_END`` 以前で earliest を採用 (>=13:00 制約).
               18:00 超なら earliest 維持 + actionable warning.
             - "終日" / None: 営業時間内なら制約なく earliest を採用.
        4. 各 visit の actual_start が確定したら **動的 lunch slot との重なりを
           再検証** (CRITICAL #1): 重なる場合は ``time_type`` に応じて
           ``lunch_end_t`` にバンプ or warning を出す.
           (_filter_unavailable_and_lunch は既に実行済みのため
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
    # Phase G-88 Step3: 有効値を解決. config=None は module 定数で挙動不変.
    _buffer_min = config.visit_buffer_min if config is not None else VISIT_BUFFER_MINUTES
    _speed_kmh = config.travel_speed_kmh if config is not None else TRAVEL_SPEED_KMH
    # 営業終了境界 (旧 PM_BLOCK_END = 18:00) のみ config 化. 正午境界は据え置き.
    _business_end = config.business_end if config is not None else PM_BLOCK_END
    # 昼休み標準長 / 取得時間帯 (compute_lunch_window へ渡す).
    _lunch_duration = config.lunch_duration_min if config is not None else LUNCH_DURATION_PREFERRED
    _lunch_window_start = config.lunch_window_start if config is not None else LUNCH_EARLIEST_START
    _lunch_window_end = config.lunch_window_end if config is not None else LUNCH_LATEST_END

    # 1) コードごとに集計
    groups: dict[tuple[UUID, int, str | None], list[V2Visit]] = {}
    for v in visits:
        if v.course_code is None:
            continue
        groups.setdefault((v.office_id, v.weekday, v.course_code), []).append(v)

    # Wave 3 (#WAVE3): lunch はコース別に動的決定する.
    # ここで初期化する ``lunch_start_min`` / ``lunch_end_min`` は **コース確定前の
    # フォールバック値** (標準枠 12:00-13:00). 各コースに入った直後に
    # ``compute_lunch_window`` で上書きする.
    pm_block_end_min = _business_end.hour * 60 + _business_end.minute  # 既定 18:00 = 1080

    # 物理不可能配置として course から外した visit の id(v) 集合.
    # 呼び出し側 (run_v2_pipeline) が after_visits からの除去に使う.
    unassigned_visit_ids: set[int] = set()

    for (office_id, weekday, course_code), gv in groups.items():
        if len(gv) < 2:
            # 単独 visit のコース: travel/lunch 補正は不要だが、Wave 4 (Phase C)
            # ケアアラーム判定だけは単独 visit にも適用する.
            # (実運用ではほぼ発火しないが、ペア成立前の中間状態 / 1 件しか配置
            # できない週などで起こりうる).
            _evaluate_care_alarm_for_visits(
                gv,
                weekday=weekday,
                course_code=course_code,
                office_name=(office_name_by_id or {}).get(office_id) or str(office_id),
                warnings=warnings,
                unassigned_visit_ids=unassigned_visit_ids,
            )
            continue
        sv = sorted(gv, key=lambda x: x.start_time)
        # 同住所連番強制 (ユーザー要望 最重要): 同住所ペアは配列上で必ず隣接させる.
        # 間に別住所が挟まると同住所メリット (移動 0 + バッファー 0) が消えるため、
        # earliest_start 再計算の前にリオーダーする.
        sv = _reorder_same_address_consecutive(sv, warnings=warnings)
        wd_jp = _weekday_jp(weekday)
        office_name = (office_name_by_id or {}).get(office_id) or str(office_id)
        # Fix E (CareFlow): 異住所同時刻 2 名以上の自動シフト + 距離最適化.
        # 同コース内で同 start_time の visit が異住所で 2 名以上見つかったら、
        # 順序を距離最適化で決めた上で後者の時刻を強制シフトする
        # (固定時刻でも例外的に動かす). 同住所同時刻ペア (家族・施設) は不変.
        # 同住所連番強制の直後 + earliest_start 再計算の直前で呼ぶことで、
        # シフト後の時刻に対し lunch / PM / shortage 判定が正しく走る.
        sv = _auto_shift_same_time_conflicts(
            sv,
            office_name=office_name,
            course_code=course_code,
            weekday=weekday,
            warnings=warnings,
            config=config,
        )
        # Wave 2 (#115) + Phase E-3 改修 (3)(4): 同住所ペアを同 start_time + 90 分
        # 占有 (= max(service 合計, 90)) に揃える. 同コース内 3 名以上は 3 名目以降を
        # unassigned に流す (auto_allocator 自動別コース化).
        # 距離最適化シフト (異住所) の後、earliest_start 再計算の前に呼ぶ.
        sv = _align_same_address_pair_to_same_time(
            sv,
            warnings=warnings,
            weekday=weekday,
            course_code=course_code,
            office_name=office_name,
            unassigned_visit_ids=unassigned_visit_ids,
            config=config,
        )

        # Wave 3 (#WAVE3): このコース用の lunch slot を動的決定する.
        # ペア align 後の占有区間を元に compute_lunch_window を呼ぶ.
        #
        # Phase B 修正: lunch=None (45 分も取れない) の扱いを変更.
        # 旧実装は標準枠 12:00-13:00 へ強制フォールバックしていたが、
        # これにより「lunch なし + warning」が想定の密集コースで 12:00-13:00 に
        # 入る visit が再 bump 対象になっていた. 新実装は
        # ``lunch_re_validate_enabled = False`` で lunch 再検証ブロック
        # (CRITICAL #1) をスキップする. compute_lunch_window が既に warning を
        # 出しているため運用者へは別経路で伝わる.
        # ``lunch_start_t`` / ``lunch_end_t`` (time オブジェクト) は AM bump
        # (line ~3303) や warning ラベル用途に残し、標準枠を仮値として保持する.
        lunch_window = compute_lunch_window(
            sv,
            warnings=warnings,
            weekday=weekday,
            course_code=course_code,
            office_name=office_name,
            duration=_lunch_duration,
            window_start=_lunch_window_start,
            window_end=_lunch_window_end,
        )
        lunch_re_validate_enabled = lunch_window is not None
        if lunch_window is None:
            # AM bump / warning label 用フォールバック (標準枠 12:00-13:00).
            lunch_start_t = LUNCH_DEFAULT_START
            lunch_end_t = LUNCH_DEFAULT_END
        else:
            lunch_start_t, lunch_end_t = lunch_window

        cumulative_travel_min = 0
        for i in range(1, len(sv)):
            prev = sv[i - 1]
            cur = sv[i]
            # 同住所は移動 0 (バッファーも不要 — 同アパート内の連続訪問は次室移動が最小限).
            if _address_bucket(prev.lat, prev.lng) == _address_bucket(cur.lat, cur.lng):
                travel_min = 0
                buffer_min = 0
            else:
                travel_min = haversine_minutes(
                    haversine_km(prev.lat, prev.lng, cur.lat, cur.lng), speed_kmh=_speed_kmh
                )
                buffer_min = _buffer_min
            cumulative_travel_min += travel_min

            # Wave 2 (#115): 同住所ペアの 2 人目は ``_align_same_address_pair_to_same_time``
            # で start_time = prev.start_time (= aligned) + end_time = prev.start +
            # prev.service + cur.service に既に設定済み. earliest_start で上書きせず
            # そのまま維持する (= ペア占有時間を確保).
            same_address_pair_second = (
                prev.start_time == cur.start_time
                and _address_bucket(prev.lat, prev.lng) == _address_bucket(cur.lat, cur.lng)
                and prev.patient_id != cur.patient_id
            )
            if same_address_pair_second:
                # cur.start_time / cur.end_time は Wave 2 で設定済み. 何もしない.
                # ただし「同住所ペアの 2 人目」の後ろの visit が earliest = cur.end_time +
                # travel + buffer になることは保証される (cur.end_time = aligned + a + b).
                continue

            desired_start = cur.start_time
            # Phase G-95 (修正2 同住所既存ペア 90 分占有): ``prev`` が「同住所バケットの
            # 別患者」と組む **既存ペア** の一員なら、 そのペア占有は本来
            # ``_align_same_address_pair_to_same_time`` で 90 分 (= max(service 合計,
            # SAME_ADDRESS_PAIR_MIN_OCCUPANCY)) に底上げされる. しかし既に placed 済
            # (= 既存ペアが before から来る / 同時刻トリオが auto_shift で de-align さ
            # れる 等) の場合、 align の 90 分底上げが効かず ``prev.end_time`` が実
            # service 長 (例: 35 分) のまま残る (= 植田 16:35 起点で 16:45 過密提案).
            #
            # ここで earliest_start の起点占有を
            # ``max(prev.end_time, pair_anchor_start + SAME_ADDRESS_PAIR_MIN_OCCUPANCY)``
            # に底上げし、 同住所 2 名 1.5 時間占有を既存ペア相手でも反映する.
            #
            # 検出: ``prev`` と **ペア関係** にある同住所・別患者 visit が同コース (sv)
            # に存在するか. ここで「ペア関係」= 次のいずれか (= 同時刻配置/連続配置):
            #   (a) 同 start_time (= align 済 / 既存同時刻ペア), または
            #   (b) 連続 (一方の end == 他方の start; 同住所は travel 0 + buffer 0 で
            #       隙間なく並ぶため、 auto_shift で de-align された 2 人目は contiguous).
            # 「同建物だが別時刻 (例: 11:00-12:00 と 12:30-13:30 で間に隙間)」の単なる
            # 連続訪問は **ペアではない** ため除外する (= 90 分占有を誤適用しない).
            #
            # ``pair_anchor_start`` は当該ペアクラスタの **最早 start_time** (= de-align
            # 前のペア起点; auto_shift は 2 人目を後ろへずらすため 1 人目の start が
            # anchor として残る). 直前で処理済の「同住所ペアの 2 人目」(= cur が prev
            # とペア) とは別概念で、 こちらは prev 自身が既存ペアの一員かを見る.
            # read-only (in-memory; prev は書き換えない. earliest 計算にのみ反映).
            #
            # Phase G-96: 判定/底上げロジックは ``_same_address_pair_occupancy_end``
            # にヘルパー化し、 段 b (_g94_resolve_cross_patient_double_booking) の
            # ずらし計算でも同一占有規則を再利用する.
            _prev_occupancy_end = _same_address_pair_occupancy_end(prev, sv)
            earliest_start = _add_minutes(_prev_occupancy_end, travel_min + buffer_min)

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
                        # Phase G-98 (懸念②): 物理不可能. ただし cur が「動かせない」
                        # (= pinned もしくは既存 visit source_kind="fixed") の場合、
                        # 不足の原因は直前に差し込まれた movable な pool 提案 (prev) に
                        # ある (例: 植田 15:30 提案を 16:00 固定の菅原の手前に入れたため、
                        # 植田の実働 + 移動で菅原に間に合わない). この場合は既存/固定枠を
                        # 守り、 原因の **prev (pool 提案) を提案不可** にする.
                        # それ以外 (cur 自身が movable な pool 提案) は従来どおり cur を
                        # 未割当にする (= forward 既存挙動を維持).
                        redirect_to_prev = (
                            (cur.is_pinned or cur.source_kind == "fixed")
                            and prev.source_kind == "pool"
                            and not prev.is_pinned
                            and prev.course_code is not None
                            and id(prev) not in unassigned_visit_ids
                        )
                        if redirect_to_prev:
                            prev.course_code = None
                            unassigned_visit_ids.add(id(prev))
                            warnings.append(
                                V2Warning(
                                    type="travel_time_shortage",
                                    message=(
                                        f"{office_name} {course_code} {wd_jp}: "
                                        f"{prev_name} 様 ({_fmt_hhmm(prev.start_time)} 提案) は "
                                        f"実働 + 移動を考慮すると次の "
                                        f"{cur_name} 様 ({_fmt_hhmm(desired_start)} 固定開始) "
                                        f"に間に合わない (必要 {travel_min + buffer_min} 分 "
                                        f"= 移動 {travel_min} 分 + バッファー {buffer_min} 分 / "
                                        f"{shortage} 分不足) — 既存の固定枠を優先し提案不可"
                                    ),
                                    weekday=weekday,
                                    actionable=True,
                                    patient_id=prev.patient_id,
                                    patient_name=prev.patient_name,
                                    current_time=_fmt_hhmm(prev.start_time),
                                    time_type=prev.time_type,
                                    preferred_start=prev.preferred_start,
                                    preferred_end=prev.preferred_end,
                                    affected_patient_ids=[prev.patient_id, cur.patient_id],
                                )
                            )
                        else:
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
                window_upper = pe if pe is not None else _business_end
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
                # 12:00 を超える場合は lunch_end_t (動的) にバンプ (午後扱い) を試す.
                # それでも収まらない (18:00 超) なら earliest 維持 + warning.
                # Wave 3 (#WAVE3): バンプ先は固定 13:00 ではなく当該コースの
                # ``lunch_end_t`` (= compute_lunch_window の終了時刻; 12:15〜13:30
                # の範囲).
                #
                # MEDIUM #2 TODO (Wave 3.5): AM/PM 境界 (現在は AM_BLOCK_END=12:00,
                # PM_BLOCK_START=13:00) も lunch window と動的連動させたい.
                # 現状の挙動: lunch_end_t=13:30 にバンプされた AM 希望 visit は
                # 「13:00-13:30 のグレーゾーン」に入ると AM/PM 判定が flaky
                # (= PM_BLOCK_START=13:00 以降のため PM 扱いだが、本来は AM 希望).
                # 暫定運用: AM block は 12:00 まで、PM block は 13:00 から、
                # 13:00-13:30 は PM 扱い (= lunch end が 13:30 にずれていても
                # PM_BLOCK_START 自体は 13:00 のまま). Wave 3.5 で
                # PM_BLOCK_START を動的に lunch_end_t と連動させる予定.
                #
                # Edge case (Phase B reviewer 2nd round): lunch_window=None (= 45 分も lunch
                # を取れない密集コース) でもバンプ先は LUNCH_DEFAULT_END=13:00 になる.
                # 13:00 が既に他 visit で埋まっている場合の衝突は本ブロックでは検出しない
                # (= 後段 _detect_cross_address_time_conflicts / unique constraint で拾う).
                # dense course は元々破綻しているので運用者は compute_lunch_window の
                # warning を見て手動再分配する想定.
                candidate = max(desired_start, earliest_start)
                if candidate >= AM_BLOCK_END:
                    bumped = lunch_end_t
                    bumped_end_min = bumped.hour * 60 + bumped.minute + cur.service_minutes
                    if bumped_end_min <= pm_block_end_min:
                        # lunch_end 開始 + service が 18:00 内 → 午後にバンプ.
                        actual_start = bumped
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{cur_name} 様 (午前希望) が移動時間で "
                                    f"earliest {_fmt_hhmm(earliest_start)} で 12:00 超過、"
                                    f"{_fmt_hhmm(bumped)} (午後) に繰り下げ"
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
                                    f"{_fmt_hhmm(bumped)} にバンプしても 18:00 を超えるため配置不可"
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
                                f"{_fmt_hhmm(_business_end)} を超過 "
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
                                    f"{_fmt_hhmm(_business_end)} を超過 "
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

            # CRITICAL #1: 昼休憩 (動的 lunch window) 再検証.
            # _filter_unavailable_and_lunch は既に実行済みのため、
            # 動的調整後に lunch break と重なるかを再チェックする.
            # Wave 3 (#WAVE3): 固定 12:00-13:00 ではなく、当該コースの
            # ``lunch_start_t`` / ``lunch_end_t`` (compute_lunch_window 結果) を使う.
            # Phase B 修正: ``lunch_re_validate_enabled = False`` (= lunch=None,
            # 45 分も取れない密集コース) のときは再検証ブロックそのものを skip
            # (= ``lunch_start_min`` / ``lunch_end_min`` の評価も短絡で省略).
            #   分単位 ``lunch_start_min`` / ``lunch_end_min`` はこのブロック内
            #   (bumped_start_min = lunch_end_min 含む) でしか参照されない.
            actual_start_min = actual_start.hour * 60 + actual_start.minute
            actual_end_min = actual_start_min + cur.service_minutes
            lunch_label = f"{_fmt_hhmm(lunch_start_t)}-{_fmt_hhmm(lunch_end_t)}"
            if lunch_re_validate_enabled:
                lunch_start_min = _time_to_min(lunch_start_t)
                lunch_end_min = _time_to_min(lunch_end_t)
                lunch_overlaps = (
                    actual_start_min < lunch_end_min and actual_end_min > lunch_start_min
                )
            else:
                # 値は使われないが、型チェック静止のため初期化しておく.
                lunch_start_min = 0
                lunch_end_min = 0
                lunch_overlaps = False
            if lunch_overlaps:
                if tt == "固定":
                    # 固定時刻は動かさない: 警告のみ.
                    warnings.append(
                        V2Warning(
                            type="travel_time_shortage",
                            message=(
                                f"{office_name} {course_code} {wd_jp}: "
                                f"{cur_name} 様 (固定 {_fmt_hhmm(actual_start)}) が "
                                f"昼休憩 {lunch_label} に重なる "
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
                    # 固定以外: lunch_end_t にバンプして再評価.
                    bumped_start_min = lunch_end_min  # 動的: 12:15〜13:30
                    bumped_end_min_v = bumped_start_min + cur.service_minutes
                    can_bump = True
                    # time_type 別に bump 可否を判定.
                    if tt == "午前":
                        # 午前希望を lunch_end にバンプ — 18:00 内なら可.
                        can_bump = bumped_end_min_v <= pm_block_end_min
                    elif tt == "時間帯":
                        # 時間帯 window 内 (= window_upper 以下) なら可.
                        pe_v = _parse_hhmm(cur.preferred_end)
                        window_upper_v = pe_v if pe_v is not None else _business_end
                        window_upper_min = window_upper_v.hour * 60 + window_upper_v.minute
                        can_bump = bumped_start_min <= window_upper_min
                    elif tt == "午後":
                        # 午後 visit が lunch に被るのは earliest < lunch_end のとき.
                        # lunch_end バンプ + service が 18:00 内なら OK.
                        can_bump = bumped_end_min_v <= pm_block_end_min
                    else:
                        # 終日 / None: 18:00 内ならバンプ.
                        can_bump = bumped_end_min_v <= pm_block_end_min

                    if can_bump:
                        actual_start = lunch_end_t
                        warnings.append(
                            V2Warning(
                                type="travel_time_shortage",
                                message=(
                                    f"{office_name} {course_code} {wd_jp}: "
                                    f"{cur_name} 様 が移動時間で昼休憩 {lunch_label} に "
                                    f"重なるため {_fmt_hhmm(lunch_end_t)} に繰り下げ"
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
                                    f"{cur_name} 様 が移動時間で昼休憩 {lunch_label} に "
                                    f"重なる (time_type={tt or '不明'}, "
                                    f"{_fmt_hhmm(lunch_end_t)} バンプも不可)"
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

        # Wave 4 (Phase C): ケアアラーム閾値 (希望時刻からの乖離) 判定.
        # earliest_start / 5 分切り上げ / lunch バンプ等で actual_start が確定した
        # 後、固定/時間帯 patient の希望時刻との乖離を評価する.
        _evaluate_care_alarm_for_visits(
            sv,
            weekday=weekday,
            course_code=course_code,
            office_name=office_name,
            warnings=warnings,
            unassigned_visit_ids=unassigned_visit_ids,
        )

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
    config: SchedulingConfig | None = None,
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
        total_min = calc_course_total_minutes(gv, config=config)
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
    config: SchedulingConfig | None = None,
) -> list[V2Visit]:
    """H5 + H10: 受入 × 時刻 + 昼休憩枠を除外 (Wave 3: lunch コース別動的).

    Args:
        skip_acceptance: True なら H5 (acceptance_calendar ×) フィルタをスキップ.
            Mode 2 (full_optimize) で使用. 受入カレンダー × は既存スケジュールの
            混雑度を表す動的データであり、既存固定枠ごと再配置する全面最適化では
            制約として意味を持たないため. 昼休憩 (H10) は常に enforce.

    H10 (Wave 3 フレキシブル化):
      - 旧仕様: visit ∩ [12:00, 13:00) → 除外.
      - 新仕様: ``(office_id, weekday, course_code)`` ごとに ``compute_lunch_window``
        で算出した動的 lunch slot と重なる visit を除外する.
        course_code が None (= pool stage で未確定) のコースは同じバケットに
        まとめて lunch を試算する.
    """
    # Phase G-88 Step3: config 注入時のみ昼休み標準長 / 取得時間帯を差し替え.
    # config=None は module 定数 (LUNCH_DURATION_PREFERRED 60 / LUNCH_EARLIEST_START
    # 11:30 / LUNCH_LATEST_END 13:30) を使い挙動不変. _is_in_lunch_break /
    # compute_lunch_window のプレフィルタ判定に効く.
    _lunch_duration = config.lunch_duration_min if config is not None else LUNCH_DURATION_PREFERRED
    _lunch_window_start = config.lunch_window_start if config is not None else LUNCH_EARLIEST_START
    _lunch_window_end = config.lunch_window_end if config is not None else LUNCH_LATEST_END

    # ---------------------------------------------------------------
    # 1) H5: 受入カレンダー × フィルタ (skip_acceptance=False 時のみ).
    # ---------------------------------------------------------------
    stage1: list[V2Visit] = []
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
        stage1.append(v)

    # ---------------------------------------------------------------
    # 2) H10: 動的 lunch (Wave 3 #WAVE3) フィルタ.
    # ---------------------------------------------------------------
    # 段階 a) 「単体で lunch 不可避」visit を弾く (= visit が [11:30, 13:30] の
    #         どこに 45 分 lunch を置いても避けられない区間に入っている).
    # 段階 b) (office_id, weekday, course_code) ごとに ``compute_lunch_window``
    #         を呼び、解決した lunch slot に重なる visit を弾く. lunch 不能の
    #         場合は最も lunch range 中央 (12:00) に近い visit を 1 件外して
    #         再試行する (greedy). 全 visit を外しても lunch 取れない場合は
    #         素通し (後段の補正 / Stage 6 で再警告).
    stage2: list[V2Visit] = []
    for v in stage1:
        if _is_in_lunch_break(
            v.start_time,
            v.end_time,
            window_start=_lunch_window_start,
            window_end=_lunch_window_end,
        ):
            code = v.patient_code or "-"
            name = v.patient_name or "-"
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"{code} {name} 様: {_weekday_jp(v.weekday)} "
                        f"{_fmt_hhmm(v.start_time)} は昼休憩 (動的 lunch 不可避) "
                        "に重なるため配置不可"
                    ),
                    weekday=v.weekday,
                    actionable=False,
                    patient_id=v.patient_id,
                    patient_name=v.patient_name,
                    current_time=_fmt_hhmm(v.start_time),
                )
            )
            continue
        stage2.append(v)

    # (office_id, weekday, course_code) ごとに grouping.
    groups: dict[tuple[UUID, int, str | None], list[V2Visit]] = {}
    for v in stage2:
        groups.setdefault((v.office_id, v.weekday, v.course_code), []).append(v)

    out: list[V2Visit] = []
    noon_min = NOON_HOUR * 60
    range_start_min = _time_to_min(_lunch_window_start)
    range_end_min = _time_to_min(_lunch_window_end)

    def _overlap_with_range(v: V2Visit) -> int:
        s = max(_time_to_min(v.start_time), range_start_min)
        e = min(_time_to_min(v.end_time), range_end_min)
        return max(0, e - s)

    def _exclusion_cost(v: V2Visit) -> tuple[int, int, int]:
        start_min = _time_to_min(v.start_time)
        # 1. 0 if start >= 12:00, 1 if start < 12:00 (= keep AM anchor).
        afternoon_first = 0 if start_min >= noon_min else 1
        return (
            afternoon_first,
            _overlap_with_range(v),
            -abs(start_min - noon_min),
        )

    for (_office_id, _wd, _cc), gv in groups.items():
        # 残存 visit (= lunch 確定後に通す候補) と除外候補.
        anchors: list[V2Visit] = list(gv)
        excluded: list[V2Visit] = []
        lunch: tuple[time, time] | None = compute_lunch_window(
            anchors,
            duration=_lunch_duration,
            window_start=_lunch_window_start,
            window_end=_lunch_window_end,
        )
        # 11:30-13:30 range と重なる visit を「除外コストが低い順」に外していく.
        # 除外コスト判定 (lex order, 小さい順に外す):
        #   1. start_time >= 12:00 (= 午後 / 昼以降側) を優先的に外す.
        #      → 午前固定 (例: 11:30-12:30) は anchor として残しやすい.
        #   2. lunch range への侵入時間が小さい visit を優先.
        #      (range 外側に少しだけ寄っている visit から外す)
        #   3. tie-break: 12:00 から遠い start_time を優先 (= range の端を埋める visit).
        while lunch is None and anchors:
            overlap_candidates = [v for v in anchors if _overlap_with_range(v) > 0]
            if not overlap_candidates:
                # range 外 visit ばかりなら lunch 必ず取れるはず. 想定外で break.
                break
            victim = min(overlap_candidates, key=_exclusion_cost)
            anchors.remove(victim)
            excluded.append(victim)
            lunch = compute_lunch_window(
                anchors,
                duration=_lunch_duration,
                window_start=_lunch_window_start,
                window_end=_lunch_window_end,
            )

        if lunch is None:
            # どうやっても lunch 取れない: 全 visit を素通し (後段の補正で再判定).
            out.extend(gv)
            continue

        # 除外候補は lunch 重複 warning + filter から外す.
        ls, le = lunch
        for v in excluded:
            code = v.patient_code or "-"
            name = v.patient_name or "-"
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"{code} {name} 様: {_weekday_jp(v.weekday)} "
                        f"{_fmt_hhmm(v.start_time)} は昼休憩 "
                        f"({_fmt_hhmm(ls)}-{_fmt_hhmm(le)}) に重なるため配置不可"
                    ),
                    weekday=v.weekday,
                    actionable=False,
                    patient_id=v.patient_id,
                    patient_name=v.patient_name,
                    current_time=_fmt_hhmm(v.start_time),
                )
            )

        # anchors のうち、lunch slot にハマる visit も追加除外
        # (compute_lunch_window はそもそも overlap 無く動くため通常空集合).
        for v in anchors:
            if _lunch_window_overlaps(v.start_time, v.end_time, lunch):
                code = v.patient_code or "-"
                name = v.patient_name or "-"
                warnings.append(
                    V2Warning(
                        type="general",
                        message=(
                            f"{code} {name} 様: {_weekday_jp(v.weekday)} "
                            f"{_fmt_hhmm(v.start_time)} は昼休憩 "
                            f"({_fmt_hhmm(ls)}-{_fmt_hhmm(le)}) に重なるため配置不可"
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


def calc_h_violations(
    visits: list[V2Visit], *, config: SchedulingConfig | None = None
) -> dict[str, int]:
    """H1-H10 の違反件数を集計.

    Phase G-88 Step3: H9 (コース定員超過) の上限を ``config.max_patients_per_course``
    で注入可能にする. ``config=None`` は module 定数 ``MAX_PATIENTS_PER_COURSE`` (= 6)
    を使い挙動不変. full-optimize / diff-add の active path で呼ばれるため、提案
    プレビューの容量超過件数を config と整合させる.
    """
    _max_per_course = (
        config.max_patients_per_course if config is not None else MAX_PATIENTS_PER_COURSE
    )
    # Phase G-88 Step3 残漏れ修正: H10 昼休み窓を config から注入 (H9 と同様).
    # config=None は module 定数 (11:30-13:30) で挙動不変.
    _lws = config.lunch_window_start if config is not None else LUNCH_EARLIEST_START
    _lwe = config.lunch_window_end if config is not None else LUNCH_LATEST_END
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
    h9 = sum(1 for c in by_course.values() if c > _max_per_course)

    # H10: 昼休憩枠と重複
    h10 = sum(
        1
        for v in visits
        if _is_in_lunch_break(v.start_time, v.end_time, window_start=_lws, window_end=_lwe)
    )

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
    op_weekdays_by_office: dict[UUID, set[int]] | None = None,
    office_name_by_id: dict[UUID, str] | None = None,
) -> list[V2Visit]:
    """Before スナップショット: 既存 patient_fixed_visits (mode='normal') から構築.

    ``pending_overlay`` が渡された場合は、PFV 値を Python オブジェクトレベルで上書きする
    (DB / SQLAlchemy セッションには触らない). マスターは絶対に変更しない.
    overlay に該当するキーがあるのに PFV が見つからない場合は warning に記録.

    Phase G-45 (拠点稼働曜日): ``op_weekdays_by_office`` が渡された場合、
    最終 ``v2_office_id`` (= course_template の office_id 由来) が当該 weekday に
    休業の場合は V2Visit を emit せず skip + 重複防止 warning emit.
    """
    # Phase G-45: 拠点休業日 skip 用 dedupe set.
    closed_warned: set[tuple[UUID, int]] = set()
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

    # course_template_id → (CourseTemplate.label, office_id) の map を事前構築 (N+1 回避)
    ct_ids = {pfv.course_template_id for pfv in pfv_rows if pfv.course_template_id is not None}
    ct_label_by_id: dict[UUID, str] = {}
    # Phase G-24: V2Visit.office_id を patient.primary_office_id ではなく
    # course_template の office_id 由来にするための逆引き map.
    # 例: INAGE 拠点患者だが PFV.course_template が TSUGA-A を指す場合 (= Phase G-8 cross-office),
    # Before の Course グループは (TSUGA, weekday, "A") に集計される.
    ct_office_by_id: dict[UUID, UUID] = {}
    if ct_ids:
        ct_rows = await db.scalars(
            select(CourseTemplate).where(
                CourseTemplate.id.in_(ct_ids),
                CourseTemplate.deleted_at.is_(None),
            )
        )
        for ct in ct_rows.all():
            ct_label_by_id[ct.id] = ct.label
            ct_office_by_id[ct.id] = ct.office_id

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
        # Phase G-24: V2Visit.office_id を course_template の office_id 由来にする.
        # patient.primary_office_id を使うと cross-office PFV (Phase G-8) で
        # Before の Course グループが (patient_office, "A") に統合され、
        # 例えば TSUGA-A 行の visit が INAGE-A に混入する問題が起きる.
        course_office_id = (
            ct_office_by_id.get(pfv.course_template_id)
            if pfv.course_template_id is not None
            else None
        )
        v2_office_id = course_office_id or patient.primary_office_id
        # Phase G-45: 拠点休業日 skip (Before legacy 経路).
        if op_weekdays_by_office is not None:
            _op_wd = op_weekdays_by_office.get(v2_office_id)
            if _op_wd is not None and pfv.weekday not in _op_wd:
                _emit_office_closed_warning(
                    warnings=warnings,
                    closed_warned=closed_warned,
                    patient_id=patient.id,
                    patient_name=patient.name,
                    weekday=pfv.weekday,
                    office_id=v2_office_id,
                    office_name_by_id=office_name_by_id,
                )
                continue
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
                office_id=v2_office_id,
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
                # Phase G-30: legacy Before 経路でも pinned PFV の is_pinned=True を
                # V2Visit に流す. diff_add 経路では before_visits → before_copies
                # (dataclasses.replace) 経由で after_visits に流入するため、
                # ここで True を立てておかないと apply_travel_corrections の
                # pinned fence が engage せず時刻が動く可能性がある.
                is_pinned=bool(pfv.is_pinned),
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


async def _load_before_visits_v2(
    db: AsyncSession,
    *,
    patients_by_id: dict[UUID, Patient],
    iso_year: int,
    iso_week: int,
    pending_overlay: dict[tuple[UUID, int], PendingEditOverlay] | None = None,
    warnings: list[V2Warning] | None = None,
    op_weekdays_by_office: dict[UUID, set[int]] | None = None,
    office_name_by_id: dict[UUID, str] | None = None,
    config: SchedulingConfig | None = None,
) -> list[V2Visit]:
    """Phase G-21 T3-1: Before スナップショットを 4 経路 union で構築する.

    4 経路 = (PFV) ∪ (weekly_pattern) ∪ (当週 DB Visit) ∪ (pending_overlay)

    dedupe key = ``(patient_id, weekday, slot_index)``. 同キーで複数経路から候補が
    出た場合の優先順位 (高い順):

        1. 既存 DB Visit (当該週)
        2. pinned PFV (``is_pinned=True``)
        3. 非 pinned PFV (``is_pinned=False``)
        4. weekly_pattern entry

    ``pending_overlay`` は **値の上書き** であって独立した経路ではない:
        上位経路で採用された entry の値を ``(patient_id, weekday)`` キーで上書きする.
        ただし overlay にしか存在しない (= 上位経路に無い) 患者は warning に出る経路を維持.

    Phase G-45 (拠点稼働曜日): ``op_weekdays_by_office`` が渡された場合、
    最終 V2Visit.office_id が当該 weekday に休業の場合は経路採用を skip + 重複防止
    warning emit. 4 経路すべてで適用する.

    Returns:
        ``list[V2Visit]``: Before スナップショット (dedupe 後).
    """
    if not patients_by_id:
        return []

    pending_overlay = pending_overlay or {}
    patient_ids = list(patients_by_id.keys())
    # Phase G-45: 拠点休業日 skip 用 dedupe set.
    closed_warned: set[tuple[UUID, int]] = set()

    # course_template_id → label の事前 map.
    pfv_rows_all = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id.in_(patient_ids),
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.slot_index == 0,
            )
        )
    ).all()
    ct_ids = {p.course_template_id for p in pfv_rows_all if p.course_template_id is not None}
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

    # dedupe key = (patient_id, weekday, slot_index). slot_index は通常 0.
    # 価値順位を上げて優先採用するため、上位採用後は下位経路を skip する.
    chosen: dict[tuple[UUID, int, int], V2Visit] = {}

    def _make_v2_visit_from_pfv(pfv: PatientFixedVisit, patient: Patient) -> V2Visit | None:
        if patient.lat is None or patient.lng is None:
            return None
        if patient.primary_office_id is None:
            return None
        overlay = pending_overlay.get((pfv.patient_id, pfv.weekday))
        if overlay is not None:
            start_time_v = overlay.new_start
            duration_v = _compute_overlay_duration(overlay, existing_duration=pfv.duration_min)
            tt = overlay.new_time_type or _extract_time_type_for_weekday(patient, pfv.weekday)
            ps_str = overlay.new_start_str
            pe_str = (
                overlay.new_end_str
                or _extract_preferred_window_for_weekday(patient, pfv.weekday)[1]
            )
        else:
            start_time_v = pfv.start_time
            duration_v = pfv.duration_min
            tt = _extract_time_type_for_weekday(patient, pfv.weekday)
            ps_str, pe_str = _extract_preferred_window_for_weekday(patient, pfv.weekday)
        end_t = _add_minutes(start_time_v, duration_v)
        am_pm = "am" if start_time_v.hour < NOON_HOUR else "pm"
        course_code = ct_label_by_id.get(pfv.course_template_id) if pfv.course_template_id else None
        addr = patient.address
        # Phase G-21 final C3: pinned PFV から作る V2Visit には is_pinned=True を
        # 立てる. Before / After 両経路で _apply_corrections_to_visits の
        # pinned fence が engage し、 diff_add 等の Before 表示でも pinned 状態が
        # 保持される. 旧実装は常に False で、 diff_add の Before に pinned 印が
        # 出ず、 fence も engage しない問題があった.
        return V2Visit(
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
            course_code=course_code,
            source_kind="fixed",
            address=addr,
            area_label=_extract_area_label(addr),
            time_type=tt,
            sex_restriction=patient.sex_restriction,
            preferred_start=ps_str,
            preferred_end=pe_str,
            requires_multiple_staff=bool(
                getattr(patient, "requires_multiple_staff", False) or False
            ),
            is_pinned=bool(pfv.is_pinned),
        )

    # 経路 1: 既存 DB Visit (当該週). 優先度最高.
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        week_sunday = date.fromisocalendar(iso_year, iso_week, 7)
    except ValueError:
        week_monday = None  # type: ignore[assignment]
        week_sunday = None  # type: ignore[assignment]

    if week_monday is not None and week_sunday is not None and patient_ids:
        visit_rows = (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id.in_(patient_ids),
                    Visit.visit_date >= week_monday,
                    Visit.visit_date <= week_sunday,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
        for v in visit_rows:
            patient = patients_by_id.get(v.patient_id)
            if patient is None or patient.lat is None or patient.lng is None:
                continue
            if patient.primary_office_id is None:
                continue
            wd = v.visit_date.weekday()
            key = (v.patient_id, wd, 0)
            if key in chosen:
                continue
            duration_min = 30
            try:
                # end_time - start_time
                start_dt_min = v.start_time.hour * 60 + v.start_time.minute
                end_dt_min = v.end_time.hour * 60 + v.end_time.minute
                if end_dt_min > start_dt_min:
                    duration_min = end_dt_min - start_dt_min
            except AttributeError:
                pass
            tt = _extract_time_type_for_weekday(patient, wd)
            ps_str, pe_str = _extract_preferred_window_for_weekday(patient, wd)
            am_pm = "am" if v.start_time.hour < NOON_HOUR else "pm"
            addr = patient.address
            # Phase G-45: 拠点休業日 skip (Before 経路 1 = 既存 DB Visit).
            if op_weekdays_by_office is not None:
                _op_wd = op_weekdays_by_office.get(patient.primary_office_id)
                if _op_wd is not None and wd not in _op_wd:
                    _emit_office_closed_warning(
                        warnings=warnings,
                        closed_warned=closed_warned,
                        patient_id=patient.id,
                        patient_name=patient.name,
                        weekday=wd,
                        office_id=patient.primary_office_id,
                        office_name_by_id=office_name_by_id,
                    )
                    continue
            chosen[key] = V2Visit(
                patient_id=patient.id,
                patient_name=patient.name,
                patient_code=patient.code,
                weekday=wd,
                start_time=v.start_time,
                end_time=v.end_time,
                service_minutes=duration_min,
                lat=float(patient.lat),
                lng=float(patient.lng),
                office_id=patient.primary_office_id,
                am_pm=am_pm,  # type: ignore[arg-type]
                source_kind="fixed",
                address=addr,
                area_label=_extract_area_label(addr),
                time_type=tt,
                sex_restriction=patient.sex_restriction,
                preferred_start=ps_str,
                preferred_end=pe_str,
                requires_multiple_staff=bool(
                    getattr(patient, "requires_multiple_staff", False) or False
                ),
            )

    # 経路 2: pinned PFV (is_pinned=True).
    for pfv in pfv_rows_all:
        if not pfv.is_pinned:
            continue
        patient = patients_by_id.get(pfv.patient_id)
        if patient is None:
            continue
        key = (pfv.patient_id, pfv.weekday, pfv.slot_index)
        if key in chosen:
            continue
        v2v = _make_v2_visit_from_pfv(pfv, patient)
        if v2v is not None:
            # Phase G-45: 拠点休業日 skip (Before 経路 2 = pinned PFV).
            if op_weekdays_by_office is not None:
                _op_wd = op_weekdays_by_office.get(v2v.office_id)
                if _op_wd is not None and v2v.weekday not in _op_wd:
                    _emit_office_closed_warning(
                        warnings=warnings,
                        closed_warned=closed_warned,
                        patient_id=patient.id,
                        patient_name=patient.name,
                        weekday=v2v.weekday,
                        office_id=v2v.office_id,
                        office_name_by_id=office_name_by_id,
                    )
                    continue
            chosen[key] = v2v

    # 経路 3: 非 pinned PFV (is_pinned=False).
    for pfv in pfv_rows_all:
        if pfv.is_pinned:
            continue
        patient = patients_by_id.get(pfv.patient_id)
        if patient is None:
            continue
        key = (pfv.patient_id, pfv.weekday, pfv.slot_index)
        if key in chosen:
            continue
        v2v = _make_v2_visit_from_pfv(pfv, patient)
        if v2v is not None:
            # Phase G-45: 拠点休業日 skip (Before 経路 3 = 非 pinned PFV).
            if op_weekdays_by_office is not None:
                _op_wd = op_weekdays_by_office.get(v2v.office_id)
                if _op_wd is not None and v2v.weekday not in _op_wd:
                    _emit_office_closed_warning(
                        warnings=warnings,
                        closed_warned=closed_warned,
                        patient_id=patient.id,
                        patient_name=patient.name,
                        weekday=v2v.weekday,
                        office_id=v2v.office_id,
                        office_name_by_id=office_name_by_id,
                    )
                    continue
            chosen[key] = v2v

    # 経路 4: weekly_pattern entries.
    for patient in patients_by_id.values():
        if patient.lat is None or patient.lng is None:
            continue
        if patient.primary_office_id is None:
            continue
        entries = _extract_weekly_entries(patient, config=config)
        for wd, st, sm, tt_raw, ps_raw, pe_raw in entries:
            key = (patient.id, wd, 0)
            if key in chosen:
                continue
            overlay = pending_overlay.get((patient.id, wd))
            if overlay is not None:
                st_eff = overlay.new_start
                sm_eff = _compute_overlay_duration(overlay, existing_duration=sm)
                tt_eff = overlay.new_time_type or tt_raw
                ps_eff = overlay.new_start_str
                pe_eff = overlay.new_end_str if overlay.new_end_str is not None else pe_raw
            else:
                st_eff = st
                sm_eff = sm
                tt_eff = tt_raw
                ps_eff = ps_raw
                pe_eff = pe_raw
            end_t = _add_minutes(st_eff, sm_eff)
            am_pm = "am" if st_eff.hour < NOON_HOUR else "pm"
            addr = patient.address
            # Phase G-45: 拠点休業日 skip (Before 経路 4 = weekly_pattern).
            if op_weekdays_by_office is not None:
                _op_wd = op_weekdays_by_office.get(patient.primary_office_id)
                if _op_wd is not None and wd not in _op_wd:
                    _emit_office_closed_warning(
                        warnings=warnings,
                        closed_warned=closed_warned,
                        patient_id=patient.id,
                        patient_name=patient.name,
                        weekday=wd,
                        office_id=patient.primary_office_id,
                        office_name_by_id=office_name_by_id,
                    )
                    continue
            chosen[key] = V2Visit(
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
                am_pm=am_pm,  # type: ignore[arg-type]
                source_kind="fixed",
                address=addr,
                area_label=_extract_area_label(addr),
                time_type=tt_eff,
                sex_restriction=patient.sex_restriction,
                preferred_start=ps_eff,
                preferred_end=pe_eff,
                requires_multiple_staff=bool(
                    getattr(patient, "requires_multiple_staff", False) or False
                ),
            )

    # pending_overlay にだけ存在する key (= 上位経路に無い) は warning.
    seen_pfv_keys: set[tuple[UUID, int]] = {(p.patient_id, p.weekday) for p in pfv_rows_all}
    if warnings is not None:
        for ov_key in pending_overlay.keys():
            if ov_key in seen_pfv_keys:
                continue
            pid, wd = ov_key
            if (pid, wd, 0) in chosen:
                continue
            p = patients_by_id.get(pid)
            pname = p.name if p is not None else None
            warnings.append(
                V2Warning(
                    type="general",
                    message=(
                        f"今週限定変更: (patient_id={pid}, weekday={wd}) に対応する "
                        f"固定枠が存在しないためオーバーレイをスキップしました"
                    ),
                    actionable=False,
                    patient_id=pid,
                    patient_name=pname,
                    weekday=wd,
                )
            )

    return list(chosen.values())


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
    # Wave 4 (Phase C): ケアアラーム閾値超過 (希望時刻から 60 分超で配置不可).
    # ``_evaluate_care_alarm_for_visits`` が emit する ``care_alarm_deviation``
    # warning のうち、unassigned に流したケース (message に「ケアアラーム閾値超過」
    # を含む) のみ ``care_alarm_exceeded`` reason に紐づける.
    # 30-60 分の「配置は維持」warning は patient が after_visits に残るため、
    # そもそも ``_identify_unassigned_patients`` の reason 分類対象にならない.
    if wtype == "care_alarm_deviation":
        if "ケアアラーム閾値超過" in msg:
            return ("care_alarm_exceeded", "general")
        # 万一 reason 分類対象になった「配置維持」warning は fixed_time_conflict に寄せる.
        return ("fixed_time_conflict", "general")
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
    # Phase E-3 改修 (4): 同住所 3 名以上を _align_same_address_pair_to_same_time で
    # 自動別コース化した時のメッセージ ("3 名以上の同住所患者を別コース移動推奨" 含む).
    # H2 enforce で逃しきれなかった残存 3 名目以降に対し emit される.
    if (
        wtype == "general"
        and "同住所" in msg
        and ("3 名以上" in msg or "3名以上" in msg)
        and "別コース" in msg
    ):
        return ("same_address_three_or_more", "stage3_set")
    # H2 同住所 3 名以上で別 set に動かしたが配置先なし.
    if wtype == "general" and "同住所" in msg:
        return ("same_address_split", "stage3_set")
    return None


def _identify_unassigned_patients(
    pool_patients: list[Patient],
    after_visits: list[V2Visit],
    warnings: list[V2Warning],
    exclude_patient_ids: set[UUID] | None = None,
) -> list[dict[str, Any]]:
    """Mode 2 (full_optimize) で after_visits に出てこなかった患者と理由を抽出する.

    P2: text 含み判定を撤去し、warning.affected_patient_ids での patient_id 照合に
    切り替えた. fallback で patient.code を message に含む warning も探すが、
    最終 fallback は ``reason="unknown"`` で固定 (旧「原因不明 (...のいずれか)」
    のような曖昧文言は撤去).

    Args:
        exclude_patient_ids: Phase G-92 — 未割当判定から除外する patient_id 集合.
            固定→希望フォールバックが成立した患者は希望候補が proposal として
            残るため (after_visits には痕跡が無くても) 未割当に計上しない.

    Returns:
        ``[{"patient_id": UUID, "patient_name": str, "patient_code": str | None,
        "reason": UnassignedReason, "reason_detail": str | None,
        "dropped_at_stage": UnassignedStage | None}, ...]``
    """
    after_pids = {v.patient_id for v in after_visits}
    excluded = exclude_patient_ids or set()
    out: list[dict[str, Any]] = []
    for p in pool_patients:
        if p.id in after_pids or p.id in excluded:
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


def _dedup_fixed_preferred_candidates(
    pool: list[V2Visit],
) -> tuple[list[V2Visit], dict[tuple[UUID, int], V2Visit]]:
    """Phase G-92 (固定優先→希望フォールバック): 同 ``(patient_id, weekday)`` に
    固定候補 (``pool_origin="fixed"``) と希望候補 (``pool_origin="preferred"``) が
    並存する場合、固定を優先採用し希望候補を脇に退避する.

    本 helper は diff_add で PFV 患者を「固定枠候補 + 希望訪問パターン候補」の
    両方で展開した直後に呼ぶ. 呼び出し前段の ``_filter_conflicting_pool_visits``
    で固定候補が既存訪問との時間衝突 (条件ｳ) により除外されていれば、 ここでは
    希望候補のみが残り、 自動的に希望側へフォールバックする.

    判定方針 (決定性維持):
      - グルーピングキーは ``(patient_id, weekday)``.
      - そのキーに ``pool_origin="fixed"`` が 1 件でも残っていれば、 同キーの
        ``pool_origin="preferred"`` を脇に退避する (= 固定優先).
      - 固定が 0 件なら希望候補をそのまま残す (= フォールバック成立).
      - ``warnings`` には何も積まない (フォールバックは正常動作であり警告でない).

    方針A (オーナー決定・意図的): 固定を持つ患者でも「希望のみ曜日」(固定が無く
    希望だけの weekday) は preferred 提案として出す. 例) 月固定・火希望のみの患者で、
    火曜は同 (patient, weekday) に固定候補が無いため希望候補が退避されず提案に残る.
    これは取りこぼし解消のための意図的挙動であり、 dedup は (patient, weekday) 単位で
    固定優先するだけなので、 別曜日の希望は影響を受けない.

    退避した希望候補は戻り値の 2 番目 ``fallback_by_key`` で返す. 後段で固定候補が
    Stage 5/6 (定員オーバー / 時間不適合) により未割当になった場合、 退避した
    希望候補の時刻情報で固定候補を差し替える (= 遅延フォールバック). 同 (patient,
    weekday) に複数の希望候補がある稀ケースでは最初の 1 件を退避先とする
    (= 決定性).

    Note:
        本 helper は diff_add 経路専用. full_optimize は ``pool_origin`` を
        既定 (``"preferred"``) のまま使うため、 全候補が preferred 扱いとなり
        本 dedup は実質 no-op (= 挙動不変, ``fallback_by_key`` は空).
    """
    if not pool:
        return list(pool), {}

    fixed_keys: set[tuple[UUID, int]] = {
        (v.patient_id, v.weekday) for v in pool if v.pool_origin == "fixed"
    }
    if not fixed_keys:
        return list(pool), {}

    kept: list[V2Visit] = []
    fallback_by_key: dict[tuple[UUID, int], V2Visit] = {}
    for v in pool:
        key = (v.patient_id, v.weekday)
        if v.pool_origin == "preferred" and key in fixed_keys:
            # 同 (patient, weekday) に固定候補があるので希望候補は退避する.
            # 複数あれば最初の 1 件のみ保持 (決定性).
            if key not in fallback_by_key:
                fallback_by_key[key] = v
            continue
        kept.append(v)
    return kept, fallback_by_key


# Phase G-92: 固定枠が入らない 3 条件の理由コード.
#   - time_no_fit  : 前訪問移動 + 実務 service + バッファで固定時刻にルート上
#                    入りきらない (= travel_time_shortage で未割当).
#   - capacity_over: 定員オーバー (MAX_PATIENTS_PER_COURSE / コース数上限超過).
#   - time_conflict: 固定時刻に既存予定と時間重複.
_G92_REASON_TIME_NO_FIT = "time_no_fit"
_G92_REASON_CAPACITY_OVER = "capacity_over"
_G92_REASON_TIME_CONFLICT = "time_conflict"


def _g92_collect_fixed_unavailable_reasons(
    patient_id: UUID,
    weekdays: set[int],
    warnings: list[V2Warning],
) -> list[str]:
    """Phase G-92: 固定枠が入らなかった理由コードを warnings から収集する.

    対象患者 (``patient_id``) の該当曜日 (``weekdays``) に紐づく warning を走査し、
    type から 3 理由コード (time_no_fit / capacity_over / time_conflict) に
    マップする. 複数該当可. 重複は除去し、 決定性のため定義順に並べる.

    照合方針:
      - ``affected_patient_ids`` または ``patient_id`` で患者一致を判定.
      - ``weekday`` が warning にあれば ``weekdays`` との一致も要求 (None は通す).
    """
    found: set[str] = set()
    for w in warnings:
        matched_pid = patient_id in (w.affected_patient_ids or []) or w.patient_id == patient_id
        if not matched_pid:
            continue
        if w.weekday is not None and w.weekday not in weekdays:
            continue
        if w.type == "travel_time_shortage":
            found.add(_G92_REASON_TIME_NO_FIT)
        elif w.type in ("course_capacity", "course_count"):
            found.add(_G92_REASON_CAPACITY_OVER)
        elif w.type == "diff_add_conflict":
            found.add(_G92_REASON_TIME_CONFLICT)
    ordered = [
        _G92_REASON_TIME_NO_FIT,
        _G92_REASON_CAPACITY_OVER,
        _G92_REASON_TIME_CONFLICT,
    ]
    return [r for r in ordered if r in found]


def _g94_resolve_cross_patient_double_booking(
    after_visits: list[V2Visit],
    *,
    pool_visit_ids: set[int],
    warnings: list[V2Warning],
    office_name_by_id: dict[UUID, str] | None = None,
    extra_existing_visits: list[V2Visit] | None = None,
    config: SchedulingConfig | None = None,
) -> set[int]:
    """Phase G-94 (修正2 ダブルブッキング): 同コース他患者と時間重複する pool 提案を解消する.

    背景:
        ``run_v2_pipeline`` の ``diff_add`` では ``after_visits = filtered_before +
        pool_visits`` となり、 既存 visit (PFV 由来など) と新規 pool 提案が同一
        ``(office_id, weekday, course_code)`` に同居しうる. ``_apply_corrections_to_visits``
        の ``_auto_shift_same_time_conflicts`` はコース内同時刻を解消するが、 pinned
        既存 visit は補正後に時刻を snapshot から復元される (= 動かない) ため、
        固定既存 visit と pool 提案が同時刻になる「他患者ダブルブッキング」が残る
        (例: 中尾 16:00 が井上 16:00 と同コース C で重なる).

    本 helper は時刻補正後の ``after_visits`` 全体を走査し、 **pool 提案** (= ``id`` が
    ``pool_visit_ids`` に含まれる visit) が **別患者の既存/他 visit** と同
    ``(office_id, weekday, course_code)`` で時間帯重複する場合に以下で解消する:

      1. 幅のある希望 (``time_type`` が 時間帯/午前/午後/終日/None) なら、
         ``_can_move_to_time`` を満たす範囲で「衝突しない最早時刻」へずらす. ずらし先は
         同コースの占有区間を避けつつ ``service_minutes`` を確保できる位置を 5 分刻みで探す.
      2. 固定 (``time_type='固定'``) でずらせない / 幅があっても入る時刻が無い場合は提案
         不可 (= 未割当化) とし、 ``id(v)`` を返り値集合に追加する. 呼び出し側はこれを
         ``after_visits`` から除去する. ``diff_add_conflict`` warning を emit するため、
         後段の ``_classify_warning_reason`` で ``fixed_time_conflict`` /
         ``_g92_collect_fixed_unavailable_reasons`` で ``time_conflict`` に整合する.

    既存 visit / 他患者の pool 提案同士の衝突は本 helper では動かさない (= pool 提案
    のみを調整対象とし、 確定済の既存配置は不変). read-only (in-memory 調整のみ).

    Phase G-95 (修正1) で衝突検出を **2 段** にした:
      - 段 a: 従来の同 ``course_code`` 確定グループ内照合 (course_code is None は除外).
      - 段 b: 段 a で処理しきれなかった pool 提案 (= ``course_code=None`` または確定
        グループに乗らなかった分) を、 同 ``(office_id, weekday)`` の **全既存確定
        visit** (= pool でない before placed; 全 course) の占有時間帯と照合する.
        PFV 無し・weekly_pattern のみ の患者 (例: 中尾 火 16:00 固定) の pool 提案が
        ``course_code=None`` のまま生成され、 別 code で placed された既存 visit
        (例: 井上 火 16:00 稲毛 C コース) と code 不一致で突き合わされない取りこぼしを
        塞ぐ. 段 a で動かした pool 提案は段 b で二重処理しない (``stage_a_handled_ids``).

    Returns:
        提案不可として ``after_visits`` から除去すべき pool visit の ``id(v)`` 集合.
    """
    if not after_visits or not pool_visit_ids:
        return set()

    _buffer_min = config.visit_buffer_min if config is not None else VISIT_BUFFER_MINUTES

    def _overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
        # 半開区間 [start, end) で 1 分でも被れば重複 (touching は非重複).
        return a_start < b_end and a_end > b_start

    # (office_id, weekday, course_code) ごとにグルーピング. course_code=None
    # (= 未確定) の visit は他 visit とコース共有が確定しないため対象外.
    groups: dict[tuple[UUID, int, str], list[V2Visit]] = defaultdict(list)
    for v in after_visits:
        if v.course_code is None:
            continue
        groups[(v.office_id, v.weekday, v.course_code)].append(v)

    unassign_ids: set[int] = set()
    # Phase G-95 (修正1 段 b ガード): 段 a (= 同 course_code 確定グループ内照合) で
    # 衝突解消 (移動) / 提案不可 (未割当) として「処理済」とした pool 提案の id.
    # 段 b は段 a で動かした pool 提案を二重処理しないようこの集合を除外する.
    stage_a_handled_ids: set[int] = set()

    for (office_id, weekday, course_code), group in groups.items():
        if len(group) < 2:
            continue
        # 当該コースの「占有区間」を 1 つでも持つ pool 提案を順に検査する.
        # 検査対象 (pool 提案) と相手 (= 別患者の任意 visit) の重複を見る.
        # 決定性のため start_time → patient_id でソートして処理順を固定する.
        ordered = sorted(group, key=lambda v: (v.start_time, str(v.patient_id)))
        for pv in ordered:
            if id(pv) not in pool_visit_ids:
                continue  # 既存 / 他患者の確定 visit は動かさない.
            if id(pv) in unassign_ids:
                continue
            # 相手 (= 別患者の visit). 既に未割当化された pool 提案は占有しないため除く.
            # Phase G-96 (修正A): 相手 ov が同住所既存ペアの一員なら占有終端を
            # ``_same_address_pair_occupancy_end`` で 90 分占有込みに底上げする.
            others = [
                ov for ov in group if ov.patient_id != pv.patient_id and id(ov) not in unassign_ids
            ]
            conflict = next(
                (
                    ov
                    for ov in others
                    if _overlaps(
                        pv.start_time,
                        pv.end_time,
                        ov.start_time,
                        # Phase G-96 (修正1): pv 自身を ov の同住所ペア相手と
                        # 誤認しないよう exclude_ids={id(pv)} を渡す.
                        _same_address_pair_occupancy_end(
                            ov, group, exclude_ids=frozenset({id(pv)})
                        ),
                    )
                ),
                None,
            )
            if conflict is None:
                continue

            # 衝突あり. 幅のある希望なら衝突しない最早時刻へずらす.
            # 占有区間 = 自分以外の同コース visit の [start, occ_end). これを避けつつ
            # service_minutes を確保できる最早 start を 5 分刻みで探す.
            # occ_end は同住所 90 分占有込み (修正A).
            busy: list[tuple[time, time]] = [
                # Phase G-96 (修正1): pv 自身を ov の同住所ペア相手と誤認しないよう
                # exclude_ids={id(pv)} を渡す (pv は下の if で既に除外済だが、 別の
                # ov の占有計算でも pv をペア候補から外す必要がある).
                (
                    ov.start_time,
                    _same_address_pair_occupancy_end(ov, group, exclude_ids=frozenset({id(pv)})),
                )
                for ov in group
                if id(ov) != id(pv) and id(ov) not in unassign_ids
            ]
            moved = False
            if pv.time_type != "固定":
                # 候補開始時刻: 自身の現在時刻 + 各占有区間の end_time + buffer.
                # 5 分刻みに切り上げ、 _can_move_to_time と占有非重複を満たす最早を採る.
                raw_candidates = [pv.start_time]
                for _bs, _be in busy:
                    raw_candidates.append(_add_minutes(_be, _buffer_min))
                seen: set[time] = set()
                for cand_raw in sorted(raw_candidates):
                    cand = _round_up_to_5min(cand_raw)
                    if cand in seen:
                        continue
                    seen.add(cand)
                    cand_end = _add_minutes(cand, pv.service_minutes)
                    if not _can_move_to_time(pv, cand):
                        continue
                    if any(_overlaps(cand, cand_end, bs, be) for bs, be in busy):
                        continue
                    if cand == pv.start_time:
                        # 既に衝突なし時刻 (= 通常ここには来ないが安全側).
                        moved = True
                        break
                    old_start = pv.start_time
                    pv.start_time = cand
                    pv.end_time = cand_end
                    moved = True
                    name = pv.patient_name or (pv.patient_code or "不明")
                    other_name = conflict.patient_name or (conflict.patient_code or "不明")
                    office_name = (office_name_by_id or {}).get(office_id, "")
                    warnings.append(
                        V2Warning(
                            type="auto_time_shift_for_conflict",
                            message=(
                                f"{office_name} {course_code} コース {_weekday_jp(weekday)}: "
                                f"{name} 様 ({_fmt_hhmm(old_start)}) が "
                                f"{other_name} 様 ({_fmt_hhmm(conflict.start_time)}-"
                                f"{_fmt_hhmm(conflict.end_time)}) と同時刻衝突のため "
                                f"{_fmt_hhmm(cand)} に変更"
                            ),
                            weekday=weekday,
                            actionable=False,
                            patient_id=pv.patient_id,
                            patient_name=pv.patient_name,
                            current_time=_fmt_hhmm(cand),
                            suggested_time=_fmt_hhmm(cand),
                            time_type=pv.time_type,
                            preferred_start=pv.preferred_start,
                            preferred_end=pv.preferred_end,
                            affected_patient_ids=[pv.patient_id],
                        )
                    )
                    break

            if moved:
                stage_a_handled_ids.add(id(pv))
                continue

            # ずらせない (固定 or 入る時刻が無い) → 提案不可 (未割当化).
            unassign_ids.add(id(pv))
            stage_a_handled_ids.add(id(pv))
            name = pv.patient_name or (pv.patient_code or "不明")
            other_name = conflict.patient_name or (conflict.patient_code or "不明")
            office_name = (office_name_by_id or {}).get(office_id, "")
            warnings.append(
                V2Warning(
                    type="diff_add_conflict",
                    message=(
                        f"{name} 様: {office_name} {course_code} コース "
                        f"{_weekday_jp(weekday)} {_fmt_hhmm(pv.start_time)}-"
                        f"{_fmt_hhmm(pv.end_time)} は同コースの {other_name} 様 "
                        f"({_fmt_hhmm(conflict.start_time)}-{_fmt_hhmm(conflict.end_time)}) "
                        "と同時刻のため提案不可"
                    ),
                    weekday=weekday,
                    actionable=True,
                    patient_id=pv.patient_id,
                    patient_name=pv.patient_name,
                    affected_patient_ids=[pv.patient_id],
                )
            )

    # -----------------------------------------------------------------------
    # Phase G-95 (修正1 段 b): course_code に依らない (office_id, weekday) 横断照合.
    #
    # 段 a は同 ``course_code`` 確定グループ内でしか衝突を見ないため、 PFV 無し
    # weekly_pattern のみ の患者 (例: 中尾 火 16:00 固定) の pool 提案が
    # ``course_code=None`` (= 未確定) のまま生成されると、 別 code/未確定で placed
    # された既存 visit (例: 井上 火 16:00 稲毛 C コース) と code 不一致で突き合わされず、
    # 同時刻衝突が残る.
    #
    # 段 b は **段 a で処理しきれなかった pool 提案** (= ``course_code=None`` または
    # 段 a の確定グループに乗らなかった分) を、 同 ``(office_id, weekday)`` の
    # **全既存確定 visit** (= pool 提案でない before placed; 全 course) の占有時間帯と
    # 照合する. 半開区間 [start, end) で重複する pool 提案を:
    #   - 幅あり (time_type が 時間帯/午前/午後/終日/None) → 衝突しない最早時刻へずらす
    #     (_can_move_to_time + 占有非重複 + service_minutes 確保).
    #   - 固定 / 入る時刻が無い → 提案不可 (未割当化) + diff_add_conflict warning.
    # 段 a で動かした pool 提案は ``stage_a_handled_ids`` で除外し二重処理を防ぐ.
    # 既存確定 visit は不変 (read-only; pool 提案のみ調整).
    #
    # Phase G-96 (修正A 同住所 90 分占有): 既存確定 visit が「同住所既存ペア」の
    # 一員なら、 占有終端を ``_same_address_pair_occupancy_end`` で
    # ``max(実 end_time, pair_anchor_start + 90 分)`` に底上げする. これにより
    # 安永+菅原 (同住所 16:00-16:35×2) の後ろに来る植田 (固定 pool) は実 end 16:35
    # ではなく 90 分占有 17:30 を見て、 希望/勤務終了を超過し提案不可になる.
    #
    # Phase G-96 (修正B pool 同士衝突): 段 b は確定済 pool を逐次
    # ``placed_pool_busy_by_ow`` へ積み、 後続 pool 提案が既配置 pool とも非重複に
    # なるようにする. これで希望のみ患者 2 人が同 (office, weekday) 同時刻に集中
    # しても 2 件目がずれる / 不能なら未割当化される (pool 同士の重なり残りを塞ぐ).
    # -----------------------------------------------------------------------

    # (office_id, weekday) ごとの「既存確定 visit (= pool でない)」全件. course を
    # 問わず集める. pool 提案同士は確定占有とみなさない (= 段 b 開始時は相手にし
    # ない; 確定した pool は placed_pool_busy_by_ow で別途逐次反映する).
    existing_visits_by_ow: dict[tuple[UUID, int], list[V2Visit]] = defaultdict(list)
    for v in after_visits:
        if id(v) in pool_visit_ids:
            continue  # pool 提案は確定占有でない.
        if id(v) in unassign_ids:
            continue
        existing_visits_by_ow[(v.office_id, v.weekday)].append(v)

    # Phase G-99 (懸念①): canary 非依存で当週の実 placed visit を衝突相手に注入する.
    # legacy before ローダ (_load_before_visits_from_pfv) は PatientFixedVisit のみ読み
    # 実 visits テーブルを読まないため、 PFV 非対応の実 visit (手動配置等) が
    # after_visits に載らず、 中尾 16:00 が井上 16:00 と同時刻でも衝突未検出になっていた.
    # run_v2_pipeline が当週 placed visit を (patient_id, weekday) 重複排除済で渡すので、
    # ここで既存確定占有として合算する (read-only; pool 提案ではない).
    for v in extra_existing_visits or []:
        existing_visits_by_ow[(v.office_id, v.weekday)].append(v)

    # 各既存 visit の「占有区間」を同住所 90 分占有込みで事前計算する (修正A).
    # 占有終端は同 (office, weekday) の既存 visit 群を pair 候補として算出する.
    existing_busy_by_ow: dict[tuple[UUID, int], list[tuple[time, time, V2Visit]]] = defaultdict(
        list
    )
    for ow_key, ow_visits in existing_visits_by_ow.items():
        for v in ow_visits:
            occ_end = _same_address_pair_occupancy_end(v, ow_visits)
            existing_busy_by_ow[ow_key].append((v.start_time, occ_end, v))

    # 修正B: 確定した pool 提案の占有区間を (office, weekday) ごとに逐次積む.
    # busy 計算でこれを既存占有と合算し、 pool 同士の同時刻衝突を解消する.
    placed_pool_busy_by_ow: dict[tuple[UUID, int], list[tuple[time, time, V2Visit]]] = defaultdict(
        list
    )

    # 段 b の検査対象 pool 提案. 決定性のため (office, weekday, start, patient) で
    # ソートして処理順を固定する.
    stage_b_targets = sorted(
        (
            pv
            for pv in after_visits
            if id(pv) in pool_visit_ids
            and id(pv) not in unassign_ids
            and id(pv) not in stage_a_handled_ids
        ),
        key=lambda v: (str(v.office_id), v.weekday, v.start_time, str(v.patient_id)),
    )

    for pv in stage_b_targets:
        ow_key = (pv.office_id, pv.weekday)
        # 相手 = 同 (office, weekday) の既存確定 visit (別患者; 全 course) +
        # 既に確定した pool 提案 (修正B). いずれも別患者のみ.
        others_b = [
            (bs, be, ov)
            for (bs, be, ov) in (
                existing_busy_by_ow.get(ow_key, []) + placed_pool_busy_by_ow.get(ow_key, [])
            )
            if ov.patient_id != pv.patient_id
        ]
        conflict_b = next(
            (
                (bs, be, ov)
                for (bs, be, ov) in others_b
                if _overlaps(pv.start_time, pv.end_time, bs, be)
            ),
            None,
        )
        if conflict_b is None:
            # 衝突なしでそのまま確定. 後続 pool の相手として占有を積む (修正B).
            placed_pool_busy_by_ow[ow_key].append((pv.start_time, pv.end_time, pv))
            continue

        busy_b: list[tuple[time, time]] = [(bs, be) for (bs, be, _ov) in others_b]
        moved_b = False
        if pv.time_type != "固定":
            raw_candidates_b = [pv.start_time]
            for _bs, _be in busy_b:
                raw_candidates_b.append(_add_minutes(_be, _buffer_min))
            seen_b: set[time] = set()
            for cand_raw in sorted(raw_candidates_b):
                cand = _round_up_to_5min(cand_raw)
                if cand in seen_b:
                    continue
                seen_b.add(cand)
                cand_end = _add_minutes(cand, pv.service_minutes)
                if not _can_move_to_time(pv, cand):
                    continue
                if any(_overlaps(cand, cand_end, bs, be) for bs, be in busy_b):
                    continue
                if cand == pv.start_time:
                    moved_b = True
                    break
                old_start = pv.start_time
                pv.start_time = cand
                pv.end_time = cand_end
                moved_b = True
                _conf_v = conflict_b[2]
                name = pv.patient_name or (pv.patient_code or "不明")
                other_name = _conf_v.patient_name or (_conf_v.patient_code or "不明")
                office_name = (office_name_by_id or {}).get(pv.office_id, "")
                warnings.append(
                    V2Warning(
                        type="auto_time_shift_for_conflict",
                        message=(
                            f"{office_name} {_weekday_jp(pv.weekday)}: "
                            f"{name} 様 ({_fmt_hhmm(old_start)}) が "
                            f"{other_name} 様 ({_fmt_hhmm(_conf_v.start_time)}-"
                            f"{_fmt_hhmm(_conf_v.end_time)}) と同時刻衝突のため "
                            f"{_fmt_hhmm(cand)} に変更"
                        ),
                        weekday=pv.weekday,
                        actionable=False,
                        patient_id=pv.patient_id,
                        patient_name=pv.patient_name,
                        current_time=_fmt_hhmm(cand),
                        suggested_time=_fmt_hhmm(cand),
                        time_type=pv.time_type,
                        preferred_start=pv.preferred_start,
                        preferred_end=pv.preferred_end,
                        affected_patient_ids=[pv.patient_id],
                    )
                )
                break

        if moved_b:
            # 修正B: 確定した (= 移動 or 現状維持) pool の占有を後続のため積む.
            placed_pool_busy_by_ow[ow_key].append((pv.start_time, pv.end_time, pv))
            continue

        # ずらせない (固定 or 入る時刻が無い) → 提案不可 (未割当化).
        unassign_ids.add(id(pv))
        _conf_v = conflict_b[2]
        name = pv.patient_name or (pv.patient_code or "不明")
        other_name = _conf_v.patient_name or (_conf_v.patient_code or "不明")
        office_name = (office_name_by_id or {}).get(pv.office_id, "")
        warnings.append(
            V2Warning(
                type="diff_add_conflict",
                message=(
                    f"{name} 様: {office_name} {_weekday_jp(pv.weekday)} "
                    f"{_fmt_hhmm(pv.start_time)}-{_fmt_hhmm(pv.end_time)} は "
                    f"{other_name} 様 ({_fmt_hhmm(_conf_v.start_time)}-"
                    f"{_fmt_hhmm(_conf_v.end_time)}) と同時刻のため提案不可"
                ),
                weekday=pv.weekday,
                actionable=True,
                patient_id=pv.patient_id,
                patient_name=pv.patient_name,
                affected_patient_ids=[pv.patient_id],
            )
        )

    return unassign_ids


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
    config: SchedulingConfig | None = None,
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

    # Phase G-88 Step3: 事業所別設定. None なら全既定 (= 現行 module 定数と同値) で
    # 挙動不変. エントリ (schedule_v2.py) が ``load_scheduling_config(db)`` を渡す.
    if config is None:
        config = DEFAULT_SCHEDULING_CONFIG

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

    # Phase G-45: 拠点稼働曜日 map をロード. cross-office PFV / sub_office 経路で
    # office_ids に含まれない office に visit が振り分けられるケースもあるため、
    # patient.primary_office_id を全部含めた和集合で query する.
    _op_office_ids_pipeline: set[UUID] = set(office_ids)
    for _p in patients_by_id.values():
        if _p.primary_office_id is not None:
            _op_office_ids_pipeline.add(_p.primary_office_id)
    op_weekdays_by_office = await _load_office_operating_weekdays(
        db, office_ids=list(_op_office_ids_pipeline)
    )
    # office_name_by_id は warnings の office 名表示に使うため、 cross-office で
    # office_ids に含まれない office の名前も追加で fetch する.
    _missing_office_ids = [oid for oid in _op_office_ids_pipeline if oid not in office_name_by_id]
    if _missing_office_ids:
        _extra_office_rows = await db.scalars(
            select(Office).where(Office.id.in_(_missing_office_ids))
        )
        for _o in _extra_office_rows.all():
            office_name_by_id[_o.id] = _o.name

    # Phase E-5 (項目 ⑥B): diff_add のみ — PFV.sub_office_id 経由でフォロー対象患者を
    # 引き込む. 主担当が他拠点でも sub_office_id が ``office_ids`` に該当するなら
    # pool 候補化する. 自動算出本体 (full_optimize) は触らない (= 既存挙動維持).
    sub_office_patient_ids: set[UUID] = set()
    sub_office_scope_set: set[UUID] = set(office_ids)
    if mode == "diff_add":
        extra_patients, _ = await _load_active_patients_via_sub_office(
            db,
            office_ids=office_ids,
            excluded_patient_ids=set(patients_by_id.keys()),
        )
        if extra_patients:
            sub_office_patient_ids = set(extra_patients.keys())
            patients_by_id.update(extra_patients)

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

        # Phase G-93 (部分不足プール): 今週すでに配置済みの visit を
        # ``(patient_id, weekday)`` 粒度で取得する (= 不足曜日の特定材料).
        # 「配置済み (= 再提案しない)」は status ∈ {planned, in_progress, completed}
        # かつ deleted_at NULL とみなす. cancelled は患者が実際には訪問されて
        # おらず再訪問が必要なため「未配置」= 再提案対象として意図的に除外する.
        # planned のみで判定すると completed (完了済) の曜日が未配置扱いになり
        # 二重提案され、 completed のみの患者が完全孤児化する不整合が生じるため、
        # in_progress / completed も配置済みに含める (= cancelled だけ除外).
        # patient 単位の在否 (``patients_with_week_visit``) は既存 orphan 判定で
        # 引き続き使い、 曜日粒度 map (``placed_slots_by_patient``) は部分不足判定で使う.
        placed_statuses = (
            VISIT_STATUS_PLANNED,
            VISIT_STATUS_IN_PROGRESS,
            VISIT_STATUS_COMPLETED,
        )
        patients_with_week_visit: set[UUID] = set()
        placed_slots_by_patient: dict[UUID, set[int]] = defaultdict(set)
        # Phase G-94 (修正1 過剰提案): 今週の実 placed visit **件数** を患者単位で
        # 数える. partial_short の回数充足チェック (固定曜日 ≠ 希望曜日のズレ患者で
        # 希望回数は満たしているのに曜日不一致で不足扱いされる過剰提案) を防ぐため、
        # 曜日集合 (placed_slots_by_patient) とは別に件数も保持する. 通常 1 患者 1
        # 曜日 1 visit だが、 同曜日複数 visit の稀ケースも正確に数えるため曜日集合
        # の cardinality ではなく実 row 数を集計する.
        placed_count_by_patient: dict[UUID, int] = defaultdict(int)
        if week_monday is not None and week_sunday is not None:
            placed_rows = (
                await db.execute(
                    select(Visit.patient_id, Visit.visit_date).where(
                        Visit.patient_id.in_(list(patients_by_id.keys())),
                        Visit.visit_date.between(week_monday, week_sunday),
                        Visit.status.in_(placed_statuses),
                        Visit.deleted_at.is_(None),
                    )
                )
            ).all()
            for _pid, _vdate in placed_rows:
                if _pid is None or _vdate is None:
                    continue
                patients_with_week_visit.add(_pid)
                placed_slots_by_patient[_pid].add(_vdate.weekday())
                placed_count_by_patient[_pid] += 1

        # 通常 pool (PFV 無し): weekly_pattern ベース展開.
        # Phase E-5: sub_office 経由で引き込まれた患者は除外 (sub_office 用 orphan
        # 経路で扱うため).
        pool_patients_no_fixed = [
            p
            for p in patients_by_id.values()
            if p.id not in patients_with_fixed and p.id not in sub_office_patient_ids
        ]

        # Phase G-93 (部分不足プール): PFV を持つ患者の固定枠を事前ロードする.
        # 孤児判定 (= 今週 visit 0 件) に加えて「今週 visit が一部あるが希望回数に
        # 不足」な患者 (= 部分不足) を曜日粒度で拾うため、 希望週内スロット曜日集合
        # (PFV ∪ weekly_pattern preferred) を計算する材料として全 PFV 患者の固定枠
        # を 1 クエリでまとめて取得する (= N+1 回避).
        _fixed_pids = [p.id for p in patients_by_id.values() if p.id in patients_with_fixed]
        all_fixed_by_patient: dict[UUID, list[PatientFixedVisit]] = defaultdict(list)
        if _fixed_pids:
            _all_pfv_rows = await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id.in_(_fixed_pids),
                    PatientFixedVisit.mode == "normal",
                )
            )
            for _pfv in _all_pfv_rows.all():
                all_fixed_by_patient[_pfv.patient_id].append(_pfv)

        # Phase G-93: 部分不足患者の集合.
        #   - PFV あり (= patients_with_fixed) かつ
        #   - 今週 visit が 1 件以上 (= patients_with_week_visit, 完全孤児ではない) かつ
        #   - sub_office 経由ではない (sub_office 患者は無条件に orphan 扱い) かつ
        #   - 希望週内スロット曜日のうち、 今週まだ配置されていない曜日が存在する.
        # 「配置済み」は placed_slots_by_patient (status ∈ {planned, in_progress,
        # completed} / deleted_at NULL) で判定するため、 completed の曜日は再提案
        # されず、 cancelled のみの曜日は未配置 = 提案対象になる. フロント
        # poolPatients (希望回数 > 実績) と方向性は揃えつつ、 cancelled の扱いは
        # 上記 placed_statuses 定義を正とする追加分類.
        partial_short_patient_ids: set[UUID] = set()
        for p in patients_by_id.values():
            if p.id not in patients_with_fixed:
                continue
            if p.id in sub_office_patient_ids:
                continue
            if p.id not in patients_with_week_visit:
                continue  # 完全孤児は既存 orphan 経路で扱う.
            # Phase G-94 (修正1 過剰提案): 回数充足を曜日判定の前段でチェックする.
            # 固定枠曜日と weekly 希望曜日がズレた患者 (固定 水木金 / 希望 火 等) は、
            # 希望回数 (frequency_per_week, 無ければ PFV スロット数) を今週の実 placed
            # visit 件数で満たしていれば「希望曜日が未配置」でも不足ではない (= フロント
            # poolPatients の回数ベース判定と整合). desired_count が確定できる場合のみ
            # 充足判定し、 充足済 (desired <= placed) なら partial_short から除外する.
            desired_count = _g94_desired_count(p, all_fixed_by_patient.get(p.id), config=config)
            if desired_count is not None and placed_count_by_patient.get(p.id, 0) >= desired_count:
                continue
            desired_wds = _g93_desired_weekdays(p, all_fixed_by_patient.get(p.id), config=config)
            uncovered = desired_wds - placed_slots_by_patient.get(p.id, set())
            if uncovered:
                partial_short_patient_ids.add(p.id)

        # 孤児 pool (PFV あり + 今週 visit 無し): PFV ベース展開.
        # Phase E-5: sub_office 経由で引き込まれた患者は無条件にここに含める
        # (主担当 office が scope 外でも sub_office で配置候補化したいため).
        # Phase G-93: 部分不足患者も同じ展開経路 (固定 + 希望フォールバック) に
        # 流すため orphan グループに含める. 既存 no_fixed / 完全孤児の挙動は不変
        # (= 追加分類のみ).
        pool_patients_orphan_fixed = [
            p
            for p in patients_by_id.values()
            if (
                p.id in patients_with_fixed
                and (
                    p.id in sub_office_patient_ids
                    or p.id not in patients_with_week_visit
                    or p.id in partial_short_patient_ids
                )
            )
        ]
        pool_patients = pool_patients_no_fixed + pool_patients_orphan_fixed

        # 孤児 patient の PFV を取得 (PFV ベース展開用)
        # PatientFixedVisit は soft-delete を持たない (固定枠は物理削除).
        # Phase E-5: sub_office 経由患者の PFV は sub_office_id が scope 内のもののみ.
        # Phase G-93: PFV は上で ``all_fixed_by_patient`` に 1 クエリでロード済なので
        # 再クエリせずそれを再利用する (= N+1 回避, 部分不足患者も同様に展開される).
        if pool_patients_orphan_fixed:
            for _orphan_p in pool_patients_orphan_fixed:
                for pfv in all_fixed_by_patient.get(_orphan_p.id, []):
                    if pfv.patient_id in sub_office_patient_ids:
                        # sub_office 経由患者: PFV.sub_office_id が scope 内のもののみ採用.
                        if (
                            pfv.sub_office_id is None
                            or pfv.sub_office_id not in sub_office_scope_set
                        ):
                            continue
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

    # Phase G-21 T3-1 (canary 切替): feature flag ``g21_new_algorithm`` が enabled な
    # 拠点に属する patient のみ 4 経路 union (PFV ∪ weekly ∪ DB Visit ∪ overlay) の
    # 新 before 経路を使う. それ以外は旧 ``_load_before_visits_from_pfv`` 継続.
    g21_enabled_offices = await _load_g21_enabled_offices(db, office_ids=office_ids)
    if g21_enabled_offices:
        g21_patients_by_id = {
            pid: p
            for pid, p in patients_by_id.items()
            if p.primary_office_id in g21_enabled_offices
        }
        legacy_patients_by_id = {
            pid: p
            for pid, p in patients_by_id.items()
            if p.primary_office_id not in g21_enabled_offices
        }
        before_visits_g21 = (
            await _load_before_visits_v2(
                db,
                patients_by_id=g21_patients_by_id,
                iso_year=iso_year,
                iso_week=iso_week,
                pending_overlay=pending_overlay,
                warnings=warnings,
                op_weekdays_by_office=op_weekdays_by_office,
                office_name_by_id=office_name_by_id,
                config=config,
            )
            if g21_patients_by_id
            else []
        )
        before_visits_legacy = (
            await _load_before_visits_from_pfv(
                db,
                patients_by_id=legacy_patients_by_id,
                pending_overlay=pending_overlay,
                warnings=warnings,
                op_weekdays_by_office=op_weekdays_by_office,
                office_name_by_id=office_name_by_id,
            )
            if legacy_patients_by_id
            else []
        )
        before_visits = before_visits_g21 + before_visits_legacy
    else:
        # canary OFF: 全 office で旧経路を使用.
        before_visits = await _load_before_visits_from_pfv(
            db,
            patients_by_id=patients_by_id,
            pending_overlay=pending_overlay,
            warnings=warnings,
            op_weekdays_by_office=op_weekdays_by_office,
            office_name_by_id=office_name_by_id,
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
    #
    # Phase G-30: weekly_pattern ベース pool (= ``pool_patients_no_fixed``) で
    # も pinned PFV を持つ患者がいる (full_optimize で patient.weekly_pattern が
    # dict + PFV あり). この場合 ``build_visits_for_pool`` の weekly_pattern 分岐
    # で V2Visit.is_pinned=True を立てたいので、 該当患者の PFV を事前ロードし
    # ``fixed_by_patient`` 経由で渡す.
    pinned_pfv_by_patient_for_pool: dict[UUID, list[PatientFixedVisit]] = {}
    _no_fixed_with_pfv_ids = [p.id for p in pool_patients_no_fixed if p.id in patients_with_fixed]
    if _no_fixed_with_pfv_ids:
        _pfv_rows = await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id.in_(_no_fixed_with_pfv_ids),
                PatientFixedVisit.mode == "normal",
            )
        )
        for _pfv in _pfv_rows.all():
            pinned_pfv_by_patient_for_pool.setdefault(_pfv.patient_id, []).append(_pfv)
    # Phase G-31: weekly_pattern + pinned PFV 経路でも PFV.course_template_id
    # から course_code を引いて V2Visit.course_code に流すため、 template_id ->
    # label map を事前ロードする (orphan 経路 / G-21 経路と同じ規約). これを
    # 怠ると build_visits_for_pool の weekly_pattern 分岐 pinned emit が
    # course_code=None で V2Visit を作り、 Stage 4 振り分けで pinned visit が
    # 別 course (例 A → M) にアサインされる事象 (G-30.1 取り残し) が再現する.
    pinned_pool_course_code_by_template_id: dict[UUID, str] = {}
    # Phase G-33: cross-office PFV 対応で template_id -> office_id map も同 query
    # で事前構築 (= N+1 を増やさず Phase G-31 の CourseTemplate ロードを拡張).
    # build_visits_for_pool の weekly_pattern 分岐 pinned emit が
    # PFV.course_template_id を持つとき、 V2Visit.office_id を template.office_id
    # に差し替える. patient.primary_office_id (= 稲毛) と異なる office (= 都賀)
    # の course_template を指す pinned PFV があると、 After で都賀 Course が
    # 失われて稲毛 Course に合体する事象が VPS で報告されていた.
    pinned_pool_office_by_template_id: dict[UUID, UUID] = {}
    if pinned_pfv_by_patient_for_pool:
        _pinned_template_ids: set[UUID] = {
            _pfv.course_template_id
            for _pfvs in pinned_pfv_by_patient_for_pool.values()
            for _pfv in _pfvs
            if _pfv.course_template_id is not None
        }
        if _pinned_template_ids:
            _pinned_ct_rows = await db.scalars(
                select(CourseTemplate).where(
                    CourseTemplate.id.in_(_pinned_template_ids),
                    CourseTemplate.deleted_at.is_(None),
                )
            )
            for _ct in _pinned_ct_rows.all():
                pinned_pool_course_code_by_template_id[_ct.id] = _ct.label
                pinned_pool_office_by_template_id[_ct.id] = _ct.office_id
    pool_visits = build_visits_for_pool(
        pool_patients_no_fixed,
        fixed_by_patient=pinned_pfv_by_patient_for_pool or None,
        pending_overlay=pending_overlay,
        course_code_by_template_id=pinned_pool_course_code_by_template_id or None,
        ct_office_by_id=pinned_pool_office_by_template_id or None,
        op_weekdays_by_office=op_weekdays_by_office,
        office_name_by_id=office_name_by_id,
        warnings=warnings,
        config=config,
    )
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
        # Phase G-33: cross-office PFV 対応で template_id -> office_id map も同 query
        # で事前構築 (orphan 経路でも cross-office を尊重する).
        orphan_office_by_template_id: dict[UUID, UUID] = {}
        if orphan_template_ids:
            ct_rows = await db.scalars(
                select(CourseTemplate).where(
                    CourseTemplate.id.in_(orphan_template_ids),
                    CourseTemplate.deleted_at.is_(None),
                )
            )
            for ct in ct_rows.all():
                orphan_course_code_by_template_id[ct.id] = ct.label
                orphan_office_by_template_id[ct.id] = ct.office_id
        pool_visits_orphan = build_visits_for_pool(
            pool_patients_orphan_fixed,
            fixed_by_patient=orphan_fixed_by_patient,
            use_fixed_as_source=True,
            pending_overlay=pending_overlay,
            course_code_by_template_id=orphan_course_code_by_template_id,
            ct_office_by_id=orphan_office_by_template_id or None,
            # Phase E-5: diff_add のみ — PFV.sub_office_id が scope 内なら
            # V2Visit.office_id を sub_office_id に差し替える. 自動算出本体
            # (full_optimize) では sub_office_patient_ids が空 = scope set 不要.
            sub_office_scope=sub_office_scope_set if mode == "diff_add" else None,
            op_weekdays_by_office=op_weekdays_by_office,
            office_name_by_id=office_name_by_id,
            warnings=warnings,
            config=config,
        )
        # Phase G-92: 固定訪問スケジュール由来の候補は pool_origin="fixed" を立てる.
        # 固定が 3 条件 (時間不適合 / 定員オーバー / 時間衝突) で入らない場合に
        # 希望訪問パターンへフォールバックするための識別子.
        for _v in pool_visits_orphan:
            _v.pool_origin = "fixed"
        pool_visits = pool_visits + pool_visits_orphan

        # Phase G-92: 固定優先→希望フォールバック.
        # diff_add のとき、 PFV を持つ患者については固定枠候補 (上の
        # pool_visits_orphan) に加えて **希望訪問パターン由来のフォールバック候補**
        # も展開しておく. 後段の dedup / 衝突フィルタで「固定が入れば固定、
        # 入らなければ希望」が自動的に選ばれる. weekly_pattern が dict でない
        # (= 希望未設定) 患者ではフォールバック候補は 0 件になり挙動不変.
        if mode == "diff_add":
            pool_visits_fallback = build_visits_for_pool(
                pool_patients_orphan_fixed,
                # use_fixed_as_source=False (既定) で patient.weekly_pattern を読む.
                pending_overlay=pending_overlay,
                op_weekdays_by_office=op_weekdays_by_office,
                office_name_by_id=office_name_by_id,
                # フォールバック候補は重複 warning を避けるため warnings を渡さない
                # (固定候補展開で既に office_closed 等は emit 済).
                warnings=None,
                config=config,
            )
            for _v in pool_visits_fallback:
                _v.pool_origin = "preferred"
            pool_visits = pool_visits + pool_visits_fallback

    # Phase G-21 T3 (Reviewer C1 fix): G21 feature flag enabled 拠点に属する
    # patient については legacy build_visits_for_pool の出力を捨て、
    # build_visits_for_pool_v2 (pinned / 非 pinned 2 経路化 + Invariant G21-A) で
    # 再生成する. これにより:
    #   - pinned PFV の is_pinned=True が after_visits に乗る (= 後段
    #     _apply_corrections_to_visits の pinned fence が engage する).
    #   - 同 (patient, weekday) で pinned + weekly_pattern entry が重複した場合、
    #     Invariant G21-A により weekly_pattern が skip + warning.
    # canary OFF 拠点は legacy 経路維持で挙動不変.
    if g21_enabled_offices:
        g21_patient_ids: set[UUID] = {
            pid for pid, p in patients_by_id.items() if p.primary_office_id in g21_enabled_offices
        }
        if g21_patient_ids:
            g21_pool_patients = [p for p in pool_patients if p.id in g21_patient_ids]
            if g21_pool_patients:
                # G21 patient の全 PFV (pinned 含む) を取得.
                g21_pfv_by_patient: dict[UUID, list[PatientFixedVisit]] = {}
                g21_pfv_rows = await db.scalars(
                    select(PatientFixedVisit).where(
                        PatientFixedVisit.patient_id.in_(list(g21_patient_ids)),
                        PatientFixedVisit.mode == "normal",
                    )
                )
                for _pfv_row in g21_pfv_rows.all():
                    g21_pfv_by_patient.setdefault(_pfv_row.patient_id, []).append(_pfv_row)

                # course_template_id -> label map (pinned 経路で V2Visit.course_code に流す).
                g21_template_ids: set[UUID] = {
                    pfv.course_template_id
                    for _pfvs in g21_pfv_by_patient.values()
                    for pfv in _pfvs
                    if pfv.course_template_id is not None
                }
                g21_course_code_by_template_id: dict[UUID, str] = {}
                if g21_template_ids:
                    g21_ct_rows = await db.scalars(
                        select(CourseTemplate).where(
                            CourseTemplate.id.in_(g21_template_ids),
                            CourseTemplate.deleted_at.is_(None),
                        )
                    )
                    for _ct in g21_ct_rows.all():
                        g21_course_code_by_template_id[_ct.id] = _ct.label

                # legacy pool_visits から G21 patient の visit を除外.
                pool_visits = [v for v in pool_visits if v.patient_id not in g21_patient_ids]

                # build_visits_for_pool_v2 で G21 patient の visit を再生成.
                g21_pool_visits = build_visits_for_pool_v2(
                    g21_pool_patients,
                    fixed_by_patient=g21_pfv_by_patient,
                    pending_overlay=pending_overlay,
                    course_code_by_template_id=g21_course_code_by_template_id or None,
                    sub_office_scope=sub_office_scope_set if mode == "diff_add" else None,
                    op_weekdays_by_office=op_weekdays_by_office,
                    office_name_by_id=office_name_by_id,
                    warnings=warnings,
                    config=config,
                )
                # Phase G-92: g21 再生成パスでも pool_origin を立てる. legacy 経路
                # (7048-7049) と同じく「固定優先→希望フォールバック」を成立させる.
                # build_visits_for_pool_v2 は pinned PFV を source_kind="fixed"
                # (is_pinned=True), weekly_pattern を source_kind="pool" で展開する
                # ため、 これを pool_origin に写像する:
                #   - source_kind="fixed" (pinned PFV 由来)   → pool_origin="fixed"
                #   - source_kind="pool"  (weekly_pattern 由来) → pool_origin="preferred"
                # これで _dedup_fixed_preferred_candidates が fixed_keys を検出でき、
                # 固定が落ちたときに fixed_fallback_preferred 分岐へ到達する.
                for _gv in g21_pool_visits:
                    _gv.pool_origin = "fixed" if _gv.source_kind == "fixed" else "preferred"
                pool_visits = pool_visits + g21_pool_visits

                # Phase G-92: g21 PFV 患者の希望フォールバック候補を補完する.
                # build_visits_for_pool_v2 は Invariant G21-A により pinned PFV と
                # 同 (patient, weekday) の weekly_pattern entry を skip するため、
                # 「同曜日で固定が落ちたら希望へ差し替える」フォールバック候補が
                # 上の再生成だけでは欠落する. legacy 経路 (7058-7072) と同様に、
                # weekly_pattern を素直に展開した preferred 候補を別途追加し、
                # _dedup で固定候補の裏に退避させる (= 遅延フォールバック源).
                # diff_add のみ (full_optimize は固定優先フォールバック対象外).
                if mode == "diff_add":
                    g21_fixed_patient_ids = set(g21_pfv_by_patient.keys())
                    g21_fallback_patients = [
                        p for p in g21_pool_patients if p.id in g21_fixed_patient_ids
                    ]
                    if g21_fallback_patients:
                        g21_pool_visits_fallback = build_visits_for_pool(
                            g21_fallback_patients,
                            # use_fixed_as_source=False (既定) で weekly_pattern を読む.
                            pending_overlay=pending_overlay,
                            op_weekdays_by_office=op_weekdays_by_office,
                            office_name_by_id=office_name_by_id,
                            # 重複 warning を避けるため warnings は渡さない.
                            warnings=None,
                            config=config,
                        )
                        for _gfv in g21_pool_visits_fallback:
                            _gfv.pool_origin = "preferred"
                        pool_visits = pool_visits + g21_pool_visits_fallback

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
        config=config,
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
            before_copies, unavailable_slots=unavailable, warnings=warnings, config=config
        )
        # 既存 visit (filtered_before) と時間重複する pool visit を除外.
        # 同 (patient_id, weekday) で時間帯が被るものを取り除き、warning を出す.
        pool_visits = _filter_conflicting_pool_visits(filtered_before, pool_visits, warnings)
        # Phase G-93 (部分不足プール: 二重提案防止): 部分不足患者は orphan として
        # filtered_before から除外されるため、 上の存在衝突フィルタでは「今週すでに
        # 配置済みの (patient, weekday)」を落とせない. ここで実 DB visit の
        # 配置済みスロット (``placed_slots_by_patient``) を曜日粒度で突合し、
        # 既に希望が入っている曜日の pool 候補 (固定/希望どちらも) を除外する.
        # → 不足している曜日 (= P070 の月曜) だけが提案に残る. warning は出さない
        # (既配置は欠落でなく正常状態のため).
        if partial_short_patient_ids:
            pool_visits = [
                pv
                for pv in pool_visits
                if not (
                    pv.patient_id in partial_short_patient_ids
                    and pv.weekday in placed_slots_by_patient.get(pv.patient_id, set())
                )
            ]
        # Phase G-92: 固定優先→希望フォールバックの dedup.
        # 同 (patient_id, weekday) に固定候補 (pool_origin="fixed") と希望候補
        # (pool_origin="preferred") が並存する場合、固定を優先採用し希望を落とす.
        # 固定候補が上の存在衝突フィルタで既に除外されていれば希望候補が残り、
        # 自動的にフォールバックする. weekday をまたいだ固定/希望の混在 (例: 月は
        # 固定・火は希望のみ) は別 (patient,weekday) なので両方残る.
        pool_visits, fallback_preferred_by_key = _dedup_fixed_preferred_candidates(pool_visits)
        # pool 内の同 (patient_id, weekday) 重複も検出
        # (weekly_pattern.entries が同曜日 2 件以上ある稀ケース対策).
        pool_visits = _filter_pool_internal_conflicts(pool_visits, warnings)
        after_visits = filtered_before + list(pool_visits)
    else:
        after_visits = list(pool_visits)
        fallback_preferred_by_key = {}

    # W41 v2 (同住所同時刻集約 ソフト制約): _enforce_h2_same_address の前に呼ぶ.
    # 同住所 patient が異なる start_time に分散している場合、最多 start_time に
    # 寄せる. 時間制約 (固定/午前/午後/時間帯) を尊重し、動かせない場合は warning.
    _consolidate_same_address_time(after_visits, warnings)

    # Stage 2: バケット
    buckets = split_into_buckets(after_visits)

    # Phase G-21 T3-3: pair_mode 制約のための link マップを事前ロード.
    # bucket 越え移動はしないため bucket ごとに enforce する.
    pair_modes_map = await _load_same_address_pair_modes(
        db, patient_ids=list(patients_by_id.keys())
    )

    # Stage 3: 距離グリーディクラスタリング (バケットごと)
    sets_by_bucket: dict[tuple[UUID, int, Literal["am", "pm"]], list[V2Set]] = {}
    for key, bucket in buckets.items():
        # silent drop fix: 重複 visit skip を warning に出す.
        sets = cluster_by_distance_greedy(bucket.visits, warnings=warnings)
        # Phase G-21 T3-3: pair_mode (blocked / preferred / required) を反映.
        # _enforce_h2_same_address の前段に呼ぶ.
        if pair_modes_map:
            _enforce_same_address_pair_mode(sets, pair_modes_map, warnings)
        _enforce_h2_same_address(sets, warnings)
        # W41 v2 (H2 強化): 同住所 3 名以上を別 set に強制分散.
        # Phase G-21 final H4: pair_modes を渡して blocked 関係を尊重させる.
        _enforce_h2_split_overflow(sets, warnings, pair_modes=pair_modes_map)
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
            config=config,
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
                            # Phase G-32: pinned visit は未割当に流さない (= 元 course
                            # を維持). Stage 4 が他 set の空き code 枯渇で fallback
                            # 不能になった場合でも、 pinned 自身は既存 course_code
                            # (= PFV.course_template_id 由来) を保持する.
                            if v.is_pinned:
                                continue
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
                        # Phase G-32: pinned visit は未割当に流さない (= 元 course
                        # を維持). manager 数超過で本 set に code を割当てられない
                        # 場合でも、 pinned 自身は PFV.course_template_id 由来の
                        # course_code を保持する.
                        if v.is_pinned:
                            continue
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
            # Phase G-32: pinned visit (= V2Visit.is_pinned=True, PFV.is_pinned 由来)
            # の course_code は **絶対動かさない**. Stage 4 conflict fallback で
            # candidate (= 既存 pinned 由来 course) が他 set と衝突して別 code に
            # 振り替わった場合でも、 pinned visit 自身は元 course を維持する.
            # 非 pinned visit のみ確定 code を書き戻し、 pinned 同住所ペア相手の
            # 非 pinned 側は pinned の course に追従させる仕組みは
            # ``existing_codes`` 経路 (candidate=pinned の course) が担保している.
            for v in am_set.visits if am_set else []:
                if v.is_pinned:
                    continue
                v.course_code = code
            for v in pm_set.visits if pm_set else []:
                if v.is_pinned:
                    continue
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
    #
    # Phase G-21 W1: 4 経路統合 — 共通 helper ``_apply_corrections_to_visits``
    # 経由で呼ぶ. pinned PFV は補正対象外として fence される.
    travel_unassigned_ids = _apply_corrections_to_visits(
        after_visits, warnings=warnings, office_name_by_id=office_name_by_id, config=config
    )
    if travel_unassigned_ids:
        after_visits = [v for v in after_visits if id(v) not in travel_unassigned_ids]

    # Phase G-94 (修正2 ダブルブッキング): 時刻補正後の after_visits 全体で「同
    # (office, weekday, course_code) で別患者の既存/他 visit と時間帯重複する pool
    # 提案」を検出し解消する. 幅のある希望は衝突しない最早時刻へずらし、 固定 /
    # 入る時刻が無い場合は提案不可 (未割当化) とする. 既存配置 (= 確定済の他患者
    # visit) は動かさず、 pool 提案のみ調整する. G-92 の after_keys 計算より前に
    # 実行することで、 衝突で落ちた固定 pool 提案が希望フォールバックに正しく流れる.
    if mode == "diff_add":
        _g94_pool_ids = {id(v) for v in pool_visits}
        # Phase G-99 (懸念①): g94 段 b の衝突相手集合に、 当週の実 placed visit を
        # canary 非依存で注入する. before ローダ (legacy = PFV のみ) では PFV 非対応の
        # 実 visit (手動配置等) が after_visits に載らず、 同時刻の提案が衝突未検出で
        # すり抜ける (中尾 16:00 vs 井上 16:00). ここで visits テーブルを直接読み、
        # after_visits に未表現の (patient_id, weekday) のみ V2Visit 化して渡す.
        # read-only (SELECT のみ). full_optimize は g94 を呼ばないため非影響.
        _g94_extra_existing: list[V2Visit] = []
        if week_monday is not None and week_sunday is not None:
            _g94_after_keys = {(v.patient_id, v.weekday) for v in after_visits}
            _g94_stmt = (
                select(Visit, Patient)
                .join(Patient, Visit.patient_id == Patient.id)
                .where(
                    Visit.visit_date.between(week_monday, week_sunday),
                    Visit.status.in_(placed_statuses),
                    Visit.deleted_at.is_(None),
                )
            )
            if office_ids:
                _g94_stmt = _g94_stmt.where(Patient.primary_office_id.in_(office_ids))
            _g94_rows = (await db.execute(_g94_stmt)).all()
            for _vrow, _prow in _g94_rows:
                if _vrow.start_time is None or _vrow.end_time is None:
                    continue
                if _prow.primary_office_id is None:
                    continue
                _wd = _vrow.visit_date.weekday()
                if (_vrow.patient_id, _wd) in _g94_after_keys:
                    continue  # after_visits に既に表現済 (二重計上回避).
                _svc = max(1, _time_to_min(_vrow.end_time) - _time_to_min(_vrow.start_time))
                _g94_extra_existing.append(
                    V2Visit(
                        patient_id=_vrow.patient_id,
                        patient_name=_prow.name,
                        patient_code=_prow.code,
                        weekday=_wd,
                        start_time=_vrow.start_time,
                        end_time=_vrow.end_time,
                        service_minutes=_svc,
                        lat=float(_prow.lat) if _prow.lat is not None else 0.0,
                        lng=float(_prow.lng) if _prow.lng is not None else 0.0,
                        office_id=_prow.primary_office_id,
                        am_pm="any",
                        source_kind="fixed",
                    )
                )
        _g94_double_booking_ids = _g94_resolve_cross_patient_double_booking(
            after_visits,
            pool_visit_ids=_g94_pool_ids,
            warnings=warnings,
            office_name_by_id=office_name_by_id,
            extra_existing_visits=_g94_extra_existing,
            config=config,
        )
        if _g94_double_booking_ids:
            after_visits = [v for v in after_visits if id(v) not in _g94_double_booking_ids]

    # Phase G-92 (固定優先→希望フォールバックの遅延差し替え + proposal_source 分類):
    # diff_add で PFV 患者の固定枠候補が Stage 5/6 (定員オーバー / 時間不適合) で
    # 未割当になった場合、 dedup 時に退避しておいた希望候補 (fallback_preferred_by_key)
    # の時刻情報で固定候補を差し替える. これにより proposal の表示時刻が「希望訪問
    # パターン」由来になり、 source='fixed_fallback_preferred' と整合する.
    #
    # 検出: after_visits は Stage 5/6 で未割当 visit を物理除去済. shared object
    # identity により pool_visits の固定候補オブジェクトと after_visits の同一性は
    # 一致するため、 (patient_id, weekday) が after に残っていなければ固定枠は失敗.
    proposal_meta_by_patient: dict[UUID, dict[str, Any]] = {}
    if mode == "diff_add":
        after_keys: set[tuple[UUID, int]] = {(v.patient_id, v.weekday) for v in after_visits}
        # Phase G-92 (Reviewer fix #2): in-place 差し替えで生成した「同曜日希望候補」の
        # id を記録する. この候補は run の Stage 5/6 を経ていない (= after_visits に
        # 乗らない) 未検証の提案であり、 配置可能性が保証できない. 同曜日で固定も
        # 希望も配置できない患者 (= 他に生存曜日が無い) はこの候補だけが pool_visits に
        # 残り、 endpoint が「提案あり」として surface する一方で
        # _identify_unassigned_patients が「未割当」に計上する二重分類が起きる.
        # 後段で「未検証 (after_visits に無い) かつ別曜日に生存提案も無い」候補を
        # pool_visits から除去し、 二重分類を解消する.
        _g92_converted_ids: set[int] = set()
        for (pid_fb, wd_fb), fallback_v in fallback_preferred_by_key.items():
            if (pid_fb, wd_fb) in after_keys:
                # 固定枠が after に残った = 固定成功. フォールバック差し替え不要.
                continue
            # 固定候補が未割当になった → pool_visits の固定候補を希望候補で差し替える.
            # Phase G-92 (Reviewer note #5): 同 (patient, weekday) に複数 PFV がある
            # 稀ケースでは先頭 1 件のみ差し替えて break する (= 決定性・意図的).
            # 退避先 fallback_v も _dedup で先頭 1 件に固定済のため整合する. 1 患者
            # 1 曜日 1 提案の前提を崩さないための仕様.
            for pv in pool_visits:
                if pv.patient_id == pid_fb and pv.weekday == wd_fb and pv.pool_origin == "fixed":
                    pv.start_time = fallback_v.start_time
                    pv.end_time = fallback_v.end_time
                    pv.service_minutes = fallback_v.service_minutes
                    pv.am_pm = fallback_v.am_pm
                    pv.time_type = fallback_v.time_type
                    pv.preferred_start = fallback_v.preferred_start
                    pv.preferred_end = fallback_v.preferred_end
                    pv.office_id = fallback_v.office_id
                    # course_code は希望側に確定枠が無いため None (= 未確定提案).
                    pv.course_code = None
                    pv.pool_origin = "fixed_fallback_preferred"
                    _g92_converted_ids.add(id(pv))
                    break

        # proposal_source / fixed_unavailable_reasons を患者単位で確定する.
        # 「固定成功」は、 固定候補 (pool_origin="fixed") が Stage 5/6 を生き残り
        # after_visits に残っていることで判定する (= after_keys に存在). 固定候補が
        # pool_visits に残っていても after から落ちていれば固定失敗扱い.
        #
        # 判定 (PFV 患者 = patients_with_fixed のみ fixed 系を検討):
        #   - 固定候補がいずれかの曜日で after に生存 → "fixed"
        #   - 固定が全滅 かつ 希望候補が存在 (差替済 or 別曜日生存)
        #       → "fixed_fallback_preferred" + 固定不可理由 + 落ちた固定候補を除外
        #   - 固定が全滅 かつ 希望候補が無い (= 希望訪問パターン未設定)
        #       → "fixed" のまま (= 既存の orphan 候補表示挙動を温存). 未割当判定は
        #         _identify_unassigned_patients が担う (後方互換).
        #   - PFV 無し → "preferred"
        _origin_state_by_pid: dict[UUID, dict[str, set[int]]] = {}
        for pv in pool_visits:
            _slot = _origin_state_by_pid.setdefault(
                pv.patient_id,
                {
                    "fixed_survived": set(),
                    "fixed_failed": set(),
                    "preferred": set(),
                    "all": set(),
                },
            )
            _slot["all"].add(pv.weekday)
            in_after = (pv.patient_id, pv.weekday) in after_keys
            if pv.pool_origin == "fixed":
                if in_after:
                    _slot["fixed_survived"].add(pv.weekday)
                else:
                    _slot["fixed_failed"].add(pv.weekday)
            else:
                # "preferred" (純希望) または "fixed_fallback_preferred" (差替済希望).
                # いずれも希望訪問パターン由来の提案候補.
                _slot["preferred"].add(pv.weekday)
        # フォールバック確定患者: after に残らなかった固定候補は proposal から除く
        # (= 希望側の提案のみを見せる).
        _drop_failed_fixed_ids: set[int] = set()
        for pid_meta, slots in _origin_state_by_pid.items():
            if slots["fixed_survived"]:
                proposal_meta_by_patient[pid_meta] = {
                    "proposal_source": "fixed",
                    "fixed_unavailable_reasons": [],
                }
            elif pid_meta in patients_with_fixed and slots["fixed_failed"] and slots["preferred"]:
                # PFV 患者: 固定候補が全滅したが希望候補が残っている = フォールバック成立.
                # 固定枠不可理由を warnings から収集 (全曜日対象).
                reasons = _g92_collect_fixed_unavailable_reasons(
                    pid_meta,
                    slots["all"],
                    warnings,
                )
                # Phase G-92 (Reviewer fix #4): 理由が warnings から確定できない場合は
                # 空配列のままにする. 旧実装は time_conflict をハードコード既定にして
                # いたが、 実際は定員 / 移動時間起因でも "time_conflict" と誤表示し得た
                # ため撤去. 理由不明 = 表示しない (= フォールバック自体は成立).
                proposal_meta_by_patient[pid_meta] = {
                    "proposal_source": "fixed_fallback_preferred",
                    "fixed_unavailable_reasons": reasons,
                }
                # after から落ちた固定候補 (pool_origin="fixed") を proposal から除外.
                for _pv in pool_visits:
                    if (
                        _pv.patient_id == pid_meta
                        and _pv.pool_origin == "fixed"
                        and (_pv.patient_id, _pv.weekday) not in after_keys
                    ):
                        _drop_failed_fixed_ids.add(id(_pv))
            elif pid_meta in patients_with_fixed:
                # PFV 患者: 固定候補が残る or 固定が落ちたが希望候補も無い.
                # 既存挙動を温存し "fixed" のまま (= orphan 候補表示 / 未割当は別経路).
                proposal_meta_by_patient[pid_meta] = {
                    "proposal_source": "fixed",
                    "fixed_unavailable_reasons": [],
                }
            else:
                proposal_meta_by_patient[pid_meta] = {
                    "proposal_source": "preferred",
                    "fixed_unavailable_reasons": [],
                }
        # Phase G-92 (Reviewer fix #2): in-place 差し替えで生成した同曜日希望候補は
        # run の Stage 5/6 を経ておらず after_visits に乗らない (= 未検証提案). これが
        # pool_visits に残ると endpoint が「提案あり」として surface する一方、 当該
        # 患者は (他に生存曜日が無ければ) _identify_unassigned_patients で「未割当」に
        # 計上され二重分類になる. 未検証の差替候補は pool_visits から除去し、 提案を
        # 出さず未割当判定に一本化する (別曜日に生存提案がある真のフォールバック患者は
        # その生存提案が after_visits に残るため影響を受けない).
        for _cv_id in _g92_converted_ids:
            _drop_failed_fixed_ids.add(_cv_id)
        if _drop_failed_fixed_ids:
            pool_visits = [v for v in pool_visits if id(v) not in _drop_failed_fixed_ids]

        # Phase G-100 (二重 surface の解消): g94 (他患者ダブルブッキング) で「提案不可」
        # と判定された pool 提案は after_visits から除去済 (8358) だが pool_visits には
        # 残るため、 endpoint (schedule_v2) が pool_visits からカードを組み「提案あり」と
        # 二重 surface していた (中尾 火16:00 / 植田 月15:30 が ⚠重複警告付きで出続ける真因).
        # ここで衝突拒否された候補を pool_visits からも除去し、 未割当判定に一本化する.
        # G-92 のフォールバック差し替え (固定→希望) は当除去より前に完了しており、 変換済
        # 候補は既に _g92_converted_ids で除去済のため影響しない. orphan 救済 / 通常の
        # pool 提案 (g94 衝突でない未配置) は対象外で従来どおり surface される.
        if _g94_double_booking_ids:
            pool_visits = [v for v in pool_visits if id(v) not in _g94_double_booking_ids]

    # W41 v2 拡張 (コース容量 duration 化): 既存の人数制約 (MAX_PATIENTS_PER_COURSE=6)
    # と独立して、コース総所要時間 (visit duration + 移動時間) が 480 分を超えていないか check.
    _check_course_capacity_minutes(
        after_visits, warnings=warnings, office_name_by_id=office_name_by_id, config=config
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
    # Phase G-92 (Reviewer fix #2): 固定→希望フォールバックが成立した患者
    # (proposal_source="fixed_fallback_preferred") は希望候補が提案として
    # pool_visits に存在する. ところが遅延差し替えは after_visits を更新しない
    # (希望候補は _dedup で fallback_preferred_by_key に退避され after に乗らない)
    # ため、 固定と希望が同曜日で他に生存曜日が無い患者は after_visits に痕跡が
    # 残らず、 _identify_unassigned_patients に「未割当」と誤計上される.
    # フォールバック成立患者は提案ありなので未割当判定から除外する.
    _g92_fallback_pids: set[UUID] = {
        pid
        for pid, meta in proposal_meta_by_patient.items()
        if meta.get("proposal_source") == "fixed_fallback_preferred"
    }
    unassigned = _identify_unassigned_patients(
        pool_patients=pool_patients,
        after_visits=after_visits,
        warnings=warnings,
        exclude_patient_ids=_g92_fallback_pids,
    )

    return {
        "proposal_batch_id": proposal_batch_id,
        "before_visits": before_visits,
        "after_visits": after_visits,
        "pool_visits": pool_visits,
        "warnings": warnings,
        "staff_count_by_weekday": staff_count_by_weekday,
        "unassigned_patients": unassigned,
        # Phase G-92: 患者単位の proposal_source + 固定不可理由
        # ({patient_id: {"proposal_source": ..., "fixed_unavailable_reasons": [...]}}).
        # diff_add 以外では空 dict. endpoint が V2DiffAddProposal に流す.
        "proposal_meta_by_patient": proposal_meta_by_patient,
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
    course_cache: dict[tuple[UUID, int, int, int], Course],
    warnings: list[str],
    dry_run: bool = False,
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

    Phase G-9 critical fix: course_cache の key は ``(template.id, iso_year,
    iso_week, weekday)`` で構成する. 旧実装の ``(office_id, weekday, code)``
    では、 同 patient.primary_office_id (= office_id) で異なる
    course_template_id を持つ PFV (Phase G-8 で導入された「他拠点 template
    を希望する」パターン) を区別できず、 先に処理した PFV の Course
    が誤って後続にも返されていた. 例: INAGE 拠点患者で TSUGA-A template
    を持つ PFV → cache_key=(INAGE, 0, 'A') に TSUGA-A Course がキャッシュ
    → 続く INAGE-A template の PFV にも TSUGA-A Course が返り、 visit が
    TSUGA-A コーステーブルに混入する不具合があった.
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

    # Phase G-9 critical fix: cache_key は template.id ベース.
    # 同 patient.primary_office_id (= office_id) で異なる template_id を持つ
    # PFV を区別できるようにする (= INAGE patient with TSUGA-A template
    # と INAGE patient with INAGE-A template を別 Course として返す).
    cache_key = (template.id, iso_year, iso_week, weekday)
    cached = course_cache.get(cache_key)
    if cached is not None:
        return cached

    # Phase G-9 critical fix: Course の office_id は template.office_id を
    # 使う. 旧実装は patient.primary_office_id (= 関数引数 office_id) を
    # 渡していたため、 INAGE 患者で TSUGA-A template を希望する PFV
    # (Phase G-8 で導入) の場合、 office_id=INAGE + template_id=TSUGA-A
    # の不整合 Course が作成されていた. template の office_id を使うことで
    # Course の office と template の office を一致させ、 2nd try fallback
    # の (office_id, code) lookup も同 template に紐づく Course だけを
    # ヒットさせる.
    course_office_id = template.office_id

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
        # 2nd try: UNIQUE 制約と同じ key (office_id, code, year, week, weekday).
        # template.office_id を使うことで「同 office × 同 code だが別 template」
        # の Course を誤検出しない.
        course = await db.scalar(
            select(Course).where(
                Course.office_id == course_office_id,
                Course.code == code,
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.weekday == weekday,
                Course.deleted_at.is_(None),
            )
        )

    if course is None:
        # Phase G-21 T3 (Reviewer H1 fix): dry_run=True では Course を新規 INSERT しない
        # (DB 不変契約). 既存 Course が無い場合は None を返し warning を残す.
        # 呼び出し側 (reset_visits_to_fixed) は course=None の場合 INSERT skip 経路に逃がす.
        if dry_run:
            warnings.append(
                f"[dry_run] 既存 Course が無いため新規作成 skip "
                f"(patient_id={pfv.patient_id}, weekday={weekday}, "
                f"template_id={template.id}, code={code})"
            )
            # dry_run では cache に入れない (= 同じ key で次の PFV も独立に warning を出す)
            return None
        # 新規作成 — reset は確定操作なので ``staff_assigned`` で生成する.
        course = Course(
            iso_year=iso_year,
            iso_week=iso_week,
            weekday=weekday,
            code=code,
            course_status=COURSE_STATUS_STAFF_ASSIGNED,
            template_id=template.id,
            office_id=course_office_id,
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
    config: SchedulingConfig | None = None,
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

    # Phase G-21 T3-6 (Reviewer C2 fix): D&D で pinned PFV を動かしたら 422 拒否.
    # 検証対象:
    #   (a) 同 (patient_id, weekday) で start_time が変更されている
    #   (b) 同 (patient_id, weekday) で end_time / duration_min が変更されている
    #   (c) 同 (patient_id, weekday) で office_id が PFV.sub_office_id /
    #       patient.primary_office_id と異なる (office 変更)
    #   (d) pinned PFV (weekday=W) に対応する plan が来ず、別 weekday に同 patient の
    #       plan のみある (weekday 移動)
    pinned_pfv_by_key: dict[tuple[UUID, int], PatientFixedVisit] = {}
    if plan_patient_ids:
        pinned_pfv_rows = (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id.in_(plan_patient_ids),
                    PatientFixedVisit.mode == "normal",
                    PatientFixedVisit.slot_index == 0,
                    PatientFixedVisit.is_pinned.is_(True),
                )
            )
        ).all()
        pinned_pfv_by_key = {(p.patient_id, p.weekday): p for p in pinned_pfv_rows}
        violations: list[dict[str, Any]] = []
        # (patient_id) -> set[weekday] で plan に含まれる weekday を集計
        plan_weekdays_by_patient: dict[UUID, set[int]] = {}
        if pinned_pfv_by_key:
            for entry in patient_visit_plans:
                pid_raw = entry.get("patient_id")
                if pid_raw is None:
                    continue
                if isinstance(pid_raw, UUID):
                    pid_v = pid_raw
                else:
                    try:
                        pid_v = UUID(str(pid_raw))
                    except (ValueError, AttributeError):
                        continue
                plans_raw = entry.get("visit_plans") or []
                for plan in plans_raw:
                    wd_v = plan.get("weekday")
                    if not isinstance(wd_v, int) or not (0 <= wd_v <= 6):
                        continue
                    plan_weekdays_by_patient.setdefault(pid_v, set()).add(wd_v)
                    pinned_pfv = pinned_pfv_by_key.get((pid_v, wd_v))
                    if pinned_pfv is None:
                        continue
                    # (a) start_time 変更検出
                    st_v = plan.get("start_time")
                    if isinstance(st_v, str):
                        parsed_st_v = _parse_hhmm(st_v)
                        if parsed_st_v is None:
                            continue
                        st_v = parsed_st_v
                    if not isinstance(st_v, time):
                        continue
                    patient_name_v = None
                    _patient_obj = patients_by_id.get(pid_v)
                    if _patient_obj is not None:
                        patient_name_v = _patient_obj.name
                    if st_v != pinned_pfv.start_time:
                        violations.append(
                            {
                                "patient_id": str(pid_v),
                                "patient_name": patient_name_v,
                                "weekday": wd_v,
                                "pfv_start": _fmt_hhmm(pinned_pfv.start_time),
                                "plan_start": _fmt_hhmm(st_v),
                                "reason": "start_time_changed",
                            }
                        )
                        warnings.append(
                            f"pinned PFV を D&D で動かす操作は拒否されました "
                            f"(patient_id={pid_v}, {_weekday_jp(wd_v)}, "
                            f"PFV={_fmt_hhmm(pinned_pfv.start_time)} → "
                            f"plan={_fmt_hhmm(st_v)})"
                        )
                        continue  # start_time NG 確定. 他の検証は skip.
                    # (b) duration / end_time 変更検出
                    dur_v = plan.get("duration_min")
                    if isinstance(dur_v, int) and dur_v > 0 and dur_v != pinned_pfv.duration_min:
                        violations.append(
                            {
                                "patient_id": str(pid_v),
                                "patient_name": patient_name_v,
                                "weekday": wd_v,
                                "pfv_start": _fmt_hhmm(pinned_pfv.start_time),
                                "plan_start": _fmt_hhmm(st_v),
                                "pfv_duration": pinned_pfv.duration_min,
                                "plan_duration": dur_v,
                                "reason": "duration_changed",
                            }
                        )
                        warnings.append(
                            f"pinned PFV の duration を D&D で変更する操作は拒否されました "
                            f"(patient_id={pid_v}, {_weekday_jp(wd_v)}, "
                            f"PFV={pinned_pfv.duration_min}分 → plan={dur_v}分)"
                        )
                        continue
                    # (c) office_id 変更検出 (PFV.sub_office_id 優先,
                    #     なければ patient.primary_office_id を期待値とする)
                    plan_office_raw = plan.get("office_id")
                    if plan_office_raw is not None:
                        if isinstance(plan_office_raw, UUID):
                            plan_office_id_v: UUID | None = plan_office_raw
                        else:
                            try:
                                plan_office_id_v = UUID(str(plan_office_raw))
                            except (ValueError, AttributeError):
                                plan_office_id_v = None
                        expected_office_id: UUID | None = pinned_pfv.sub_office_id or (
                            _patient_obj.primary_office_id if _patient_obj is not None else None
                        )
                        if (
                            plan_office_id_v is not None
                            and expected_office_id is not None
                            and plan_office_id_v != expected_office_id
                        ):
                            violations.append(
                                {
                                    "patient_id": str(pid_v),
                                    "patient_name": patient_name_v,
                                    "weekday": wd_v,
                                    "pfv_start": _fmt_hhmm(pinned_pfv.start_time),
                                    "pfv_office_id": str(expected_office_id),
                                    "plan_office_id": str(plan_office_id_v),
                                    "reason": "office_changed",
                                }
                            )
                            warnings.append(
                                f"pinned PFV の拠点を D&D で変更する操作は拒否されました "
                                f"(patient_id={pid_v}, {_weekday_jp(wd_v)}, "
                                f"PFV office={expected_office_id} → plan office={plan_office_id_v})"
                            )
        # (d) pinned PFV (weekday=W) に対応する plan が plan_patient_ids に含まれて
        #     いるのに、同 patient × W の entry が消えている → 別 weekday に移動した
        #     と判定する.
        plan_patient_ids_set_local: set[UUID] = set()
        for _pid_check in plan_patient_ids:
            plan_patient_ids_set_local.add(_pid_check)
        for (pid_v, wd_v), pinned_pfv in pinned_pfv_by_key.items():
            if pid_v not in plan_patient_ids_set_local:
                continue  # plan に出てこない (= unassigned) なら旧 visit 保護される
            plan_wds = plan_weekdays_by_patient.get(pid_v, set())
            if wd_v in plan_wds:
                continue  # この weekday 自体の plan は存在 (= 上のループで個別検証済)
            # 同 patient で別 weekday の plan は存在するのに、pinned weekday の plan が無い
            # → weekday 移動と判定.
            if plan_wds:
                patient_name_v = None
                _patient_obj = patients_by_id.get(pid_v)
                if _patient_obj is not None:
                    patient_name_v = _patient_obj.name
                violations.append(
                    {
                        "patient_id": str(pid_v),
                        "patient_name": patient_name_v,
                        "weekday": wd_v,
                        "pfv_start": _fmt_hhmm(pinned_pfv.start_time),
                        "moved_to_weekdays": sorted(plan_wds),
                        "reason": "weekday_changed",
                    }
                )
                warnings.append(
                    f"pinned PFV の曜日を D&D で変更する操作は拒否されました "
                    f"(patient_id={pid_v}, PFV {_weekday_jp(wd_v)} → "
                    f"plan {[_weekday_jp(w) for w in sorted(plan_wds)]})"
                )
        if violations:
            raise PinnedVisitMovedError(violations)

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

    # CareFlow 本番バグ修正 (Option A): soft-delete で消し損ねた「保護対象 active visit」
    # を事前にロードし、apply 時に同じ unique key (patient_id, visit_date, start_time,
    # visit_group_id) で衝突する INSERT は skip + warning に逃がす.
    # 対象は active 全員 (= patient_ids). plan_patient_ids 限定ではなく、FE が plan に
    # 含めなかった unassigned 患者の保護 visit との衝突も検出する必要があるため.
    # Wave 3 lunch 3rd reviewer 指摘 (#1): 衝突 warning に既存 visit identity を含めるため
    # set ではなく dict[key, Visit] で保持し、skip branch から既存 visit を即取得できるよう
    # にする.
    protected_existing_keys_apply: dict[tuple[UUID, date, time, UUID | None], Visit] = {}
    if patient_ids:
        week_sunday_apply = date.fromordinal(week_monday.toordinal() + 6)
        protected_rows_apply = await db.scalars(
            select(Visit).where(
                Visit.patient_id.in_(patient_ids),
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday_apply,
            )
        )
        for v in protected_rows_apply.all():
            protected_existing_keys_apply[
                (v.patient_id, v.visit_date, v.start_time, v.visit_group_id)
            ] = v

    # W41 v2 拡張 (今週限定オーバーレイ): pending_edits を defensive に再適用.
    # FE 側は通常 patient_visit_plans に既にオーバーレイを反映済みで送ってくるが、
    # backend 側でも (patient_id, weekday) ベースで上書きする.
    apply_overlay = _build_pending_edit_overlay(pending_edits)

    # Phase G-88 Step3 残漏れ修正: apply_week_only 内の 2 つの昼休みゲートが
    # 固定 11:30-13:30 で判定していた (config-aware プレビューと不整合 →
    # 非既定昼休み窓で誤スキップしうる). config の昼休み窓を解決し両ゲートに渡す.
    # config=None は module 定数で挙動不変 (回帰ゼロ).
    _lws = config.lunch_window_start if config is not None else LUNCH_EARLIEST_START
    _lwe = config.lunch_window_end if config is not None else LUNCH_LATEST_END

    # Wave 1 (#115): visit_plans を V2Visit に変換し ``apply_travel_corrections``
    # で時刻補正 (auto_shift + 同住所 align + バッファー + 5 分切上 + lunch 再検証
    # + shortage 判定) を適用してから DB INSERT する.
    #
    # 旧実装は無補正で INSERT していたため、異住所同時刻 8 ペアが DB に残存する
    # 真因. Wave 1 で 4 経路 (run_v2_pipeline / apply_week_only /
    # reset_visits_to_fixed / apply_individual_proposal) を統合する.
    #
    # フロー:
    #   1) plan を V2Visit (= 補正対象) + 「INSERT 用 metadata (code, staff, office)」に分解.
    #   2) ``apply_travel_corrections`` を呼び in-place で time 補正.
    #   3) 補正後の V2Visit と metadata を突き合わせて Visit を INSERT.
    office_name_by_id: dict[UUID, str] = {}
    office_rows_for_name = await db.scalars(
        select(Office).where(Office.id.in_(office_ids), Office.deleted_at.is_(None))
    )
    for office_row in office_rows_for_name.all():
        office_name_by_id[office_row.id] = office_row.name or str(office_row.id)

    # 2-3) plan を V2Visit に正規化.
    v2_visits: list[V2Visit] = []
    v2_meta: list[dict[str, Any]] = []  # plan-index 同期メタデータ

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
            # H10: 昼休憩枠と重なる visit はスキップ (Wave 1 で時刻補正後にも再検査
            # するが、入力時点で明確に動的 lunch 枠を取れない plan は弾く).
            # Wave 3 (#WAVE3): ``_is_in_lunch_break`` は「lunch slot 11:30-13:30 の
            # どこに置いても 45 分 lunch も避けられない区間」を判定する.
            if _is_in_lunch_break(st, et, window_start=_lws, window_end=_lwe):
                warnings.append(
                    f"patient_id={patient_id}: {_weekday_jp(wd)} {_fmt_hhmm(st)}-"
                    f"{_fmt_hhmm(et)} は昼休憩 ({_fmt_hhmm(_lws)}-{_fmt_hhmm(_lwe)} "
                    "動的枠) に重なるため配置不可"
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
            staff_raw = plan.get("assigned_staff_id")
            staff_id_meta: UUID | None
            if staff_raw is None:
                staff_id_meta = None
            elif isinstance(staff_raw, UUID):
                staff_id_meta = staff_raw
            else:
                try:
                    staff_id_meta = UUID(str(staff_raw))
                except (ValueError, AttributeError):
                    staff_id_meta = None
            # V2Visit 構築. lat/lng は patient.lat / patient.lng (V2Visit は float 必須
            # なので、None 患者は Wave 1 の時刻補正対象外 → fallback で UI 入力をそのまま使う).
            if patient.lat is None or patient.lng is None:
                # 座標欠落 — 補正対象外として metadata だけ蓄積 (旧仕様で INSERT).
                v2_meta.append(
                    {
                        "v2_index": None,
                        "patient_id": patient.id,
                        "office_id": office_id,
                        "weekday": wd,
                        "code": str(code),
                        "start_time": st,
                        "end_time": et,
                        "staff_id": staff_id_meta,
                    }
                )
                continue
            tt_for_corr = _extract_time_type_for_weekday(patient, wd)
            ps_str, pe_str = _extract_preferred_window_for_weekday(patient, wd)
            # Phase G-21 T3 (Reviewer H3 fix): pinned PFV と一致する plan は
            # is_pinned=True を立てる. これにより apply_travel_corrections の
            # pinned fence が engage し、start_time / end_time / course_code が
            # 補正で動かないことが保証される.
            _matched_pinned = pinned_pfv_by_key.get((patient.id, wd))
            v2_is_pinned_apply = bool(
                _matched_pinned is not None and _matched_pinned.start_time == st
            )
            v2 = V2Visit(
                patient_id=patient.id,
                patient_name=patient.name,
                patient_code=patient.code,
                weekday=wd,
                start_time=st,
                end_time=et,
                service_minutes=dur,
                lat=float(patient.lat),
                lng=float(patient.lng),
                office_id=office_id,
                am_pm="am" if st.hour < NOON_HOUR else "pm",
                source_kind="pool",
                course_code=str(code),
                time_type=tt_for_corr,
                preferred_start=ps_str,
                preferred_end=pe_str,
                is_pinned=v2_is_pinned_apply,
            )
            v2_visits.append(v2)
            v2_meta.append(
                {
                    "v2_index": len(v2_visits) - 1,
                    "patient_id": patient.id,
                    "office_id": office_id,
                    "weekday": wd,
                    "code": str(code),
                    "start_time": st,
                    "end_time": et,
                    "staff_id": staff_id_meta,
                }
            )

    # Phase G-21 final C2: overlay 適用後の pinned 再検証.
    # 旧実装は line 6877-7044 で raw visit_plans のみ検証していたが、 pending_edits
    # (overlay) は per-plan ループ内 (L7213-7236) で st を上書きするため、 overlay
    # で pinned PFV の start_time を silently 動かす経路があった.
    # ここで「post-overlay 後の v2_meta」 を pinned_pfv_by_key と再突合し、 違反が
    # あれば PinnedVisitMovedError raise する (endpoint は 422 へ).
    if pinned_pfv_by_key:
        overlay_violations: list[dict[str, Any]] = []
        for meta in v2_meta:
            pid_m = meta["patient_id"]
            wd_m = meta["weekday"]
            pinned_pfv_m = pinned_pfv_by_key.get((pid_m, wd_m))
            if pinned_pfv_m is None:
                continue
            st_m = meta["start_time"]
            et_m = meta["end_time"]
            if not isinstance(st_m, time) or not isinstance(et_m, time):
                continue
            # 期待 duration_min (= PFV と一致するかは end-start で間接的に判定).
            # PFV.start_time と完全一致しないなら違反.
            if st_m != pinned_pfv_m.start_time:
                _p_obj = patients_by_id.get(pid_m)
                overlay_violations.append(
                    {
                        "patient_id": str(pid_m),
                        "patient_name": _p_obj.name if _p_obj is not None else None,
                        "weekday": wd_m,
                        "pfv_start": _fmt_hhmm(pinned_pfv_m.start_time),
                        "plan_start": _fmt_hhmm(st_m),
                        "reason": "start_time_changed_by_overlay",
                    }
                )
                warnings.append(
                    f"pinned PFV を pending_edits (overlay) で動かす操作は拒否されました "
                    f"(patient_id={pid_m}, {_weekday_jp(wd_m)}, "
                    f"PFV={_fmt_hhmm(pinned_pfv_m.start_time)} → "
                    f"overlay={_fmt_hhmm(st_m)})"
                )
        if overlay_violations:
            raise PinnedVisitMovedError(overlay_violations)

    # Wave 1: 時刻補正を適用. Phase G-21 W1 で _apply_corrections_to_visits 経由に統一.
    # V2Warning は文字列メッセージに展開して warnings に追加.
    v2_warnings: list[V2Warning] = []
    travel_unassigned_ids = _apply_corrections_to_visits(
        v2_visits, warnings=v2_warnings, office_name_by_id=office_name_by_id, config=config
    )
    for vw in v2_warnings:
        warnings.append(vw.message)

    # INSERT 用 course_cache.
    course_cache: dict[tuple[UUID, int, str], Course] = {}
    courses_created_counter: list[int] = [0]
    inserted_visits = 0
    new_visits_with_staff: list[tuple[Visit, UUID]] = []

    for meta in v2_meta:
        v2_idx = meta["v2_index"]
        if v2_idx is None:
            # 座標欠落 visit: V2Visit に変換できなかったため、補正なしでそのまま INSERT.
            corrected_start: time = meta["start_time"]
            corrected_end: time = meta["end_time"]
            corrected_code: str | None = meta["code"]
        else:
            v2 = v2_visits[v2_idx]
            if id(v2) in travel_unassigned_ids or v2.course_code is None:
                # 物理不可能と判定された visit は INSERT 対象外 (unassigned に流す).
                warnings.append(
                    f"patient_id={meta['patient_id']}: {_weekday_jp(meta['weekday'])} "
                    f"の補正で物理不可能と判定されたため INSERT スキップ"
                )
                continue
            corrected_start = v2.start_time
            corrected_end = v2.end_time
            corrected_code = v2.course_code
        # 補正後に lunch にハマったケースは ``apply_travel_corrections`` 内で
        # 警告 + lunch_end_t (動的) へ繰り下げ済み. 万一ハマったままの visit は
        # 最後の防衛としてスキップ.
        # Wave 3 (#WAVE3): ``_is_in_lunch_break`` は 11:30-13:30 動的枠での
        # 「45 分 lunch も取れない」判定 (最広範囲チェック).
        if _is_in_lunch_break(corrected_start, corrected_end, window_start=_lws, window_end=_lwe):
            warnings.append(
                f"patient_id={meta['patient_id']}: {_weekday_jp(meta['weekday'])} "
                f"{_fmt_hhmm(corrected_start)}-{_fmt_hhmm(corrected_end)} "
                f"補正後も昼休憩 ({_fmt_hhmm(_lws)}-{_fmt_hhmm(_lwe)} 動的枠) "
                "に重なるためスキップ"
            )
            continue
        visit_date = date.fromordinal(week_monday.toordinal() + meta["weekday"])
        # CareFlow 本番バグ修正 (Option A): 保護対象 active visit (status='confirmed' /
        # source='manual' / status='completed' 等、または unassigned 患者の保持された
        # 旧 visit) と unique key 衝突する場合は INSERT スキップ + warning.
        # apply_week_only の INSERT は visit_group_id=None 固定.
        protect_key_apply: tuple[UUID, date, time, UUID | None] = (
            meta["patient_id"],
            visit_date,
            corrected_start,
            None,
        )
        if protect_key_apply in protected_existing_keys_apply:
            existing = protected_existing_keys_apply[protect_key_apply]
            patient_obj = patients_by_id.get(meta["patient_id"])
            patient_name = (
                patient_obj.name
                if patient_obj is not None and patient_obj.name
                else f"patient_id={meta['patient_id']}"
            )
            warnings.append(
                f"{patient_name} ({_weekday_jp(meta['weekday'])} "
                f"{_fmt_hhmm(corrected_start)}-{_fmt_hhmm(corrected_end)}): "
                f"既存 visit (id={existing.id} status={existing.status} "
                f"source={existing.source}) と衝突するため適用スキップ"
            )
            continue
        course = await _resolve_course_for_code(
            db,
            office_id=meta["office_id"],
            iso_year=iso_year,
            iso_week=iso_week,
            weekday=meta["weekday"],
            code=str(corrected_code or meta["code"]),
            course_cache=course_cache,
            courses_created_counter=courses_created_counter,
            warnings=warnings,
        )
        new_visit = Visit(
            patient_id=meta["patient_id"],
            visit_date=visit_date,
            start_time=corrected_start,
            end_time=corrected_end,
            type="regular",
            status="planned",
            source="auto_alloc_v2w",
            required_staff_count=1,
            course_id=(course.id if course is not None else None),
            note=f"apply_week_only_v2 iso_year={iso_year} iso_week={iso_week}",
        )
        db.add(new_visit)
        inserted_visits += 1
        if meta["staff_id"] is not None:
            new_visits_with_staff.append((new_visit, meta["staff_id"]))

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


async def resolve_reset_office_ids(db: AsyncSession, patient_id: UUID) -> list[UUID]:
    """1 患者の型→週同期 (``reset_visits_to_fixed``) に渡す office_ids を導出する.

    Wave U-1 (§2.2 A 経路の共通部品): ``reset_visits_to_fixed`` は serving office を
    内部解決するが、対象患者を ``patients_by_id`` に載せるには primary_office_id と
    PFV.sub_office_id を office_ids に含める必要がある. ``sync-fixed-to-week`` endpoint
    と同一の導出規則を共有し、プール採用 (PUT fixed-visits A) / 範囲最適化 apply A から
    再利用する.
    """
    office_ids: set[UUID] = set()
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id))
    if patient is not None and patient.primary_office_id is not None:
        office_ids.add(patient.primary_office_id)
    sub_rows = await db.scalars(
        select(PatientFixedVisit.sub_office_id)
        .where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.sub_office_id.is_not(None),
        )
        .distinct()
    )
    for oid in sub_rows.all():
        if oid is not None:
            office_ids.add(oid)
    return list(office_ids)


async def reset_visits_to_fixed(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
    mode: Literal["legacy", "auto"] = "legacy",
    dry_run: bool = False,
    config: SchedulingConfig | None = None,
    patient_id: UUID | None = None,
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

    Phase G-21 T3-5: ``mode`` 引数で 2 mode に分岐:
        * ``legacy`` (default, 後方互換): Phase G-10/G-11 挙動.
          全 PFV を pinned 扱い + ``apply_travel_corrections`` 完全スキップ.
        * ``auto``: pinned PFV のみ厳守 + 非 pinned 患者は weekly_pattern 配置経路.
          ``apply_travel_corrections`` で移動時間補正を通す.

    Phase G-21 T3-5: ``dry_run=True`` の場合は DB を変更せず件数のみ返却する.

    Wave U-0 (変更反映先の統一 §2.2-1): ``patient_id`` を指定すると、削除・再生成の
    対象を当該 1 患者に限定する (= ``sync-fixed-to-week`` の 1 患者版). ``None``
    (既定) の場合は従来どおり ``office_ids`` 範囲の全 active 患者を対象にする
    (= 全患者版の挙動は一切変わらない). フィルタは ``patients_by_id`` (再生成側) と
    ``delete_target_patient_ids`` (削除側) の両方に適用し、対称性を保つ. その他の
    保護規則 (source='manual' / status='completed' 保護, 衝突 INSERT スキップ) は
    全患者版と同一.

    本関数は ``await db.flush()`` のみ呼ぶ. commit は呼び出し側.
    """
    if iso_year < 2000 or iso_year > 2100:
        raise ValueError(f"iso_year out of range: {iso_year}")
    if iso_week < 1 or iso_week > 53:
        raise ValueError(f"iso_week out of range: {iso_week}")
    if mode not in ("legacy", "auto"):
        raise ValueError(f"unsupported reset mode: {mode!r}")

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
    # Wave U-0 (§2.2-1): patient_id 指定時は再生成対象を当該 1 患者に限定する.
    # 全患者版 (patient_id=None) の挙動は不変.
    if patient_id is not None:
        patients_by_id = {pid: p for pid, p in patients_by_id.items() if pid == patient_id}
    patient_ids = list(patients_by_id.keys())

    # CareFlow 本番バグ修正 (Bug A): step1 の削除対象 patient 範囲を「status 不問 +
    # 対象 office 範囲」に広げる. 旧実装は active 患者 (= patient_ids) のみを削除
    # 対象にしていたため、 患者が非稼働 (inactive / suspended / pending) になると
    # 過去に再生成系で作られた旧 visit (source∈auto/reset_v2 等, status='planned')
    # が削除されず週ビューに残り続けた (= ゴースト). step2 の再生成は従来通り
    # active 患者の PFV のみ → 非稼働患者は消えて再生成されない = 正しい挙動.
    delete_target_patient_ids = await _load_visit_delete_target_patient_ids(
        db, office_ids=office_ids
    )
    # Wave U-0 (§2.2-1): patient_id 指定時は削除対象も当該 1 患者に限定し、
    # 再生成対象 (patients_by_id) と対称にする (誤って他患者 visit を消さない).
    if patient_id is not None:
        delete_target_patient_ids = delete_target_patient_ids & {patient_id}

    # 1) 対象週の active visits を取得 (削除対象 patient 範囲).
    # W41 v2 final cross-review (C-Codex-2): source / status で絞り、
    # 手動作成 (source != auto-generated) / 完了済み (status != planned)
    # / キャンセル済み visit は保護する (= Bug A 修正でも source/status 保護は維持).
    # C-Claude-1: with_for_update() で行ロックを取得し、同じ週に対する
    # 並行 reset / apply を直列化する.
    from datetime import UTC as _UTC  # noqa: N814  (UTC alias for clarity)
    from datetime import datetime as _dt

    visits_to_delete: list[Visit] = []
    if delete_target_patient_ids:
        week_sunday = date.fromordinal(week_monday.toordinal() + 6)
        stmt = (
            select(Visit)
            .where(
                Visit.patient_id.in_(delete_target_patient_ids),
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
    if visits_to_delete and not dry_run:
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
    # Phase G-21 T3-5: dry_run=True の場合は soft-delete をスキップし
    # soft_deleted_count=0 のまま (DB 不変). 後段で dry_run early return する.

    # CareFlow 本番バグ修正 (Option A): soft-delete で消し損ねた「保護対象 active visit」
    # を事前にロードし、PFV からの INSERT 時に同じ unique key (patient_id, visit_date,
    # start_time, visit_group_id) で衝突するものは skip + warning に逃がす.
    # これを行わないと、status='confirmed' / source='manual' / status='completed'
    # 等の保護 visit が残ったまま PFV を INSERT し、partial unique index
    # uq_visits_pds_group_active に違反して 409 IntegrityError になる.
    # Wave 3 lunch 3rd reviewer 指摘 (#1): 衝突 warning に既存 visit identity を含めるため
    # set ではなく dict[key, Visit] で保持し、skip branch から既存 visit を即取得できるよう
    # にする.
    protected_existing_keys: dict[tuple[UUID, date, time, UUID | None], Visit] = {}
    if patient_ids:
        week_sunday_protect = date.fromordinal(week_monday.toordinal() + 6)
        protected_rows = await db.scalars(
            select(Visit).where(
                Visit.patient_id.in_(patient_ids),
                Visit.deleted_at.is_(None),
                Visit.visit_date >= week_monday,
                Visit.visit_date <= week_sunday_protect,
            )
        )
        for v in protected_rows.all():
            protected_existing_keys[
                (v.patient_id, v.visit_date, v.start_time, v.visit_group_id)
            ] = v

    # M-2 恒久対策 (Wave U-3): 生存 manual_week visit の (patient_id, visit_date) 集合。
    # 再生成ループでこの集合にある日をスキップし、「この週だけの決定」が型スロットを
    # 一時上書きする意味論を完成させる。削除側は変更しない（manual_week は U-0 で保護済み）。
    manual_week_day_keys: set[tuple[UUID, date]] = {
        (v.patient_id, v.visit_date)
        for v in protected_existing_keys.values()
        if v.source == VISIT_SOURCE_MANUAL_WEEK
    }

    # 2) patient_fixed_visits から visits を再生成
    pfv_rows = await db.scalars(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id.in_(patient_ids),
            PatientFixedVisit.mode == "normal",
        )
    )
    pfv_list = list(pfv_rows.all())

    # CareFlow 本番バグ修正 (Bug B): PFV ごとの「実際に担当する拠点 (serving office)」を
    # 解決する map を構築する. 1860-1906 (Phase E-5 / G-33) と同じ優先順位
    #   sub_office_id (PFV) > course_template.office_id > patient.primary_office_id
    # を reset 経路にも適用する. 旧実装は primary_office_id 固定だったため、
    # 主拠点=都賀 (土曜休業) の患者が PFV で稲毛 (土曜稼働) コースに割当てられて
    # いても、 operating_weekday 判定が主拠点基準になり土曜だけ生成 skip された.
    _ct_ids_reset = {
        pfv.course_template_id for pfv in pfv_list if pfv.course_template_id is not None
    }
    ct_office_by_id_reset: dict[UUID, UUID] = {}
    if _ct_ids_reset:
        _ct_rows_reset = await db.scalars(
            select(CourseTemplate).where(
                CourseTemplate.id.in_(_ct_ids_reset),
                CourseTemplate.deleted_at.is_(None),
            )
        )
        for _ct in _ct_rows_reset.all():
            ct_office_by_id_reset[_ct.id] = _ct.office_id

    def _serving_office_id_for_pfv(pfv: PatientFixedVisit, patient: Patient) -> UUID | None:
        """PFV の serving office を優先順位で解決する (Bug B).

        sub_office_id (Phase E-5) > course_template.office_id (Phase G-33
        cross-office PFV) > patient.primary_office_id.
        """
        if pfv.sub_office_id is not None:
            return pfv.sub_office_id
        if pfv.course_template_id is not None:
            _co = ct_office_by_id_reset.get(pfv.course_template_id)
            if _co is not None:
                return _co
        return patient.primary_office_id

    # Wave 1 (#115): _detect_cross_address_time_conflicts は「データ不備」検出に
    # 縮小済み (= 座標 None / office None のみ). 異住所同時刻ペアは Wave 1 で
    # apply_travel_corrections の auto_shift が解消する.
    # 旧 hotfix の「異住所同時刻 → warning log のみで reset 続行」は維持. 検出
    # ロジック自体は data integrity 監視のため残す (座標 None patient のみ拾う).
    @dataclass(frozen=True)
    class _PfvWithOffice:
        patient_id: UUID
        weekday: int
        start_time: time
        course_template_id: UUID | None
        office_id: UUID | None

    pfv_items: list[_PfvWithOffice] = []
    for pfv in pfv_list:
        p = patients_by_id.get(pfv.patient_id)
        # Bug B: 衝突検出も serving office 基準で判定する.
        _serving_oid = _serving_office_id_for_pfv(pfv, p) if p is not None else None
        pfv_items.append(
            _PfvWithOffice(
                patient_id=pfv.patient_id,
                weekday=pfv.weekday,
                start_time=pfv.start_time,
                course_template_id=pfv.course_template_id,
                office_id=_serving_oid,
            )
        )
    pfv_conflicts = _detect_cross_address_time_conflicts(pfv_items, patients_by_id)
    if pfv_conflicts:
        # データ不備 (座標 None) のみ拾う. 通常の異住所同時刻ペアは Wave 1 の
        # auto_shift で解消するため、ここの warning は監視用途のみ.
        import logging

        _log = logging.getLogger(__name__)
        _log.warning(
            "reset_visits_to_fixed: データ不備で %d 件のペア検出 (座標 None 患者等). "
            "reset 続行 (conflicts=%s)",
            len(pfv_conflicts),
            pfv_conflicts[:5],
        )

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
    # Phase G-9 critical fix: key を (template.id, iso_year, iso_week, weekday)
    # ベースに変更. 旧 (office_id, weekday, code) 構成では、 同 office で
    # 異なる template_id を持つ PFV を区別できず誤キャッシュヒットが発生していた.
    course_cache: dict[tuple[UUID, int, int, int], Course] = {}

    # Wave 1 (#115): PFV → V2Visit へ変換し apply_travel_corrections で時刻補正を
    # 通してから INSERT. 4 経路統合のうち reset_visits_to_fixed.
    #
    # フロー:
    #   1) PFV ごとに course を解決 (= code を確定).
    #   2) V2Visit を build + course metadata を蓄積.
    #   3) apply_travel_corrections (group by office × weekday × code).
    #   4) 補正後の (start, end, course_code) で Visit を INSERT.
    office_name_by_id_for_corr: dict[UUID, str] = {}
    office_rows_for_name = await db.scalars(
        select(Office).where(Office.id.in_(office_ids), Office.deleted_at.is_(None))
    )
    for office_row in office_rows_for_name.all():
        office_name_by_id_for_corr[office_row.id] = office_row.name or str(office_row.id)

    # Phase G-45: 拠点稼働曜日 map をロード. patient_ids が指す拠点も含めるため、
    # reset 対象の office_ids に加えて patient.primary_office_id も query 範囲に入れる.
    # 通常 patient の primary_office_id は office_ids 内だが、 cross-office PFV の
    # 防衛として明示的に和集合する.
    # Bug B: serving office (sub_office / course_template office) も operating_weekday
    # 判定対象に含めるため、 PFV の serving office を query 範囲に加える.
    _op_office_ids: set[UUID] = set(office_ids)
    for _p in patients_by_id.values():
        if _p.primary_office_id is not None:
            _op_office_ids.add(_p.primary_office_id)
    for _pfv in pfv_list:
        _pp = patients_by_id.get(_pfv.patient_id)
        if _pp is None:
            continue
        _so = _serving_office_id_for_pfv(_pfv, _pp)
        if _so is not None:
            _op_office_ids.add(_so)
    op_weekdays_by_office_reset = await _load_office_operating_weekdays(
        db, office_ids=list(_op_office_ids)
    )
    # Phase G-45: 拠点休業日 skip 用 dedupe set.
    closed_warned_reset: set[tuple[UUID, int]] = set()

    v2_visits_reset: list[V2Visit] = []
    v2_meta_reset: list[dict[str, Any]] = []
    for pfv in pfv_list:
        patient = patients_by_id.get(pfv.patient_id)
        if patient is None or patient.primary_office_id is None:
            continue
        end_t = _add_minutes(pfv.start_time, pfv.duration_min)
        if end_t <= pfv.start_time:
            continue
        # Bug B: serving office (sub_office > course_template office > primary) を
        # 使う. 旧実装は primary_office_id 固定で、 cross-office PFV (主拠点 休業日
        # × serving office 稼働日) の visit が誤って skip されていた.
        office_id = _serving_office_id_for_pfv(pfv, patient)
        if office_id is None:
            continue
        # Phase G-45 / Bug B: serving office が当該 weekday に休業の場合は
        # visit 再生成を skip し、 string warning として記録する.
        _op_wd_reset = op_weekdays_by_office_reset.get(office_id)
        if _op_wd_reset is not None and pfv.weekday not in _op_wd_reset:
            _key = (patient.id, pfv.weekday)
            if _key not in closed_warned_reset:
                closed_warned_reset.add(_key)
                _office_name = office_name_by_id_for_corr.get(office_id) or "拠点"
                warnings.append(
                    f"{_weekday_jp(pfv.weekday)}: 拠点 {_office_name} は休業日のため "
                    f"{patient.name or '不明'} 様の visit を再生成しません. "
                    "サブ拠点で受ける場合は患者マスタを編集してください"
                )
            continue
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
            dry_run=dry_run,
        )
        # ローテーションで staff_id を選ぶ
        pool = staff_by_office_weekday.get((office_id, pfv.weekday), [])
        staff_id: UUID | None = None
        if pool:
            idx = rotation_idx.get((office_id, pfv.weekday), 0)
            staff_id = pool[idx % len(pool)]
            rotation_idx[(office_id, pfv.weekday)] = idx + 1
            courses_used_keys.add((office_id, pfv.weekday, staff_id))
        course_code_str = course.code if course is not None else "M"
        # V2Visit 構築. lat/lng None patient は補正対象外として metadata だけ蓄積.
        if patient.lat is None or patient.lng is None:
            v2_meta_reset.append(
                {
                    "v2_index": None,
                    "patient_id": patient.id,
                    "office_id": office_id,
                    "weekday": pfv.weekday,
                    "start_time": pfv.start_time,
                    "end_time": end_t,
                    "course": course,
                    "staff_id": staff_id,
                }
            )
            continue
        # Phase G-10 / Phase G-21 T3-5:
        #   mode='legacy' (既定): 全 PFV を pinned 扱い (= "固定") で apply_travel_corrections
        #     を完全スキップする (= Phase G-10/G-11 後方互換).
        #   mode='auto'         : pinned PFV のみ "固定" 厳守. 非 pinned 患者は
        #     patient.weekly_pattern の time_type / preferred_start / preferred_end を採用
        #     し、 apply_travel_corrections が時間帯範囲内で時刻 shift する.
        if mode == "auto" and not pfv.is_pinned:
            tt_for_corr = _extract_time_type_for_weekday(patient, pfv.weekday) or "時間帯"
            ps_raw, pe_raw = _extract_preferred_window_for_weekday(patient, pfv.weekday)
            ps_str = ps_raw if ps_raw else _fmt_hhmm(pfv.start_time)
            pe_str = pe_raw
        else:
            tt_for_corr = "固定"
            ps_str = _fmt_hhmm(pfv.start_time)
            pe_str = None
        # Phase G-21 W1: pinned PFV は補正対象外 (= is_pinned=True で fence).
        # mode='legacy' (= 全 PFV を pinned 扱い) でも安全策として True にして
        # apply_travel_corrections が呼ばれた場合に時刻が動かないようにする.
        # (legacy 経路では apply_travel_corrections 自体スキップするため実害なし.)
        v2_is_pinned = pfv.is_pinned or (mode == "legacy")
        v2 = V2Visit(
            patient_id=patient.id,
            patient_name=patient.name,
            patient_code=patient.code,
            weekday=pfv.weekday,
            start_time=pfv.start_time,
            end_time=end_t,
            service_minutes=pfv.duration_min,
            lat=float(patient.lat),
            lng=float(patient.lng),
            office_id=office_id,
            am_pm="am" if pfv.start_time.hour < NOON_HOUR else "pm",
            source_kind="fixed",
            course_code=course_code_str,
            time_type=tt_for_corr,
            preferred_start=ps_str,
            preferred_end=pe_str,
            is_pinned=v2_is_pinned,
        )
        v2_visits_reset.append(v2)
        v2_meta_reset.append(
            {
                "v2_index": len(v2_visits_reset) - 1,
                "patient_id": patient.id,
                "office_id": office_id,
                "weekday": pfv.weekday,
                "start_time": pfv.start_time,
                "end_time": end_t,
                "course": course,
                "staff_id": staff_id,
            }
        )

    # Phase G-11 / Phase G-21 T3-5 / Wave 1:
    #   mode='legacy' (既定): apply_travel_corrections を完全スキップ (Phase G-10/G-11 後方互換).
    #   mode='auto'         : apply_travel_corrections を呼んで移動時間補正を通す.
    #     pinned PFV は time_type='固定' で auto_shift を最小限に抑え、 非 pinned 患者は
    #     time_type='時間帯' 等で時刻 shift を許可する.
    travel_unassigned_ids_reset: set[int] = set()
    if mode == "auto" and v2_visits_reset:
        v2_warnings_reset: list[V2Warning] = []
        travel_unassigned_ids_reset = _apply_corrections_to_visits(
            v2_visits_reset,
            warnings=v2_warnings_reset,
            office_name_by_id=office_name_by_id_for_corr,
            config=config,
        )
        for vw in v2_warnings_reset:
            warnings.append(vw.message)
    else:
        _ = office_name_by_id_for_corr

    # H1: 1 PFV ごとに await db.flush() を呼ぶと O(N) DB roundtrip になる.
    #     visits は一括で add → 1 回 flush → assignments を一括 add → 1 回 flush.
    # Phase G-21 T3-5: dry_run=True の場合は DB を更新せず 3 種の件数のみ返す.
    inserted_visits = 0
    visits_to_skip_protected = 0
    visits_to_skip_conflict = 0
    new_visits_with_staff: list[tuple[Visit, UUID]] = []
    for meta in v2_meta_reset:
        v2_idx = meta["v2_index"]
        if v2_idx is None:
            # 座標欠落: 補正なしでそのまま INSERT (旧仕様).
            corrected_start_r: time = meta["start_time"]
            corrected_end_r: time = meta["end_time"]
        else:
            v2 = v2_visits_reset[v2_idx]
            if id(v2) in travel_unassigned_ids_reset or v2.course_code is None:
                warnings.append(
                    f"patient_id={meta['patient_id']}: {_weekday_jp(meta['weekday'])} "
                    f"の補正で物理不可能と判定されたため reset INSERT スキップ"
                )
                visits_to_skip_conflict += 1
                continue
            corrected_start_r = v2.start_time
            corrected_end_r = v2.end_time
        visit_date = date.fromordinal(week_monday.toordinal() + meta["weekday"])
        # M-2 恒久対策 (Wave U-3): 同 (patient_id, visit_date) に生存 manual_week visit
        # がある日は再生成をスキップする（「この週だけの決定」が型スロットを上書き）。
        if (meta["patient_id"], visit_date) in manual_week_day_keys:
            patient_obj = patients_by_id.get(meta["patient_id"])
            patient_name = (
                patient_obj.name
                if patient_obj is not None and patient_obj.name
                else str(meta["patient_id"])
            )
            logger.info(
                "reset_visits_to_fixed: M-2 skip: %s %s に manual_week visit が存在するため再生成スキップ",
                patient_name,
                visit_date,
            )
            visits_to_skip_protected += 1
            continue
        # CareFlow 本番バグ修正 (Option A): 保護対象 active visit (status='confirmed' /
        # source='manual' / status='completed' 等) と unique key 衝突する場合は
        # INSERT スキップ + warning. PFV 由来 INSERT は visit_group_id=None 固定.
        protect_key: tuple[UUID, date, time, UUID | None] = (
            meta["patient_id"],
            visit_date,
            corrected_start_r,
            None,
        )
        if protect_key in protected_existing_keys:
            existing = protected_existing_keys[protect_key]
            patient_obj = patients_by_id.get(meta["patient_id"])
            patient_name = (
                patient_obj.name
                if patient_obj is not None and patient_obj.name
                else f"patient_id={meta['patient_id']}"
            )
            warnings.append(
                f"{patient_name} ({_weekday_jp(meta['weekday'])} "
                f"{_fmt_hhmm(corrected_start_r)}-{_fmt_hhmm(corrected_end_r)}): "
                f"既存 visit (id={existing.id} status={existing.status} "
                f"source={existing.source}) と衝突するため再生成スキップ"
            )
            visits_to_skip_protected += 1
            continue
        if dry_run:
            inserted_visits += 1
            continue
        course_for_meta: Course | None = meta["course"]
        new_visit = Visit(
            patient_id=meta["patient_id"],
            visit_date=visit_date,
            start_time=corrected_start_r,
            end_time=corrected_end_r,
            type="regular",
            status="planned",
            source="reset_v2",
            required_staff_count=1,
            course_id=(course_for_meta.id if course_for_meta is not None else None),
            note=f"reset_to_fixed_v2 iso_year={iso_year} iso_week={iso_week}",
        )
        db.add(new_visit)
        inserted_visits += 1
        if meta["staff_id"] is not None:
            new_visits_with_staff.append((new_visit, meta["staff_id"]))

    # Phase G-21 T3-5: dry_run=True の場合は DB INSERT/flush をスキップして件数だけ返す.
    if dry_run:
        return {
            "visits_regenerated": 0,
            "visits_soft_deleted": 0,
            "courses_used": 0,
            "visits_to_insert": inserted_visits,
            "visits_to_skip_protected": visits_to_skip_protected,
            "visits_to_skip_conflict": visits_to_skip_conflict,
            "dry_run": True,
            "warnings": warnings,
        }

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
    config: SchedulingConfig | None = None,
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

    # W-6: 主担当拠点が未設定 (NULL) の患者は週次 visit 生成から除外されるため、
    # 固定枠 (PFV) を確定させても毎週現れない「ねじれ」になる. 入口で 422 ブロックする.
    # pool-bulk-apply も本サービスを再利用するため、ここに置くと両経路をカバーできる.
    guard_patient = await db.scalar(select(Patient).where(Patient.id == patient_id))
    if guard_patient is not None and guard_patient.primary_office_id is None:
        from fastapi import HTTPException as _HTTPException
        from fastapi import status as _status

        raise _HTTPException(
            status_code=_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "主担当拠点が未設定のため配置できません。患者マスタで主担当拠点を設定してください。"
            ),
        )

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

    # Phase G-21 final H1: 既存 PFV に pinned (is_pinned=True) が含まれていれば
    # apply-individual で上書き / 削除させない. apply-individual は提案に応じて
    # PFV を DELETE/UPDATE/INSERT するため、 pinned PFV を無条件に動かす経路に
    # なっていた. pinned 解除を先に要求して bypass を防ぐ.
    pinned_existing = [p for p in existing if p.is_pinned]
    if pinned_existing:
        # 422 拒否 — HTTPException は service 層で直接 raise する (本 helper を
        # 呼ぶ schedule_v2 endpoint はそのまま FastAPI に伝播する).
        from fastapi import HTTPException as _HTTPException
        from fastapi import status as _status

        raise _HTTPException(
            status_code=_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "pinned_pfv_cannot_be_applied",
                "message": (
                    f"{len(pinned_existing)} 件の pinned PFV を apply-individual で "
                    "上書きする操作は拒否されました. 先に完全固定を解除してください."
                ),
                "pinned_weekdays": sorted({p.weekday for p in pinned_existing}),
            },
        )

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

    # Fix D2 (CareFlow #103): 異住所同時刻衝突を境界で検出して 422 で拒否する.
    # 同一拠点の他患者の既存 PFV と (weekday, start_time, course_template_id)
    # が一致 + 異住所なら CrossAddressTimeConflictError を上げる.
    # 同住所ペア (家族・施設) は許容. course_template_id が一致しない場合は別コース
    # 扱いで衝突対象外 (= 物理的に別スタッフが訪問するため OK).
    target_patient = await db.scalar(select(Patient).where(Patient.id == patient_id))
    if target_patient is not None and target_patient.primary_office_id is not None:
        # 同一拠点の他患者 PFV (mode='normal', slot_index=0) を取得.
        other_pfv_rows = await db.scalars(
            select(PatientFixedVisit)
            .join(Patient, Patient.id == PatientFixedVisit.patient_id)
            .where(
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.slot_index == 0,
                PatientFixedVisit.patient_id != patient_id,
                Patient.primary_office_id == target_patient.primary_office_id,
                Patient.deleted_at.is_(None),
                Patient.status == "active",
            )
        )
        other_pfv_list = list(other_pfv_rows.all())
        # patients_by_id: 提案中 patient + 他 patient を全部含める.
        patients_by_id: dict[UUID, Patient] = {target_patient.id: target_patient}
        if other_pfv_list:
            other_patient_ids = list({p.patient_id for p in other_pfv_list})
            other_patients = (
                await db.scalars(select(Patient).where(Patient.id.in_(other_patient_ids)))
            ).all()
            for p in other_patients:
                patients_by_id[p.id] = p

        # 提案 PFV を擬似アイテムにラップ (まだ DB に無いため).
        @dataclass(frozen=True)
        class _ProposedPfv:
            patient_id: UUID
            weekday: int
            start_time: time
            course_template_id: UUID | None
            office_id: UUID

        # 提案側の course_template_id: 既存 PFV から継承 (なければ None).
        proposed_items: list[_ProposedPfv] = []
        for wd, (st, _dur) in proposed_by_wd.items():
            ex = existing_by_wd.get(wd)
            ct_id = ex.course_template_id if ex is not None else None
            proposed_items.append(
                _ProposedPfv(
                    patient_id=patient_id,
                    weekday=wd,
                    start_time=st,
                    course_template_id=ct_id,
                    office_id=target_patient.primary_office_id,
                )
            )

        # 他患者 PFV を office_id 付きにラップ (Patient.primary_office_id を解決).
        @dataclass(frozen=True)
        class _ExistingPfvProxy:
            patient_id: UUID
            weekday: int
            start_time: time
            course_template_id: UUID | None
            office_id: UUID | None

        other_items: list[_ExistingPfvProxy] = []
        for op in other_pfv_list:
            p = patients_by_id.get(op.patient_id)
            other_items.append(
                _ExistingPfvProxy(
                    patient_id=op.patient_id,
                    weekday=op.weekday,
                    start_time=op.start_time,
                    course_template_id=op.course_template_id,
                    office_id=(p.primary_office_id if p is not None else None),
                )
            )

        conflicts = _detect_cross_address_time_conflicts(
            [*proposed_items, *other_items],
            patients_by_id,
        )
        # 提案 patient が関与する conflict のみ採用 (他患者同士の既存衝突は
        # 本 endpoint の責務外 — 別途 reset-to-fixed 等で検出される).
        target_pid_str = str(patient_id)
        relevant = [c for c in conflicts if target_pid_str in c["patient_ids"]]
        if relevant:
            raise CrossAddressTimeConflictError(relevant)

        # Phase G-21 W1: 4 経路統合 — 提案 PFV を V2Visit に展開し
        # ``_apply_corrections_to_visits`` を通して移動時間補正の警告を surface する.
        # DB UPDATE 前の参考情報として warnings に追記する.
        if target_patient.lat is not None and target_patient.lng is not None:
            proposal_visits: list[V2Visit] = []
            for wd, (st, dur) in proposed_by_wd.items():
                end_t = _add_minutes(st, dur)
                proposal_visits.append(
                    V2Visit(
                        patient_id=patient_id,
                        patient_name=target_patient.name,
                        patient_code=target_patient.code,
                        weekday=wd,
                        start_time=st,
                        end_time=end_t,
                        service_minutes=dur,
                        lat=float(target_patient.lat),
                        lng=float(target_patient.lng),
                        office_id=target_patient.primary_office_id,
                        am_pm="am" if st.hour < NOON_HOUR else "pm",
                        source_kind="fixed",
                        course_code="M",
                        time_type="固定",
                        preferred_start=_fmt_hhmm(st),
                    )
                )
            if proposal_visits:
                proposal_warnings: list[V2Warning] = []
                _apply_corrections_to_visits(
                    proposal_visits,
                    warnings=proposal_warnings,
                    office_name_by_id=None,
                    config=config,
                )
                for vw in proposal_warnings:
                    warnings.append(vw.message)

    # P0-2 (I-04): 適用時再検証 (TOCTOU 緩和). ``with_for_update`` ロック取得後・
    # 書込前の本位置で、他患者 PFV との時間衝突 (V3) / 昼休み重複 (V4) / コース容量 (V5)
    # を read-only 再検証し、warning 文言をレスポンス ``warnings`` に載せる (ブロックしない).
    # 注: pinned 保護 (カーネル V2 / has_errors) はここでは 422 化しない. 上の
    # ``pinned_existing`` ガード (mode='normal' の既存 pinned があれば先に 422) が発火する
    # ため、二重 422 を避ける設計 (設計書 §2.1 / Commit 2 スコープ). 警告文言は
    # カーネル (Commit 1) が生成する日本語をそのまま流用し、ここでは重複定義しない.
    if config is not None:
        from app.schemas.v2.patient_fixed_visit import (
            PatientFixedVisitV2Base as _PfvValidationItem,
        )
        from app.services.scheduling.pfv_validator import (
            validate_pfv_changes as _validate_pfv_changes,
        )

        _validation_items = [
            _PfvValidationItem(
                weekday=wd,
                start_time=st,
                duration_min=dur,
                # course_template_id は既存 PFV から継承 (無ければ None). カーネル V3/V5 の
                # course 単位グルーピングを既存の異住所検出ロジックと一致させる.
                # 既知の制限 (レビューLOW): 新規曜日は None バケットに落ちるため、その曜日の
                # V5 容量警告は偽陰性になり得る (warning-only のため安全側). course_code から
                # の解決は将来課題.
                course_template_id=(
                    existing_by_wd[wd].course_template_id if wd in existing_by_wd else None
                ),
                slot_index=0,
                is_pinned=False,
            )
            for wd, (st, dur) in proposed_by_wd.items()
        ]
        _validation = await _validate_pfv_changes(
            db, patient_id, _validation_items, "normal", config=config
        )
        for _w in _validation.warnings_only:
            warnings.append(_w.message)

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


# ---------------------------------------------------------------------------
# Phase G-17: 表示週の全 Course 担当 + visit_staff_assignments を一括解除
# ---------------------------------------------------------------------------


async def unassign_all_staff_for_week(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
) -> dict[str, int]:
    """Phase G-17: 表示週の全 Course 担当 + visit_staff_assignments を一括解除.

    手順:
      1. ``courses`` WHERE iso_year/iso_week (+ office_id) の
         ``assigned_staff_id`` を NULL にする. ``course_status`` / ``template_id``
         等は維持し course 自体は残す.
      2. ``visits`` WHERE visit_date が当該週 (Mon-Sun) かつ ``deleted_at IS NULL``
         かつ patient.primary_office_id in office_ids の id を取得.
      3. ``visit_staff_assignments`` WHERE visit_id IN (...) を物理 delete.

    トランザクション内で完結する (``db.flush()`` のみ. commit は呼び出し側).

    Args:
        db: AsyncSession.
        iso_year: ISO 年 (2020-2100).
        iso_week: ISO 週 (1-53).
        office_ids: 対象拠点. 空なら全 active 拠点.

    Returns:
        ``{"courses_unassigned": N, "visit_assignments_removed": M}`` の dict.
    """
    if iso_year < 2000 or iso_year > 2100:
        raise ValueError(f"iso_year out of range: {iso_year}")
    if iso_week < 1 or iso_week > 53:
        raise ValueError(f"iso_week out of range: {iso_week}")

    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        week_sunday = date.fromisocalendar(iso_year, iso_week, 7)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: year={iso_year} week={iso_week}") from exc

    # 全拠点指定対応 (office_ids 空 = 全 active 拠点)
    if not office_ids:
        office_rows = await db.scalars(select(Office.id).where(Office.deleted_at.is_(None)))
        office_ids = list(office_rows.all())
        if not office_ids:
            return {"courses_unassigned": 0, "visit_assignments_removed": 0}

    # 1) courses.assigned_staff_id を NULL に
    # course は (iso_year, iso_week, office_id) で絞る. soft-delete は除外.
    # 既に NULL のものはカウントしない (== "実際に解除した数" を返す).
    course_rows = await db.scalars(
        select(Course).where(
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.office_id.in_(office_ids),
            Course.deleted_at.is_(None),
            Course.assigned_staff_id.is_not(None),
        )
    )
    courses_to_unassign = list(course_rows.all())
    courses_unassigned = 0
    for c in courses_to_unassign:
        c.assigned_staff_id = None
        courses_unassigned += 1

    # 2) 当該週 (Mon-Sun) + office_ids 内の patient に紐づく visit を抽出.
    # Visit には office_id が無いので patient.primary_office_id 経由でフィルタ.
    visit_id_rows = await db.scalars(
        select(Visit.id)
        .join(Patient, Patient.id == Visit.patient_id)
        .where(
            Visit.visit_date >= week_monday,
            Visit.visit_date <= week_sunday,
            Visit.deleted_at.is_(None),
            Patient.primary_office_id.in_(office_ids),
        )
    )
    visit_ids = list(visit_id_rows.all())

    # 3) visit_staff_assignments を物理 delete.
    visit_assignments_removed = 0
    if visit_ids:
        from sqlalchemy import delete as sa_delete

        result = await db.execute(
            sa_delete(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
        )
        visit_assignments_removed = int(result.rowcount or 0)

    # 4) visits.primary_staff_id / secondary_staff_id / manual_staff_override も解除する。
    #    PO 報告 (2026-07-03): VSA だけ消して primary_staff_id を残すと、訪問モニター
    #    (primary_staff_id でグルーピング) に旧担当が表示され続ける不整合が出ていた。
    #    「一斉未割当」は担当の完全リセットなので、visit 本体の担当列と欠勤対応の
    #    手動差し替え保護フラグも揃えて解除する。
    if visit_ids:
        from sqlalchemy import update as sa_update

        await db.execute(
            sa_update(Visit)
            .where(
                Visit.id.in_(visit_ids),
                (Visit.primary_staff_id.is_not(None))
                | (Visit.secondary_staff_id.is_not(None))
                | (Visit.manual_staff_override.is_(True)),
            )
            .values(primary_staff_id=None, secondary_staff_id=None, manual_staff_override=False)
        )

    await db.flush()
    return {
        "courses_unassigned": courses_unassigned,
        "visit_assignments_removed": visit_assignments_removed,
    }


__all__ = [
    "AM_BLOCK_END",
    "AM_BLOCK_START",
    "COURSE_MAX_MINUTES",
    "LUNCH_CANDIDATE_STEP_MIN",
    "LUNCH_DEFAULT_END",
    "LUNCH_DEFAULT_START",
    "LUNCH_DURATION_FALLBACK",
    "LUNCH_DURATION_PREFERRED",
    "LUNCH_EARLIEST_START",
    "LUNCH_END",
    "LUNCH_LATEST_END",
    "LUNCH_LATEST_START",
    "LUNCH_START",
    "MAX_PATIENTS_PER_COURSE",
    "MAX_PATIENTS_PER_SET",
    "NOON_HOUR",
    "PM_BLOCK_END",
    "PM_BLOCK_START",
    "SAME_ADDRESS_TOLERANCE",
    "TRAVEL_SPEED_KMH",
    "G21_NEW_ALGORITHM_FEATURE_KEY",
    "CrossAddressTimeConflictError",
    "PinnedVisitMovedError",
    "V2Bucket",
    "V2Set",
    "V2Visit",
    "V2Warning",
    "_address_bucket",
    "_apply_corrections_to_visits",
    "_auto_shift_same_time_conflicts",
    "_consolidate_same_address_time",
    "_detect_cross_address_time_conflicts",
    "_enforce_h2_same_address",
    "_enforce_h2_split_overflow",
    "_enforce_same_address_pair_mode",
    "_g93_desired_weekdays",
    "_g94_desired_count",
    "_g94_resolve_cross_patient_double_booking",
    "_is_in_lunch_break",
    "_load_before_visits_from_pfv",
    "_load_before_visits_v2",
    "_load_g21_enabled_offices",
    "_load_same_address_pair_modes",
    "_load_unavailable_slots",
    "apply_individual_proposal",
    "apply_travel_corrections",
    "apply_week_only",
    "build_visits_for_pool",
    "build_visits_for_pool_v2",
    "calc_course_total_minutes",
    "calc_h_violations",
    "calc_total_distance",
    "cluster_by_distance_greedy",
    "combine_am_pm_sets",
    "compute_lunch_window",
    "count_active_staff_per_weekday",
    "determine_am_pm",
    "enforce_course_count_constraint",
    "haversine_km",
    "haversine_minutes",
    "reset_visits_to_fixed",
    "run_v2_pipeline",
    "split_into_buckets",
    "unassign_all_staff_for_week",
]
