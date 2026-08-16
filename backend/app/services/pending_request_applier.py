"""PendingRequestApplier — W2-BE5.

設計仕様書 v0.9 §3.5 / §4.4 / API 契約 v0.1 §9.2 に対応する承認時の業務反映層。

責務 (`docs/plans/v2-allocation-redesign.md` §4.4 / API 契約 §9.2):
    request.request_type に応じた業務テーブル更新を実行する。
    対応する request_type は 9 種類:

    | request_type             | 主に触るテーブル                                |
    |--------------------------|--------------------------------------------------|
    | staff_off                | staff_weekly_overrides (新規 INSERT)             |
    | staff_event              | staff_events (新規 INSERT)                       |
    | staff_mentor             | 廃止 (Phase 2 §3)。適用時は graceful reject (410) を返す |
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
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.pending_request import PendingRequest
from app.models.staff import Staff, StaffEvent, StaffWeeklyOverride
from app.models.visit import VISIT_STATUS_CANCELLED, VISIT_STATUS_PLANNED, Visit
from app.schemas.v2.enums import RequestScope, RequestStatus, RequestType
from app.services.geocoding.client import geocode_address
from app.services.manager_course_sync import sync_manager_course_templates
from app.services.patient_code import generate_next_patient_code
from app.services.proposed_visits_pfv import (
    ProposedVisitsError,
    _resolve_course_template_id,
    apply_proposed_visits_as_normal_pfv,
)
from app.services.scheduling.config import load_scheduling_config

logger = logging.getLogger(__name__)

# Phase G-86: 自動採番した patient code が UNIQUE 衝突したときの再採番上限。
_PATIENT_CODE_RETRY_MAX = 5


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
    """``staff_mentor``: **廃止済み** (Phase 2 §3・新人同行 v1.1).

    旧「曜日×午前/午後×同行者」機構 (staff_companion_assignments・向きが逆) を撤去した。
    新人フラグは PATCH /staff で直接更新し、同行はスケジュール画面の同行モードで
    設定する (accompaniments が唯一の正典)。

    既存の未処理 ``staff_mentor`` 申請が残っていても一覧表示は壊れない
    (enum / FE ラベルは存続) が、承認 (適用) しようとした場合はここで
    graceful reject する。管理者は申請を却下し、必要な設定を新経路で行う。
    """
    _ = db, payload
    raise PendingRequestApplyError(
        "この申請タイプ（新人同行）は廃止されました。"
        "新人フラグはスタッフ編集、同行はスケジュール画面から設定してください。",
        http_status=410,
    )


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

    Phase G-86: ``code`` が空 / None なら ``generate_next_patient_code`` で自動採番する
    (``name`` は従来どおり必須). **自動採番した場合のみ** INSERT の UNIQUE 衝突を
    savepoint (``begin_nested``) 単位で巻き戻して採番からやり直す (最大数回). 手入力
    code の衝突は従来どおり呼び出し元へ伝播する (= ユーザー入力エラー).
    """
    raw_code = payload.get("code")
    name = payload.get("name")
    if not name:
        raise PendingRequestApplyError(
            "patient_create: name is required",
            http_status=422,
        )
    # 非空文字列なら手入力 code として採用、それ以外 (None / 空文字 / 空白) は自動採番。
    manual_code = raw_code.strip() if isinstance(raw_code, str) and raw_code.strip() else None
    auto_code = manual_code is None

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

    def _build_patient(code_value: str) -> Patient:
        return Patient(
            code=code_value,
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

    if not auto_code:
        # 手入力 code: 従来どおり flush し、UNIQUE 衝突は呼び出し元へ伝播 (409/422)。
        row = _build_patient(manual_code or "")
        db.add(row)
        await db.flush()
    else:
        # 自動採番: UNIQUE 衝突時は savepoint を巻き戻して採番からやり直す。
        # savepoint 単位の rollback なので外側の TX (proposed_visits 等) は壊さない。
        row = None  # type: ignore[assignment]
        for _ in range(_PATIENT_CODE_RETRY_MAX):
            next_code = await generate_next_patient_code(db)
            try:
                async with db.begin_nested():  # savepoint — race-safe
                    candidate = _build_patient(next_code)
                    db.add(candidate)
                    await db.flush()
            except IntegrityError:
                # 並行採番で同一 code が先に確定。再採番してリトライ。
                continue
            row = candidate
            break
        if row is None:
            raise PendingRequestApplyError(
                "patient_create: failed to allocate a unique patient code",
                http_status=409,
            )

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
    """``patient_visit_add``: 既存患者の空き枠直接配置 (scope で恒常 / 単発を分岐).

    Phase G-85: ``scope`` (``request.scope`` または payload, 既定 ``permanent``) で
    2 経路に分岐する:

    - ``permanent`` / 未指定 → ``_apply_visit_add_permanent`` (Phase G-84 の現行ロジック.
      normal PFV へ 1 枠マージ追加. 毎週固定. **完全不変・後方互換**).
    - ``one_time`` → ``_apply_visit_add_onetime`` (その週だけの単発. Visit を 1 件 INSERT.
      PFV は触らない).

    scope は ``RequestScope`` enum (``one_time`` / ``permanent``) のいずれか. 不正値は 422.
    """
    scope = request.scope or payload.get("scope") or RequestScope.PERMANENT.value
    if scope not in {RequestScope.ONE_TIME.value, RequestScope.PERMANENT.value}:
        raise PendingRequestApplyError(
            f"patient_visit_add: invalid scope {scope!r}",
            http_status=422,
        )

    if scope == RequestScope.ONE_TIME.value:
        await _apply_visit_add_onetime(db, request, payload)
        return

    await _apply_visit_add_permanent(db, request, payload)


async def _apply_visit_add_permanent(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_visit_add`` (scope=permanent, Phase G-84 A-4): 既存患者の normal PFV に 1 枠を追加.

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


async def _apply_visit_add_onetime(
    db: AsyncSession, request: PendingRequest, payload: _Payload
) -> None:
    """``patient_visit_add`` (scope=one_time, Phase G-85): その週だけの単発配置.

    PFV (毎週固定パターン) は **触らず**, 対象週の実 ``Visit`` を 1 件だけ INSERT する.
    現場ボード (``board_service.load_board_buckets``) は実 Visit を週次 Course と
    INNER JOIN して描画するため, 以下の条件を満たすことでその週のボードに出る:
        - ``deleted_at IS NULL`` / ``status == 'planned'`` / ``type == 'regular'`` (normal 表示)
        - ``visit_date`` が対象 ISO 週内
        - ``course_id`` が対象週の実 Course (deleted_at NULL) を指す

    ★``source = 'manual'`` が必須: Layer-1 再展開 (``layer1_expander``) は
    ``source = 'auto'`` の planned visit を purge して再生成するが, manual は保護される.
    これにより単発が再算出で消えず, かつ翌週に複製もされない (PFV を介さないため).

    payload (one_time 必須):
        ``{patient_id, course_id, iso_year, iso_week, proposed_visits:[1枠],
           placement?, warnings?, override_reason?}``
        weekday は payload (任意) または ``proposed_visits[0].weekday`` から解決する.

    冪等性: 呼び出し元 (``PendingRequestApplier.apply``) の ``applied_at`` ガード内で
    実行されるため, 同一 request の再 approve では本関数は呼ばれず Visit を二重作成しない
    (raw INSERT なので冪等性はこのガードに依存する点に注意).
    """
    patient_id = _coerce_uuid(payload.get("patient_id") or request.target_patient_id)
    if patient_id is None:
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): patient_id is required",
            http_status=422,
        )

    # one_time 必須 payload: course_id / iso_year / iso_week.
    course_id = _coerce_uuid(payload.get("course_id"))
    iso_year_raw = payload.get("iso_year")
    iso_week_raw = payload.get("iso_week")
    if course_id is None or iso_year_raw is None or iso_week_raw is None:
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): course_id, iso_year, iso_week are required",
            http_status=422,
        )
    try:
        iso_year = int(iso_year_raw)
        iso_week = int(iso_week_raw)
    except (TypeError, ValueError):
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): iso_year / iso_week must be int",
            http_status=422,
        ) from None

    proposed_visits = payload.get("proposed_visits")
    if (
        not proposed_visits
        or not isinstance(proposed_visits, list)
        or len(proposed_visits) != 1
        or not isinstance(proposed_visits[0], dict)
    ):
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): proposed_visits must contain exactly 1 object",
            http_status=422,
        )
    new_visit = proposed_visits[0]

    # weekday: payload 優先, 無ければ proposed_visits[0].weekday.
    weekday_raw = payload.get("weekday")
    if weekday_raw is None:
        weekday_raw = new_visit.get("weekday")
    if (
        not isinstance(weekday_raw, int)
        or isinstance(weekday_raw, bool)
        or not (0 <= weekday_raw <= 6)
    ):
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): weekday must be int 0-6",
            http_status=422,
        )
    weekday = weekday_raw

    new_start = _coerce_time(new_visit.get("start_time"))
    if new_start is None:
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): proposed_visits[0].start_time must be 'HH:MM'",
            http_status=422,
        )

    duration_raw = new_visit.get("duration_min", 30)
    try:
        new_duration = int(duration_raw)
    except (TypeError, ValueError):
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): proposed_visits[0].duration_min must be int",
            http_status=422,
        ) from None
    if new_duration < 1 or new_duration > 480:
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): proposed_visits[0].duration_min must be 1..480",
            http_status=422,
        )

    # end_time = start + duration (> start を検証).
    start_dt = datetime.combine(date.min, new_start)
    end_dt = start_dt + timedelta(minutes=new_duration)
    if end_dt.date() != date.min:
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): end_time overflows the day",
            http_status=422,
        )
    new_end = end_dt.time()
    if new_end <= new_start:
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): end_time must be > start_time",
            http_status=422,
        )

    # visit_date = 対象 ISO 週 × weekday (0=Mon → ISO day 1).
    try:
        visit_date = date.fromisocalendar(iso_year, iso_week, weekday + 1)
    except ValueError as exc:
        raise PendingRequestApplyError(
            f"patient_visit_add(one_time): invalid iso week {iso_year}-W{iso_week}: {exc}",
            http_status=422,
        ) from exc

    # 患者存在チェック.
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise PendingRequestApplyError(
            f"patient_visit_add(one_time): patient {patient_id} not found",
            http_status=404,
        )

    # Course 検証: 存在・deleted_at NULL・iso_year/iso_week/weekday が payload と一致.
    # 不一致だとボードに出ない / 別週に出るため INSERT 前に 422 で弾く.
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise PendingRequestApplyError(
            f"patient_visit_add(one_time): course {course_id} not found",
            http_status=422,
        )
    if course.iso_year != iso_year or course.iso_week != iso_week or course.weekday != weekday:
        raise PendingRequestApplyError(
            "patient_visit_add(one_time): course does not match the target "
            f"week/weekday (course={course.iso_year}-W{course.iso_week} wd={course.weekday}, "
            f"payload={iso_year}-W{iso_week} wd={weekday})",
            http_status=422,
        )

    # 単発の精密 赤再判定 (恒常より強い): その週の対象 Course の実 Visit を読んで
    # time_overlap (区間 [start,end) 重複, 端点接触は除外) と capacity_full
    # (≥ config.max_patients_per_course) を判定する.
    # 設定値注入の是正: 定員上限は module 定数直参照ではなく事業所別設定 (DB) を尊重する.
    config = await load_scheduling_config(db)
    existing_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.course_id == course_id,
                Visit.visit_date == visit_date,
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()

    extra_red: list[str] = []
    new_start_min = new_start.hour * 60 + new_start.minute
    new_end_min = new_end.hour * 60 + new_end.minute
    for ev in existing_visits:
        ev_start_min = ev.start_time.hour * 60 + ev.start_time.minute
        ev_end_min = ev.end_time.hour * 60 + ev.end_time.minute
        # [start, end) 区間重複 (端点接触は重複としない).
        if new_start_min < ev_end_min and ev_start_min < new_end_min:
            extra_red.append("time_overlap")
            break
    if len(existing_visits) >= config.max_patients_per_course:
        extra_red.append("capacity_full")

    # 赤警告があるのに override_reason 空なら 422 (クライアント申告 + サーバ再判定を合算).
    _validate_override_reason_for_red(payload, extra_red_codes=extra_red)

    # primary_staff_id = 対象 Course の assigned_staff_id (無ければ NULL 可).
    primary_staff_id = course.assigned_staff_id

    # 単発 Visit を 1 件 INSERT (PFV は触らない).
    # ★source='manual': Layer-1 auto purge から保護 + 翌週複製を防ぐ.
    db.add(
        Visit(
            patient_id=patient_id,
            primary_staff_id=primary_staff_id,
            visit_date=visit_date,
            start_time=new_start,
            end_time=new_end,
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="manual",
            course_id=course_id,
            required_staff_count=1,
            note="現場ボード単発配置 (G-85)",
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
