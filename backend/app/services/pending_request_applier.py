"""PendingRequestApplier — W2-BE5.

設計仕様書 v0.9 §3.5 / §4.4 / API 契約 v0.1 §9.2 に対応する承認時の業務反映層。

責務 (`docs/plans/v2-allocation-redesign.md` §4.4 / API 契約 §9.2):
    request.request_type に応じた業務テーブル更新を実行する。
    対応する request_type は 9 種類:

    | request_type             | 主に触るテーブル                                |
    |--------------------------|--------------------------------------------------|
    | staff_off                | staff_weekly_overrides (新規 INSERT)             |
    | staff_event              | staff_events (新規 INSERT)                       |
    | staff_mentor             | staff.is_trainee (UPDATE) / staff_companion_assignments (UPSERT) ※W11-BE |
    | staff_create             | staff (新規 INSERT)                               |
    | patient_create           | patients (新規 INSERT)                            |
    | patient_cancel           | visits.status = "cancelled" (UPDATE)              |
    | patient_reschedule       | visits.start_time/end_time/staff (UPDATE)         |
    |                          | scope=permanent のとき patients.weekly_pattern も |
    | patient_special_week_on  | patients.special_week_active (追加)               |
    | patient_special_week_off | patients.special_week_active (削除)               |

冪等性 (§3.5.3 / 受入基準):
    - approve 済み (status="approved" かつ approved_at が NOT NULL) で再度 apply が
      呼ばれた場合は何もしない (二重反映を防止)。
    - applier 自体は status / approved_at の更新を **行わない** ことに注意。
      呼び出し側 (api/v1/pending_requests.py) が同一 SQLAlchemy セッション内で
      ステータス遷移と本 applier の `apply()` を同時に実行し、
      両者を 1 トランザクションで commit する。

トランザクション境界 (受入基準 5):
    - applier は session.commit() / session.rollback() を **呼ばない**。
    - 例外は呼び出し元 (HTTP layer) に伝播し、HTTP layer が rollback を司る。
    - 失敗時にステータス更新も含めて rollback されるよう、
      呼び出し側で try/except + rollback を入れる契約とする。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.pending_request import PendingRequest
from app.models.staff import Staff, StaffEvent, StaffWeeklyOverride
from app.models.staff_companion_assignment import StaffCompanionAssignment
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.schemas.v2.enums import RequestScope, RequestStatus, RequestType
from app.services.geocoding.client import geocode_address
from app.services.manager_course_sync import sync_manager_course_templates
from app.services.proposed_visits_pfv import (
    ProposedVisitsError,
    _resolve_course_template_id,
    apply_proposed_visits_as_normal_pfv,
)

logger = logging.getLogger(__name__)


class PendingRequestApplyError(Exception):
    """Applier から HTTP 層に投げる業務エラー。

    呼び出し側の HTTP 層は本例外を 4xx (主に 422 / 409) に翻訳する。
    """

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


# 各 handler が受け取る payload (申請時 payload か edited_payload のいずれか)
_Payload = dict[str, Any]


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coerce_time(value: Any) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            h, m = value.split(":")[:2]
            return time(int(h), int(m))
        except (ValueError, IndexError):
            return None
    return None


def _date_to_iso(d: date) -> tuple[int, int, int]:
    iso = d.isocalendar()
    return iso.year, iso.week, d.weekday()


# Phase G-84 A-2: 赤警告コード (満員 / 時刻重複). フロント (computePlacementWarnings)
# / 仕様書 §警告判定ルール と一致させる. ここに無いコードは黄警告扱い (= 強行可,
# override_reason 不要) とする.
_RED_WARNING_CODES: frozenset[str] = frozenset({"capacity_full", "time_overlap"})


def _extract_red_warnings(payload: _Payload) -> list[str]:
    """payload の ``warnings`` から赤警告 (red) の code リストを抽出する.

    warnings は ``[{level: "red"|"yellow", code: str, message: str}]`` 形式
    (Phase G-84 A-2). level=="red" もしくは code が ``_RED_WARNING_CODES`` の
    いずれかなら赤とみなす (level 改ざんに備えて code 側でも判定する).
    """
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        return []
    red: list[str] = []
    for w in warnings:
        if not isinstance(w, dict):
            continue
        level = w.get("level")
        code = w.get("code")
        code_str = str(code) if code is not None else ""
        if level == "red" or code_str in _RED_WARNING_CODES:
            red.append(code_str or "red")
    return red


def _validate_override_reason_for_red(
    payload: _Payload,
    *,
    extra_red_codes: list[str] | None = None,
) -> None:
    """赤警告があるのに override_reason が空なら 422 で拒否する (Phase G-84 A-2).

    クライアント申告の warnings (改ざん想定) に加え、サーバ側で再判定した赤警告
    (``extra_red_codes``) も合算して判定する. 赤が 1 件でもあり override_reason が
    空 / 空白なら ``PendingRequestApplyError(422)``.

    placement / warnings 自体は業務反映に使わない (監査 / 表示用メタ). 本検証だけが
    applier 内で warnings を参照する唯一の箇所.
    """
    red_codes = _extract_red_warnings(payload)
    if extra_red_codes:
        red_codes = red_codes + list(extra_red_codes)
    if not red_codes:
        return
    override_reason = payload.get("override_reason")
    if override_reason is None or not str(override_reason).strip():
        raise PendingRequestApplyError(
            "赤警告 (" + ", ".join(sorted(set(red_codes))) + ") を強行するには "
            "override_reason (理由) が必須です",
            http_status=422,
        )


class PendingRequestApplier:
    """承認時に request_type に応じた業務テーブル更新を実行する。

    冪等性 (§3.5.3): 同一申請の二重適用を防止する。
    トランザクション: 適用失敗時は status 変更を含めて呼び出し側で rollback。
    """

    async def apply(self, db: AsyncSession, request: PendingRequest) -> None:
        """request.request_type に応じて対応する handler を呼び出す。

        approve 済み (applied_at IS NOT NULL or status='approved' with approved_at)
        の request に対して再度呼ばれた場合は黙って no-op。

        W7-BE3 (Codex Must-fix #5): ``applied_at`` 列が NOT NULL の場合は確実に
        反映済みであるため二重反映を防止する。後方互換のため ``approved_at`` 経由の
        判定も残す。
        """
        # ----- 冪等性ガード -----
        if getattr(request, "applied_at", None) is not None:
            # 既に業務反映完了。何もしない (二重反映の防止)。
            return
        if request.status == RequestStatus.APPROVED.value and request.approved_at is not None:
            # 既に適用済み (W2-BE5 経路). 何もしない (二重反映の防止)。
            return

        handler = _HANDLERS.get(request.request_type)
        if handler is None:
            raise PendingRequestApplyError(
                f"Unsupported request_type: {request.request_type!r}",
                http_status=422,
            )

        # ``edited_payload`` (編集して承認) があればそちらを優先。
        payload: _Payload = (
            dict(request.edited_payload)
            if request.edited_payload is not None
            else dict(request.payload or {})
        )

        await handler(db, request, payload)


# ----------------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------------


async def _apply_staff_off(db: AsyncSession, request: PendingRequest, payload: _Payload) -> None:
    """``staff_off``: スタッフのその週だけの休み登録 (staff_weekly_overrides)."""
    staff_id = _coerce_uuid(payload.get("staff_id") or request.target_staff_id)
    target = _coerce_date(payload.get("date") or request.target_date)
    if staff_id is None or target is None:
        raise PendingRequestApplyError(
            "staff_off: staff_id and date are required",
            http_status=422,
        )

    iso_year, iso_week, weekday = _date_to_iso(target)
    override_type = str(payload.get("override_type") or payload.get("type") or "off")
    start_time = _coerce_time(payload.get("start_time"))
    end_time = _coerce_time(payload.get("end_time"))
    reason = payload.get("reason") or payload.get("note")

    row = StaffWeeklyOverride(
        staff_id=staff_id,
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        override_type=override_type,
        start_time=start_time,
        end_time=end_time,
        reason=reason,
    )
    db.add(row)
    await db.flush()


async def _apply_staff_event(db: AsyncSession, request: PendingRequest, payload: _Payload) -> None:
    """``staff_event``: イベント新規登録 (staff_events)."""
    staff_id = _coerce_uuid(payload.get("staff_id") or request.target_staff_id)
    if staff_id is None:
        raise PendingRequestApplyError(
            "staff_event: staff_id is required",
            http_status=422,
        )

    starts_at_raw = payload.get("starts_at")
    ends_at_raw = payload.get("ends_at")

    if starts_at_raw is None or ends_at_raw is None:
        # date + start_time / end_time 形式も許容 (staff_events.py と同じ規約)
        d = _coerce_date(payload.get("date") or request.target_date)
        s_t = _coerce_time(payload.get("start_time"))
        e_t = _coerce_time(payload.get("end_time"))
        if d is None or s_t is None or e_t is None:
            raise PendingRequestApplyError(
                "staff_event: starts_at/ends_at or (date + start_time + end_time) required",
                http_status=422,
            )
        starts_at = datetime.combine(d, s_t)
        ends_at = datetime.combine(d, e_t)
    else:
        starts_at = _parse_iso_dt(starts_at_raw)
        ends_at = _parse_iso_dt(ends_at_raw)

    if starts_at is None or ends_at is None or starts_at >= ends_at:
        raise PendingRequestApplyError(
            "staff_event: starts_at must be < ends_at",
            http_status=422,
        )

    row = StaffEvent(
        staff_id=staff_id,
        event_type=str(payload.get("event_type") or payload.get("type") or "event"),
        starts_at=starts_at,
        ends_at=ends_at,
        title=payload.get("title"),
        note=payload.get("note"),
    )
    db.add(row)
    await db.flush()


async def _apply_staff_mentor(db: AsyncSession, request: PendingRequest, payload: _Payload) -> None:
    """``staff_mentor``: W10-BE1 で mentor_id 廃止 → is_trainee (bool) を更新.

    W11-BE: assignments[] による companion-assignments 一括 upsert にも対応。

    payload modes (両方同時指定可):
    1. is_trainee: bool optional → staff.is_trainee 更新
    2. assignments: list[{weekday: int, part: 'am'|'pm'|'full', companion_staff_id: UUID}] optional
       → 当該 trainee_staff_id の既存全行を DELETE → assignments を INSERT (1 TX で完結、PUT 同等の冪等)

    旧 mentor_id フィールドは無視される。
    """
    staff_id = _coerce_uuid(payload.get("staff_id") or request.target_staff_id)
    if staff_id is None:
        raise PendingRequestApplyError(
            "staff_mentor: staff_id is required",
            http_status=422,
        )

    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise PendingRequestApplyError(
            f"staff_mentor: staff {staff_id} not found",
            http_status=404,
        )

    # mode 1: is_trainee 更新
    is_trainee_raw = payload.get("is_trainee")
    if is_trainee_raw is not None:
        staff.is_trainee = bool(is_trainee_raw)

    # mode 2: assignments 一括 upsert
    assignments = payload.get("assignments")
    if assignments is not None:
        if not isinstance(assignments, list):
            raise PendingRequestApplyError(
                "staff_mentor: assignments must be a list", http_status=422
            )

        validated: list[dict[str, Any]] = []
        seen_keys: set[tuple[int, str]] = set()

        for i, a in enumerate(assignments):
            if not isinstance(a, dict):
                raise PendingRequestApplyError(f"assignments[{i}] must be object", http_status=422)

            wd = a.get("weekday")
            part = a.get("part")
            cid = _coerce_uuid(a.get("companion_staff_id"))

            if not isinstance(wd, int) or wd < 0 or wd > 6:
                raise PendingRequestApplyError(f"assignments[{i}].weekday invalid", http_status=422)
            if part not in ("am", "pm", "full"):
                raise PendingRequestApplyError(f"assignments[{i}].part invalid", http_status=422)
            if cid is None:
                raise PendingRequestApplyError(
                    f"assignments[{i}].companion_staff_id required", http_status=422
                )
            if cid == staff_id:
                raise PendingRequestApplyError(
                    f"assignments[{i}]: self-companion not allowed", http_status=422
                )

            # (weekday, part) 重複チェック
            key: tuple[int, str] = (wd, part)
            if key in seen_keys:
                raise PendingRequestApplyError(
                    f"assignments[{i}]: duplicate (weekday, part)", http_status=422
                )

            # full / am-pm 排他チェック
            if part == "full" and any(w == wd for w, _ in seen_keys):
                raise PendingRequestApplyError(
                    f"assignments[{i}]: full conflicts with am/pm on same weekday",
                    http_status=422,
                )
            if part in ("am", "pm") and (wd, "full") in seen_keys:
                raise PendingRequestApplyError(
                    f"assignments[{i}]: am/pm conflicts with existing full on same weekday",
                    http_status=422,
                )

            seen_keys.add(key)

            # companion staff の存在 + role/status 検証
            companion = await db.scalar(
                select(Staff).where(Staff.id == cid, Staff.deleted_at.is_(None))
            )
            if companion is None:
                raise PendingRequestApplyError(
                    f"assignments[{i}]: companion staff not found", http_status=422
                )
            if companion.role not in ("manager", "staff"):
                raise PendingRequestApplyError(
                    f"assignments[{i}]: companion role must be manager/staff", http_status=422
                )
            if companion.status != "active":
                raise PendingRequestApplyError(
                    f"assignments[{i}]: companion not active", http_status=422
                )

            validated.append(
                {
                    "trainee_staff_id": staff_id,
                    "weekday": wd,
                    "part": part,
                    "companion_staff_id": cid,
                }
            )

        # trainee.is_trainee=True 必須: まだ true でなければ強制 true 化
        if not staff.is_trainee:
            staff.is_trainee = True

        # 既存 staff_companion_assignments を全削除 → INSERT (1 TX)
        await db.execute(
            delete(StaffCompanionAssignment).where(
                StaffCompanionAssignment.trainee_staff_id == staff_id
            )
        )
        for a in validated:
            db.add(StaffCompanionAssignment(**a))

    await db.flush()


async def _apply_staff_create(db: AsyncSession, request: PendingRequest, payload: _Payload) -> None:
    """``staff_create``: 新規スタッフを INSERT."""
    name = payload.get("name")
    if not name:
        raise PendingRequestApplyError(
            "staff_create: name is required",
            http_status=422,
        )
    primary_office_id = _coerce_uuid(payload.get("primary_office_id"))
    # W10-BE1: mentor_id 廃止 → is_trainee (bool) に置き換え
    is_trainee_raw = payload.get("is_trainee")
    is_trainee = bool(is_trainee_raw) if is_trainee_raw is not None else False

    row = Staff(
        code=payload.get("code"),
        name=str(name),
        kana=payload.get("kana"),
        sex=payload.get("sex"),
        status=str(payload.get("status") or "active"),
        role=str(payload.get("role") or "staff"),
        primary_office_id=primary_office_id,
        is_trainee=is_trainee,
        note=payload.get("note"),
    )
    db.add(row)
    await db.flush()

    # W16-A-4: manager 新規追加 → 当該拠点の M 系 course_templates を自動同期
    if row.role == "manager" and row.primary_office_id is not None:
        await sync_manager_course_templates(db, office_id=row.primary_office_id)


async def _apply_patient_create(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_create``: 新規患者を INSERT.

    StageB-backend: payload に ``proposed_visits`` (任意) があれば、患者 INSERT 後に
    同一 TX でその患者の normal PFV (slot_index=0) を設定する
    (= 患者作成 + スケジュール確定を 1 承認で). ``proposed_visits`` 無し / 空の場合は
    従来どおり患者作成のみ (現行不変). 失敗時は患者 INSERT もまとめて呼び出し元で
    rollback される。
    """
    code = payload.get("code")
    name = payload.get("name")
    if not code or not name:
        raise PendingRequestApplyError(
            "patient_create: code and name are required",
            http_status=422,
        )

    # Phase G-84 A-2/A-3: placement/warnings/override_reason は業務反映に使わない
    # (監査/表示用メタ) が、赤警告があるのに override_reason 空なら 422 で拒否する.
    _validate_override_reason_for_red(payload)

    primary_office_id = _coerce_uuid(payload.get("primary_office_id"))

    # 座標: payload に lat/lng が明示されていればそれを優先 (再 geocode しない)。
    # 未指定 (None) かつ address があれば、患者 INSERT 前に住所を best-effort で
    # ジオコードして lat/lng を補完する (モバイル経路では lat/lng=null で申請されるため)。
    # ジオコード失敗 / 例外時は lat/lng を None のままにして患者作成は続行する
    # (同一 TX・例外は握り潰してログのみ)。
    lat = payload.get("lat")
    lng = payload.get("lng")
    address = payload.get("address")
    if lat is None and lng is None and address and str(address).strip():
        try:
            geo = await geocode_address(str(address))
            if geo is not None:
                lat = geo.lat
                lng = geo.lng
        except Exception as exc:  # noqa: BLE001 - best-effort: 患者作成はブロックしない
            logger.warning("patient_create geocode failed (best-effort, skipped): %s", exc)

    row = Patient(
        code=str(code),
        name=str(name),
        kana=payload.get("kana"),
        sex=payload.get("sex"),
        status=str(payload.get("status") or "active"),
        insurance=payload.get("insurance"),
        address=address,
        lat=lat,
        lng=lng,
        primary_office_id=primary_office_id,
        sex_restriction=payload.get("sex_restriction"),
        requires_multiple_staff=bool(payload.get("requires_multiple_staff", False)),
        weekly_pattern=payload.get("weekly_pattern"),
        special_weekly_pattern=payload.get("special_weekly_pattern"),
        special_week_active=payload.get("special_week_active") or [],
        note=payload.get("note"),
    )
    db.add(row)
    await db.flush()

    # StageB-backend: proposed_visits があれば normal PFV を同一 TX で設定する。
    proposed_visits = payload.get("proposed_visits")
    if proposed_visits:
        try:
            await apply_proposed_visits_as_normal_pfv(
                db, patient=row, proposed_visits=proposed_visits
            )
        except ProposedVisitsError as exc:
            # 共通サービスの検証エラーを applier の業務エラーに翻訳する
            # (患者 INSERT も呼び出し元で rollback される)。
            raise PendingRequestApplyError(
                f"patient_create: {exc}", http_status=exc.http_status
            ) from exc


async def _apply_patient_visit_add(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_visit_add`` (Phase G-84 A-4): 既存患者の normal PFV に 1 枠を追加.

    payload:
        ``{patient_id, patient_name?, proposed_visits:[1枠], placement?,
           warnings?, override_reason?}``

    ★設計 (code-reviewer CRITICAL 是正): visit_add は「対象 weekday に 1 枠だけ追加
    /置換」する操作なので、全パターン PUT 上書き
    (``apply_proposed_visits_as_normal_pfv``) は使わない. ``apply_…`` は当該患者の
    normal PFV を **全削除** (slot_index=0 / 1 両方) → slot_index=0 のみ再 INSERT する
    ため、``requires_multiple_staff=True`` 患者の同曜日 2 コース目 (slot_index=1)
    normal PFV を恒久消失させてしまう. そのため本ハンドラは **対象 weekday の
    slot_index=0 行だけ** を upsert する:
        1. course を ``_resolve_course_template_id`` で解決
           (course_template_id 優先, 無ければ course_code トークン).
        2. 対象 ``(patient_id, mode='normal', weekday=対象, slot_index=0)`` 行が
           あれば DELETE し、新しい 1 枠を INSERT.
           **slot_index=1 / 他 weekday の行には一切触れない** (保全).
        3. weekday/start_time/duration_min のバリデーションは
           ``apply_proposed_visits_as_normal_pfv`` と同基準 (0-6, HH:MM, 1..480).

    赤警告再判定の限界 (MEDIUM #1): サーバ側の time_overlap 再判定は「対象 weekday の
    slot_index=0 行が既に存在する＝置換が起きる」という近似でしか赤を立てない.
    他コース (slot_index=1) や他患者との **実時刻** 重複は検証していない. 正確な
    時刻重複検証は別レイヤ (フロント computePlacementWarnings / スケジューラ) に委ねる.

    placement / warnings / override_reason は業務反映に使わない (監査 / 表示用メタ).
    赤警告検証のみ ``_validate_override_reason_for_red`` で参照する.
    """
    patient_id = _coerce_uuid(payload.get("patient_id") or request.target_patient_id)
    if patient_id is None:
        raise PendingRequestApplyError(
            "patient_visit_add: patient_id is required",
            http_status=422,
        )

    proposed_visits = payload.get("proposed_visits")
    if not proposed_visits or not isinstance(proposed_visits, list):
        raise PendingRequestApplyError(
            "patient_visit_add: proposed_visits (1 件) is required",
            http_status=422,
        )
    if len(proposed_visits) != 1:
        raise PendingRequestApplyError(
            "patient_visit_add: proposed_visits must contain exactly 1 visit",
            http_status=422,
        )

    new_visit = proposed_visits[0]
    if not isinstance(new_visit, dict):
        raise PendingRequestApplyError(
            "patient_visit_add: proposed_visits[0] must be an object",
            http_status=422,
        )

    # MEDIUM #2 (防御的キー抽出): payload 由来 dict をそのまま使わず、必要キーだけ
    # 抽出する. 余計なキー (id / placement 由来など) を業務反映に持ち込まない.
    new_weekday = new_visit.get("weekday")
    if (
        not isinstance(new_weekday, int)
        or isinstance(new_weekday, bool)
        or not (0 <= new_weekday <= 6)
    ):
        raise PendingRequestApplyError(
            "patient_visit_add: proposed_visits[0].weekday must be int 0-6",
            http_status=422,
        )

    new_start = _coerce_time(new_visit.get("start_time"))
    if new_start is None:
        raise PendingRequestApplyError(
            "patient_visit_add: proposed_visits[0].start_time must be 'HH:MM'",
            http_status=422,
        )

    duration_raw = new_visit.get("duration_min", 30)
    try:
        new_duration = int(duration_raw)
    except (TypeError, ValueError):
        raise PendingRequestApplyError(
            "patient_visit_add: proposed_visits[0].duration_min must be int",
            http_status=422,
        ) from None
    if new_duration < 1 or new_duration > 480:
        raise PendingRequestApplyError(
            "patient_visit_add: proposed_visits[0].duration_min must be 1..480",
            http_status=422,
        )

    # 患者存在チェック.
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise PendingRequestApplyError(
            f"patient_visit_add: patient {patient_id} not found",
            http_status=404,
        )

    # 対象 weekday の slot_index=0 行が既存か判定 (置換 = time_overlap 相当の赤).
    # 注意: slot_index=1 / 他 weekday の行は読み出さない (保全対象なので無関係).
    existing_slot0 = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.weekday == new_weekday,
            PatientFixedVisit.slot_index == 0,
        )
    )

    # サーバ側 赤警告再判定 (A-2 / 限界は docstring 参照): 対象 weekday の slot0 が
    # 既存 = 置換が起きる → time_overlap (赤) とみなす.
    extra_red: list[str] = []
    if existing_slot0 is not None:
        extra_red.append("time_overlap")

    # 赤警告があるのに override_reason 空なら 422 (クライアント申告 + サーバ再判定を合算).
    _validate_override_reason_for_red(payload, extra_red_codes=extra_red)

    # course 解決 (course_template_id 優先, 無ければ course_code トークン).
    try:
        course_template_id = await _resolve_course_template_id(
            db,
            course_template_id=_coerce_uuid(new_visit.get("course_template_id")),
            course_code=new_visit.get("course_code"),
        )
    except ProposedVisitsError as exc:
        raise PendingRequestApplyError(
            f"patient_visit_add: {exc}", http_status=exc.http_status
        ) from exc

    # ターゲット upsert: 対象 weekday の slot_index=0 行のみ置換.
    # slot_index=1 / 他 weekday には一切触れない (multi-staff 患者の 2 コース目を保全).
    if existing_slot0 is not None:
        await db.delete(existing_slot0)
        # UNIQUE (patient_id, mode, weekday, slot_index) 制約に同 TX 内で再 INSERT
        # するため、DELETE を先に DB へ反映させてから INSERT する.
        await db.flush()

    db.add(
        PatientFixedVisit(
            patient_id=patient_id,
            mode="normal",
            weekday=new_weekday,
            start_time=new_start,
            duration_min=new_duration,
            course_template_id=course_template_id,
            slot_index=0,
        )
    )
    await db.flush()


async def _apply_patient_cancel(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_cancel``: visits の該当行を ``status='cancelled'`` に更新."""
    visit_id = _coerce_uuid(payload.get("visit_id"))
    target_visit: Visit | None = None
    if visit_id is not None:
        target_visit = await db.scalar(
            select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        )
    else:
        # patient_id + date から該当 visit を 1 件特定する
        patient_id = _coerce_uuid(payload.get("patient_id") or request.target_patient_id)
        v_date = _coerce_date(payload.get("date") or request.target_date)
        if patient_id is None or v_date is None:
            raise PendingRequestApplyError(
                "patient_cancel: visit_id or (patient_id + date) is required",
                http_status=422,
            )
        target_visit = await db.scalar(
            select(Visit)
            .where(
                Visit.patient_id == patient_id,
                Visit.visit_date == v_date,
                Visit.deleted_at.is_(None),
            )
            .order_by(Visit.start_time)
        )

    if target_visit is None:
        raise PendingRequestApplyError(
            "patient_cancel: visit not found",
            http_status=404,
        )

    target_visit.status = VISIT_STATUS_CANCELLED  # type: ignore[assignment]
    await db.flush()


async def _apply_patient_reschedule(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_reschedule``: visits の時刻 / 担当を更新.

    scope=``permanent`` のとき patients.weekly_pattern も更新する (§3.5.6).
    """
    scope = request.scope or payload.get("scope")
    if scope is None:
        raise PendingRequestApplyError(
            "patient_reschedule: scope is required (one_time / permanent)",
            http_status=422,
        )
    if scope not in {RequestScope.ONE_TIME.value, RequestScope.PERMANENT.value}:
        raise PendingRequestApplyError(
            f"patient_reschedule: invalid scope {scope!r}",
            http_status=422,
        )

    visit_id = _coerce_uuid(payload.get("visit_id"))
    patient_id = _coerce_uuid(payload.get("patient_id") or request.target_patient_id)
    v_date = _coerce_date(payload.get("date") or request.target_date)
    new_start = _coerce_time(payload.get("new_start_time") or payload.get("start_time"))
    new_end = _coerce_time(payload.get("new_end_time") or payload.get("end_time"))
    new_primary_staff = _coerce_uuid(payload.get("new_primary_staff_id"))

    target_visit: Visit | None = None
    if visit_id is not None:
        target_visit = await db.scalar(
            select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        )
    elif patient_id is not None and v_date is not None:
        target_visit = await db.scalar(
            select(Visit)
            .where(
                Visit.patient_id == patient_id,
                Visit.visit_date == v_date,
                Visit.deleted_at.is_(None),
            )
            .order_by(Visit.start_time)
        )

    if target_visit is None:
        raise PendingRequestApplyError(
            "patient_reschedule: visit not found",
            http_status=404,
        )

    if new_start is not None:
        target_visit.start_time = new_start
    if new_end is not None:
        target_visit.end_time = new_end
    if new_primary_staff is not None:
        target_visit.primary_staff_id = new_primary_staff
    if target_visit.start_time >= target_visit.end_time:
        raise PendingRequestApplyError(
            "patient_reschedule: start_time must be < end_time",
            http_status=422,
        )

    if scope == RequestScope.PERMANENT.value:
        # 当該以降の固定パターンも更新する (§3.5.6)
        new_pattern = payload.get("new_weekly_pattern") or payload.get("weekly_pattern")
        if new_pattern is not None:
            patient = await db.scalar(
                select(Patient).where(
                    Patient.id == target_visit.patient_id,
                    Patient.deleted_at.is_(None),
                )
            )
            if patient is None:
                raise PendingRequestApplyError(
                    "patient_reschedule: patient not found",
                    http_status=404,
                )
            patient.weekly_pattern = new_pattern

    await db.flush()


async def _apply_patient_special_week_on(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_special_week_on``: ``patients.special_week_active`` に追加."""
    patient_id = _coerce_uuid(payload.get("patient_id") or request.target_patient_id)
    iso_year = payload.get("iso_year")
    iso_week = payload.get("iso_week")
    if patient_id is None or iso_year is None or iso_week is None:
        raise PendingRequestApplyError(
            "patient_special_week_on: patient_id, iso_year, iso_week are required",
            http_status=422,
        )

    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise PendingRequestApplyError(
            "patient_special_week_on: patient not found",
            http_status=404,
        )

    current: list[dict[str, int]] = list(patient.special_week_active or [])
    ref = {"iso_year": int(iso_year), "iso_week": int(iso_week)}
    if not any(
        item.get("iso_year") == ref["iso_year"] and item.get("iso_week") == ref["iso_week"]
        for item in current
    ):
        current.append(ref)
    patient.special_week_active = current
    # ``special_weekly_pattern`` を同時に渡された場合は反映する
    if payload.get("special_weekly_pattern") is not None:
        patient.special_weekly_pattern = payload["special_weekly_pattern"]
    await db.flush()


async def _apply_patient_special_week_off(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_special_week_off``: ``patients.special_week_active`` から削除."""
    patient_id = _coerce_uuid(payload.get("patient_id") or request.target_patient_id)
    iso_year = payload.get("iso_year")
    iso_week = payload.get("iso_week")
    if patient_id is None or iso_year is None or iso_week is None:
        raise PendingRequestApplyError(
            "patient_special_week_off: patient_id, iso_year, iso_week are required",
            http_status=422,
        )

    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise PendingRequestApplyError(
            "patient_special_week_off: patient not found",
            http_status=404,
        )

    current: list[dict[str, int]] = list(patient.special_week_active or [])
    iy = int(iso_year)
    iw = int(iso_week)
    new_list = [
        item for item in current if not (item.get("iso_year") == iy and item.get("iso_week") == iw)
    ]
    patient.special_week_active = new_list
    await db.flush()


async def _apply_staff_status_update(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """スタッフの status 列を更新 (active / on_leave / retired)."""
    staff_id = _coerce_uuid(payload.get("staff_id") or request.target_staff_id)
    if staff_id is None:
        raise PendingRequestApplyError(
            "staff_status_update: staff_id is required",
            http_status=422,
        )
    new_status = payload.get("status")
    if new_status not in ("active", "on_leave", "retired"):
        raise PendingRequestApplyError(
            f"staff_status_update: status must be one of active/on_leave/retired, got {new_status!r}",
            http_status=422,
        )
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise PendingRequestApplyError(
            f"staff_status_update: staff {staff_id} not found",
            http_status=404,
        )
    prev_status = staff.status
    staff.status = new_status
    await db.flush()

    # W16-A-4: manager の active <-> 非 active 切替で M course_templates を sync
    if staff.role == "manager" and staff.primary_office_id is not None:
        if prev_status != new_status and (prev_status == "active" or new_status == "active"):
            await sync_manager_course_templates(db, office_id=staff.primary_office_id)


async def _apply_patient_status_update(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """患者の status 列を更新 (active / suspended / admitted / pending / cancelled)."""
    patient_id = _coerce_uuid(payload.get("patient_id") or request.target_patient_id)
    if patient_id is None:
        raise PendingRequestApplyError(
            "patient_status_update: patient_id is required",
            http_status=422,
        )
    new_status = payload.get("status")
    if new_status not in ("active", "suspended", "admitted", "pending", "cancelled"):
        raise PendingRequestApplyError(
            f"patient_status_update: status must be one of active/suspended/admitted/pending/cancelled, got {new_status!r}",
            http_status=422,
        )
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise PendingRequestApplyError(
            f"patient_status_update: patient {patient_id} not found",
            http_status=404,
        )
    patient.status = new_status
    await db.flush()


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _parse_iso_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # FastAPI でデシリアライズ済みの可能性もあるが、JSON 経由は str
        try:
            # naive-datetime に揃える (staff_events.py と同方針)
            v = value.replace("Z", "+00:00") if value.endswith("Z") else value
            dt = datetime.fromisoformat(v)
            return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
        except ValueError:
            return None
    return None


_HandlerFn = Callable[[AsyncSession, PendingRequest, _Payload], Awaitable[None]]

_HANDLERS: dict[str, _HandlerFn] = {
    RequestType.STAFF_OFF.value: _apply_staff_off,
    RequestType.STAFF_EVENT.value: _apply_staff_event,
    RequestType.STAFF_MENTOR.value: _apply_staff_mentor,
    RequestType.STAFF_CREATE.value: _apply_staff_create,
    RequestType.PATIENT_CREATE.value: _apply_patient_create,
    RequestType.PATIENT_CANCEL.value: _apply_patient_cancel,
    RequestType.PATIENT_RESCHEDULE.value: _apply_patient_reschedule,
    RequestType.PATIENT_SPECIAL_WEEK_ON.value: _apply_patient_special_week_on,
    RequestType.PATIENT_SPECIAL_WEEK_OFF.value: _apply_patient_special_week_off,
    RequestType.STAFF_STATUS_UPDATE.value: _apply_staff_status_update,
    RequestType.PATIENT_STATUS_UPDATE.value: _apply_patient_status_update,
    # Phase G-84 A-4: 既存患者の空き枠直接配置 (normal PFV へ 1 枠マージ追加).
    RequestType.PATIENT_VISIT_ADD.value: _apply_patient_visit_add,
}


__all__ = [
    "PendingRequestApplier",
    "PendingRequestApplyError",
]
