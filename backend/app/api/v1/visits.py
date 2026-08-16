"""Visit CRUD endpoints (Phase 2 domain router, W2-BE4 拡張).

Staff role users may only see visits where they are assigned as primary,
secondary, or mentor staff; admin/manager see everything.

W2-BE4 (`docs/plans/v2-allocation-redesign.md` v0.9 §3.3 / §4.5):
  - レスポンスに ``staff_assignments`` (visit_staff_assignments の一覧) を含める
  - 入力で ``course_id`` / ``required_staff_count`` / ``visit_group_id`` を受理
  - ``POST /visits/{id}/staff`` / ``DELETE /visits/{id}/staff/{staff_id}``
    で多対多テーブルを操作 (API 契約 §5.4 / §5.5)
  - スタッフロールの可視性は visit_staff_assignments も含めて判定
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.course import Course
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User, normalize_user_role
from app.models.visit import (
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.visit import VisitCreate, VisitRead, VisitUpdate
from app.schemas.visit_checkin import CheckinCreate, QrResolveRead
from app.services.accompaniment import (
    AccompanimentEntry,
    accompaniment_visibility_condition,
    is_accompaniment_visit_for_staff,
    resolve_accompaniment_by_visit,
)
from app.services.checkin.judge import (
    JST,
    judge_checkin,
    resolve_patient_for_visit,
    resolve_qr_patient,
)
from app.services.checkin.notify import (
    notify_checkin_anomalies,
    notify_checkin_mismatch,
    resolve_checkin_missing,
)
from app.services.constraint_override_notify import (
    ConstraintWarning,
    collect_constraint_warnings_for_patients,
    collect_constraint_warnings_for_staff,
    constraint_confirmation_detail,
)
from app.services.op_log_service import fmt_time, fmt_weekday, record_op
from app.utils.db import try_advisory_xact_lock

router = APIRouter()


def _staff_visibility_filter(staff_id: UUID):
    """Visit が当該スタッフに見える条件 (primary/secondary/mentor または assignments).

    新人同行 (§6.4 C-1): 新人本人の「今日の訪問」「今週の予定」に同行訪問が出るよう、
    同行リンク条件 (visit 直リンク OR course リンク・live JOIN) を OR で足す。
    """
    # Note: visit_staff_assignments のサブクエリでも該当する visit を含める
    assignments_subq = select(VisitStaffAssignment.visit_id).where(
        VisitStaffAssignment.staff_id == staff_id
    )
    return or_(
        Visit.primary_staff_id == staff_id,
        Visit.secondary_staff_id == staff_id,
        Visit.mentor_staff_id == staff_id,
        Visit.id.in_(assignments_subq),
        accompaniment_visibility_condition(staff_id),
    )


async def _guard_constraint_violations(
    db,
    *,
    patient_id: UUID,
    staff_id: UUID | None = None,
    course_id: UUID | None = None,
) -> None:
    """NG スタッフ / 性別制限に触れる割当なら 422 で弾く (visits 直 API の防御).

    正典設計書: ``docs/plans/patient-ng-staff-design.md`` §7-2。

    **確認フロー (422 → acknowledge → 通知) ではなく単純 422** にする理由:
    本 router (``POST /visits`` / ``PATCH /visits/{id}`` / ``POST /visits/{id}/staff``)
    は FE 導線を持たない汎用 CRUD = 「管理者が API を直接叩く」経路であり、
    確認ダイアログを出す相手 (UI) が居ない。承認して通す意思表示は、確認フローを
    備えた業務経路 (訪問を移動 / 配置 / 提案採用) 側で行う。detail の ``code`` は
    全経路で統一し (``constraint_confirmation_required``)、将来 UI が付いたときに
    同じパーサで確認フローへ格上げできるようにしておく。

    ``staff_id`` (名指し) と ``course_id`` (コースの現担当) の両方を見る。
    同一スタッフで重複した警告は 1 件にまとめる。
    """
    warnings: list[ConstraintWarning] = []
    seen: set[tuple[str, UUID, UUID]] = set()

    def _extend(rows: list[ConstraintWarning]) -> None:
        for w in rows:
            key = (w.kind, w.patient_id, w.staff_id)
            if key in seen:
                continue
            seen.add(key)
            warnings.append(w)

    if staff_id is not None:
        _extend(
            await collect_constraint_warnings_for_staff(
                db, staff_id=staff_id, patient_ids=[patient_id]
            )
        )
    if course_id is not None:
        course = await db.scalar(
            select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
        )
        if course is not None:
            _extend(
                await collect_constraint_warnings_for_patients(
                    db, course=course, patient_ids=[patient_id]
                )
            )
    if warnings:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=constraint_confirmation_detail(warnings),
        )


async def _load_assignments(db, visit_id: UUID) -> list[dict]:
    """Load visit_staff_assignments rows for a single visit."""
    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_id)
        )
    ).all()
    return [
        {
            "visit_id": r.visit_id,
            "staff_id": r.staff_id,
            "assigned_at": r.created_at,
        }
        for r in rows
    ]


def _project_latest_checkin(row: VisitCheckin) -> dict:
    """VisitCheckin 行を VisitRead.latest_checkin (CheckinRead 形) に射影する。"""
    return {
        "id": row.id,
        "kind": row.kind,
        "match_status": row.match_status,
        "distance_m": float(row.distance_m) if row.distance_m is not None else None,
        "accuracy_m": float(row.accuracy_m) if row.accuracy_m is not None else None,
        "scanned_at": row.scanned_at,
        "checkin_source": row.checkin_source,
        "reason": row.reason,
        "is_override": row.is_override,
    }


async def _load_latest_checkin(db, visit_id: UUID) -> dict | None:
    """Load the most recent visit_checkins row (any kind) for a visit.

    Append-only テーブルから ``scanned_at DESC LIMIT 1`` で最新の打刻を採用する。
    VisitRead.latest_checkin に非破壊で載せる射影 (CheckinRead 形)。
    """
    row = await db.scalar(
        select(VisitCheckin)
        .where(VisitCheckin.visit_id == visit_id)
        .order_by(VisitCheckin.scanned_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    return _project_latest_checkin(row)


async def _load_latest_checkins_bulk(db, visit_ids: list[UUID]) -> dict[UUID, dict]:
    """対象 visit 群の最新打刻 (any kind) を 1 クエリでバッチ取得する。

    一覧 (list_visits) の N+1 を避けるため、monitor の latest-per-kind 取得と同様に
    ``scanned_at DESC`` で全行を取得し visit_id ごと最初に出た行 (= 最新) を採用する。
    """
    latest: dict[UUID, dict] = {}
    if not visit_ids:
        return latest
    rows = (
        await db.scalars(
            select(VisitCheckin)
            .where(VisitCheckin.visit_id.in_(visit_ids))
            .order_by(VisitCheckin.scanned_at.desc())
        )
    ).all()
    for r in rows:
        if r.visit_id not in latest:  # DESC 並びなので最初に出た = 最新。
            latest[r.visit_id] = _project_latest_checkin(r)
    return latest


def _serialize_visit(
    visit: Visit,
    assignments: list[dict] | None = None,
    latest_checkin: dict | None = None,
    accompaniments: list[dict] | None = None,
) -> dict:
    """Project a Visit (with optional eager-loaded patient/primary_staff) into
    the VisitRead shape, including denormalized `patient_name`/`staff_name`
    and v2 ``staff_assignments`` (visit_staff_assignments).
    """
    data = {
        "id": visit.id,
        "patient_id": visit.patient_id,
        "primary_staff_id": visit.primary_staff_id,
        "secondary_staff_id": visit.secondary_staff_id,
        "mentor_staff_id": visit.mentor_staff_id,
        "visit_date": visit.visit_date,
        "start_time": visit.start_time,
        "end_time": visit.end_time,
        "type": visit.type,
        "status": visit.status,
        "source": visit.source,
        # 週のピン (青ピン / 2026-08-09)。手書き dict のため列追加時はここにも
        # 足すこと — 漏れると VisitRead の default False で上書きされ、DB が true
        # でも API が false を返す (実際に起きた事故)。
        "week_pinned": bool(getattr(visit, "week_pinned", False)),
        # 予定外訪問 (adhoc-checkin 生成 / 設計 §3)。week_pinned と同じ罠の位置:
        # 手書き dict なのでここに足さないと VisitRead の default False で潰れる。
        "is_unplanned": visit.is_unplanned,
        "note": visit.note,
        "kaipoke_id": visit.kaipoke_id,
        # W2-BE4 v2 fields
        "course_id": getattr(visit, "course_id", None),
        "required_staff_count": getattr(visit, "required_staff_count", 1),
        "visit_group_id": getattr(visit, "visit_group_id", None),
        "created_at": visit.created_at,
        "updated_at": visit.updated_at,
        "deleted_at": visit.deleted_at,
        "patient_name": getattr(visit.patient, "name", None) if visit.patient is not None else None,
        "staff_name": (
            getattr(visit.primary_staff, "name", None) if visit.primary_staff is not None else None
        ),
        # 患者ジオコード (Numeric → float). モバイル QR チェックインの距離プレビュー
        # 用に非破壊で公開する. patient 未ロード / 未ジオコードは None.
        "patient_lat": (
            float(visit.patient.lat)
            if visit.patient is not None and visit.patient.lat is not None
            else None
        ),
        "patient_lng": (
            float(visit.patient.lng)
            if visit.patient is not None and visit.patient.lng is not None
            else None
        ),
        # モバイル訪問カードの性別ウォッシュ/📍住所用 (R-9)。patient 未ロードは None。
        "patient_sex": getattr(visit.patient, "sex", None) if visit.patient is not None else None,
        "patient_address": (
            getattr(visit.patient, "address", None) if visit.patient is not None else None
        ),
        "staff_assignments": assignments or [],
        # QR チェックイン (Phase 1) の最新打刻. 既存呼出は None のまま (非破壊).
        "latest_checkin": latest_checkin,
        # 同行 (非破壊追加). 一般化 決定#5 で複数名対応。``accompaniments`` が全件
        # (決定的順序)、``accompaniment`` は後方互換の先頭要素。
        # **手書き dict の罠**: week_pinned / is_unplanned と同じ位置づけで、ここに
        # 足し忘れると VisitRead の default (None / []) で潰れて DB に居るのに
        # API が返さない (過去に実際に起きた事故)。
        "accompaniments": accompaniments or [],
        "accompaniment": (accompaniments[0] if accompaniments else None),
    }
    return data


def _restrict_to_qr_capability(data: dict) -> dict:
    """QR capability (担当外) の GET レスポンスを絞り込む (設計 §4-2 / ディレクター決定).

    「QR 所持 = 現地に居る」で正当化できるのは **その訪問を遂行するのに要る情報**
    まで。resolve v2 (§4-1) が既に開示している範囲 (患者氏名 / 時間帯 / status /
    予定担当名) と、現地判定に要る住所・座標・性別は残し、事業所の業務情報は落とす:

    * ``note`` (申し送り・業務メモ) / ``kaipoke_id`` (外部システムの識別子)
    * ``staff_assignments`` (誰が組まれているかの一覧) / ``accompaniment`` (同行者名)
    * ``latest_checkin.reason`` — ``latest_checkin`` は「この visit の最新打刻」で
      あって**自分の打刻とは限らない**。担当スタッフが書いた場所違い / 未訪問の
      理由 (自由記述) は業務メモと同質なので落とす。退出導線の判断に要る構造情報
      (``kind`` / ``scanned_at`` / ``match_status`` 等) は残す。

    担当者本人の GET (通常の可視性で通った場合) は従来どおり全量を返す。
    """
    latest_checkin = data.get("latest_checkin")
    return {
        **data,
        "note": None,
        "kaipoke_id": None,
        "staff_assignments": [],
        "accompaniment": None,
        "accompaniments": [],
        "latest_checkin": (
            {**latest_checkin, "reason": None} if latest_checkin is not None else None
        ),
    }


def _accompaniment_payload(resolved: list[AccompanimentEntry] | None) -> list[dict]:
    """resolve_accompaniment_by_visit のエントリ列を VisitRead.accompaniments 形へ.

    順序は解決側が保証する決定的順序 (support 優先 → 名前昇順) をそのまま保つ。
    先頭が後方互換の単数 ``accompaniment`` になるので、並べ替えてはいけない。
    """
    if not resolved:
        return []
    return [e.to_payload() for e in resolved]


@router.get("", response_model=list[VisitRead], summary="List visits")
async def list_visits(
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    week_start: Annotated[
        date | None, Query(description="Inclusive start date (yyyy-MM-dd)")
    ] = None,
    week_end: Annotated[date | None, Query(description="Inclusive end date (yyyy-MM-dd)")] = None,
    staff_id: Annotated[
        UUID | None,
        Query(description="Filter to visits where this staff is primary/secondary/mentor"),
    ] = None,
    course_id: Annotated[
        UUID | None,
        Query(description="Filter to visits in this course (W2-BE4)"),
    ] = None,
    patient_id: Annotated[
        UUID | None,
        Query(
            description=(
                "Filter to visits for a single patient. Wave Next 1 H3: "
                "PatientScheduleDetailDialog で当該患者の今週分のみ取得する用途."
            ),
        ),
    ] = None,
) -> list[dict]:
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    stmt = (
        select(Visit)
        .where(Visit.deleted_at.is_(None))
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    # Determine the effective staff filter:
    #   - staff role: always force-narrow to the caller's own staff_id (ignore
    #     any client-supplied value to prevent cross-staff peeking).
    #   - admin/manager: honor the optional `staff_id` query for the mobile
    #     "my visits" view; without it they continue to see everyone.
    if user.role == "staff":
        if user.staff_id is None:
            return []
        stmt = stmt.where(_staff_visibility_filter(user.staff_id))
    elif staff_id is not None:
        stmt = stmt.where(_staff_visibility_filter(staff_id))
    if week_start is not None:
        stmt = stmt.where(Visit.visit_date >= week_start)
    if week_end is not None:
        stmt = stmt.where(Visit.visit_date <= week_end)
    if course_id is not None:
        stmt = stmt.where(Visit.course_id == course_id)
    if patient_id is not None:
        stmt = stmt.where(Visit.patient_id == patient_id)
    stmt = stmt.order_by(Visit.visit_date, Visit.start_time).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    # Bulk-load assignments for the listed visits in a single query
    visit_ids = [v.id for v in rows]
    assignments_by_visit: dict[UUID, list[dict]] = {vid: [] for vid in visit_ids}
    if visit_ids:
        asn_rows = (
            await db.scalars(
                select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
            )
        ).all()
        for a in asn_rows:
            assignments_by_visit.setdefault(a.visit_id, []).append(
                {
                    "visit_id": a.visit_id,
                    "staff_id": a.staff_id,
                    "assigned_at": a.created_at,
                }
            )
    # 最新打刻を 1 クエリでバッチ取得し (N+1 回避)、各 visit に紐付ける。GET /visits/{id}
    # との契約対称性のため一覧でも latest_checkin を返す (QR チェックイン)。
    latest_by_visit = await _load_latest_checkins_bulk(db, visit_ids)
    # 新人同行 (§6.4): 訪問群の同行者を 1 度に解決し、各 visit に非破壊で載せる。
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, list(rows))
    return [
        _serialize_visit(
            v,
            assignments=assignments_by_visit.get(v.id, []),
            latest_checkin=latest_by_visit.get(v.id),
            accompaniments=_accompaniment_payload(accompaniment_by_visit.get(v.id)),
        )
        for v in rows
    ]


@router.get(
    "/resolve-qr/{token}",
    response_model=QrResolveRead,
    summary="QR トークン → 本日の担当 visit 解決 (汎用カメラ・ディープリンク用)",
)
async def resolve_qr_visits(
    token: str,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    """`/q/{token}` ランディングページの遷移先解決 (v2 = 凍結コントラクト 2026-08-16)。

    正典設計書: ``docs/plans/qr-open-checkin-design.md`` §4-1。

    患者宅 QR を標準カメラ / Chrome で読むと `/q/{token}` へ遷移するため、FE は
    本 API でトークンを「本日 (JST) のその患者の visit」へ解決し、担当なら訪問
    詳細 (`/m/today/{visitId}`)、担当外なら代行 / 予定外の選択画面へ進む。

    - 認可は打刻 (`_load_visit_for_checkin`) と同方針: admin/staff かつ
      staff 紐付け必須 (未紐付けは打刻者を確定できないため 403)。
    - トークン解決は判定 (`_resolve_patient`) と同ルール: 未知 = 404 /
      失効 (ローテ済) = 410。visit 文脈が無いので 410 の案内は氏名を出さない。
    - **候補は当日 (JST)・未削除・取消以外の全件** (v1 の「自分の担当のみ」は
      撤廃)。担当かどうかは ``is_mine`` で返す。担当外に患者氏名・予定スタッフ名を
      開示するのは「QR 所持 = 現地に居る」を認可の鍵とする決定#1 による
      (誤った利用者への記録を防ぐ確認表示に氏名が必須)。
    """
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    staff_id = user.staff_id
    if staff_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not linked to a staff record",
        )

    patient = await resolve_qr_patient(db, token)

    today_jst = datetime.now(UTC).astimezone(JST).date()
    rows = (
        await db.scalars(
            select(Visit)
            .where(
                Visit.patient_id == patient.id,
                Visit.visit_date == today_jst,
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
            )
            .options(selectinload(Visit.primary_staff))
            .order_by(Visit.start_time)
        )
    ).all()

    # is_mine は既存の可視性条件をそのまま再利用する (担当集合の定義を二重に
    # 書かない)。候補 id に絞った 1 クエリで解決する。
    mine: set[UUID] = set()
    if rows:
        mine = set(
            (
                await db.scalars(
                    select(Visit.id).where(
                        Visit.id.in_([v.id for v in rows]),
                        _staff_visibility_filter(staff_id),
                    )
                )
            ).all()
        )

    return {
        "patient_name": patient.name,
        "candidates": [
            {
                "visit_id": v.id,
                "start_time": v.start_time,
                "end_time": v.end_time,
                "status": v.status,
                "planned_staff_name": (
                    getattr(v.primary_staff, "name", None) if v.primary_staff is not None else None
                ),
                "is_mine": v.id in mine,
                "is_unplanned": v.is_unplanned,
            }
            for v in rows
        ],
    }


@router.get("/{visit_id}", response_model=VisitRead, summary="Get visit by id")
async def get_visit(
    visit_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    qr_token: Annotated[
        str | None,
        Query(
            description=(
                "患者宅 QR のトークン (後方互換)。``X-QR-Token`` ヘッダを推奨。"
                "担当外でも**この visit の患者**に解決できれば 200 を返す "
                "(QR capability 分岐・設計 §4-2)。代行打刻の訪問詳細表示用。"
            ),
        ),
    ] = None,
    x_qr_token: Annotated[
        str | None,
        Header(
            alias="X-QR-Token",
            description=(
                "患者宅 QR のトークン (推奨)。クエリだとアクセスログ / Referer に "
                "トークンが残るため、ヘッダ指定を優先する。"
            ),
        ),
    ] = None,
) -> dict:
    """訪問詳細を返す。

    QR capability 分岐 (設計 ``docs/plans/qr-open-checkin-design.md`` §4-2):
    トークン (``X-QR-Token`` ヘッダ優先・``?qr_token=`` も後方互換で受理) が
    この visit の患者に解決できれば、担当外スタッフでも 200 を返す (代行打刻の
    詳細画面を打刻経路と同じ認可で一本化する)。トークン無しの担当外は従来どおり
    404 (存在秘匿)。

    **GET は失敗も 404 に丸める**: 通常の可視性で見えない visit に対しては、
    トークンが別患者 (409) / 失効 (410) でも 404 を返す。さもないと担当外が
    任意の visit_id にトークンを添えるだけで「その ID の visit は実在する」を
    引き出せてしまい、存在秘匿 (決定#1) が崩れる。打刻 (checkin / checkout) は
    現地で読んだ QR の誤りをスタッフに伝える必要があるため 409 / 410 のまま。

    **capability 経由 (担当外) のレスポンスは絞り込む**: 現地に居る前提で正当な
    範囲 (患者氏名 / 住所 / 座標 / 性別 / 時間帯 / status / 予定担当名 = resolve v2
    で既に開示済みの範囲) は返すが、業務メモ (``note``) / カイポケ ID /
    担当者一覧 / 同行者名 / 打刻理由 (``latest_checkin.reason``) は落とす。
    """
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    # ヘッダ優先 (トークンの露出面を減らす)。
    effective_qr_token = x_qr_token or qr_token

    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    assignments = await _load_assignments(db, visit.id)

    # capability (QR) だけで通ったか = 担当外の閲覧か。true なら projection を絞る。
    via_qr_capability = False
    if user.role == "staff":
        if user.staff_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        # Visibility: primary/secondary/mentor staff OR a visit_staff_assignments row
        assigned_staff_ids = {a["staff_id"] for a in assignments}
        visible = user.staff_id in (
            {visit.primary_staff_id, visit.secondary_staff_id, visit.mentor_staff_id}
            | assigned_staff_ids
        )
        # 新人同行 (§6.4 C-1): 同行リンク (visit 直リンク OR course リンク) も可視に
        # 含める (漏れると新人がカードをタップした瞬間 404)。
        if not visible:
            visible = await is_accompaniment_visit_for_staff(
                db, visit_id=visit.id, course_id=visit.course_id, staff_id=user.staff_id
            )
        # QR capability (§4-2): 現地 QR を持っていれば担当外でも詳細を返す。
        # 解決失敗 (別患者 409 / 失効 410) は **GET に限り 404 へ丸める**: 元々
        # 見えない visit なので、エラーコードの差から存在を推測させない。
        if not visible:
            try:
                visible = via_qr_capability = await _qr_capability_allows(
                    db, visit, effective_qr_token
                )
            except HTTPException as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
                ) from exc
        if not visible:
            # Hide existence from non-assigned staff.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    latest_checkin = await _load_latest_checkin(db, visit.id)
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, [visit])
    payload = _serialize_visit(
        visit,
        assignments=assignments,
        latest_checkin=latest_checkin,
        accompaniments=_accompaniment_payload(accompaniment_by_visit.get(visit.id)),
    )
    if via_qr_capability:
        payload = _restrict_to_qr_capability(payload)
    return payload


@router.post(
    "",
    response_model=VisitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create visit",
)
async def create_visit(
    payload: VisitCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict:
    # NG スタッフ / 性別制限 (§7-2): 作成時に担当 / コースが指定されていれば検査する。
    await _guard_constraint_violations(
        db,
        patient_id=payload.patient_id,
        staff_id=payload.primary_staff_id,
        course_id=payload.course_id,
    )
    visit = Visit(**payload.model_dump())
    db.add(visit)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Conflict: duplicate value"
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc
    # Reload with relationships for the response.
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit.id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    assignments = await _load_assignments(db, visit.id)
    return _serialize_visit(visit, assignments=assignments)


@router.patch("/{visit_id}", response_model=VisitRead, summary="Update visit")
async def update_visit(
    visit_id: UUID,
    payload: VisitUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict:
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    changes = payload.model_dump(exclude_unset=True)
    # 青ピン (week_pinned) は蓋 (PO 決定 2026-08-09): 配置 (日付・時刻) の変更は
    # 解除するまで人手でも不可。担当・状態・メモ等の非配置フィールドは許可する
    # (青ピンが守るのは「今週この位置」であって業務メタ情報ではない)。
    # week_pinned 自体の変更 (= 解除操作) はもちろん許可。
    _placement_fields = {"visit_date", "start_time", "end_time"}
    if (
        bool(getattr(visit, "week_pinned", False))
        and changes.get("week_pinned") is not False
        and _placement_fields & set(changes.keys())
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="今週固定（青ピン）されています。解除してから時刻・日付を変更してください",
        )

    # NG スタッフ / 性別制限 (§7-2): 担当 (primary_staff_id) かコース (course_id) が
    # **変わるとき**だけ検査する (無関係なフィールドの更新で既存の割当を蒸し返さない)。
    # 患者の付け替えも同時に来た場合は変更後の患者で見る。
    _eff_patient_id = changes.get("patient_id") or visit.patient_id
    _new_staff_id = (
        changes["primary_staff_id"]
        if "primary_staff_id" in changes and changes["primary_staff_id"] != visit.primary_staff_id
        else None
    )
    _new_course_id = (
        changes["course_id"]
        if "course_id" in changes and changes["course_id"] != visit.course_id
        else None
    )
    if _new_staff_id is not None or _new_course_id is not None:
        await _guard_constraint_violations(
            db,
            patient_id=_eff_patient_id,
            staff_id=_new_staff_id,
            course_id=_new_course_id,
        )

    for field, value in changes.items():
        setattr(visit, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Conflict: duplicate value"
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    assignments = await _load_assignments(db, visit.id)
    return _serialize_visit(visit, assignments=assignments)


@router.delete(
    "/{visit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete visit (admin / manager)",
)
async def delete_visit(
    visit_id: UUID,
    db: DbDep,
    current_user: Annotated[User, Depends(require_role("admin"))],
    cascade_fixed_visit: Annotated[
        bool,
        Query(
            description=(
                "True: 同 (patient_id, weekday) の patient_fixed_visits "
                "(mode='normal' / 'special') も同時に物理削除する (W18 Codex-fix 重大-2)。"
                "B-5 配置移動 (delete + place-and-fix) で旧曜日の固定枠が残って"
                "翌週以降 Layer 1 で二重展開するのを防ぐ。"
            ),
        ),
    ] = False,
    cascade_partner: Annotated[
        bool,
        Query(
            description=(
                "True (default): 同 visit_group_id を持つペア visit "
                "(2 名体制対応の partner visit) も同時に soft-delete する "
                "(W37 Phase 2-A)。False: 当該 visit のみ片方削除する."
            ),
        ),
    ] = True,
    op_group_id: Annotated[
        UUID | None,
        Query(
            description="操作グループ ID（Wave U-3 undo/redo）。FE が DnD の delete+place に同値を送る。省略で自動発行。",
        ),
    ] = None,
) -> None:
    """visit を soft-delete する.

    W18 Codex-fix 重大-1: RBAC を admin / manager に拡張.
    W18 Codex-fix 重大-2: ``cascade_fixed_visit=true`` のとき、当該 visit の
    ``(patient_id, visit_date.weekday())`` に紐付く ``patient_fixed_visits``
    (mode='normal' / 'special' 両方) を **物理削除** する.

    W37 Phase 2-A: ``cascade_partner=true`` (default) のとき、当該 visit と
    同じ ``visit_group_id`` を持つ partner visit (2 名体制ペア) も同時に
    soft-delete する. 「2 名はペアで削除」が標準フロー. 入力ミスで片方だけ
    消したいケースは ``cascade_partner=false`` で対応.

    両 cascade は併用可能 (cascade_fixed_visit=true & cascade_partner=true で
    ペアの visit + 共通 weekday の固定枠 normal/special を全て削除).
    """
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # ----- W37 Phase 2-A: partner visit の同時 soft-delete -----
    # visit_group_id が NULL の場合 (1 名体制) は cascade_partner の値に依らず
    # no-op (partner はそもそも存在しない).
    partner_visits: list[Visit] = []
    if cascade_partner and visit.visit_group_id is not None:
        partner_rows = (
            await db.scalars(
                select(Visit).where(
                    Visit.visit_group_id == visit.visit_group_id,
                    Visit.id != visit.id,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
        partner_visits = list(partner_rows)

    # cascade_fixed_visit: visit の (patient_id, weekday) に紐付く固定枠を物理削除.
    # mode='normal' と 'special' の両方を対象にする (どちらが残っていても
    # 翌週展開で二重訪問になり得るため). slot_index 0/1 も全て削除される
    # (W37 Phase 2-A: 2 名体制患者の partner も含めた全 slot を一掃).
    #
    # 青ピン (week_pinned) は蓋 (PO 決定 2026-08-09): 解除するまで人手でも
    # 削除できない。「今週の盤面を凍らせる」ための明示的な 2 段操作にする。
    if bool(getattr(visit, "week_pinned", False)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="今週固定（青ピン）されています。解除してから削除してください",
        )

    # PO 決定 (2026-08-09): 完全固定 (旧 pinned) の 422 ブロックは撤廃。
    # 完全固定の意味は「エンジンが動かさない」であり、人手の削除は常に正当。
    # FE 側が「これは完全固定です」の確認を出したうえで到達する。
    if cascade_fixed_visit:
        old_weekday = visit.visit_date.weekday()
        await db.execute(
            delete(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == visit.patient_id,
                PatientFixedVisit.weekday == old_weekday,
            )
        )

    # Wave U-3: op_log 用データを commit 前に確保
    _op_patient_id = visit.patient_id
    _op_visit_date = visit.visit_date
    _op_start_time = visit.start_time
    _op_all_visit_ids = [str(visit.id)] + [str(pv.id) for pv in partner_visits]

    # 当該 visit を soft-delete.
    visit.deleted_at = func.now()
    # partner visits も soft-delete (W37 Phase 2-A).
    for pv in partner_visits:
        pv.deleted_at = func.now()
    await db.commit()

    # Wave U-3: 操作ジャーナルに記録（ベストエフォート）
    _wd = _op_visit_date.weekday()
    _label = f"訪問を削除（{fmt_weekday(_wd)}{fmt_time(_op_start_time)}）"
    await record_op(
        db,
        user_id=current_user.id,
        iso_year=_op_visit_date.isocalendar()[0],
        iso_week=_op_visit_date.isocalendar()[1],
        op_group_id=op_group_id,
        op_kind="delete_visit",
        label=_label,
        forward_payload={"op": "soft_delete_visits", "visit_ids": _op_all_visit_ids},
        inverse_payload={"op": "restore_visits", "visit_ids": _op_all_visit_ids},
    )
    await db.commit()

    return None


# ---------------------------------------------------------------------------
# W2-BE4: visit_staff_assignments operations (API 契約 §5.4 / §5.5)
# ---------------------------------------------------------------------------


class VisitStaffAddRequest(BaseModel):
    """``POST /visits/{id}/staff`` リクエスト."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID


@router.post(
    "/{visit_id}/staff",
    response_model=VisitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add staff to visit (visit_staff_assignments) — W2-BE4",
)
async def add_visit_staff(
    visit_id: UUID,
    payload: VisitStaffAddRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict:
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # NG スタッフ / 性別制限 (§7-2): 当該 visit の患者に対して NG / 性別制限外の
    # スタッフを足そうとしていれば 422 (書き込み前)。
    await _guard_constraint_violations(db, patient_id=visit.patient_id, staff_id=payload.staff_id)

    assignment = VisitStaffAssignment(visit_id=visit_id, staff_id=payload.staff_id)
    db.add(assignment)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg or "primary" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: staff already assigned to this visit",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc

    assignments = await _load_assignments(db, visit_id)
    return _serialize_visit(visit, assignments=assignments)


@router.delete(
    "/{visit_id}/staff/{staff_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove staff from visit (visit_staff_assignments) — W2-BE4",
)
async def remove_visit_staff(
    visit_id: UUID,
    staff_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    # 確認: visit が存在するか
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")

    # 既存 assignment を削除. 該当行が無い場合は 404
    # 先に存在チェックしてから削除する (SQLAlchemy 2.0 の Result では rowcount が
    # 利用できない方言があるため)
    existing = await db.scalar(
        select(VisitStaffAssignment).where(
            VisitStaffAssignment.visit_id == visit_id,
            VisitStaffAssignment.staff_id == staff_id,
        )
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found",
        )
    await db.execute(
        delete(VisitStaffAssignment).where(
            VisitStaffAssignment.visit_id == visit_id,
            VisitStaffAssignment.staff_id == staff_id,
        )
    )
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# QR 訪問チェックイン (Phase 1) — checkin / checkout / no-show
#
# staff が自分の visit に対して到着 / 退出 / 未訪問を打刻する。判定 (距離・
# match_status) は ``app.services.checkin.judge`` がサーバ側で確定する。返却は
# 既存 GET /visits/{id} と同形 (VisitRead) で、最新打刻を ``latest_checkin`` に
# 非破壊で載せる (フロント me.ts の MyVisit を壊さない / 設計 R1・R7)。
# ---------------------------------------------------------------------------


def _require_staff_actor(user: User) -> UUID:
    """打刻者 (staff) を確定する。admin/staff かつ staff 紐付け必須 (設計 §2-1)."""
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    if user.staff_id is None:
        # User に staff 未紐付け → 打刻者を確定できない。
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not linked to a staff record",
        )
    return user.staff_id


async def _qr_capability_allows(db, visit: Visit, qr_token: str | None) -> bool:
    """QR capability 分岐: トークンが**この visit の患者**に解決できれば通す。

    正典設計書 ``docs/plans/qr-open-checkin-design.md`` §4-2。担当外でも
    「現地の QR を持っている」= 現地証明を認可の鍵とする (決定#1)。判定は判定側の
    ``_resolve_patient`` に丸ごと委ね、未知 = 404 / 失効 = 410 / 別患者 = 409 の
    エラー意味論を打刻経路と 1 本に保つ。``qr_token`` 無しは False = 担当外は
    従来どおり 404 (決定#6 の「担当外は QR 必須」をサーバ側で強制)。
    """
    if not qr_token:
        return False
    await resolve_patient_for_visit(db, visit, qr_token)
    return True


async def _load_visit_for_checkin(
    db, visit_id: UUID, user: User, *, qr_token: str | None = None
) -> tuple[Visit, UUID]:
    """打刻用に visit をロードし、呼び出し元が「自分の visit」か検証する。

    visit ガード (削除 / 取消 / 当日) は judge が行うため、ここでは
    ``deleted_at`` でフィルタしない (削除済みは judge が 409 を返す)。
    可視性 (担当外) は存在を秘匿するため 404 とする (既存 get_visit と同方針)。

    可視性判定を ``_staff_visibility_filter`` (WHERE 句) で直接絞らず、visit を
    無条件にロードしてから Python 側で担当判定するのは意図的:
      - 担当外 (visibility NG) は 404 で「存在を秘匿」したいが、
      - 削除済み / 取消 / 当日外 (= 担当はしている) は judge が 409 を返す必要がある。
    フィルタで一括に弾くと、削除済み visit が「行なし」となり 409 ではなく 404 に
    潰れてしまい、この 409 と 404 の区別が付かなくなる。両者を分けるため visit を
    取得後に Python で可視性のみ判定し、deleted/cancelled/当日外は judge に委ねる。

    ``qr_token`` (代行打刻・設計 §4-2) が渡された場合は、可視でなくてもトークンが
    この visit の患者に解決できれば通す (QR capability 分岐)。no-show は担当専用の
    ままにするため、この引数を渡さない。

    検証済みの ``(visit, staff_id)`` を返す (``staff_id`` は非 NULL を保証)。
    """
    staff_id = _require_staff_actor(user)

    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    assignments = await _load_assignments(db, visit.id)
    assigned_staff_ids = {a["staff_id"] for a in assignments}
    visible = staff_id in (
        {visit.primary_staff_id, visit.secondary_staff_id, visit.mentor_staff_id}
        | assigned_staff_ids
    )
    # 新人同行 (§6.4 C-1): 同行リンクも可視に含める。新人は自分の ID でチェックイン
    # 可能 (カイポケ上も正規 2 人目扱いのため打刻も本人が行う設計判断)。judge_checkin は
    # 渡された staff_id で VisitCheckin を記録するのみで「担当≠打刻者」を警告する経路は
    # 無いため、同行者打刻はそのまま正当な打刻として扱われる (別途の抑止は不要)。
    if not visible:
        visible = await is_accompaniment_visit_for_staff(
            db, visit_id=visit.id, course_id=visit.course_id, staff_id=staff_id
        )
    # 代行打刻 (§4-2): 担当外でも現地 QR を持っていれば通す。
    if not visible:
        visible = await _qr_capability_allows(db, visit, qr_token)
    if not visible:
        # 担当外には存在を秘匿する。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return visit, staff_id


async def _checkin_response(db, visit_id: UUID) -> dict:
    """打刻後の visit を GET /visits/{id} と同形で組み立てる (latest_checkin 付き)."""
    visit = await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id)
        .options(
            selectinload(Visit.patient),
            selectinload(Visit.primary_staff),
        )
    )
    assignments = await _load_assignments(db, visit_id)
    latest_checkin = await _load_latest_checkin(db, visit_id)
    accompaniment_by_visit = await resolve_accompaniment_by_visit(db, [visit])
    return _serialize_visit(
        visit,
        assignments=assignments,
        latest_checkin=latest_checkin,
        accompaniments=_accompaniment_payload(accompaniment_by_visit.get(visit_id)),
    )


@router.post(
    "/{visit_id}/checkin",
    response_model=VisitRead,
    summary="QR チェックイン (到着) — staff (自分の visit)",
)
async def checkin_visit(
    visit_id: UUID,
    payload: CheckinCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    # 代行打刻 (§4-2): 担当外でも現地 QR を持っていれば通す。
    visit, staff_id = await _load_visit_for_checkin(db, visit_id, user, qr_token=payload.qr_token)
    checkin = await judge_checkin(db, visit, staff_id, payload, "arrival")
    # status 退行ガード: 既に completed の visit に再 checkin しても completed の
    # まま据え置く (in_progress へ巻き戻さない)。planned / in_progress のときのみ
    # in_progress へ進める。checkin 行 (append-only) は status に依らず記録される。
    if visit.status in (VISIT_STATUS_PLANNED, VISIT_STATUS_IN_PROGRESS):
        visit.status = VISIT_STATUS_IN_PROGRESS
    # Phase 5-2: 場所違い (mismatch) は admin/manager へイベント駆動で冪等通知する
    # (同一 transaction で add し、下の commit で一括永続化)。
    await notify_checkin_mismatch(db, visit=visit, checkin=checkin)
    # QR 打刻の開放 (§4-4): 代行 / 予定外 / NG 交差も同じ形で冪等通知する
    # (記録は止めない = 決定#5。気づきは通知とモニターで担保する)。
    await notify_checkin_anomalies(db, visit=visit, checkin=checkin)
    # 遅刻→到着で cron 生成済みの「未訪問」通知が残らないよう、到着記録と同一
    # transaction で当該 visit の missing 通知を解消する (全ユーザー分削除)。
    await resolve_checkin_missing(db, visit.id)
    await db.commit()
    return await _checkin_response(db, visit_id)


@router.post(
    "/{visit_id}/checkout",
    response_model=VisitRead,
    summary="QR チェックアウト (退出) — staff (自分の visit)",
)
async def checkout_visit(
    visit_id: UUID,
    payload: CheckinCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    # 代行の退出も QR 再スキャン (§4-2「退出は改めて現地で読む」) で通す。
    visit, staff_id = await _load_visit_for_checkin(db, visit_id, user, qr_token=payload.qr_token)
    checkin = await judge_checkin(db, visit, staff_id, payload, "departure")
    visit.status = VISIT_STATUS_COMPLETED
    # 予定外訪問 (§3) の end_time は生成時の暫定値。退出打刻で実退出時刻へ更新する
    # (滞在時間・モニターのバー長を実績に合わせる)。開始以前へ巻き戻る値は採らない。
    if visit.is_unplanned:
        actual_end = _as_jst_time(checkin.scanned_at)
        if actual_end > visit.start_time:
            visit.end_time = actual_end
    # 到着は担当本人・退出だけ代行が押した場合でも代行 / NG 交差に気づけるよう、
    # 退出経路でも通知する (reference_id = visit.id で冪等なので、到着時に通知済み
    # なら重複しない)。
    await notify_checkin_anomalies(db, visit=visit, checkin=checkin)
    await db.commit()
    return await _checkin_response(db, visit_id)


# ---------------------------------------------------------------------------
# 予定外訪問の打刻 (POST /visits/adhoc-checkin) — 設計 §4-3
# ---------------------------------------------------------------------------

# 予定外訪問の暫定所要時間 (分)。患者の基本訪問時間が取れないときの既定
# (PO 確認事項 §9-1: 60 分固定で良いか)。
ADHOC_DEFAULT_SERVICE_MINUTES = 60


def _adhoc_lock_key(patient_id: UUID, staff_id: UUID, day: date) -> int:
    """(患者 × スタッフ × 当日) の advisory lock キー (signed bigint) を作る.

    同一患者宅で連打・二重送信された ``adhoc-checkin`` が並走すると、二重生成
    ガードの SELECT が互いの未 commit 行を見られず visit が 2 本生える。
    PostgreSQL の transaction-level advisory lock (``try_advisory_xact_lock``) で
    この 3 つ組を直列化する (commit / rollback で自動解放)。
    """
    raw = f"adhoc:{patient_id}:{staff_id}:{day.isoformat()}".encode()
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big", signed=True)


def _as_jst_time(dt: datetime) -> time:
    """timestamptz (SQLite の naive は UTC) を JST の壁時計 (秒切り捨て) にする."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST).time().replace(second=0, microsecond=0)


# device_time が当日 (JST) 外だったときの 422 文言。FE はこれをそのまま「破棄した
# 理由」として表示できる (退避キューから消す判断がユーザーに伝わる文にする)。
ADHOC_STALE_DEVICE_TIME_DETAIL = (
    "打刻した日付が変わったため送信できません。管理者に連絡してください"
)


def _adhoc_visit_moment(device_time: datetime | None, now: datetime) -> datetime:
    """予定外 visit の ``visit_date`` / ``start_time`` の基準時刻を決める。

    圏外で退避された打刻はネットワーク復帰まで送信されない。サーバ受信時刻を
    基準にすると数時間後の再送で訪問時刻がずれ、日を跨ぐと日付ごと誤るため、
    端末が記録した ``device_time`` を基準に採る (``scanned_at`` は従来どおり
    サーバ時刻 = 受信の事実を保つ)。

    * 未来の ``device_time`` は**無視**してサーバ時刻を使う (端末の時計ズレ対策。
      進んだ時計をそのまま採ると未来時刻の visit が生える)。
    * 当日 (JST) 外の ``device_time`` は 422。visit は ``visit_date`` 1 日で完結
      する行で、判定 (``_guard_visit``) も当日のみ許すため、日跨ぎの再送は
      サーバ時刻に丸めると別日の訪問を今日の実績として記録してしまう。
    * ``device_time`` 無しは従来どおりサーバ時刻。
    """
    if device_time is None:
        return now
    dt = device_time if device_time.tzinfo is not None else device_time.replace(tzinfo=UTC)
    if dt > now:
        return now
    if dt.astimezone(JST).date() != now.astimezone(JST).date():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ADHOC_STALE_DEVICE_TIME_DETAIL,
        )
    return dt


def _weekly_pattern_service_minutes(patient: Patient) -> int | None:
    """``weekly_pattern`` (サマリ形式 → entries) から基本訪問時間 (分) を取る."""
    pattern = patient.weekly_pattern
    if not isinstance(pattern, dict):
        return None
    candidates: list[object] = [pattern.get("service_minutes")]
    entries = pattern.get("entries")
    if isinstance(entries, list):
        candidates.extend(e.get("service_minutes") for e in entries if isinstance(e, dict))
    for raw in candidates:
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
    return None


async def _patient_service_minutes(db, patient: Patient) -> int:
    """患者の基本訪問時間 (分)。①固定枠 PFV → ②希望パターン → ③既定 60 分.

    優先順位は配置系の既存解決 (``api/v1/special_visits.py`` の
    ``_resolve_service_minutes``) と**同順**に揃える (同じ患者の所要時間が経路に
    よって食い違わないようにする)。既定値だけは設計 §3 の予定外訪問向けに 60 分
    (あちらは配置枠なので 30 分)。予定外訪問の枠はあくまで暫定で、退出打刻で実
    時刻へ更新される。
    """
    duration = await db.scalar(
        select(PatientFixedVisit.duration_min)
        .where(
            PatientFixedVisit.patient_id == patient.id,
            PatientFixedVisit.mode == "normal",
        )
        .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        .limit(1)
    )
    if duration:
        return int(duration)
    return _weekly_pattern_service_minutes(patient) or ADHOC_DEFAULT_SERVICE_MINUTES


@router.post(
    "/adhoc-checkin",
    response_model=VisitRead,
    summary="予定外訪問の到着打刻 (QR 必須) — visit を生成して記録",
)
async def adhoc_checkin(
    payload: CheckinCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    """当日予定が無い患者宅の QR 打刻。visit 生成 + 到着打刻 + 通知を 1 TX で行う。

    正典設計書 ``docs/plans/qr-open-checkin-design.md`` §3 / §4-3。

    - ``qr_token`` **必須** (無ければ 422)。患者特定に QR が構造上必要であり、
      担当外の記録は QR 所持を認可の鍵とする (決定#1 / #6)。未知 = 404 /
      失効 = 410 は resolve と同じ意味論。
    - **打刻時刻の基準**: ``device_time`` (端末時刻) があり当日 (JST) かつ未来で
      なければそれを ``visit_date`` / ``start_time`` の基準に採る (圏外退避 →
      復帰後の再送で訪問時刻がずれないように)。当日外は 422 / 未来は無視して
      サーバ時刻。``scanned_at`` は常にサーバ時刻 (``_adhoc_visit_moment``)。
    - 生成値: ``visit_date`` = 当日 (JST) / ``start_time`` = 打刻時刻 /
      ``end_time`` = 開始 + 患者の基本訪問時間 (無ければ 60 分・**退出打刻で実時刻へ
      更新**) / ``course_id`` = NULL / ``primary_staff_id`` = 打刻スタッフ /
      ``status`` = in_progress / ``is_unplanned`` = true。
      primary を打刻者にするのは、生成後に本人の「今日の訪問」に出て退出導線が
      既存可視性のまま成立するため (決定#4 は既存**予定** visit の話)。
    - 二重生成ガード: 同患者 × 同スタッフ × 当日 × in_progress の予定外 visit が
      既にあれば、新規生成せずその visit へ打刻を追記して返す (再スキャンで増殖
      させない)。同時 POST (連打・二重送信) は advisory lock で直列化し、後発は
      409 で弾く (未 commit の相手が見えず 2 本生えるのを防ぐ)。
    - 返却は既存 checkin と同形 (VisitRead + latest_checkin)。
    """
    staff_id = _require_staff_actor(user)
    if payload.qr_token is None or not payload.qr_token.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="qr_token is required for adhoc check-in",
        )

    patient = await resolve_qr_patient(db, payload.qr_token)

    now = datetime.now(UTC)
    # オフライン退避の再送で訪問時刻がずれないよう、端末時刻を基準に採る
    # (当日外は 422 / 未来は無視・``_adhoc_visit_moment``)。
    moment = _adhoc_visit_moment(payload.device_time, now)
    today_jst = moment.astimezone(JST).date()

    # 同時 POST の直列化 (二重生成ガードの前段)。lock は commit / rollback で
    # 自動解放される。取得できない = 同じ 3 つ組の打刻が処理中なので、増殖を
    # 作らずに 409 を返して FE のリトライに委ねる (再取得すれば既存 visit が
    # ガードに引っ掛かり追記になる)。
    if not await try_advisory_xact_lock(db, _adhoc_lock_key(patient.id, staff_id, today_jst)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="別の打刻を処理中です。少し待ってからもう一度お試しください",
        )

    # 二重生成ガード (§4-3)。
    visit = await db.scalar(
        select(Visit)
        .where(
            Visit.patient_id == patient.id,
            Visit.primary_staff_id == staff_id,
            Visit.visit_date == today_jst,
            Visit.status == VISIT_STATUS_IN_PROGRESS,
            Visit.is_unplanned.is_(True),
            Visit.deleted_at.is_(None),
        )
        .order_by(Visit.start_time)
        .limit(1)
    )
    if visit is None:
        start_time = _as_jst_time(moment)
        start_dt = datetime.combine(today_jst, start_time)
        end_dt = start_dt + timedelta(minutes=await _patient_service_minutes(db, patient))
        # 日跨ぎは当日内に収める (visit は visit_date 1 日で完結する行のため)。
        end_time = end_dt.time() if end_dt.date() == today_jst else time(23, 59)
        visit = Visit(
            patient_id=patient.id,
            primary_staff_id=staff_id,
            visit_date=today_jst,
            start_time=start_time,
            end_time=end_time,
            type="regular",
            status=VISIT_STATUS_IN_PROGRESS,
            source="manual",
            is_unplanned=True,
        )
        db.add(visit)
        # checkin の FK (visit_id) を満たすため commit 前に id を確定させる。
        await db.flush()
    # judge / 通知が患者名・座標を引けるよう解決済み患者を関係に載せる
    # (relationship は lazy="noload" = 生成直後も dup ガード経路も未ロード)。
    # ``set_committed_value`` は「読み込み済み」として入れるだけで SQL も dirty も
    # 発生させない (代入だと不要な履歴処理が走る)。
    set_committed_value(visit, "patient", patient)
    visit_id = visit.id

    checkin = await judge_checkin(db, visit, staff_id, payload, "arrival", now=now)
    await notify_checkin_mismatch(db, visit=visit, checkin=checkin)
    await notify_checkin_anomalies(db, visit=visit, checkin=checkin)
    await db.commit()
    return await _checkin_response(db, visit_id)


@router.post(
    "/{visit_id}/no-show",
    response_model=VisitRead,
    summary="未訪問記録 (理由必須) — staff (自分の visit)",
)
async def no_show_visit(
    visit_id: UUID,
    payload: CheckinCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict:
    # no_show は理由必須 (場所違いの強行記録とは異なり、未実施の説明を残す)。
    if payload.reason is None or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reason is required for no-show",
        )
    visit, staff_id = await _load_visit_for_checkin(db, visit_id, user)
    # no_show は visit.status を据置 (planned のまま; モニターが時間ベースで判定)。
    await judge_checkin(db, visit, staff_id, payload, "no_show")
    await db.commit()
    return await _checkin_response(db, visit_id)
