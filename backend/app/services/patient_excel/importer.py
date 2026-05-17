"""Excel パース + 差分計算 + DB 反映.

エントリポイント:
  * ``parse_and_diff`` — Excel bytes → (患者行差分, PFV 行差分, summary).
    プレビュー (dry_run=True) でも apply (dry_run=False) でも事前にこの関数で
    差分計算する.
  * ``apply_changes`` — ``parse_and_diff`` の戻り値を受け取り、DB に書き戻す.
    1 transaction で INSERT / UPDATE / DELETE をまとめて実行 (partial commit).
    error 行は ``parse_and_diff`` の時点で ``op=None`` として除外されているので
    ``apply_changes`` には積まれず、自動的に skip される.

差分の表現:
  * ``PatientExcelImportRow`` — patient sheet の 1 行に対応.
  * ``PfvExcelImportRow`` — pfv sheet の 1 行に対応.
  * いずれも ``operation`` が "new" / "update" / "delete" / "noop" / "error".
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from io import BytesIO
from typing import Any
from uuid import UUID

from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.schemas.v2.patient_excel import (
    PatientExcelChange,
    PatientExcelImportRow,
    PatientExcelImportSummary,
    PfvExcelImportRow,
)
from app.services.patient_excel.schema import (
    INSURANCE_VALUES,
    PATIENT_COL_INDEX,
    PFV_COL_INDEX,
    PFV_MODE_VALUES,
    SEX_RESTRICTION_VALUES,
    SEX_VALUES,
    SHEET_PATIENTS,
    SHEET_PFV,
    STATUS_VALUES,
    TIME_TYPE_VALUES,
    WEEKDAY_LABEL_TO_INT,
    is_magic_clear,
    is_magic_delete,
)

# ---------------------------------------------------------------------------
# cell value helpers
# ---------------------------------------------------------------------------


def _is_blank(value: Any) -> bool:
    """空セルの判定. openpyxl は空セルを None で返すが、空文字 / 空白文字も空扱い."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _read_str(value: Any) -> str | None:
    """セル値を str に正規化する (空セルは None)."""
    if _is_blank(value):
        return None
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _read_int(value: Any) -> int | None:
    """セル値を int に正規化. 失敗時は ValueError."""
    if _is_blank(value):
        return None
    if isinstance(value, bool):
        # Excel で bool が出ることはほぼ無いが念のため
        raise ValueError(f"int 型として読めません: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"int 型として読めません (小数): {value!r}")
    s = str(value).strip()
    return int(s)


def _read_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(str(value).strip())


def _read_uuid(value: Any) -> UUID | None:
    if _is_blank(value):
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value).strip())


def _read_hhmm(value: Any) -> str | None:
    """セル値を "HH:MM" 文字列として正規化. 失敗時は ValueError."""
    if _is_blank(value):
        return None
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, datetime):
        return f"{value.hour:02d}:{value.minute:02d}"
    s = str(value).strip()
    # ``HH:MM`` または ``HH:MM:SS``
    parts = s.split(":")
    if len(parts) < 2:
        raise ValueError(f"HH:MM 形式ではありません: {s!r}")
    h = int(parts[0])
    m = int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"時刻の範囲外: {s!r}")
    return f"{h:02d}:{m:02d}"


def _hhmm_to_time(s: str) -> time:
    h, m = s.split(":")[:2]
    return time(int(h), int(m))


def _row_is_empty(row: tuple[Any, ...]) -> bool:
    return all(_is_blank(v) for v in row)


# ---------------------------------------------------------------------------
# lookup builders
# ---------------------------------------------------------------------------


async def _load_offices_by_code(db: AsyncSession) -> dict[str, Office]:
    """code → Office (deleted_at IS NULL のみ)."""
    rows = (
        await db.scalars(
            select(Office).where(
                Office.deleted_at.is_(None),
                Office.code.is_not(None),
            )
        )
    ).all()
    return {str(o.code): o for o in rows}


async def _load_course_templates_by_label(
    db: AsyncSession,
) -> dict[tuple[UUID, str], CourseTemplate]:
    """(office_id, label) → CourseTemplate (deleted_at IS NULL のみ).

    label は label そのもの (大文字小文字区別あり) で使う.
    """
    rows = (
        await db.scalars(select(CourseTemplate).where(CourseTemplate.deleted_at.is_(None)))
    ).all()
    return {(ct.office_id, ct.label): ct for ct in rows}


async def _load_patients_by_id(db: AsyncSession) -> dict[UUID, Patient]:
    rows = (await db.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
    return {p.id: p for p in rows}


async def _load_patient_codes(db: AsyncSession) -> set[str]:
    """既存 (alive) の patient.code 集合 (重複チェック用)."""
    rows = (
        await db.execute(
            select(Patient.code).where(Patient.deleted_at.is_(None), Patient.code.is_not(None))
        )
    ).all()
    return {r[0] for r in rows}


async def _load_pfvs_by_key(
    db: AsyncSession,
    alive_patient_ids: set[UUID],
) -> dict[tuple[UUID, str, int, int], PatientFixedVisit]:
    """(patient_id, mode, weekday, slot_index) → PatientFixedVisit.

    alive な患者の PFV のみ load する (全 PFV bulk SELECT を避けるため).
    """
    if not alive_patient_ids:
        return {}
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id.in_(alive_patient_ids))
        )
    ).all()
    return {(p.patient_id, p.mode, p.weekday, p.slot_index): p for p in rows}


# ---------------------------------------------------------------------------
# Patient sheet parser
# ---------------------------------------------------------------------------


def _parse_patient_row(
    row_number: int,
    row: tuple[Any, ...],
    *,
    existing_patients: dict[UUID, Patient],
    existing_patients_by_code: dict[str, Patient],
    offices_by_code: dict[str, Office],
    existing_codes: set[str],
    already_seen_codes: set[str],
) -> tuple[PatientExcelImportRow, dict[str, Any] | None]:
    """1 行を差分行に変換する.

    戻り値の第 2 要素は apply 時に使う「DB 用 dict」. operation が "error" / "noop" /
    "delete" のときは None or 部分的に意味のあるもの.
    """
    cells: dict[str, Any] = {}
    for col_key, idx in PATIENT_COL_INDEX.items():
        cells[col_key] = row[idx] if idx < len(row) else None

    # 共通: patient_id / patient_code を読む.
    raw_id = cells["patient_id"]
    raw_code = cells["patient_code"]
    raw_delete = cells["delete_flag"]

    # patient_id がある場合は UUID パース.
    try:
        patient_id = _read_uuid(raw_id) if not _is_blank(raw_id) else None
    except ValueError:
        return (
            PatientExcelImportRow(
                row_number=row_number,
                patient_id=None,
                patient_code=_read_str(raw_code),
                operation="error",
                error_message=f"patient_id が UUID 形式ではありません: {raw_id!r}",
            ),
            None,
        )

    patient_code = _read_str(raw_code)

    # 既存患者の検索.
    existing_patient: Patient | None = None
    if patient_id is not None:
        existing_patient = existing_patients.get(patient_id)
        if existing_patient is None:
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient_code,
                    operation="error",
                    error_message=f"patient_id が DB に存在しません: {patient_id}",
                ),
                None,
            )

    # 削除フラグ
    if is_magic_delete(raw_delete):
        # 1) patient_id があれば既存通り (上で resolution 済み)
        if existing_patient is not None:
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=existing_patient.id,
                    patient_code=existing_patient.code,
                    operation="delete",
                ),
                {"_patient_id": existing_patient.id, "_op": "delete"},
            )
        # 2) patient_id 空でも patient_code があれば code で解決
        if patient_code is not None:
            resolved = existing_patients_by_code.get(patient_code)
            if resolved is None:
                # 該当 code が DB に居ない → idempotent な noop 扱い (再 import で
                # error にしないため). export → 1 行削除 → 再 import のようなケースを
                # 想定。
                return (
                    PatientExcelImportRow(
                        row_number=row_number,
                        patient_id=None,
                        patient_code=patient_code,
                        operation="noop",
                    ),
                    None,
                )
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=resolved.id,
                    patient_code=resolved.code,
                    operation="delete",
                ),
                {"_patient_id": resolved.id, "_op": "delete"},
            )
        # 3) 両方空 → error
        return (
            PatientExcelImportRow(
                row_number=row_number,
                patient_id=None,
                patient_code=None,
                operation="error",
                error_message=(
                    "削除フラグが指定されましたが patient_id / patient_code がありません"
                ),
            ),
            None,
        )

    # 各列をパース.
    parsed: dict[str, Any] = {}
    errors: list[str] = []

    # name / kana / address / note: 文字列系
    for k in ("name", "kana", "address", "note"):
        v = cells[k]
        if _is_blank(v):
            parsed[k] = None
        elif is_magic_clear(v):
            parsed[k] = ("CLEAR", None)
        else:
            parsed[k] = ("SET", _read_str(v))

    # patient_code (現在値と diff したいので raw を保持)
    if _is_blank(raw_code):
        parsed["code"] = None
    else:
        parsed["code"] = ("SET", patient_code)

    # enum 系
    def _parse_enum(key: str, allowed: tuple[str, ...]) -> None:
        v = cells[key]
        if _is_blank(v):
            parsed[key] = None
            return
        if is_magic_clear(v):
            parsed[key] = ("CLEAR", None)
            return
        s = _read_str(v)
        if s not in allowed:
            errors.append(f"列「{key}」の値が候補外: {s!r} (許容: {','.join(allowed)})")
            return
        parsed[key] = ("SET", s)

    _parse_enum("sex", SEX_VALUES)
    _parse_enum("status", STATUS_VALUES)
    _parse_enum("insurance", INSURANCE_VALUES)
    _parse_enum("sex_restriction", SEX_RESTRICTION_VALUES)

    # lat / lng (float, 範囲チェック)
    for k, lo, hi in (("lat", -90.0, 90.0), ("lng", -180.0, 180.0)):
        v = cells[k]
        if _is_blank(v):
            parsed[k] = None
            continue
        if is_magic_clear(v):
            parsed[k] = ("CLEAR", None)
            continue
        try:
            f = _read_float(v)
        except (ValueError, TypeError):
            errors.append(f"列「{k}」が数値ではありません: {v!r}")
            continue
        if f is None or f < lo or f > hi:
            errors.append(f"列「{k}」が範囲外 [{lo}, {hi}]: {f!r}")
            continue
        parsed[k] = ("SET", f)

    # 拠点コード → primary_office_id
    raw_office = cells["office_code"]
    if _is_blank(raw_office):
        parsed["primary_office_id"] = None
    elif is_magic_clear(raw_office):
        parsed["primary_office_id"] = ("CLEAR", None)
    else:
        oc = _read_str(raw_office)
        office = offices_by_code.get(oc) if oc else None
        if office is None:
            errors.append(f"拠点コードが DB に存在しません: {oc!r}")
        else:
            parsed["primary_office_id"] = ("SET", office.id)

    if errors:
        return (
            PatientExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient_code,
                operation="error",
                error_message=" / ".join(errors),
            ),
            None,
        )

    # ---- 操作判定 ----
    if existing_patient is None:
        # 新規候補
        # 必須項目チェック.
        missing: list[str] = []
        required_map: dict[str, str] = {
            "patient_code": "patient_code",
            "name": "name",
            "sex": "sex",
            "status": "status",
            "address": "address",
        }
        for spec_key, parsed_key in required_map.items():
            if spec_key == "patient_code":
                if not patient_code:
                    missing.append("patient_code")
                continue
            v = parsed.get(parsed_key)
            if v is None or (isinstance(v, tuple) and v[0] == "CLEAR"):
                missing.append(spec_key)
        if missing:
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=None,
                    patient_code=patient_code,
                    operation="error",
                    error_message=f"新規作成に必要な項目が不足: {', '.join(missing)}",
                ),
                None,
            )
        # 重複チェック (DB の他レコード + 同ファイル内の他行).
        if patient_code in existing_codes:
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=None,
                    patient_code=patient_code,
                    operation="error",
                    error_message=f"patient_code が既に存在します: {patient_code!r}",
                ),
                None,
            )
        if patient_code in already_seen_codes:
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=None,
                    patient_code=patient_code,
                    operation="error",
                    error_message=(
                        f"patient_code が同ファイル内で重複しています: {patient_code!r}"
                    ),
                ),
                None,
            )
        already_seen_codes.add(patient_code)  # type: ignore[arg-type]

        # 仮 UUID をここで発番する。PFV シート側で patient_code 経由でこの新規患者を
        # 参照できるよう、apply_changes は同じ UUID で Patient を INSERT する。
        new_patient_id = uuid.uuid4()
        new_dict: dict[str, Any] = {
            "_op": "new",
            "_new_patient_id": new_patient_id,
            "id": new_patient_id,
            "code": patient_code,
            # API レスポンス schema (PatientV2Read.special_week_active: list[dict] = [])
            # は NOT NULL の list を要求する。DB DEFAULT は NULL なので、INSERT 時に
            # 明示的に [] を埋めないと GET /api/v1/patients が Pydantic validation で
            # 500 になる。Excel importer は通常の POST /patients を通らずに直接 ORM
            # INSERT するので、ここで明示的に補完する必要がある。
            "special_week_active": [],
        }
        # 必須以外も含めて値をフラット化.
        for k, v in parsed.items():
            if k in ("code",):
                continue
            if v is None:
                # 空セル: 触らない (新規時は default に任せる).
                continue
            if isinstance(v, tuple):
                tag, val = v
                # CLEAR は新規時は単に「None で INSERT」と等価.
                new_dict[k] = val
        changes_for_view = [
            PatientExcelChange(field=k, old_value=None, new_value=new_dict[k])
            for k in sorted(new_dict.keys())
            if not k.startswith("_") and k not in ("code", "id")
        ]
        return (
            PatientExcelImportRow(
                row_number=row_number,
                patient_id=new_patient_id,
                patient_code=patient_code,
                operation="new",
                changes=changes_for_view,
            ),
            new_dict,
        )

    # 既存患者の update / noop.
    changes: list[PatientExcelChange] = []
    update_dict: dict[str, Any] = {"_patient_id": existing_patient.id, "_op": "update"}

    # patient_code は通常変更しない方針だが、明示的に書いてあって既存値と違えば update.
    if parsed.get("code") is not None:
        _, new_code = parsed["code"]
        if new_code != existing_patient.code:
            # 重複チェック: DB の他レコード + 同ファイル内の他行 (自分以外).
            if new_code in (existing_codes | already_seen_codes) - {existing_patient.code}:
                return (
                    PatientExcelImportRow(
                        row_number=row_number,
                        patient_id=existing_patient.id,
                        patient_code=patient_code,
                        operation="error",
                        error_message=f"patient_code が既に存在します: {new_code!r}",
                    ),
                    None,
                )
            already_seen_codes.add(new_code)
            changes.append(
                PatientExcelChange(
                    field="code",
                    old_value=existing_patient.code,
                    new_value=new_code,
                )
            )
            update_dict["code"] = new_code

    # 他フィールド.
    field_map: dict[str, str] = {
        "name": "name",
        "kana": "kana",
        "sex": "sex",
        "status": "status",
        "insurance": "insurance",
        "address": "address",
        "lat": "lat",
        "lng": "lng",
        "primary_office_id": "primary_office_id",
        "sex_restriction": "sex_restriction",
        "note": "note",
    }
    for parsed_key, orm_attr in field_map.items():
        v = parsed.get(parsed_key)
        if v is None:
            continue  # 空セル = 触らない
        tag, val = v if isinstance(v, tuple) else ("SET", v)
        if tag == "CLEAR":
            new_val: Any = None
        else:
            new_val = val
        old_val = getattr(existing_patient, orm_attr, None)
        # Numeric (Decimal) と float の比較対策
        if old_val is not None and isinstance(new_val, float):
            try:
                if float(old_val) == float(new_val):
                    continue
            except (TypeError, ValueError):
                pass
        if old_val == new_val:
            continue
        changes.append(
            PatientExcelChange(
                field=orm_attr,
                old_value=_serializable(old_val),
                new_value=_serializable(new_val),
            )
        )
        update_dict[orm_attr] = new_val

    if not changes:
        return (
            PatientExcelImportRow(
                row_number=row_number,
                patient_id=existing_patient.id,
                patient_code=existing_patient.code,
                operation="noop",
            ),
            None,
        )

    return (
        PatientExcelImportRow(
            row_number=row_number,
            patient_id=existing_patient.id,
            patient_code=existing_patient.code,
            operation="update",
            changes=changes,
        ),
        update_dict,
    )


def _serializable(value: Any) -> Any:
    """Pydantic Response 用に安全な型へ変換 (UUID/Decimal/time など)."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (int, float, bool, str)):
        return value
    # Decimal は float 化, time/datetime は ISO 文字列.
    try:
        # Decimal
        return float(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# PFV sheet parser
# ---------------------------------------------------------------------------


class _PendingNewPatient:
    """同 import 内で新規追加される患者の最小限の代理オブジェクト。

    PFV 行のパーサーが ``patient.code`` / ``patient.primary_office_id`` を参照する
    ため、Patient ORM と同じ属性面を持つ軽量オブジェクトを差し込む。
    """

    __slots__ = ("id", "code", "primary_office_id")

    def __init__(
        self,
        *,
        patient_id: UUID,
        code: str,
        primary_office_id: UUID | None,
    ) -> None:
        self.id = patient_id
        self.code = code
        self.primary_office_id = primary_office_id


def _parse_pfv_row(
    row_number: int,
    row: tuple[Any, ...],
    *,
    existing_patients: dict[UUID, Patient],
    pending_new_patients: dict[UUID, _PendingNewPatient],
    patient_code_to_id: dict[str, UUID],
    course_templates: dict[tuple[UUID, str], CourseTemplate],
    existing_pfvs: dict[tuple[UUID, str, int, int], PatientFixedVisit],
    pending_new_keys: set[tuple[UUID, str, int, int]],
) -> tuple[PfvExcelImportRow, dict[str, Any] | None]:
    cells: dict[str, Any] = {}
    for col_key, idx in PFV_COL_INDEX.items():
        cells[col_key] = row[idx] if idx < len(row) else None

    raw_id = cells["patient_id"]
    raw_code = cells["patient_code"]
    raw_delete = cells["delete_flag"]

    # patient_id (任意; 空なら patient_code で解決を試みる)
    try:
        patient_id = _read_uuid(raw_id)
    except ValueError:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=None,
                operation="error",
                error_message=f"patient_id が UUID 形式ではありません: {raw_id!r}",
            ),
            None,
        )

    patient_code = _read_str(raw_code)

    # patient_id 空 → patient_code で解決
    if patient_id is None:
        if patient_code is None:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=None,
                    patient_code=None,
                    operation="error",
                    error_message="patient_id または patient_code が必須です",
                ),
                None,
            )
        resolved_id = patient_code_to_id.get(patient_code)
        if resolved_id is None:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=None,
                    patient_code=patient_code,
                    operation="error",
                    error_message=(
                        f"patient_code が DB にも import 内にも存在しません: {patient_code!r}"
                    ),
                ),
                None,
            )
        patient_id = resolved_id
    elif patient_code is not None:
        # 両方記入されている → 整合性チェック。患者を取り違えてデータ破壊する事故を防ぐ.
        expected_id_from_code = patient_code_to_id.get(patient_code)
        if expected_id_from_code is not None and expected_id_from_code != patient_id:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient_code,
                    operation="error",
                    error_message=(
                        f"patient_id と patient_code が異なる患者を指しています "
                        f"(id={patient_id}, code={patient_code} → 期待 id={expected_id_from_code})"
                    ),
                ),
                None,
            )

    # 既存患者 → ORM patient を取得 / 新規候補 → pseudo patient を組み立て
    patient = existing_patients.get(patient_id)
    if patient is None:
        pending = pending_new_patients.get(patient_id)
        if pending is None:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient_code,
                    operation="error",
                    error_message=f"patient_id が DB に存在しません: {patient_id}",
                ),
                None,
            )
        # 同 import 内で新規追加される患者。Patient ORM はまだ無いので
        # _parse_pfv_row 内で使う最小限の属性を pseudo オブジェクトで賄う。
        patient = pending  # type: ignore[assignment]

    # weekday / slot_index / mode
    raw_weekday = cells["weekday"]
    if _is_blank(raw_weekday):
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                operation="error",
                error_message="weekday が空です",
            ),
            None,
        )
    wd_str = _read_str(raw_weekday)
    weekday = WEEKDAY_LABEL_TO_INT.get(wd_str or "")
    if weekday is None:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                operation="error",
                error_message=f"weekday の値が候補外: {wd_str!r} (許容: 月火水木金土日)",
            ),
            None,
        )

    raw_slot = cells["slot_index"]
    try:
        slot_index = _read_int(raw_slot)
    except (ValueError, TypeError):
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                operation="error",
                error_message=f"slot_index が整数ではありません: {raw_slot!r}",
            ),
            None,
        )
    if slot_index is None:
        slot_index = 0
    if slot_index < 0 or slot_index > 1:
        # NOTE: 仕様書には 0-9 と書いてあるが DB CHECK 制約は 0-1. DB に合わせる.
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                operation="error",
                error_message=f"slot_index は 0 または 1 のみ: {slot_index}",
            ),
            None,
        )

    raw_mode = cells["mode"]
    if _is_blank(raw_mode):
        mode = "normal"
    else:
        mode_str = _read_str(raw_mode)
        if mode_str not in PFV_MODE_VALUES:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient.code,
                    weekday=weekday,
                    slot_index=slot_index,
                    operation="error",
                    error_message=f"mode の値が候補外: {mode_str!r}",
                ),
                None,
            )
        mode = mode_str

    key = (patient_id, mode, weekday, slot_index)
    existing_pfv = existing_pfvs.get(key)

    # 削除フラグ (PFV は物理削除)
    if is_magic_delete(raw_delete):
        if existing_pfv is None:
            # 存在しないものを delete しようとした → noop 扱い (warn でなく許容)
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient.code,
                    weekday=weekday,
                    slot_index=slot_index,
                    operation="noop",
                ),
                None,
            )
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="delete",
            ),
            {"_pfv_id": existing_pfv.id, "_op": "delete"},
        )

    # time_type
    raw_tt = cells["time_type"]
    if _is_blank(raw_tt):
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="error",
                error_message="time_type が空です",
            ),
            None,
        )
    time_type = _read_str(raw_tt)
    if time_type not in TIME_TYPE_VALUES:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="error",
                error_message=f"time_type の値が候補外: {time_type!r}",
            ),
            None,
        )

    # start_time
    try:
        start_str = _read_hhmm(cells["start_time"])
    except (ValueError, TypeError) as exc:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="error",
                error_message=f"start_time が HH:MM 形式ではありません: {exc}",
            ),
            None,
        )
    if start_str is None:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="error",
                error_message="start_time が空です",
            ),
            None,
        )

    # end_time / duration_min: どちらかから duration_min を導出.
    raw_end = cells["end_time"]
    raw_duration = cells["duration_min"]
    duration_min: int | None = None
    if not _is_blank(raw_duration):
        try:
            duration_min = _read_int(raw_duration)
        except (ValueError, TypeError) as exc:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient.code,
                    weekday=weekday,
                    slot_index=slot_index,
                    operation="error",
                    error_message=f"duration_min が整数ではありません: {exc}",
                ),
                None,
            )
    if duration_min is None and not _is_blank(raw_end):
        try:
            end_str = _read_hhmm(raw_end)
        except (ValueError, TypeError) as exc:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient.code,
                    weekday=weekday,
                    slot_index=slot_index,
                    operation="error",
                    error_message=f"end_time が HH:MM 形式ではありません: {exc}",
                ),
                None,
            )
        if end_str is not None:
            sh, sm = (int(x) for x in start_str.split(":"))
            eh, em = (int(x) for x in end_str.split(":"))
            duration_min = (eh * 60 + em) - (sh * 60 + sm)
    if duration_min is None:
        duration_min = 30  # default
    if duration_min < 1 or duration_min > 480:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="error",
                error_message=f"duration_min が範囲外 [1, 480]: {duration_min}",
            ),
            None,
        )

    # course_template_code → course_template_id
    raw_ct = cells["course_template_code"]
    course_template_id: UUID | None = None
    if not _is_blank(raw_ct):
        ct_label = _read_str(raw_ct)
        if patient.primary_office_id is None:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient.code,
                    weekday=weekday,
                    slot_index=slot_index,
                    operation="error",
                    error_message=(
                        f"course_template_code {ct_label!r} 指定だが患者の primary_office_id "
                        "が未設定です"
                    ),
                ),
                None,
            )
        ct = course_templates.get((patient.primary_office_id, ct_label or ""))
        if ct is None:
            return (
                PfvExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient.code,
                    weekday=weekday,
                    slot_index=slot_index,
                    operation="error",
                    error_message=(f"course_template_code が患者拠点に存在しません: {ct_label!r}"),
                ),
                None,
            )
        course_template_id = ct.id

    # ---- ファイル内 (patient_id, mode, weekday, slot_index) 重複検査 ----
    if key in pending_new_keys:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="error",
                error_message=(
                    "同ファイル内で (patient_id, mode, weekday, slot_index) が重複しています"
                ),
            ),
            None,
        )

    new_start = _hhmm_to_time(start_str)
    if existing_pfv is None:
        pending_new_keys.add(key)
        new_dict: dict[str, Any] = {
            "_op": "new",
            "patient_id": patient_id,
            "mode": mode,
            "weekday": weekday,
            "slot_index": slot_index,
            "start_time": new_start,
            "duration_min": duration_min,
            "course_template_id": course_template_id,
        }
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="new",
                changes=[
                    PatientExcelChange(field="start_time", old_value=None, new_value=start_str),
                    PatientExcelChange(
                        field="duration_min", old_value=None, new_value=duration_min
                    ),
                ],
            ),
            new_dict,
        )

    # update / noop
    changes: list[PatientExcelChange] = []
    update_dict: dict[str, Any] = {"_pfv_id": existing_pfv.id, "_op": "update"}
    if existing_pfv.start_time != new_start:
        changes.append(
            PatientExcelChange(
                field="start_time",
                old_value=_serializable(existing_pfv.start_time),
                new_value=start_str,
            )
        )
        update_dict["start_time"] = new_start
    if existing_pfv.duration_min != duration_min:
        changes.append(
            PatientExcelChange(
                field="duration_min",
                old_value=existing_pfv.duration_min,
                new_value=duration_min,
            )
        )
        update_dict["duration_min"] = duration_min
    if existing_pfv.course_template_id != course_template_id:
        changes.append(
            PatientExcelChange(
                field="course_template_id",
                old_value=_serializable(existing_pfv.course_template_id),
                new_value=_serializable(course_template_id),
            )
        )
        update_dict["course_template_id"] = course_template_id
    # time_type は patients.weekly_pattern 側の情報. PFV テーブル自体には保存先が無い.
    # 仕様書「time_type → patients.weekly_pattern にも反映」は WeeklyPattern の構造
    # 上、PFV 行 1 件から単純に書き換えると他曜日の設定を壊しうるため、本実装では
    # 表示用のみとし、import 時には patients.weekly_pattern を変更しない (TODO).
    if not changes:
        return (
            PfvExcelImportRow(
                row_number=row_number,
                patient_id=patient_id,
                patient_code=patient.code,
                weekday=weekday,
                slot_index=slot_index,
                operation="noop",
            ),
            None,
        )
    return (
        PfvExcelImportRow(
            row_number=row_number,
            patient_id=patient_id,
            patient_code=patient.code,
            weekday=weekday,
            slot_index=slot_index,
            operation="update",
            changes=changes,
        ),
        update_dict,
    )


# ---------------------------------------------------------------------------
# public entrypoints
# ---------------------------------------------------------------------------


async def parse_and_diff(
    db: AsyncSession,
    *,
    file_bytes: bytes,
) -> tuple[
    list[PatientExcelImportRow],
    list[PfvExcelImportRow],
    PatientExcelImportSummary,
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Excel をパースして差分行 + summary + apply 用 op list を返す.

    戻り値:
      - patient_rows: API レスポンス用の per-row 結果
      - pfv_rows: API レスポンス用の per-row 結果
      - summary: 集計
      - patient_ops: apply 用の DB op list (内部利用)
      - pfv_ops: apply 用の DB op list (内部利用)
    """
    wb = load_workbook(BytesIO(file_bytes), data_only=True)

    if SHEET_PATIENTS not in wb.sheetnames:
        raise ValueError(f"シート「{SHEET_PATIENTS}」が見つかりません")
    if SHEET_PFV not in wb.sheetnames:
        raise ValueError(f"シート「{SHEET_PFV}」が見つかりません")

    existing_patients = await _load_patients_by_id(db)
    existing_codes = await _load_patient_codes(db)
    offices_by_code = await _load_offices_by_code(db)
    course_templates = await _load_course_templates_by_label(db)
    existing_pfvs = await _load_pfvs_by_key(db, set(existing_patients.keys()))
    # 削除パスで patient_code → Patient を解決するため (patient_id 列が空でも
    # patient_code リンクで削除できるようにする).
    existing_patients_by_code: dict[str, Patient] = {
        p.code: p for p in existing_patients.values() if p.code
    }

    # ---- 患者シート ----
    ws_p = wb[SHEET_PATIENTS]
    patient_rows: list[PatientExcelImportRow] = []
    patient_ops: list[dict[str, Any]] = []
    already_seen_codes: set[str] = set()
    # row_number は 1-indexed; ヘッダーが 1 行目, データは 2 行目から.
    for r_idx, row in enumerate(ws_p.iter_rows(min_row=2, values_only=True), start=2):
        if _row_is_empty(row):
            continue
        diff_row, op = _parse_patient_row(
            r_idx,
            row,
            existing_patients=existing_patients,
            existing_patients_by_code=existing_patients_by_code,
            offices_by_code=offices_by_code,
            existing_codes=existing_codes,
            already_seen_codes=already_seen_codes,
        )
        patient_rows.append(diff_row)
        if op is not None:
            patient_ops.append(op)

    # PFV シートが patient_code 経由で新規患者をリンクできるよう、
    # patient_code → patient_id の lookup を構築する。新規患者は
    # _parse_patient_row が ``_new_patient_id`` (uuid.uuid4()) を発番済み。
    pending_new_patients: dict[UUID, _PendingNewPatient] = {}
    patient_code_to_id: dict[str, UUID] = {}
    # 既存患者 (alive) は code → id を載せる
    for existing in existing_patients.values():
        if existing.code:
            patient_code_to_id[existing.code] = existing.id
    # 同 import 内の新規患者は仮 UUID を載せる
    for op in patient_ops:
        if op.get("_op") != "new":
            continue
        new_id: UUID | None = op.get("_new_patient_id")
        new_code: str | None = op.get("code")
        if new_id is None or new_code is None:
            continue
        patient_code_to_id[new_code] = new_id
        pending_new_patients[new_id] = _PendingNewPatient(
            patient_id=new_id,
            code=new_code,
            primary_office_id=op.get("primary_office_id"),
        )

    # ---- PFV シート ----
    ws_f = wb[SHEET_PFV]
    pfv_rows: list[PfvExcelImportRow] = []
    pfv_ops: list[dict[str, Any]] = []
    pending_new_keys: set[tuple[UUID, str, int, int]] = set()
    for r_idx, row in enumerate(ws_f.iter_rows(min_row=2, values_only=True), start=2):
        if _row_is_empty(row):
            continue
        diff_row, op = _parse_pfv_row(
            r_idx,
            row,
            existing_patients=existing_patients,
            pending_new_patients=pending_new_patients,
            patient_code_to_id=patient_code_to_id,
            course_templates=course_templates,
            existing_pfvs=existing_pfvs,
            pending_new_keys=pending_new_keys,
        )
        pfv_rows.append(diff_row)
        if op is not None:
            pfv_ops.append(op)

    # ---- summary ----
    summary = PatientExcelImportSummary()
    for r in patient_rows:
        match r.operation:
            case "new":
                summary.patients_new += 1
            case "update":
                summary.patients_update += 1
            case "delete":
                summary.patients_delete += 1
            case "error":
                summary.patients_error += 1
            case "noop":
                summary.patients_noop += 1
    for r in pfv_rows:
        match r.operation:
            case "new":
                summary.pfv_new += 1
            case "update":
                summary.pfv_update += 1
            case "delete":
                summary.pfv_delete += 1
            case "error":
                summary.pfv_error += 1
            case "noop":
                summary.pfv_noop += 1

    return patient_rows, pfv_rows, summary, patient_ops, pfv_ops


async def apply_changes(
    db: AsyncSession,
    *,
    patient_ops: list[dict[str, Any]],
    pfv_ops: list[dict[str, Any]],
) -> None:
    """差分を DB に反映する (partial commit).

    ``patient_ops`` / ``pfv_ops`` は ``parse_and_diff`` の時点で error 行が除外
    された有効な op (new / update / delete) のみを含む. error 行は ``op=None``
    として既に skip されているので、ここでは触れない.
    SQLAlchemy 例外は呼び出し側にバブルアップする (rollback は呼び出し側で実施).
    """
    # 1) Patient: new → insert / update → 既存行を更新 / delete → soft delete
    patients_by_id: dict[UUID, Patient] = {}
    for op in patient_ops:
        op_type = op.get("_op")
        if op_type == "new":
            data = {k: v for k, v in op.items() if not k.startswith("_")}
            new_patient = Patient(**data)
            db.add(new_patient)
        elif op_type == "update":
            pid = op["_patient_id"]
            if pid not in patients_by_id:
                patients_by_id[pid] = await db.get(Patient, pid)
            patient = patients_by_id[pid]
            if patient is None:
                continue
            for k, v in op.items():
                if k.startswith("_"):
                    continue
                setattr(patient, k, v)
        elif op_type == "delete":
            pid = op["_patient_id"]
            if pid not in patients_by_id:
                patients_by_id[pid] = await db.get(Patient, pid)
            patient = patients_by_id[pid]
            if patient is not None:
                patient.deleted_at = func.now()
                # 関連 PFV を物理削除 (soft-delete 患者に orphan PFV が残ると
                # スケジュール生成側でゴースト枠として現れるのを防ぐ).
                pfvs_to_cleanup = (
                    await db.scalars(
                        select(PatientFixedVisit).where(PatientFixedVisit.patient_id == pid)
                    )
                ).all()
                for pfv in pfvs_to_cleanup:
                    await db.delete(pfv)
    # 中間 flush で patient row を確定 (PFV new で patient_id を再参照する可能性).
    await db.flush()

    # 2) PFV: new → insert / update → 既存行を更新 / delete → 物理削除
    pfvs_by_id: dict[UUID, PatientFixedVisit] = {}
    for op in pfv_ops:
        op_type = op.get("_op")
        if op_type == "new":
            data = {k: v for k, v in op.items() if not k.startswith("_")}
            db.add(PatientFixedVisit(**data))
        elif op_type == "update":
            pfv_id = op["_pfv_id"]
            if pfv_id not in pfvs_by_id:
                pfvs_by_id[pfv_id] = await db.get(PatientFixedVisit, pfv_id)
            pfv = pfvs_by_id[pfv_id]
            if pfv is None:
                continue
            for k, v in op.items():
                if k.startswith("_"):
                    continue
                setattr(pfv, k, v)
        elif op_type == "delete":
            pfv_id = op["_pfv_id"]
            obj = await db.get(PatientFixedVisit, pfv_id)
            if obj is not None:
                await db.delete(obj)
    await db.flush()
