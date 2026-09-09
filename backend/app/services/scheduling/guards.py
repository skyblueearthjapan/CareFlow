"""予定に入れてよい患者かどうかの入口ガード (Phase 2 契約・先に固定).

正典: ``docs/plans/patient-status-schedule-design-2026-09-09.md`` §7-3(d)。

非稼働 (``Patient.status != 'active'``) の患者を新しい予定に入れる操作は
**基本は止める** (PO 決定 Q16)。ただし「入院中のまま進む道」は作らず、FE には
``can_override: true`` を返して「ステータスを稼働中に変更しますか？」の確認
→ PATCH status=active (復帰フロー) → 元の操作の再実行、という導線を取らせる。

本モジュールは **契約 (例外の形) を固定するだけ** で、エンドポイントへの結線は
Phase 2 で行う (§7-3(d) の適用先一覧)。

# Phase 2: not wired into endpoints yet

ステータスの語彙 (判定・日本語ラベル) は **``app.services.patient_status_sync`` が
単一ソース** (設計 §7-1)。ここでは再宣言せず import して使う。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select

from app.models.patient import Patient
from app.services.patient_status_sync import is_schedulable_status, status_label

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ガードの 422 に載せるエラーコード (FE はこれで上書き導線を出し分ける)。
PATIENT_NOT_ACTIVE_CODE = "patient_not_active"


def _blocked_message(patient: Patient) -> str:
    return f"{patient.name}様は{status_label(patient.status)}のため予定に入れられません"


def _blocked_detail(patient: Patient) -> dict[str, Any]:
    """422 detail / bulk の excluded エントリ共通の中身。"""
    return {
        "code": PATIENT_NOT_ACTIVE_CODE,
        "patient_id": str(patient.id),
        "status": patient.status,
        "status_label": status_label(patient.status),
        "can_override": True,
        "message": _blocked_message(patient),
    }


async def _load_patient(db: AsyncSession, patient_id: UUID) -> Patient:
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="患者が見つかりません",
        )
    return patient


async def ensure_patient_schedulable(db: AsyncSession, patient_id: UUID) -> Patient:
    """単一患者向けガード。非稼働なら 422 (``code='patient_not_active'``)。

    Raises:
        HTTPException 404: 患者が存在しない / 論理削除済み。
        HTTPException 422: ``status != 'active'``。detail は
            ``{code, patient_id, status, status_label, can_override, message}``。

    Returns:
        Patient: 稼働中の患者 (呼び出し側でそのまま使える)。
    """
    patient = await _load_patient(db, patient_id)
    if not is_schedulable_status(patient.status):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_blocked_detail(patient),
        )
    return patient


async def split_schedulable_patient_ids(
    db: AsyncSession,
    ids: Iterable[UUID],
) -> tuple[list[UUID], list[dict[str, Any]]]:
    """一括系エンドポイント向け: 稼働中とそれ以外に仕分ける (例外は投げない)。

    ``pool-bulk-apply`` のように対象が混在しうる操作で使う。除外理由は
    ``warnings[]`` にそのまま載せられる形で返す。

    Args:
        db: セッション。
        ids: 患者 ID (重複可)。**入力順を保つ** (重複は最初の 1 回のみ)。

    Returns:
        (schedulable_ids, excluded):
            - ``schedulable_ids``: 稼働中の患者 ID (入力順)。
            - ``excluded``: ``{patient_id, status, status_label, message}`` の一覧。
              存在しない / 論理削除済みの ID も ``status=None`` で除外側に入れる
              (一括系は 404 で全体を落とさない)。
    """
    ordered: list[UUID] = []
    seen: set[UUID] = set()
    for pid in ids:
        if pid in seen:
            continue
        seen.add(pid)
        ordered.append(pid)
    if not ordered:
        return [], []

    rows = await db.scalars(
        select(Patient).where(Patient.id.in_(ordered), Patient.deleted_at.is_(None))
    )
    by_id = {p.id: p for p in rows.all()}

    schedulable: list[UUID] = []
    excluded: list[dict[str, Any]] = []
    for pid in ordered:
        patient = by_id.get(pid)
        if patient is None:
            excluded.append(
                {
                    "patient_id": str(pid),
                    "status": None,
                    "status_label": status_label(None),
                    "message": "患者が見つかりません",
                }
            )
            continue
        if is_schedulable_status(patient.status):
            schedulable.append(pid)
            continue
        detail = _blocked_detail(patient)
        excluded.append(
            {
                "patient_id": detail["patient_id"],
                "status": detail["status"],
                "status_label": detail["status_label"],
                "message": detail["message"],
            }
        )
    return schedulable, excluded
