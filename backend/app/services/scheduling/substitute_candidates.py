"""急休の代替候補エンジン (read-only).

正典 = ``docs/plans/week-cockpit-design.md`` §2-1 (Phase E / BE-1・2026-08-22 追補済)。
調査 = ``docs/plans/week-cockpit-investigation.md`` §4。

「この人がこの日休む」を入力に、その日の担当訪問を **コース単位** で束ね、
束ごとに「代わりに入れる人」を ◎(ok) / △(warn) / ×(ng) で列挙する。

判定は既存エンジンと **同じ関数** を流用する (ロジック複製禁止):
    - ハード制約: ``layer3_assignment`` の ``load_active_staff`` /
      ``_staff_satisfies_gender`` / ``_staff_satisfies_ng`` /
      ``_has_event_overlap_with_buffer`` / ``StaffInfo.effective_office_for_weekday``
    - 時間重なり: ``pfv_validator._find_conflict`` (移動時間 + バッファ・同住所ペア免除)
    - 同行拘束: ``accompaniment.load_accompaniment_visits_by_staff``
    - 継続性: ``layer3_assignment`` の患者ローテ段階ペナルティ定数
      (``_PATIENT_RECENT_PENALTY_BY_INDEX``) と ``_load_patient_recent_staff``

DB は SELECT のみ (書込なし)。付替の実行は既存 API
(``PATCH /courses/{id}`` / ``POST /schedule/v2/visit-assign-staff-week``) が担う。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date as date_cls
from datetime import datetime, timedelta
from datetime import time as time_cls
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff, StaffEvent, StaffWeeklyOverride
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.schemas.substitute import (
    SubstituteAbsentStaff,
    SubstituteCandidate,
    SubstituteCandidatesResponse,
    SubstituteGroup,
    SubstituteReason,
    SubstituteVisit,
)
from app.services.accompaniment import load_accompaniment_visits_by_staff
from app.services.scheduling.config import SchedulingConfig, load_scheduling_config
from app.services.scheduling.layer3_assignment import (
    _PATIENT_RECENT_PENALTY_BY_INDEX,
    CourseAssignmentTarget,
    Layer3Assigner,
    VisitTimeSlot,
    _has_event_overlap_with_buffer,
    _staff_satisfies_gender,
    _staff_satisfies_ng,
)
from app.services.scheduling.pfv_validator import _find_conflict
from app.services.scheduling.proposal_solver import ExistingVisit

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_NG = "ng"

# 整列順 (設計書 §2-1: status ok→warn→ng → score desc)
_STATUS_RANK: dict[str, int] = {STATUS_OK: 0, STATUS_WARN: 1, STATUS_NG: 2}

CODE_OFF = "off"
CODE_NG_STAFF = "ng_staff"
CODE_GENDER = "gender"
CODE_TRAINEE = "trainee"
CODE_OFFICE = "office"
CODE_EVENT_OVERLAP = "event_overlap"
CODE_TIME_OVERLAP = "time_overlap"
CODE_NOT_WORKING_DAY = "not_working_day"
CODE_ACCOMPANIMENT = "accompaniment"

# ng に落とす code (= ハード制約). 残り (event_overlap / time_overlap /
# accompaniment) は warn。
_NG_CODES: frozenset[str] = frozenset(
    {CODE_OFF, CODE_NOT_WORKING_DAY, CODE_NG_STAFF, CODE_GENDER, CODE_TRAINEE, CODE_OFFICE}
)

# score のレンジ調整。継続性は Layer3 の患者ローテ段階ペナルティ定数
# (1e6 / 5e5 / 2e5) を患者ごとに **合算** し、この除数で実用的な桁に落とす
# (直近1回前 = 1000pt / 2回前 = 500pt / 3回前 = 200pt)。
SCORE_CONTINUITY_SCALE: float = 1000.0
# その日すでに持っている訪問 1 件あたりの減点 (負荷 -)。
SCORE_LOAD_PENALTY: float = 50.0

# 現場向け文言 (FE の SEX_RESTRICTION_LABELS と同じ日本語)。
_SEX_RESTRICTION_JP: dict[str, str] = {
    "female_only": "女性のみ",
    "male_only": "男性のみ",
}

# course_id が無い訪問 (臨時 / 未所属) をまとめる束のラベル。
UNGROUPED_LABEL = "臨時・未所属"

# course_id が無い束に対して ``CourseAssignmentTarget`` を組むためのダミー ID.
# 判定にも表示にも使わない (``target.course_id`` は参照しない) が、dataclass が
# UUID を要求するため固定値を置く。
_SYNTHETIC_COURSE_ID = UUID("00000000-0000-0000-0000-000000000000")


class SubstituteCandidatesError(Exception):
    """サービス層から HTTP 層へ伝播する業務エラー (4xx に翻訳される)."""

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DayVisit:
    """当日の訪問 1 件 + 時間重なり判定に必要な座標 (座標は欠損しうる)."""

    visit: Visit
    patient_id: UUID
    patient_name: str
    lat: float | None
    lng: float | None

    @property
    def start_time(self) -> time_cls:
        return self.visit.start_time

    @property
    def end_time(self) -> time_cls:
        return self.visit.end_time


def _hhmm(t: time_cls) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _coord(value: object) -> float | None:
    """``Numeric`` 列 (Decimal) / float / None を float | None に正規化."""
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


def _minutes(start: time_cls, end: time_cls) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def _plain_overlap(a: _DayVisit, b: _DayVisit) -> bool:
    """素の半開区間交差 (``a.start < b.end AND b.start < a.end``).

    **座標が欠損している側の保守的フォールバック**。同住所免除も移動時間も判定
    できないため、区間が触れていれば重なりとして扱う (``accompaniment._overlaps``
    が座標欠損時に免除しないのと同じ方針・(0,0) 補完で衝突を消さない)。
    """
    return a.start_time < b.end_time and b.start_time < a.end_time


def _to_existing_visit(dv: _DayVisit) -> ExistingVisit:
    """``_find_conflict`` に渡す純データへ (座標が揃っているものだけ渡すこと)."""
    lat = dv.lat
    lng = dv.lng
    if lat is None or lng is None:  # pragma: no cover - 呼び出し側で保証
        raise ValueError("_to_existing_visit requires lat/lng")
    return ExistingVisit(
        start_time=dv.start_time,
        end_time=dv.end_time,
        lat=lat,
        lng=lng,
        service_minutes=max(0, _minutes(dv.start_time, dv.end_time)),
        patient_id=str(dv.patient_id),
    )


def _find_time_conflict(
    target: _DayVisit,
    others: list[_DayVisit],
    *,
    config: SchedulingConfig,
) -> _DayVisit | None:
    """``target`` が ``others`` の誰かと時間的にぶつかるなら、その相手を返す.

    座標が揃っている相手は ``pfv_validator._find_conflict`` (= 提案ソルバ /
    自動割当と同一規則: 移動時間 + バッファーの分離・同住所ペア免除・同一患者は
    対象外) で判定する。どちらかの座標が欠けている組み合わせは ``_plain_overlap``
    で保守的に判定する (= 免除しない。(0,0) 補完で衝突が消えるのを避ける)。
    """
    ordered = sorted(others, key=lambda o: (o.start_time, o.end_time, str(o.visit.id)))
    if target.lat is not None and target.lng is not None:
        geo = [o for o in ordered if o.lat is not None and o.lng is not None]
        plain = [o for o in ordered if o.lat is None or o.lng is None]
        if geo:
            by_ev: dict[int, _DayVisit] = {}
            others_ev: list[ExistingVisit] = []
            for o in geo:
                ev = _to_existing_visit(o)
                others_ev.append(ev)
                by_ev[id(ev)] = o
            hit = _find_conflict(_to_existing_visit(target), others_ev, config=config)
            if hit is not None:
                return by_ev[id(hit)]
    else:
        plain = ordered
    for o in plain:
        if _plain_overlap(target, o):
            return o
    return None


def _sex_restriction_label(restrictions: frozenset[str]) -> str:
    labels = [_SEX_RESTRICTION_JP.get(r, r) for r in sorted(restrictions)]
    return " / ".join(labels)


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


async def build_substitute_candidates(
    db: AsyncSession,
    *,
    staff_id: UUID,
    target_date: date_cls,
    course_id: UUID | None = None,
) -> SubstituteCandidatesResponse:
    """``staff_id`` が ``target_date`` に抜けたときの代替候補を組み立てる (read-only).

    Args:
        db: 共有 AsyncSession (SELECT のみ).
        staff_id: 休む (= 抜ける) スタッフ.
        target_date: 対象日.
        course_id: 指定するとそのコースの束だけを返す.

    Returns:
        ``SubstituteCandidatesResponse``.

    Raises:
        SubstituteCandidatesError: スタッフ / コースが存在しない (404).
    """
    absent = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if absent is None:
        raise SubstituteCandidatesError("Staff not found", http_status=404)
    if course_id is not None:
        live_course = await db.scalar(
            select(Course.id).where(Course.id == course_id, Course.deleted_at.is_(None))
        )
        if live_course is None:
            raise SubstituteCandidatesError("Course not found", http_status=404)

    weekday = target_date.weekday()
    week_monday = target_date - timedelta(days=weekday)
    iso_year, iso_week, _ = target_date.isocalendar()

    warnings: list[str] = []

    # ---------- 1. その日の全訪問 (cancelled / deleted は対象外) ----------
    day_rows = (
        await db.execute(
            select(Visit, Course, Patient)
            # 削除済み (soft-delete) 患者の訪問は候補計算にも表示にも含めない。
            .join(Patient, (Patient.id == Visit.patient_id) & (Patient.deleted_at.is_(None)))
            .outerjoin(
                Course,
                (Course.id == Visit.course_id) & (Course.deleted_at.is_(None)),
            )
            .where(
                Visit.visit_date == target_date,
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
            )
        )
    ).all()

    def _owner_of(visit: Visit, course: Course | None) -> UUID | None:
        """盤面 (StaffWeekBoard) の行決定と同じ規則: primary_staff_id 優先 → コース担当."""
        if visit.primary_staff_id is not None:
            return visit.primary_staff_id
        if course is not None:
            return course.assigned_staff_id
        return None

    def _day_visit(visit: Visit, patient: Patient) -> _DayVisit:
        return _DayVisit(
            visit=visit,
            patient_id=patient.id,
            patient_name=patient.name,
            lat=_coord(patient.lat),
            lng=_coord(patient.lng),
        )

    # 候補スタッフの当日負荷 / 時間重なり判定に使う「staff_id -> 当日の訪問」
    visits_by_owner: dict[UUID, list[_DayVisit]] = {}
    day_visit_by_id: dict[UUID, _DayVisit] = {}
    for visit, course, patient in day_rows:
        dv = _day_visit(visit, patient)
        day_visit_by_id[visit.id] = dv
        owner = _owner_of(visit, course)
        if owner is not None:
            visits_by_owner.setdefault(owner, []).append(dv)

    # ---------- 2. 対象訪問 (= 抜ける人の担当) をコース単位に束ねる ----------
    target_rows = [row for row in day_rows if _owner_of(row[0], row[1]) == staff_id]
    if course_id is not None:
        target_rows = [row for row in target_rows if row[0].course_id == course_id]

    if not target_rows:
        note = (
            f"指定されたコースには、{absent.name} さんのこの日の担当訪問がありません。"
            if course_id is not None
            else f"{absent.name} さんはこの日に担当の訪問がありません。"
        )
        return SubstituteCandidatesResponse(
            absent_staff=SubstituteAbsentStaff(id=absent.id, name=absent.name),
            date=target_date,
            weekday=weekday,
            groups=[],
            warnings=[note],
        )

    grouped: dict[UUID | None, list[tuple[Visit, Course | None, Patient]]] = {}
    for visit, course, patient in target_rows:
        grouped.setdefault(visit.course_id if course is not None else None, []).append(
            (visit, course, patient)
        )
    for rows in grouped.values():
        rows.sort(key=lambda r: (r[0].start_time, str(r[0].id)))

    # 束の並び: コース code 昇順 → 臨時/未所属は最後。
    def _group_sort_key(item: tuple[UUID | None, list]) -> tuple[int, str, str]:
        cid, rows = item
        if cid is None:
            return (1, "", "")
        course = rows[0][1]
        return (0, course.code if course is not None else "", str(cid))

    ordered_groups = sorted(grouped.items(), key=_group_sort_key)

    # ---------- 3. 患者の NG スタッフ (束の union) ----------
    all_patient_ids = {p.id for _v, _c, p in target_rows}
    ng_rows = (
        await db.execute(
            select(PatientNgStaff.patient_id, PatientNgStaff.staff_id).where(
                PatientNgStaff.patient_id.in_(list(all_patient_ids))
            )
        )
    ).all()
    ng_by_patient: dict[UUID, set[UUID]] = {}
    for pid, sid in ng_rows:
        ng_by_patient.setdefault(pid, set()).add(sid)

    # ---------- 4. 候補スタッフ母集団 (Layer3 と同一ソース) ----------
    assigner = Layer3Assigner()
    staff_pool = [
        s
        for s in await assigner.load_active_staff(
            db, iso_year=iso_year, iso_week=iso_week, week_monday=week_monday
        )
        if s.staff_id != staff_id
    ]
    if not staff_pool:
        warnings.append("稼働スタッフが登録されていません。")
    pool_ids = [s.staff_id for s in staff_pool]

    # 「休み」と「そもそも非勤務日」を区別するための 1 クエリ。
    # (``load_active_staff`` は off を work_days から引き算済みで理由を残さない。
    #  新人フラグは ``StaffInfo.is_trainee`` に入っているので再引きしない。)
    off_staff_ids = set(
        (
            await db.scalars(
                select(StaffWeeklyOverride.staff_id).where(
                    StaffWeeklyOverride.iso_year == iso_year,
                    StaffWeeklyOverride.iso_week == iso_week,
                    StaffWeeklyOverride.weekday == weekday,
                    StaffWeeklyOverride.override_type == "off",
                    StaffWeeklyOverride.staff_id.in_(pool_ids),
                )
            )
        ).all()
    )

    # ---------- 5. その日のイベント (cancelled は除外) ----------
    events_by_staff = await _load_events_for_date(db, staff_ids=pool_ids, target_date=target_date)

    # ---------- 6. その日の同行 (メンター) 拘束 ----------
    # 患者は当日分をまとめて引いた ``day_rows`` から引き当てる (同じ日・同じ除外条件
    # なので必ず載っている。ORM の identity map 都合で eager-load が効かない場合が
    # あるため ``Visit.patient`` には依存しない)。
    accompaniment_by_staff: dict[UUID, list[_DayVisit]] = {}
    for sid, acc_visits in (
        await load_accompaniment_visits_by_staff(db, staff_ids=pool_ids, dates=[target_date])
    ).items():
        for av in acc_visits:
            dv = day_visit_by_id.get(av.id)
            if dv is not None:
                accompaniment_by_staff.setdefault(sid, []).append(dv)

    # ---------- 7. 拠点名 (表示用) / 移動時間の設定 ----------
    office_names = {
        oid: name for oid, name in (await db.execute(select(Office.id, Office.name))).all()
    }
    config = await load_scheduling_config(db)

    # ---------- 8. 継続性 (馴染み) の素 — Layer3 と同一ソース ----------
    patient_recent_staff = await assigner._load_patient_recent_staff(db, week_monday=week_monday)

    groups: list[SubstituteGroup] = []
    for cid, rows in ordered_groups:
        course = rows[0][1]
        course_label = f"{course.code}コース" if course is not None else UNGROUPED_LABEL

        gender_restrictions = frozenset(
            p.sex_restriction for _v, _c, p in rows if p.sex_restriction
        )
        ng_union: set[UUID] = set()
        ng_by_patient_in_group: dict[UUID, frozenset[UUID]] = {}
        for _v, _c, p in rows:
            pids = ng_by_patient.get(p.id)
            if pids:
                ng_union |= pids
                ng_by_patient_in_group[p.id] = frozenset(pids)

        target = CourseAssignmentTarget(
            course_id=cid if cid is not None else _SYNTHETIC_COURSE_ID,
            weekday=weekday,
            course_code=course.code if course is not None else "-",
            centroid_lat=None,
            centroid_lng=None,
            gender_restrictions=gender_restrictions,
            patient_ids=[p.id for _v, _c, p in rows],
            visits=[
                VisitTimeSlot(start_time=v.start_time, end_time=v.end_time) for v, _c, _p in rows
            ],
            office_id=course.office_id if course is not None else None,
            ng_staff_ids=frozenset(ng_union),
            ng_staff_by_patient=ng_by_patient_in_group,
        )

        group_day_visits = [_day_visit(v, p) for v, _c, p in rows]
        visit_by_patient = {p.id: v for v, _c, p in rows}

        candidates: list[SubstituteCandidate] = []
        for staff in staff_pool:
            reasons: list[SubstituteReason] = []

            # --- ハード: 休み / 非勤務日 ---
            if weekday not in staff.work_days:
                if staff.staff_id in off_staff_ids:
                    reasons.append(
                        SubstituteReason(code=CODE_OFF, message="この日は休みの登録があります")
                    )
                else:
                    reasons.append(
                        SubstituteReason(
                            code=CODE_NOT_WORKING_DAY, message="この曜日は勤務日ではありません"
                        )
                    )

            # --- ハード: 新人 ---
            # 新人はコースを持たない運用 (PO確定)。訪問単位の付替 API も 422 で弾く。
            if staff.is_trainee:
                reasons.append(
                    SubstituteReason(
                        code=CODE_TRAINEE,
                        message="新人は単独で担当にできません（同行で割り当ててください）",
                    )
                )

            # --- ハード: NG スタッフ ---
            if not _staff_satisfies_ng(staff, target):
                for pid, ng_ids in ng_by_patient_in_group.items():
                    if staff.staff_id in ng_ids:
                        pname = next(p.name for _v, _c, p in rows if p.id == pid)
                        reasons.append(
                            SubstituteReason(
                                code=CODE_NG_STAFF,
                                message=f"{pname} さんの NG スタッフに指定されています",
                                visit_id=visit_by_patient[pid].id,
                            )
                        )

            # --- ハード: 性別制限 (理由は制限を持つ患者の訪問を指す) ---
            if not _staff_satisfies_gender(staff, target):
                for _v, _c, p in rows:
                    if not p.sex_restriction:
                        continue
                    one = replace(target, gender_restrictions=frozenset({p.sex_restriction}))
                    if _staff_satisfies_gender(staff, one):
                        continue
                    reasons.append(
                        SubstituteReason(
                            code=CODE_GENDER,
                            message=(
                                f"{p.name} さんの性別制限"
                                f"（{_sex_restriction_label(one.gender_restrictions)}）"
                                "に適合しません"
                            ),
                            visit_id=visit_by_patient[p.id].id,
                        )
                    )

            # --- ハード: 拠点 ---
            if target.office_id is not None:
                eff = staff.effective_office_for_weekday(weekday)
                if eff != target.office_id:
                    reasons.append(
                        SubstituteReason(
                            code=CODE_OFFICE,
                            message=(
                                "拠点が異なります"
                                f"（このコースは {office_names.get(target.office_id, '別拠点')}）"
                            ),
                        )
                    )

            # --- 警告: イベント重なり (±15分バッファ込み) ---
            if _has_event_overlap_with_buffer(
                staff_id=staff.staff_id,
                course=target,
                weekday=weekday,
                events_by_staff=events_by_staff,
                week_monday=week_monday,
            ):
                reasons.append(
                    SubstituteReason(
                        code=CODE_EVENT_OVERLAP,
                        message="この時間帯に個別業務（イベント）が入っています",
                    )
                )

            # --- 警告: 本人の別訪問 / 同行との時間重なり ---
            own_visits = visits_by_owner.get(staff.staff_id, [])
            acc_visits = accompaniment_by_staff.get(staff.staff_id, [])
            for tgt in group_day_visits:
                clash = _find_time_conflict(tgt, own_visits, config=config)
                if clash is not None:
                    reasons.append(
                        SubstituteReason(
                            code=CODE_TIME_OVERLAP,
                            message=(
                                f"{_hhmm(clash.start_time)}-{_hhmm(clash.end_time)} "
                                f"{clash.patient_name} さんの訪問と重なります"
                            ),
                            visit_id=tgt.visit.id,
                        )
                    )
                acc_clash = _find_time_conflict(tgt, acc_visits, config=config)
                if acc_clash is not None:
                    reasons.append(
                        SubstituteReason(
                            code=CODE_ACCOMPANIMENT,
                            message=(
                                f"{_hhmm(acc_clash.start_time)}-{_hhmm(acc_clash.end_time)} "
                                f"{acc_clash.patient_name} さんの同行が入っています"
                            ),
                            visit_id=tgt.visit.id,
                        )
                    )

            codes = {r.code for r in reasons}
            if codes & _NG_CODES:
                cand_status = STATUS_NG
            elif codes:
                cand_status = STATUS_WARN
            else:
                cand_status = STATUS_OK

            load_today = len(own_visits)
            score = _score_for(
                staff_id=staff.staff_id,
                patient_ids=target.patient_ids,
                patient_recent_staff=patient_recent_staff,
                load_today=load_today,
            )

            eff_office = staff.effective_office_for_weekday(weekday)
            candidates.append(
                SubstituteCandidate(
                    staff_id=staff.staff_id,
                    name=staff.name,
                    sex=staff.sex,
                    office_name=office_names.get(eff_office) if eff_office else None,
                    status=cand_status,
                    reasons=reasons,
                    score=score,
                    load_today=load_today,
                )
            )

        candidates.sort(key=lambda c: (_STATUS_RANK[c.status], -c.score, c.name, str(c.staff_id)))
        groups.append(
            SubstituteGroup(
                course_id=cid,
                course_label=course_label,
                visits=[
                    SubstituteVisit(
                        visit_id=v.id,
                        patient_id=p.id,
                        patient_name=p.name,
                        start_time=_hhmm(v.start_time),
                        end_time=_hhmm(v.end_time),
                        week_pinned=bool(v.week_pinned),
                        status=v.status,
                    )
                    for v, _c, p in rows
                ],
                candidates=candidates,
            )
        )
        if not any(c.status == STATUS_OK for c in candidates):
            warnings.append(f"{course_label}: そのまま入れる候補（◎）がいません。")

    return SubstituteCandidatesResponse(
        absent_staff=SubstituteAbsentStaff(id=absent.id, name=absent.name),
        date=target_date,
        weekday=weekday,
        groups=groups,
        warnings=warnings,
    )


def _score_for(
    *,
    staff_id: UUID,
    patient_ids: list[UUID],
    patient_recent_staff: dict[UUID, list[UUID]],
    load_today: int,
) -> float:
    """継続性 (+) - 負荷 (-) の推奨スコア. 高いほど推奨.

    継続性は「その患者を最近担当した人ほど高い」。Layer3 の
    ``_PATIENT_RECENT_PENALTY_BY_INDEX`` (直近 1/2/3 回前) を単一ソースに使うが、
    **符号の向きが逆** である点が Layer3 との違い: 毎週の自動割付では「同じ患者に
    毎回同じ人」を避けるための減点、急な休みの代替では「馴染みの人ほど望ましい」
    ための加点。束に複数患者が居る場合は Layer3 の max ではなく **合算** する
    (= 馴染みの患者が多いほど良い代替・2026-08-22 レビュー確定)。

    移動項は Phase G-90 で ``_cost_single_cell`` から撤去済みのため無し。
    Layer3 の決定的ジッタ (``_deterministic_random``) は「同じ人ばかり当たるのを
    崩す」ための揺らぎであり、人が読む推奨順には混ぜない。
    """
    continuity = 0.0
    for pid in patient_ids:
        recent = patient_recent_staff.get(pid)
        if not recent:
            continue
        for idx, sid in enumerate(recent):
            if sid == staff_id:
                continuity += _PATIENT_RECENT_PENALTY_BY_INDEX.get(idx, 0.0)
                break
    return round(continuity / SCORE_CONTINUITY_SCALE - SCORE_LOAD_PENALTY * load_today, 3)


async def _load_events_for_date(
    db: AsyncSession,
    *,
    staff_ids: list[UUID],
    target_date: date_cls,
) -> dict[UUID, list[StaffEvent]]:
    """対象日に掛かる ``staff_events`` を staff 別に返す (cancelled は SQL 側で除外).

    ``staff_events.cancelled_at`` (mig 0075) が入っている行 = 「今週だけ外した」
    イベントなので、拘束の判定から外す。
    """
    if not staff_ids:
        return {}
    range_start = datetime.combine(target_date, time_cls(0, 0))
    range_end = datetime.combine(target_date + timedelta(days=1), time_cls(0, 0))
    rows = list(
        (
            await db.scalars(
                select(StaffEvent).where(
                    StaffEvent.staff_id.in_(staff_ids),
                    StaffEvent.starts_at < range_end,
                    StaffEvent.ends_at >= range_start,
                    StaffEvent.cancelled_at.is_(None),
                )
            )
        ).all()
    )
    result: dict[UUID, list[StaffEvent]] = {}
    for ev in rows:
        result.setdefault(ev.staff_id, []).append(ev)
    return result
