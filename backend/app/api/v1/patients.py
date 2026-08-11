"""Patient CRUD endpoints (v2 schema, W1-BE1; W7-BE1 RBAC hardening).

設計仕様書 v0.9 §4.1 / API 契約 v0.1 §1 に対応する v2 endpoints。

* リクエスト/レスポンスは ``app.schemas.v2.patient`` の型を使用 (re-export 経由)。
* RBAC (W7-BE1 で強化, Codex Must-fix #1):
    - GET (list)           — admin / manager は全件、staff は自分の担当患者のみ
    - GET (detail)         — admin / manager は任意、staff は担当患者のみ (範囲外は 404)
    - POST / PATCH         — admin / manager
    - DELETE (soft delete) — admin only

  Staff の「担当患者」は以下のいずれかを満たす患者:
    1. ``visits.primary_staff_id`` / ``secondary_staff_id`` / ``mentor_staff_id``
       が当該 staff の (v1 互換)
    2. ``visit_staff_assignments`` テーブルに当該 staff の行がある visit
       (v2 正規; W2-BE4 で導入)
  ``visits.deleted_at IS NULL`` のものに限定する。
* 旧フィールド (``age``, ``ng_time_start``, ``ng_time_end``, ``required_staff_count``,
  ``area``, ``ng_staff_ids``, ``preferred_staff_ids``, ``specified_type``,
  ``continuous_request``) は v2 schema が ``extra='ignore'`` で受理するため、
  旧クライアントからの送信は静かに無視される。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.patient import Patient
from app.models.patient_ng_staff import PatientNgStaff
from app.models.patient_same_address_link import PatientSameAddressLink
from app.models.user import User, normalize_user_role
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.patient import PatientCreate, PatientRead, PatientUpdate
from app.schemas.patient_same_address_link import SameAddressCandidate
from app.services.geocoding.hash import normalize_address
from app.services.patient_code import generate_next_patient_code
from app.services.scheduling.auto_allocator_v2 import SAME_ADDRESS_TOLERANCE, _address_bucket

# Phase G-86: 自動採番した code が UNIQUE 衝突したときの再採番上限。
_PATIENT_CODE_RETRY_MAX = 5

router = APIRouter()


async def _commit_or_409(db) -> None:
    """Commit and translate IntegrityError into a stable HTTP response.

    `unique`/`duplicate` -> 409 Conflict; other FK/check errors -> 422.
    """
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate value",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc


def _model_dump_for_orm(payload: PatientCreate | PatientUpdate, *, partial: bool) -> dict:
    """Serialise a v2 schema into a dict suitable for SQLAlchemy assignment.

    * Pydantic validators may emit ``WeeklyPatternV2`` instances; the ORM
      JSONB columns expect ``dict``. ``model_dump(mode='json')`` ensures
      everything goes through Pydantic JSON serialisation, which yields
      a plain dict tree we can hand off to JSONB.
    * ``primary_office_id`` (UUID) is preserved as a UUID object — JSON
      mode would coerce it to string, which the ORM column would then
      reject. We revert just that field manually.
    """
    data = payload.model_dump(
        mode="json",
        exclude_unset=partial,
        exclude_none=False,
    )
    # UUID columns: model_dump(mode='json') stringifies. Map back to UUID
    # so SQLAlchemy receives the right type.
    if "primary_office_id" in data and data["primary_office_id"] is not None:
        try:
            data["primary_office_id"] = UUID(str(data["primary_office_id"]))
        except (ValueError, TypeError):
            data["primary_office_id"] = None
    # ``special_week_active`` is a list[dict] (already JSON-mode-friendly).
    return data


def _staff_patient_ids_subquery(staff_id: UUID):
    """Return a scalar subquery yielding patient_ids visible to the given staff.

    A patient is visible to staff if they have any (non-deleted) visit where the
    staff is primary/secondary/mentor (v1 互換) **or** the staff has a row in
    ``visit_staff_assignments`` for that visit (v2 正規; §4.5).
    """
    assigned_visit_ids = select(VisitStaffAssignment.visit_id).where(
        VisitStaffAssignment.staff_id == staff_id
    )
    return (
        select(Visit.patient_id)
        .where(
            Visit.deleted_at.is_(None),
            or_(
                Visit.primary_staff_id == staff_id,
                Visit.secondary_staff_id == staff_id,
                Visit.mentor_staff_id == staff_id,
                Visit.id.in_(assigned_visit_ids),
            ),
        )
        .distinct()
    )


def _ng_staff_count_expr():
    """患者 1 行ごとの NG スタッフ件数を返す相関スカラーサブクエリ.

    一覧 GET で 1 クエリのまま件数を載せるための集計 (N+1 禁止・設計書 §4-1)。
    ``patients`` の各行に相関するため、外側の SELECT にそのまま同梱できる。
    """
    return (
        select(func.count())
        .select_from(PatientNgStaff)
        .where(PatientNgStaff.patient_id == Patient.id)
        .correlate(Patient)
        .scalar_subquery()
    )


async def _attach_ng_staff_count(db, patient: Patient) -> Patient:
    """単票経路 (detail / create / update) で ``ng_staff_count`` を載せる.

    ``Patient`` ORM に対応する列は無く、``PatientRead.ng_staff_count`` は
    from_attributes でこの動的属性を読む (未設定なら default 0)。
    """
    count = await db.scalar(
        select(func.count())
        .select_from(PatientNgStaff)
        .where(PatientNgStaff.patient_id == patient.id)
    )
    patient.ng_staff_count = int(count or 0)  # type: ignore[attr-defined]
    return patient


@router.get("", response_model=list[PatientRead], summary="List patients")
async def list_patients(
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Patient]:
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    # 登録ナンバー (code) 昇順で常に固定表示。code 未設定は末尾、同コードは登録順で安定化。
    # NG スタッフ件数は相関サブクエリで同梱する (追加クエリを撃たない)。
    stmt = (
        select(Patient, _ng_staff_count_expr().label("ng_staff_count"))
        .where(Patient.deleted_at.is_(None))
        .order_by(Patient.code.asc().nulls_last(), Patient.created_at.asc())
    )
    if user.role == "staff":
        # Staff sees only patients they are assigned to via visits.
        # If the staff has no linked staff_id, return an empty page (defensive).
        if user.staff_id is None:
            return []
        stmt = stmt.where(Patient.id.in_(_staff_patient_ids_subquery(user.staff_id)))
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()

    out: list[Patient] = []
    for patient, ng_count in rows:
        patient.ng_staff_count = int(ng_count or 0)  # type: ignore[attr-defined]
        out.append(patient)
    return out


@router.get("/{patient_id}", response_model=PatientRead, summary="Get patient by id")
async def get_patient(
    patient_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> Patient:
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if user.role == "staff":
        # Staff: only own patients. Return 404 (not 403) to avoid leaking
        # the existence of patients outside their scope.
        if user.staff_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        in_scope = await db.scalar(
            select(Patient.id)
            .where(
                Patient.id == patient_id,
                Patient.id.in_(_staff_patient_ids_subquery(user.staff_id)),
            )
            .limit(1)
        )
        if in_scope is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _attach_ng_staff_count(db, patient)


@router.post(
    "",
    response_model=PatientRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create patient",
)
async def create_patient(
    payload: PatientCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> Patient:
    data = _model_dump_for_orm(payload, partial=False)

    # Phase G-86: code が空 / None なら自動採番する。
    # 手入力 code はそのまま使い、衝突時は従来どおり 409 (_commit_or_409)。
    auto_code = not data.get("code")
    if not auto_code:
        patient = Patient(**data)
        db.add(patient)
        await _commit_or_409(db)
        await db.refresh(patient)
        return patient

    # 自動採番経路: UNIQUE 衝突 (並行採番) 時は採番からやり直す。
    last_exc: IntegrityError | None = None
    for _ in range(_PATIENT_CODE_RETRY_MAX):
        data["code"] = await generate_next_patient_code(db)
        patient = Patient(**data)
        db.add(patient)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            last_exc = exc
            continue
        await db.refresh(patient)
        return patient

    # リトライ上限到達 (高頻度の並行採番衝突)。409 で返す。
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Conflict: failed to allocate a unique patient code",
    ) from last_exc


@router.patch("/{patient_id}", response_model=PatientRead, summary="Update patient")
async def update_patient(
    patient_id: UUID,
    payload: PatientUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> Patient:
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    data = _model_dump_for_orm(payload, partial=True)
    for field, value in data.items():
        setattr(patient, field, value)
    await _commit_or_409(db)
    await db.refresh(patient)
    # refresh で動的属性が落ちるため、返す直前に載せ直す。
    return await _attach_ng_staff_count(db, patient)


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete patient (admin only)",
)
async def delete_patient(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """Soft-delete patient.

    M3: 同住所紐付け行 (:class:`PatientSameAddressLink`) は a/b 両側について物理削除する.
    DB 上 FK ON DELETE CASCADE が付いていないため、reviewer 指摘に従い endpoint で
    明示削除する (= soft delete patient を re-create した時に古い link が復活しない
    ことを保証).

    NG スタッフ行 (:class:`PatientNgStaff`) も同じ理由で物理削除する
    (soft delete では FK CASCADE が発火しない — 同住所リンクと同じ既知罠).
    """
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    patient.deleted_at = func.now()

    # M3: 同住所紐付けを a/b 両側について物理削除.
    link_rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                or_(
                    PatientSameAddressLink.patient_a_id == patient_id,
                    PatientSameAddressLink.patient_b_id == patient_id,
                )
            )
        )
    ).all()
    for link in link_rows:
        await db.delete(link)

    # NG スタッフ行を物理削除 (soft delete では FK CASCADE が発火しないため).
    await db.execute(delete(PatientNgStaff).where(PatientNgStaff.patient_id == patient_id))

    await db.commit()
    return None


# ---------------------------------------------------------------------------
# Phase G-21 T2: 同住所候補取得
# ---------------------------------------------------------------------------


@router.get(
    "/{patient_id}/same-address-candidates",
    response_model=list[SameAddressCandidate],
    summary="Phase G-21: 同住所候補 (同 address_bucket かつ住所文字列一致) 取得",
)
async def get_same_address_candidates(
    patient_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> list[SameAddressCandidate]:
    """同住所候補 (= ``_address_bucket`` 同一かつ ``address`` 文字列一致の他患者) を返す.

    各候補について :class:`PatientSameAddressLink` から ``pair_mode`` を引く. 行が
    無ければ ``"preferred"`` (= デフォルト) を返す.

    RBAC: admin / manager / staff (read). staff は role チェックのみ (= patient 単位
    の絞り込みは行わない. UI 側の表示制御に委ねる).
    """
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    base = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # 座標 / 住所文字列のいずれかが欠けていれば候補なし.
    if base.lat is None or base.lng is None or not base.address:
        return []

    base_lat = float(base.lat)
    base_lng = float(base.lng)
    base_bucket = _address_bucket(base_lat, base_lng)
    # M2: NFKC + whitespace collapse + strip 正規化 (全半角/末尾空白による
    # false-negative 回避). 比較は正規化後の文字列同士で行う.
    base_address_norm = normalize_address(base.address)

    # H4: 全 active 患者ロードを避けるため SQL 側で lat/lng range で一次絞り込み.
    # bucket 量子化は ``round(x/T)*T`` のため、bucket 中心 ± T/2 を厳密に含むのは
    # [-T/2, +T/2). 浮動小数誤差 + 隣接 bucket の境界をカバーするため ± T (~100m)
    # で広めに引き、Python 側で bucket equality を最終確認する.
    # address は SQL 側で `==` (= 文字列一致した行のみ) と NFKC 正規化一致 (= 全角
    # 半角 / 末尾空白の混在ケース) の OR で絞り込みたいが、SQL 関数が dialect 依存
    # (PostgreSQL / SQLite テスト両対応) になるため文字列等値のみ SQL でかけ、
    # Python 側で NFKC 等値を最終確認する (raw 文字列一致なら通る + 正規化一致でも
    # 通る; 既存データの大半は同一文字列入力なので range クエリだけで十分絞れる).
    lat_min = base_lat - SAME_ADDRESS_TOLERANCE
    lat_max = base_lat + SAME_ADDRESS_TOLERANCE
    lng_min = base_lng - SAME_ADDRESS_TOLERANCE
    lng_max = base_lng + SAME_ADDRESS_TOLERANCE
    other_rows = (
        await db.scalars(
            select(Patient).where(
                Patient.id != patient_id,
                Patient.deleted_at.is_(None),
                Patient.status == "active",
                Patient.lat.is_not(None),
                Patient.lng.is_not(None),
                Patient.address.is_not(None),
                Patient.lat.between(lat_min, lat_max),
                Patient.lng.between(lng_min, lng_max),
            )
        )
    ).all()

    candidates: list[Patient] = []
    for other in other_rows:
        # _address_bucket は SAME_ADDRESS_TOLERANCE (= 0.001) で量子化済.
        if other.lat is None or other.lng is None:
            continue
        if _address_bucket(float(other.lat), float(other.lng)) != base_bucket:
            continue
        # M2: NFKC 正規化 + strip 後の equality で判定 (= 全角半角 / 末尾空白の
        # false-negative 回避).
        if other.address is None or normalize_address(other.address) != base_address_norm:
            continue
        candidates.append(other)

    if not candidates:
        return []

    # candidate ごとに PatientSameAddressLink を一括引き.
    candidate_ids = [c.id for c in candidates]
    link_rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                (
                    (PatientSameAddressLink.patient_a_id == patient_id)
                    & (PatientSameAddressLink.patient_b_id.in_(candidate_ids))
                )
                | (
                    (PatientSameAddressLink.patient_b_id == patient_id)
                    & (PatientSameAddressLink.patient_a_id.in_(candidate_ids))
                )
            )
        )
    ).all()
    link_by_other_id: dict[UUID, PatientSameAddressLink] = {}
    for link in link_rows:
        other_id = link.patient_b_id if link.patient_a_id == patient_id else link.patient_a_id
        link_by_other_id[other_id] = link

    out: list[SameAddressCandidate] = []
    for c in candidates:
        link_opt: PatientSameAddressLink | None = link_by_other_id.get(c.id)
        out.append(
            SameAddressCandidate(
                patient_id=c.id,
                patient_code=c.code,
                patient_name=c.name,
                address=c.address,
                pair_mode=link_opt.pair_mode if link_opt is not None else "preferred",
                decided_by_user_id=link_opt.decided_by_user_id if link_opt is not None else None,
                note=link_opt.note if link_opt is not None else None,
            )
        )
    return out
