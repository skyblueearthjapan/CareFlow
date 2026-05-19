"""完全置換インポート (バックアップ復元) — 患者マスタ + PFV.

通常 import (``importer.py``) との違い:

  * Excel に無い既存 alive 患者は **soft delete** (関連 PFV は物理削除).
  * Excel の **空セル** は NULL で上書き (現在値維持ではなく).
  * `<DELETE>` / `<CLEAR>` フラグは使わない (Excel に無い = 削除、空セル = NULL).
  * 全 PFV を一度物理削除してから Excel 通りに再投入する (= シート完全置換).
  * **atomic transaction**: error 1 件でも全 rollback (partial commit ではない).

エントリポイント:
  * ``parse_and_diff_replace_all`` — Excel bytes → 差分行 + summary + apply 用 op list.
  * ``apply_replace_all`` — DB に書き戻す (1 transaction, all-or-nothing).
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
    PatientExcelReplaceAllSummary,
    PfvExcelImportRow,
)

# Phase E-8: 「希望訪問パターン」シート 1 行を parse するヘルパーは importer.py
# 側 (`_parse_weekly_row`) に実装済. 完全置換でも同じセマンティクスを使うため
# import して再利用.
from app.services.patient_excel.importer import (
    _parse_weekly_row as _parse_weekly_row_replace,  # noqa: E402
)
from app.services.patient_excel.schema import (
    DEFAULT_TIME_TYPE,
    INSURANCE_VALUES,
    PATIENT_COL_INDEX,
    PFV_COL_INDEX,
    PFV_MODE_VALUES,
    SEX_RESTRICTION_VALUES,
    SEX_VALUES,
    SHEET_PATIENTS,
    SHEET_PFV,
    SHEET_WEEKLY,
    STATUS_VALUES,
    TIME_TYPE_VALUES,
    WEEKDAY_LABEL_TO_INT,
)

# ---------------------------------------------------------------------------
# cell value helpers (importer.py の同名関数を完全置換用に最小限再実装)
# 完全置換セマンティクスでは <DELETE> / <CLEAR> magic word を無視するため、
# importer の関数をそのまま流用するわけにいかない.
# ---------------------------------------------------------------------------


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _read_str(value: Any) -> str | None:
    if _is_blank(value):
        return None
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _read_int(value: Any) -> int | None:
    if _is_blank(value):
        return None
    if isinstance(value, bool):
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


def _read_bool(value: Any) -> bool | None:
    """TRUE/FALSE → bool. Phase E-7 (gap P0-1) requires_multiple_staff 用."""
    if _is_blank(value):
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().upper()
    if s in ("TRUE", "1", "YES", "Y"):
        return True
    if s in ("FALSE", "0", "NO", "N"):
        return False
    raise ValueError(f"bool として読めません: {value!r}")


def _read_hhmm(value: Any) -> str | None:
    if _is_blank(value):
        return None
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, datetime):
        return f"{value.hour:02d}:{value.minute:02d}"
    s = str(value).strip()
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


def _serializable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (int, float, bool, str)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Patient row parser (replace-all セマンティクス)
# ---------------------------------------------------------------------------


def _parse_patient_row_replace(
    row_number: int,
    row: tuple[Any, ...],
    *,
    existing_patients: dict[UUID, Patient],
    existing_patients_by_code: dict[str, Patient],
    deleted_patients_by_code: dict[str, Patient],
    offices_by_code: dict[str, Office],
    already_seen_codes: set[str],
) -> tuple[PatientExcelImportRow, dict[str, Any] | None]:
    """1 行をパースして差分行 + apply 用 op を返す.

    完全置換セマンティクス:
      - <DELETE> / <CLEAR> マーカーは無視 (空セル = NULL の方が強い).
      - 既存 patient_id にマッチしたら全列を Excel 値で上書き (blank → NULL).
      - patient_id 空 + patient_code が DB の alive 患者と一致 → 同じく上書き
        (UI 経由で id を消した行も identity を保つ).
      - patient_id 空 + patient_code が DB に無い (or soft-deleted) → 新規 or 復活.
    """
    cells: dict[str, Any] = {}
    for col_key, idx in PATIENT_COL_INDEX.items():
        cells[col_key] = row[idx] if idx < len(row) else None

    raw_id = cells["patient_id"]
    raw_code = cells["patient_code"]

    # patient_id のパース
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

    # patient_id 指定がある場合は alive 既存患者を resolve
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

    # patient_id 空 + patient_code 指定 → alive 既存 patient を code で resolve
    if existing_patient is None and patient_code is not None:
        existing_patient = existing_patients_by_code.get(patient_code)
        if existing_patient is not None:
            patient_id = existing_patient.id

    # ---- 列パース (空セルは None = NULL に上書き) ----
    parsed: dict[str, Any] = {}
    errors: list[str] = []

    # 文字列系
    for k in ("name", "kana", "address", "note"):
        parsed[k] = _read_str(cells[k])

    # patient_code (新規時は必須).
    parsed["code"] = patient_code

    # enum 系: 空はそのまま None、値があれば候補チェック
    def _parse_enum(key: str, allowed: tuple[str, ...]) -> None:
        v = cells[key]
        if _is_blank(v):
            parsed[key] = None
            return
        s = _read_str(v)
        if s not in allowed:
            errors.append(f"列「{key}」の値が候補外: {s!r} (許容: {','.join(allowed)})")
            return
        parsed[key] = s

    _parse_enum("sex", SEX_VALUES)
    _parse_enum("status", STATUS_VALUES)
    _parse_enum("insurance", INSURANCE_VALUES)
    _parse_enum("sex_restriction", SEX_RESTRICTION_VALUES)

    # lat / lng
    for k, lo, hi in (("lat", -90.0, 90.0), ("lng", -180.0, 180.0)):
        v = cells[k]
        if _is_blank(v):
            parsed[k] = None
            continue
        try:
            f = _read_float(v)
        except (ValueError, TypeError):
            errors.append(f"列「{k}」が数値ではありません: {v!r}")
            continue
        if f is None or f < lo or f > hi:
            errors.append(f"列「{k}」が範囲外 [{lo}, {hi}]: {f!r}")
            continue
        parsed[k] = f

    # 拠点コード → primary_office_id
    raw_office = cells["office_code"]
    if _is_blank(raw_office):
        parsed["primary_office_id"] = None
    else:
        oc = _read_str(raw_office)
        office = offices_by_code.get(oc) if oc else None
        if office is None:
            errors.append(f"拠点コードが DB に存在しません: {oc!r}")
        else:
            parsed["primary_office_id"] = office.id

    # Phase E-7 (gap P0-1): requires_multiple_staff. NOT NULL bool 列.
    # 完全置換セマンティクスでは空セル = False で確定上書き.
    raw_req_multi = cells["requires_multiple_staff"]
    if _is_blank(raw_req_multi):
        parsed["requires_multiple_staff"] = False
    else:
        try:
            parsed["requires_multiple_staff"] = _read_bool(raw_req_multi)
        except ValueError as exc:
            errors.append(f"列「requires_multiple_staff」が TRUE/FALSE ではありません: {exc}")

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

    # ---- code 重複チェック (同ファイル内) ----
    if patient_code is not None:
        if patient_code in already_seen_codes:
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=patient_id,
                    patient_code=patient_code,
                    operation="error",
                    error_message=(
                        f"patient_code が同ファイル内で重複しています: {patient_code!r}"
                    ),
                ),
                None,
            )
        already_seen_codes.add(patient_code)

    # ---- 操作判定 ----
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
        # Phase E-7 (gap P0-1): NOT NULL bool 列. 完全置換では空セル=False.
        "requires_multiple_staff": "requires_multiple_staff",
        "note": "note",
    }

    if existing_patient is not None:
        # update: 全列を Excel 値で上書き (blank → NULL).
        # patient_code は通常変更しない方針だが、明示的に書いてあって既存値と違えば update.

        # NOT NULL カラムが空セル (None) になっていないか確認
        required_update = ["name", "status"]
        null_required = [k for k in required_update if parsed.get(k) is None]
        if null_required:
            return (
                PatientExcelImportRow(
                    row_number=row_number,
                    patient_id=existing_patient.id,
                    patient_code=existing_patient.code,
                    operation="error",
                    error_message=f"必須項目が空セルです (NULL 不可): {', '.join(null_required)}",
                ),
                None,
            )

        changes: list[PatientExcelChange] = []
        update_dict: dict[str, Any] = {"_patient_id": existing_patient.id, "_op": "update"}

        if patient_code is not None and patient_code != existing_patient.code:
            changes.append(
                PatientExcelChange(
                    field="code",
                    old_value=existing_patient.code,
                    new_value=patient_code,
                )
            )
            update_dict["code"] = patient_code

        for parsed_key, orm_attr in field_map.items():
            new_val = parsed.get(parsed_key)
            old_val = getattr(existing_patient, orm_attr, None)
            # Numeric/float 比較対策
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

    # ---- 新規 or 復活 ----
    # 必須チェック
    missing: list[str] = []
    if not patient_code:
        missing.append("patient_code")
    for spec_key in ("name", "sex", "status", "address"):
        if parsed.get(spec_key) is None:
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

    # 復活 (soft-deleted 同じ code が DB に居る)
    resurrect_target = deleted_patients_by_code.get(patient_code) if patient_code else None
    if resurrect_target is not None:
        resurrect_id = resurrect_target.id
        old_deleted_at = resurrect_target.deleted_at
        updates: dict[str, Any] = {}
        changes_for_view: list[PatientExcelChange] = [
            PatientExcelChange(
                field="deleted_at",
                old_value=_serializable(old_deleted_at),
                new_value=None,
            )
        ]
        for parsed_key, orm_attr in field_map.items():
            new_val = parsed.get(parsed_key)
            old_val = getattr(resurrect_target, orm_attr, None)
            if old_val is not None and isinstance(new_val, float):
                try:
                    if float(old_val) == float(new_val):
                        continue
                except (TypeError, ValueError):
                    pass
            if old_val == new_val:
                continue
            changes_for_view.append(
                PatientExcelChange(
                    field=orm_attr,
                    old_value=_serializable(old_val),
                    new_value=_serializable(new_val),
                )
            )
            updates[orm_attr] = new_val
        return (
            PatientExcelImportRow(
                row_number=row_number,
                patient_id=resurrect_id,
                patient_code=patient_code,
                operation="update",
                changes=changes_for_view,
            ),
            {
                "_op": "resurrect",
                "_patient_id": resurrect_id,
                "_updates": updates,
            },
        )

    # 純粋新規. importer と同じく仮 UUID 発番 + special_week_active=[] を補完.
    new_patient_id = uuid.uuid4()
    new_dict: dict[str, Any] = {
        "_op": "new",
        "_new_patient_id": new_patient_id,
        "id": new_patient_id,
        "code": patient_code,
        "special_week_active": [],
    }
    for parsed_key, orm_attr in field_map.items():
        val = parsed.get(parsed_key)
        if val is None:
            continue  # default に任せる
        new_dict[orm_attr] = val

    changes_for_view = [
        PatientExcelChange(field=k, old_value=None, new_value=_serializable(new_dict[k]))
        for k in sorted(new_dict.keys())
        if not k.startswith("_") and k not in ("id",)
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


# ---------------------------------------------------------------------------
# PFV row parser (replace-all セマンティクス: 全件物理削除 → 再投入)
# ---------------------------------------------------------------------------


class _PendingNewPatient:
    """importer._PendingNewPatient と同等. PFV パース時の patient 参照に使う."""

    __slots__ = ("id", "code", "primary_office_id")

    def __init__(
        self, *, patient_id: UUID, code: str | None, primary_office_id: UUID | None
    ) -> None:
        self.id = patient_id
        self.code = code
        self.primary_office_id = primary_office_id


def _parse_pfv_row_replace(
    row_number: int,
    row: tuple[Any, ...],
    *,
    existing_patients: dict[UUID, Patient],
    pending_new_patients: dict[UUID, _PendingNewPatient],
    patient_code_to_id: dict[str, UUID],
    course_templates: dict[tuple[UUID, str], CourseTemplate],
    pending_new_keys: set[tuple[UUID, str, int, int]],
    offices_by_code: dict[str, Office] | None = None,
) -> tuple[PfvExcelImportRow, dict[str, Any] | None]:
    """1 PFV 行をパース. 完全置換では update / delete は無く、new のみ.

    既存 PFV は全件物理削除されてから Excel の各行を INSERT するため、
    op_type は "new" のみ (or "error").
    """
    cells: dict[str, Any] = {}
    for col_key, idx in PFV_COL_INDEX.items():
        cells[col_key] = row[idx] if idx < len(row) else None

    raw_id = cells["patient_id"]
    raw_code = cells["patient_code"]

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
        patient = pending  # type: ignore[assignment]

    # weekday
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

    # time_type
    # E-4: 空セルは default で吸収 (round-trip 運用 0 エラー). 詳細は importer.py の
    # 同 block コメント参照.
    raw_tt = cells["time_type"]
    if _is_blank(raw_tt):
        time_type: str | None = DEFAULT_TIME_TYPE
    else:
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

    # end_time / duration_min
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
        duration_min = 30
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

    # Phase E-5 (項目 ⑥B): sub_office_code → sub_office_id を先に解決.
    raw_sub_office = cells.get("sub_office_code")
    sub_office_id: UUID | None = None
    if not _is_blank(raw_sub_office) and offices_by_code is not None:
        oc = _read_str(raw_sub_office)
        if oc:
            office = offices_by_code.get(oc)
            if office is not None:
                sub_office_id = office.id

    # course_template_code
    # E-4: 「拠点に存在しない code」は error にせず course_template_id=None として
    # 取り込み、PFV 自体は保持する (round-trip / バックアップ運用優先).
    # importer.py の同 block コメント参照.
    # Phase E-5: sub_office_id が指定された場合は sub_office を優先して解決.
    raw_ct = cells["course_template_code"]
    course_template_id: UUID | None = None
    if not _is_blank(raw_ct):
        ct_label = _read_str(raw_ct)
        resolved = False
        if sub_office_id is not None:
            ct = course_templates.get((sub_office_id, ct_label or ""))
            if ct is not None:
                course_template_id = ct.id
                resolved = True
        if not resolved and patient.primary_office_id is not None:
            ct = course_templates.get((patient.primary_office_id, ct_label or ""))
            if ct is not None:
                course_template_id = ct.id

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
    pending_new_keys.add(key)

    new_start = _hhmm_to_time(start_str)
    new_dict: dict[str, Any] = {
        "_op": "new",
        "patient_id": patient_id,
        "mode": mode,
        "weekday": weekday,
        "slot_index": slot_index,
        "start_time": new_start,
        "duration_min": duration_min,
        "course_template_id": course_template_id,
        # Phase E-5 (項目 ⑥B): サブ拠点 ID.
        "sub_office_id": sub_office_id,
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
                PatientExcelChange(field="duration_min", old_value=None, new_value=duration_min),
            ],
        ),
        new_dict,
    )


# ---------------------------------------------------------------------------
# public entrypoints
# ---------------------------------------------------------------------------


async def parse_and_diff_replace_all(
    db: AsyncSession,
    *,
    file_bytes: bytes,
) -> tuple[
    list[PatientExcelImportRow],
    list[PfvExcelImportRow],
    PatientExcelReplaceAllSummary,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],  # patients_to_soft_delete (UI preview)
]:
    """Excel をパースして「完全置換」用の差分行 + summary + apply op list を返す.

    戻り値の最後の要素 ``patients_to_soft_delete_preview`` は
    Excel に無い alive 既存患者の {patient_id, patient_code, name} 一覧.
    """
    wb = load_workbook(BytesIO(file_bytes), data_only=True)

    if SHEET_PATIENTS not in wb.sheetnames:
        raise ValueError(f"シート「{SHEET_PATIENTS}」が見つかりません")
    if SHEET_PFV not in wb.sheetnames:
        raise ValueError(f"シート「{SHEET_PFV}」が見つかりません")

    # ---- DB lookup ----
    rows = (await db.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
    existing_patients: dict[UUID, Patient] = {p.id: p for p in rows}
    existing_patients_by_code: dict[str, Patient] = {p.code: p for p in rows if p.code}

    deleted_rows = (
        await db.scalars(
            select(Patient).where(Patient.deleted_at.is_not(None), Patient.code.is_not(None))
        )
    ).all()
    deleted_patients_by_code: dict[str, Patient] = {p.code: p for p in deleted_rows}

    offices_rows = (
        await db.scalars(
            select(Office).where(Office.deleted_at.is_(None), Office.code.is_not(None))
        )
    ).all()
    offices_by_code: dict[str, Office] = {str(o.code): o for o in offices_rows}

    ct_rows = (
        await db.scalars(select(CourseTemplate).where(CourseTemplate.deleted_at.is_(None)))
    ).all()
    course_templates: dict[tuple[UUID, str], CourseTemplate] = {
        (ct.office_id, ct.label): ct for ct in ct_rows
    }

    # ---- patient sheet ----
    ws_p = wb[SHEET_PATIENTS]
    patient_rows: list[PatientExcelImportRow] = []
    patient_ops: list[dict[str, Any]] = []
    already_seen_codes: set[str] = set()
    # Excel に出てきた既存 patient_id の集合 (Excel に居る = 削除しない).
    excel_patient_ids_seen: set[UUID] = set()

    for r_idx, row in enumerate(ws_p.iter_rows(min_row=2, values_only=True), start=2):
        if _row_is_empty(row):
            continue
        diff_row, op = _parse_patient_row_replace(
            r_idx,
            row,
            existing_patients=existing_patients,
            existing_patients_by_code=existing_patients_by_code,
            deleted_patients_by_code=deleted_patients_by_code,
            offices_by_code=offices_by_code,
            already_seen_codes=already_seen_codes,
        )
        patient_rows.append(diff_row)
        if op is not None:
            patient_ops.append(op)
        # alive 既存患者が Excel に居る → 削除リストから除外
        if diff_row.operation in ("update", "noop") and diff_row.patient_id is not None:
            excel_patient_ids_seen.add(diff_row.patient_id)

    # ---- Excel に無い alive 既存患者 = soft delete 対象 ----
    patients_to_soft_delete: list[Patient] = [
        p for pid, p in existing_patients.items() if pid not in excel_patient_ids_seen
    ]
    patients_to_soft_delete_preview: list[dict[str, Any]] = [
        {"patient_id": str(p.id), "patient_code": p.code, "name": p.name}
        for p in patients_to_soft_delete
    ]

    # ---- PFV ref maps ----
    pending_new_patients: dict[UUID, _PendingNewPatient] = {}
    patient_code_to_id: dict[str, UUID] = {}
    for existing in existing_patients.values():
        if existing.code and existing.id in excel_patient_ids_seen:
            patient_code_to_id[existing.code] = existing.id
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
    # resurrection 対象の patient も PFV から参照できるよう登録
    deleted_by_id = {p.id: p for p in deleted_patients_by_code.values()}
    for op in patient_ops:
        if op.get("_op") != "resurrect":
            continue
        resurrect_pid: UUID = op["_patient_id"]
        original = deleted_by_id.get(resurrect_pid)
        if original is None or not original.code:
            continue
        updated_office = op["_updates"].get("primary_office_id", original.primary_office_id)
        patient_code_to_id[original.code] = resurrect_pid
        pending_new_patients[resurrect_pid] = _PendingNewPatient(
            patient_id=resurrect_pid,
            code=original.code,
            primary_office_id=updated_office,
        )

    # ---- PFV sheet ----
    ws_f = wb[SHEET_PFV]
    pfv_rows: list[PfvExcelImportRow] = []
    pfv_ops: list[dict[str, Any]] = []
    pending_new_keys: set[tuple[UUID, str, int, int]] = set()
    for r_idx, row in enumerate(ws_f.iter_rows(min_row=2, values_only=True), start=2):
        if _row_is_empty(row):
            continue
        diff_row, op = _parse_pfv_row_replace(
            r_idx,
            row,
            existing_patients=existing_patients,
            pending_new_patients=pending_new_patients,
            patient_code_to_id=patient_code_to_id,
            course_templates=course_templates,
            pending_new_keys=pending_new_keys,
            # Phase E-5: sub_office_code 解決用.
            offices_by_code=offices_by_code,
        )
        pfv_rows.append(diff_row)
        if op is not None:
            pfv_ops.append(op)

    # ---- 希望訪問パターン シート (Phase E-8) ----
    # SHEET_WEEKLY が存在する場合のみ読み込み. 該当行がある patient の
    # weekly_pattern を完全上書き. SHEET_WEEKLY に行が無い patient の
    # weekly_pattern は維持 (= 既存値を破壊しない安全策).
    if SHEET_WEEKLY in wb.sheetnames:
        ws_w = wb[SHEET_WEEKLY]
        weekly_pattern_updates: dict[UUID, dict[str, Any] | None] = {}
        for r_idx, row in enumerate(ws_w.iter_rows(min_row=2, values_only=True), start=2):
            if _row_is_empty(row):
                continue
            result = _parse_weekly_row_replace(
                r_idx,
                row,
                existing_patients=existing_patients,
                existing_patients_by_code=existing_patients_by_code,
                patient_code_to_id=patient_code_to_id,
                pending_new_patients=pending_new_patients,
            )
            if result is None:
                continue
            pid, wp = result
            weekly_pattern_updates[pid] = wp if wp else None

        ops_by_pid: dict[UUID, dict[str, Any]] = {}
        for op in patient_ops:
            op_pid = op.get("_patient_id") or op.get("_new_patient_id")
            if op_pid is not None:
                ops_by_pid[op_pid] = op

        for pid, wp in weekly_pattern_updates.items():
            # 既存値と同じなら skip (= noop 維持、round-trip 安定化)
            existing_patient = existing_patients.get(pid)
            existing_wp = existing_patient.weekly_pattern if existing_patient else None
            if existing_wp == wp:
                continue

            existing_op = ops_by_pid.get(pid)
            if existing_op is None:
                # noop だった patient → 新規 update op を追加
                patient_ops.append(
                    {
                        "_op": "update",
                        "_patient_id": pid,
                        "weekly_pattern": wp,
                    }
                )
                # patient_rows の noop → update に上書き
                for pr in patient_rows:
                    if pr.patient_id == pid and pr.operation == "noop":
                        pr.operation = "update"
                        pr.changes.append(
                            {
                                "field": "weekly_pattern",
                                "old": _serializable(existing_wp),
                                "new": "(希望訪問パターン更新)",
                            }
                        )
                        break
            else:
                op_type = existing_op.get("_op")
                if op_type == "resurrect":
                    existing_op.setdefault("_updates", {})["weekly_pattern"] = wp
                elif op_type in ("new", "update"):
                    existing_op["weekly_pattern"] = wp

    # ---- 既存 PFV 件数 (全件再投入なので "replace count" として表示) ----
    # alive patients の PFV のみ count (soft-deleted patients の PFV は除外)
    pfv_count_row = (
        await db.execute(
            select(func.count(PatientFixedVisit.id)).where(
                PatientFixedVisit.patient_id.in_(
                    select(Patient.id).where(Patient.deleted_at.is_(None))
                )
            )
        )
    ).scalar_one()
    pfv_to_replace = int(pfv_count_row or 0)

    # ---- summary ----
    summary = PatientExcelReplaceAllSummary()
    for r in patient_rows:
        match r.operation:
            case "new":
                summary.patients_to_create += 1
            case "update":
                summary.patients_to_update += 1
            case "error":
                summary.patients_error += 1
    summary.patients_to_soft_delete = len(patients_to_soft_delete)
    for r in pfv_rows:
        match r.operation:
            case "new":
                summary.pfv_to_create += 1
            case "error":
                summary.pfv_error += 1
    summary.pfv_to_replace = pfv_to_replace

    return (
        patient_rows,
        pfv_rows,
        summary,
        patient_ops,
        pfv_ops,
        patients_to_soft_delete_preview,
    )


async def apply_replace_all(
    db: AsyncSession,
    *,
    patient_ops: list[dict[str, Any]],
    pfv_ops: list[dict[str, Any]],
    patient_ids_to_soft_delete: list[UUID],
) -> None:
    """完全置換を DB に反映する (1 transaction, atomic).

    順序:
      1. 削除対象 patient の関連 PFV を全件物理削除 (orphan 防止 / FK 整合)
      2. 削除対象 patient を soft delete (deleted_at=now)
      3. 残り (alive) 全 patient の PFV を全件物理削除 (replace 仕様)
      4. patient_ops を順に apply (new / update / resurrect)
      5. pfv_ops を順に INSERT

    例外は呼び出し側にバブルアップ (rollback は呼び出し側で実施).
    """
    # 1) 削除対象 patient の PFV を物理削除
    if patient_ids_to_soft_delete:
        pfvs_for_delete = (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id.in_(patient_ids_to_soft_delete)
                )
            )
        ).all()
        for pfv in pfvs_for_delete:
            await db.delete(pfv)
        await db.flush()
        # 2) 削除対象 patient を soft delete
        for pid in patient_ids_to_soft_delete:
            patient = await db.get(Patient, pid)
            if patient is not None:
                patient.deleted_at = func.now()
        await db.flush()

    # 3) 残り (alive) の patient の PFV を全件物理削除. Excel から再投入する.
    #    削除対象は既に上で消したので、ここでは「alive な patient のもの」のみ.
    alive_patient_ids = (
        await db.scalars(select(Patient.id).where(Patient.deleted_at.is_(None)))
    ).all()
    if alive_patient_ids:
        rest_pfvs = (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id.in_(list(alive_patient_ids))
                )
            )
        ).all()
        for pfv in rest_pfvs:
            await db.delete(pfv)
        await db.flush()

    # 4) Patient ops
    patients_by_id: dict[UUID, Patient] = {}
    for op in patient_ops:
        op_type = op.get("_op")
        if op_type == "new":
            data = {k: v for k, v in op.items() if not k.startswith("_")}
            new_patient = Patient(**data)
            db.add(new_patient)
        elif op_type == "resurrect":
            pid = op["_patient_id"]
            if pid not in patients_by_id:
                patients_by_id[pid] = await db.get(Patient, pid)
            patient = patients_by_id[pid]
            if patient is None:
                continue
            patient.deleted_at = None
            for k, v in op["_updates"].items():
                setattr(patient, k, v)
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
    await db.flush()

    # 5) PFV: 新規 INSERT のみ
    for op in pfv_ops:
        if op.get("_op") != "new":
            continue
        data = {k: v for k, v in op.items() if not k.startswith("_")}
        db.add(PatientFixedVisit(**data))
    await db.flush()
