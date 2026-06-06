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


__all__ = [
    "COURSE_MAX_MINUTES",
    "DEFAULT_OFFICE_OPERATING_WEEKDAYS",
    "MAX_PATIENTS_PER_COURSE",
    "SAME_ADDRESS_TOLERANCE",
]
