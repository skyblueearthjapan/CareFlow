"""Checkin-settings endpoints (QR 訪問チェックイン Phase 4).

GET  /checkin-settings        有効値 (= DB 行 or 既定の合成) + 各値が既定か (admin/manager)
PUT  /checkin-settings        部分更新 (範囲外 / review<match は 422) (admin/manager)
GET  /checkin-settings/public 距離系のみ (match/review/accuracy) を返す (全ログインユーザ)

* 全社一律 = DB 単位の単一行 (シングルトン). 行が無ければローダーが全既定を返す
  (lazy: 初回 PUT で行を作る). 既定値は ``judge.DEFAULT_THRESHOLDS``.
* PUT は明示 ``null`` でその列を既定に戻し、未指定の列は変更しない
  (= ``model_fields_set`` で区別). 範囲は Pydantic Field (422)、交差条件
  ``review_m >= match_m`` はマージ後の有効値で再検証 (422) + DB CHECK で二重防御.
* public は staff も読める最小サブセット. モバイル到着プレビューの概算しきい値
  同期に使う (時間系は出さない).

``scheduling_settings.py`` を雛形とする (Phase G-88).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.checkin_settings import CheckinSettings
from app.models.user import User
from app.schemas.checkin_settings import (
    SETTING_FIELDS,
    CheckinSettingsDefaults,
    CheckinSettingsPublic,
    CheckinSettingsRead,
    CheckinSettingsUpdate,
    CheckinSettingsValues,
)
from app.services.checkin.judge import DEFAULT_THRESHOLDS

router = APIRouter()


async def _load_singleton(db: DbDep) -> CheckinSettings | None:
    """checkin_settings シングルトン行 (無ければ None) を返す."""
    return await db.scalar(select(CheckinSettings).where(CheckinSettings.is_singleton.is_(True)))


def _effective(row: CheckinSettings | None, field: str) -> int:
    """行 (or None) の列値. NULL / 行無しはコード既定にフォールバック."""
    if row is not None:
        value = getattr(row, field, None)
        if value is not None:
            return int(value)
    return DEFAULT_THRESHOLDS[field]


def _build_read(row: CheckinSettings | None) -> CheckinSettingsRead:
    """行 (or None) から GET/PUT レスポンス (有効値 + 既定フラグ) を組む."""
    values = CheckinSettingsValues(**{f: _effective(row, f) for f in SETTING_FIELDS})
    # 各値が既定由来か = 行が無い or その列が NULL.
    is_default = CheckinSettingsDefaults(
        **{f: (row is None or getattr(row, f) is None) for f in SETTING_FIELDS}
    )
    return CheckinSettingsRead(values=values, is_default=is_default)


@router.get(
    "",
    response_model=CheckinSettingsRead,
    summary="Phase 4: チェックインしきい値の有効値取得 (admin/manager)",
)
async def get_checkin_settings(
    db: DbDep,
    # RB (2026-07-08): 閲覧は全ロール (PC版の表示統一)。PUT は admin/manager のまま。
    _actor: Annotated[User, Depends(require_role("admin", "staff"))],
) -> CheckinSettingsRead:
    row = await _load_singleton(db)
    return _build_read(row)


@router.get(
    "/public",
    response_model=CheckinSettingsPublic,
    summary="Phase 4: 距離しきい値の取得 (全ログインユーザ・モバイルプレビュー同期用)",
)
async def get_checkin_settings_public(
    db: DbDep,
    _actor: CurrentActiveUser,
) -> CheckinSettingsPublic:
    """staff を含む全ログインユーザが読める距離系しきい値 (match/review/accuracy).

    モバイルの到着プレビュー (概算) が管理者の設定変更に追随するための最小サブセット。
    時間系 (猶予 / 遅延 / 退出忘れ) は露出しない。
    """
    row = await _load_singleton(db)
    return CheckinSettingsPublic(
        match_m=_effective(row, "match_m"),
        review_m=_effective(row, "review_m"),
        accuracy_m=_effective(row, "accuracy_m"),
    )


@router.put(
    "",
    response_model=CheckinSettingsRead,
    summary="Phase 4: チェックインしきい値の部分更新 (admin/manager)",
)
async def update_checkin_settings(
    payload: CheckinSettingsUpdate,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> CheckinSettingsRead:
    """部分更新 (lazy upsert).

    * 未指定の列は変更しない (``model_fields_set`` で判定).
    * 明示 ``null`` はその列を既定に戻す (DB 列を NULL に).
    * マージ後の有効値で ``review_m >= match_m`` を再検証 (422).
    * audit_logs に ``action="checkin_settings_update"`` を記録.
    """
    row = await _load_singleton(db)

    updates = payload.model_dump(include=payload.model_fields_set)

    before: dict[str, object | None] = {}
    after: dict[str, object | None] = {}
    if row is None:
        row = CheckinSettings(is_singleton=True)
        # 新規行は適用前の各列が全て NULL (= 既定由来) なので before は全 None 固定.
        for field in SETTING_FIELDS:
            before[field] = None
        db.add(row)
    else:
        for field in SETTING_FIELDS:
            before[field] = getattr(row, field)

    for field, value in updates.items():
        setattr(row, field, value)

    # マージ後の有効値 (既定 fallback 後) で交差条件を検証する.
    # 片側のみ更新で他方の既定値と矛盾するケース (例: match_m=400, review_m 既定 300)
    # も確実に検出する (DB CHECK は両列非 NULL 時のみ発火するため API 側で補完).
    if _effective(row, "review_m") < _effective(row, "match_m"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="review_m は match_m 以上である必要があります",
        )

    for field in SETTING_FIELDS:
        after[field] = getattr(row, field)

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="checkin_settings_update",
            target_table="checkin_settings",
            target_id=str(row.id),
            before=before,
            after=after,
        )
    )

    await db.commit()
    await db.refresh(row)
    return _build_read(row)
