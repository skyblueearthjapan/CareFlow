"""Scheduling-settings endpoints (Phase G-88 Step2).

GET /scheduling-settings   有効値 (= DB 行 or 既定の合成) + 各値が既定か (admin/manager)
PUT /scheduling-settings   部分更新 (範囲外 422) (admin/manager)

* 事業所 = DB 単位の単一行 (シングルトン). 行が無ければローダーが全既定を返す.
* PUT は明示 ``null`` でその列を既定に戻し、未指定の列は変更しない
  (= ``model_fields_set`` で区別).
* 範囲バリデーションは Pydantic Field 制約 (422). 時刻の前後関係は、片方のみ更新
  した場合も既存値とマージした最終値で再検証する (422).

⚠️ この Step では最適化挙動は変えない (Step3 で SchedulingConfig を注入). 本 API は
設定値の保存・読み出しのみを担う.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.scheduling_settings import SchedulingSettings
from app.models.user import User
from app.schemas.scheduling_settings import (
    SchedulingSettingsDefaults,
    SchedulingSettingsRead,
    SchedulingSettingsUpdate,
    SchedulingSettingsValues,
)
from app.services.scheduling.config import (
    build_scheduling_config,
    load_scheduling_settings_row,
)

router = APIRouter()

# 設定列名 (PUT のマージ / is_default 判定で共通利用).
_SETTING_FIELDS: tuple[str, ...] = (
    "visit_buffer_min",
    "travel_speed_kmh",
    "lunch_duration_min",
    "lunch_window_start",
    "lunch_window_end",
    "business_start",
    "business_end",
    "max_patients_per_course",
)


def _build_read(row: SchedulingSettings | None) -> SchedulingSettingsRead:
    """行 (or None) から GET/PUT レスポンス (有効値 + 既定フラグ) を組む."""
    config = build_scheduling_config(row)
    values = SchedulingSettingsValues(
        visit_buffer_min=config.visit_buffer_min,
        travel_speed_kmh=config.travel_speed_kmh,
        lunch_duration_min=config.lunch_duration_min,
        lunch_window_start=config.lunch_window_start,
        lunch_window_end=config.lunch_window_end,
        business_start=config.business_start,
        business_end=config.business_end,
        max_patients_per_course=config.max_patients_per_course,
    )
    # 各値が既定由来か = 行が無い or その列が NULL.
    is_default = SchedulingSettingsDefaults(
        **{field: (row is None or getattr(row, field) is None) for field in _SETTING_FIELDS}
    )
    return SchedulingSettingsRead(values=values, is_default=is_default)


@router.get(
    "",
    response_model=SchedulingSettingsRead,
    summary="Phase G-88: 最適化設定の有効値取得 (全ロール・read-only)",
)
async def get_scheduling_settings(
    db: DbDep,
    # 閲覧系画面 (PC スケジュール・現場ボード) が営業時間等の表示に使うため
    # staff にも read を許可する。更新 (PUT) は引き続き admin/manager のみ。
    _actor: Annotated[User, Depends(require_role("admin", "staff"))],
) -> SchedulingSettingsRead:
    row = await load_scheduling_settings_row(db)
    return _build_read(row)


@router.put(
    "",
    response_model=SchedulingSettingsRead,
    summary="Phase G-88: 最適化設定の部分更新 (admin/manager)",
)
async def update_scheduling_settings(
    payload: SchedulingSettingsUpdate,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> SchedulingSettingsRead:
    """部分更新.

    * 未指定の列は変更しない (``model_fields_set`` で判定).
    * 明示 ``null`` はその列を既定に戻す (DB 列を NULL に).
    * マージ後の最終値で時刻の前後関係を再検証 (422).
    * audit_logs に ``action="scheduling_settings_update"`` を記録.
    """
    row = await load_scheduling_settings_row(db)

    updates = payload.model_dump(include=payload.model_fields_set)

    before: dict[str, object | None] = {}
    after: dict[str, object | None] = {}
    if row is None:
        row = SchedulingSettings(is_singleton=True)
        # 新規行は適用前の各列が全て NULL (= 既定由来) なので before は全 None 固定.
        for field in _SETTING_FIELDS:
            before[field] = None
        db.add(row)
    else:
        for field in _SETTING_FIELDS:
            before[field] = _jsonable(getattr(row, field))

    for field, value in updates.items():
        setattr(row, field, value)

    # マージ後の「有効値」(既定 fallback 後) で時刻の前後関係を検証する.
    # 片側を明示 null で既定に戻した場合でも、合成後の有効値で矛盾を検出する.
    _validate_time_order(row)

    for field in _SETTING_FIELDS:
        after[field] = _jsonable(getattr(row, field))

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="scheduling_settings_update",
            target_table="scheduling_settings",
            target_id=str(row.id),
            before=before,
            after=after,
        )
    )

    await db.commit()
    await db.refresh(row)
    return _build_read(row)


def _validate_time_order(row: SchedulingSettings) -> None:
    """マージ後の「有効値」(既定 fallback 後) で時刻の前後関係を検証. 違反は 422.

    生 row 値 (NULL 含む) ではなく ``build_scheduling_config`` が合成した有効値で
    検証することで、片側を明示 null で既定に戻し他方が極端な既存値のときの矛盾
    (例: business_start 既定 09:30 >= business_end 09:00) も確実に検出する.
    """
    config = build_scheduling_config(row)
    if config.lunch_window_start >= config.lunch_window_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="lunch_window_start は lunch_window_end より前である必要があります",
        )
    if config.business_start >= config.business_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="business_start は business_end より前である必要があります",
        )


def _jsonable(value: object | None) -> object | None:
    """audit_log JSONB 用に time / Decimal を文字列・数値へ正規化."""
    if value is None:
        return None
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
