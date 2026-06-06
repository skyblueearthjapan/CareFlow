"""Phase G-88 Step2: 自動最適化の事業所別設定 (SchedulingConfig) + ローダー.

本モジュールは「最適化の挙動を変えるための値の **入れ物**」を提供する. この
Step では最適化パイプラインへは一切注入しない (Step3 で注入). ここで定義する
``SchedulingConfig`` は frozen dataclass で、各フィールドの既定値は
``scheduling.constants`` の ``DEFAULT_*`` を出所にする.

設定の保存場所は専用テーブル ``scheduling_settings`` (事業所 = DB 単位の単一行,
各列 nullable). ``load_scheduling_config`` が単一行を読み、各列が NULL なら
constants 既定にフォールバックして ``SchedulingConfig`` を組む (行が無ければ全既定).

⚠️ 本モジュールは scheduling パッケージ内の **leaf に準じる** 立ち位置で、
最適化本体 (``auto_allocator_v2`` 等) を import しない (循環 import 回避).
依存は ``constants`` (leaf) と ORM モデルのみ.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduling_settings import SchedulingSettings
from app.services.scheduling.constants import (
    DEFAULT_BUSINESS_END,
    DEFAULT_BUSINESS_START,
    DEFAULT_LUNCH_DURATION_MINUTES,
    DEFAULT_LUNCH_WINDOW_END,
    DEFAULT_LUNCH_WINDOW_START,
    DEFAULT_TRAVEL_SPEED_KMH,
    DEFAULT_VISIT_BUFFER_MINUTES,
    MAX_PATIENTS_PER_COURSE,
)


@dataclass(frozen=True)
class SchedulingConfig:
    """自動最適化の事業所別設定 (有効値 = DB 行 or 既定値の合成).

    フィールドは管理 UI で設定可能な 8 項目に 1:1 対応する. すべて確定した
    具体値 (NULL なし) を持つ. NULL フォールバックは ``load_scheduling_config``
    が行い、ここには有効値のみが入る.
    """

    visit_buffer_min: int  # ① 既定 8 / 0..30
    travel_speed_kmh: float  # ② 既定 20 / 10..40
    lunch_duration_min: int  # ③a 既定 60 / 30..90
    lunch_window_start: time  # ③b 既定 11:30
    lunch_window_end: time  # ③b 既定 13:30
    business_start: time  # ④ 既定 09:30
    business_end: time  # ④ 既定 18:00
    max_patients_per_course: int  # ⑤ 既定 6 / 1..10


# constants 由来の既定値で構築した「全既定」設定. 設定行が存在しない / 全列
# NULL のときの有効値であり、Step3 で最適化に注入される際のフォールバック基準.
DEFAULT_SCHEDULING_CONFIG: SchedulingConfig = SchedulingConfig(
    visit_buffer_min=DEFAULT_VISIT_BUFFER_MINUTES,
    travel_speed_kmh=DEFAULT_TRAVEL_SPEED_KMH,
    lunch_duration_min=DEFAULT_LUNCH_DURATION_MINUTES,
    lunch_window_start=DEFAULT_LUNCH_WINDOW_START,
    lunch_window_end=DEFAULT_LUNCH_WINDOW_END,
    business_start=DEFAULT_BUSINESS_START,
    business_end=DEFAULT_BUSINESS_END,
    max_patients_per_course=MAX_PATIENTS_PER_COURSE,
)


def build_scheduling_config(row: SchedulingSettings | None) -> SchedulingConfig:
    """``SchedulingSettings`` 行 (or None) から有効値 ``SchedulingConfig`` を組む.

    * ``row`` が None: 全既定 (= ``DEFAULT_SCHEDULING_CONFIG``).
    * 各列が NULL: その列だけ constants 既定にフォールバック.

    DB の numeric 列は ``Decimal`` で返りうるため ``travel_speed_kmh`` は
    ``float`` に正規化する (SchedulingConfig 契約 = float).
    """
    if row is None:
        return DEFAULT_SCHEDULING_CONFIG

    return SchedulingConfig(
        visit_buffer_min=(
            row.visit_buffer_min
            if row.visit_buffer_min is not None
            else DEFAULT_VISIT_BUFFER_MINUTES
        ),
        travel_speed_kmh=(
            float(row.travel_speed_kmh)
            if row.travel_speed_kmh is not None
            else DEFAULT_TRAVEL_SPEED_KMH
        ),
        lunch_duration_min=(
            row.lunch_duration_min
            if row.lunch_duration_min is not None
            else DEFAULT_LUNCH_DURATION_MINUTES
        ),
        lunch_window_start=(
            row.lunch_window_start
            if row.lunch_window_start is not None
            else DEFAULT_LUNCH_WINDOW_START
        ),
        lunch_window_end=(
            row.lunch_window_end if row.lunch_window_end is not None else DEFAULT_LUNCH_WINDOW_END
        ),
        business_start=(
            row.business_start if row.business_start is not None else DEFAULT_BUSINESS_START
        ),
        business_end=(row.business_end if row.business_end is not None else DEFAULT_BUSINESS_END),
        max_patients_per_course=(
            row.max_patients_per_course
            if row.max_patients_per_course is not None
            else MAX_PATIENTS_PER_COURSE
        ),
    )


async def load_scheduling_settings_row(db: AsyncSession) -> SchedulingSettings | None:
    """シングルトンの ``scheduling_settings`` 行を 1 件返す (無ければ None).

    事業所 = DB 単位の単一行運用のため、``is_singleton = true`` の最初の 1 行を
    取得する (理論上 1 行のみ; 部分 UNIQUE 制約で重複を防止).
    """
    return await db.scalar(
        select(SchedulingSettings).where(SchedulingSettings.is_singleton.is_(True))
    )


async def load_scheduling_config(db: AsyncSession) -> SchedulingConfig:
    """有効な ``SchedulingConfig`` を返す.

    設定行が無ければ全既定、行があれば各列 NULL を constants 既定で埋めて合成する.
    Step3 で最適化パイプラインへ引数注入する際の入口となる.
    """
    row = await load_scheduling_settings_row(db)
    return build_scheduling_config(row)
