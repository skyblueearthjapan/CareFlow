"""scheduling 共通定数 (単一ソース) — Phase G-88 の土台.

このモジュールは scheduling パッケージ内の **leaf** であり、同パッケージ内の
他モジュール (``auto_allocator_v2`` / ``auto_allocator`` / ``layer2_clustering`` /
``layer3_assignment`` / ``propose_slots_service`` / ``proposal_solver`` /
``board_service`` 等) を一切 import しない. これにより循環 import を構造的に防ぐ.

ここに集約するのは「複数モジュールで同値・同概念が重複定義されていた真の重複」
のみ. 値は現状 (リファクタ前) と完全に同一であり、挙動は一切変えない.

⚠️ 重要: A-E (``auto_allocator_v2._COURSE_CODES``) と A-D
(``layer2_clustering.COURSE_CODES``) は **別機能** の上限であり、本モジュールには
集約しない (統一しないこと). 同様に ``VISIT_BUFFER_MINUTES`` /
``TRAVEL_SPEED_KMH`` / 昼休み定数群 / 営業枠 (AM/PM_BLOCK) は今回のリファクタ
対象外であり、各モジュール側に残す (設定化は後工程).
"""

from __future__ import annotations

from datetime import time

# H9: 1 コース (午前 + 午後 合計) の上限人数 (§12.1).
# 出所: 旧 ``auto_allocator_v2.MAX_PATIENTS_PER_COURSE`` (= 6).
# ``layer2_clustering`` / ``auto_allocator`` / ``propose_slots_service`` でも
# 同値が重複定義されていたため単一ソース化.
MAX_PATIENTS_PER_COURSE: int = 6

# W41 v2 拡張 (コース容量 duration 化): 1 コース (1 スタッフ × 1 日, 昼休憩除く)
# の所要時間上限 (分). 9:00-12:00 + 13:00-18:00 = 8 時間 = 480 分.
# 出所: 旧 ``auto_allocator_v2.COURSE_MAX_MINUTES`` (= 480).
COURSE_MAX_MINUTES: int = 480

# H2: 同住所判定の許容誤差 (緯度経度の絶対差 ≒ 100m).
# 出所: 旧 ``auto_allocator_v2.SAME_ADDRESS_TOLERANCE`` (= 0.001).
# ``auto_allocator`` (v1) でも同値が重複定義されていたため単一ソース化.
SAME_ADDRESS_TOLERANCE: float = 0.001

# Phase G-45: 拠点稼働曜日 (= operating_weekdays) のデフォルト値 (= 月-土).
# DB カラム NULL / 不正値 (= リスト以外 or 範囲外要素を含む) の場合に
# 各 coerce/loader がフォールバックする.
# 出所: 旧 ``auto_allocator_v2.DEFAULT_OFFICE_OPERATING_WEEKDAYS`` /
# ``layer3_assignment._DEFAULT_OFFICE_OPERATING_WEEKDAYS`` (= {0,1,2,3,4,5}).
DEFAULT_OFFICE_OPERATING_WEEKDAYS: frozenset[int] = frozenset({0, 1, 2, 3, 4, 5})


# ---------------------------------------------------------------------------
# Phase G-88 Step2: SchedulingConfig の既定値の単一ソース.
#
# ⚠️ 重要 (挙動不変の保証):
#   ここに定義する既定値は、Step2 で新設する ``SchedulingConfig`` /
#   ``DEFAULT_SCHEDULING_CONFIG`` / 設定テーブルローダーが「行 / 列 NULL の
#   フォールバック値」として参照するための **出所** にすぎない. 既存の最適化
#   コード (``auto_allocator_v2`` / ``proposal_solver`` 等) が現に import して
#   使う module 定数 (``VISIT_BUFFER_MINUTES`` / ``TRAVEL_SPEED_KMH`` / 昼休み
#   定数群 / ``AM_BLOCK_START`` / ``PM_BLOCK_END`` 等) は **一切移動・改変しない**.
#   それらは各モジュール側に現状の定義・値のまま残し、本モジュールの値は単に
#   それと同値を別途定義する (= Step3 で引数注入に切り替えるまで挙動不変).
#
# 各既定値の出所 (= 既存の最適化コードの定数と同値であること):
#   - ① 訪問間バッファー: ``auto_allocator_v2.VISIT_BUFFER_MINUTES`` (= 8)
#   - ② 移動速度:        ``auto_allocator_v2.TRAVEL_SPEED_KMH``    (= 20.0)
#   - ③a 昼休み長さ:     ``auto_allocator_v2.LUNCH_DURATION_PREFERRED`` (= 60)
#   - ③b 昼休み開始/終了: ``auto_allocator_v2.LUNCH_EARLIEST_START`` (11:30) /
#                         ``auto_allocator_v2.LUNCH_LATEST_END``    (13:30)
#   - ④ 営業開始/終了:   ``auto_allocator_v2.AM_BLOCK_START`` (09:30) /
#                         ``auto_allocator_v2.PM_BLOCK_END``   (18:00)
#   - ⑤ 1 コース最大人数: ``MAX_PATIENTS_PER_COURSE`` (= 6, 本モジュール上部で集約済)
# ---------------------------------------------------------------------------

# ① 訪問間バッファー (分). 既定 8 / 範囲 0..30.
DEFAULT_VISIT_BUFFER_MINUTES: int = 8

# ② 移動速度 (km/h). 既定 20 / 範囲 10..40.
DEFAULT_TRAVEL_SPEED_KMH: float = 20.0

# ③a 昼休み・標準の長さ (分). 既定 60 / 範囲 30..90.
DEFAULT_LUNCH_DURATION_MINUTES: int = 60

# ③b 昼休み・取得時間帯 (開始 / 終了). 既定 11:30 / 13:30.
DEFAULT_LUNCH_WINDOW_START: time = time(11, 30)
DEFAULT_LUNCH_WINDOW_END: time = time(13, 30)

# ④ 営業時間 (開始 / 終了). 既定 09:30 / 18:00.
DEFAULT_BUSINESS_START: time = time(9, 30)
DEFAULT_BUSINESS_END: time = time(18, 0)

# ⑤ 1 コース最大人数. 既定は ``MAX_PATIENTS_PER_COURSE`` (= 6) を出所にする
# (別名は設けず、SchedulingConfig 構築側で ``MAX_PATIENTS_PER_COURSE`` を参照).


__all__ = [
    "COURSE_MAX_MINUTES",
    "DEFAULT_BUSINESS_END",
    "DEFAULT_BUSINESS_START",
    "DEFAULT_LUNCH_DURATION_MINUTES",
    "DEFAULT_LUNCH_WINDOW_END",
    "DEFAULT_LUNCH_WINDOW_START",
    "DEFAULT_OFFICE_OPERATING_WEEKDAYS",
    "DEFAULT_TRAVEL_SPEED_KMH",
    "DEFAULT_VISIT_BUFFER_MINUTES",
    "MAX_PATIENTS_PER_COURSE",
    "SAME_ADDRESS_TOLERANCE",
]
