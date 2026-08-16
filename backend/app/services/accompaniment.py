"""同行 (accompaniment) サービス — 設計 §5 / §6.4 / 一般化 §3.

正典設計書:
- `docs/plans/trainee-accompaniment-design.md` v1.1 (基盤・新人同行)
- `docs/plans/general-accompaniment-design.md` (一般化・mig 0072)

- ``expand_accompaniment_defaults``: 毎週の既定を週へ物質化する (冪等・週全体)。
  週生成 / 固定枠に戻す / 自動割当 の 3 地点から呼ぶ。孤立リンク掃除 (S-1) を同梱。
- 可視性ヘルパー: モバイル 3 経路 (§6.4 C-1) が同行訪問を「同行スタッフ本人の訪問」と
  みなすための条件 (visit 直リンク OR course リンク・live JOIN)。
- 射影ヘルパー: VisitRead / モニター / カイポケ CSV へ同行者を非破壊で載せる解決。
  一般化 決定#5 により **1 訪問に複数同行者**を認めるため、解決は全件返却
  (決定的順序) を正とし、単数しか表現できない口は先頭要素を採る。
- 重複判定ヘルパー: PUT の確定ブロック (時間重複 422) 用。一般化 決定#1 で
  **本人担当との重複**も同じ土俵で検査する (``load_own_duty_visits``)。

**唯一の正典** は本テーブル。読み出しは常に live JOIN (``courses.deleted_at IS NULL``)
で守り、``visits.*_staff_id`` / VSA には書かない (設計 §2)。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accompaniment import (
    ACCOMPANIMENT_KIND_SUPPORT,
    ACCOMPANIMENT_KIND_TRAINEE,
    Accompaniment,
    AccompanimentDefault,
)
from app.models.course import COURSE_STATUS_PROPOSED, Course
from app.models.course_template import CourseTemplate
from app.models.notification import Notification
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

# ---------------------------------------------------------------------------
# kind の自動判定 (一般化 §2)
# ---------------------------------------------------------------------------


def resolve_accompaniment_kind(staff: Staff) -> str:
    """同行リンクの ``kind`` をスタッフ属性から決める (**サーバ自動判定**).

    一般化 §2: ``kind`` は API 入力で受け取らない。詐称を防ぎ、「新人かどうか」の
    判定を ``staff.is_trainee`` 1 箇所に閉じるため、保存の直前にここで決める。
    """
    return ACCOMPANIMENT_KIND_TRAINEE if staff.is_trainee else ACCOMPANIMENT_KIND_SUPPORT


# ---------------------------------------------------------------------------
# 展開 (物質化) — 設計 §5.1
# ---------------------------------------------------------------------------


async def _cleanup_orphan_links(db: AsyncSession, iso_year: int, iso_week: int) -> int:
    """当該週の soft-delete 済み visit / course を参照する同行リンクを物理削除 (S-1).

    visit は soft-delete のため FK CASCADE が発火しない。読み出しは live JOIN で
    守られるが、蓄積を防ぐため物理削除する。返却 = 削除件数。
    """
    try:
        monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError:
        return 0
    sunday = monday + timedelta(days=6)

    removed = 0

    dead_course_ids = list(
        (
            await db.scalars(
                select(Course.id).where(
                    Course.iso_year == iso_year,
                    Course.iso_week == iso_week,
                    Course.deleted_at.is_not(None),
                )
            )
        ).all()
    )
    if dead_course_ids:
        res = await db.execute(
            delete(Accompaniment).where(Accompaniment.course_id.in_(dead_course_ids))
        )
        removed += int(res.rowcount or 0)

    dead_visit_ids = list(
        (
            await db.scalars(
                select(Visit.id).where(
                    Visit.visit_date >= monday,
                    Visit.visit_date <= sunday,
                    Visit.deleted_at.is_not(None),
                )
            )
        ).all()
    )
    if dead_visit_ids:
        res = await db.execute(
            delete(Accompaniment).where(Accompaniment.visit_id.in_(dead_visit_ids))
        )
        removed += int(res.rowcount or 0)

    return removed


async def expand_accompaniment_defaults(db: AsyncSession, iso_year: int, iso_week: int) -> int:
    """毎週の既定を当該週の同行リンクへ物質化する (冪等・週全体) — 設計 §5.1.

    - **冪等**: 既存リンク (staff × course) はスキップする。
    - **週全体**: defaults はスタッフ×曜日で高々数行のため常に全走査する
      (スコープ絞りのバグを構造的に避ける)。
    - 展開対象コース: ``template_id 一致 AND weekday 一致 AND course_status != 'proposed'
      AND deleted_at IS NULL``。proposed へはリンクを張らない (再算出 soft-delete で
      孤立するため)。defaults 側は ``course_templates.deleted_at IS NULL`` を常時適用。
    - 孤立リンク掃除 (S-1) を先に行う。
    - ``kind`` は既定行の値をそのまま引き継ぐ (既定を作った時点の自動判定結果)。

    ``commit`` はしない (``flush`` のみ)。呼び出し側 (週生成 / reset / 自動割当) が
    トランザクション境界を制御する。返却 = 新規に張ったリンク数。
    """
    await _cleanup_orphan_links(db, iso_year, iso_week)

    defaults = list(
        (
            await db.scalars(
                select(AccompanimentDefault)
                .join(
                    CourseTemplate,
                    AccompanimentDefault.course_template_id == CourseTemplate.id,
                )
                .where(CourseTemplate.deleted_at.is_(None))
            )
        ).all()
    )
    if not defaults:
        return 0

    # 既存リンク (staff, course) を 1 度だけロードして冪等判定に使う。
    existing_pairs: set[tuple[UUID, UUID]] = set(
        (
            await db.execute(
                select(
                    Accompaniment.accompanying_staff_id,
                    Accompaniment.course_id,
                ).where(Accompaniment.course_id.is_not(None))
            )
        ).all()
    )

    created = 0
    for d in defaults:
        courses = list(
            (
                await db.scalars(
                    select(Course).where(
                        Course.iso_year == iso_year,
                        Course.iso_week == iso_week,
                        Course.weekday == d.weekday,
                        Course.template_id == d.course_template_id,
                        Course.course_status != COURSE_STATUS_PROPOSED,
                        Course.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        for course in courses:
            key = (d.accompanying_staff_id, course.id)
            if key in existing_pairs:
                continue
            db.add(
                Accompaniment(
                    accompanying_staff_id=d.accompanying_staff_id,
                    target_type="course",
                    course_id=course.id,
                    source="default",
                    kind=d.kind,
                    created_by=d.created_by,
                )
            )
            existing_pairs.add(key)
            created += 1

    if created:
        await db.flush()
    return created


# ---------------------------------------------------------------------------
# 可視性 (§6.4 C-1) — visit 直リンク OR course リンク (live JOIN)
# ---------------------------------------------------------------------------


def accompaniment_visibility_condition(staff_id: UUID) -> ColumnElement[bool]:
    """当該スタッフに同行訪問が見える WHERE 条件 (``visits.py`` の一覧用).

    visit 直リンク OR course リンク (``courses.deleted_at IS NULL`` の live JOIN)。
    ``_staff_visibility_filter`` の ``or_(...)`` に足す。
    """
    direct_subq = select(Accompaniment.visit_id).where(
        Accompaniment.accompanying_staff_id == staff_id,
        Accompaniment.visit_id.is_not(None),
    )
    course_subq = (
        select(Accompaniment.course_id)
        .join(Course, Accompaniment.course_id == Course.id)
        .where(
            Accompaniment.accompanying_staff_id == staff_id,
            Accompaniment.course_id.is_not(None),
            Course.deleted_at.is_(None),
        )
    )
    return or_(
        Visit.id.in_(direct_subq),
        and_(Visit.course_id.is_not(None), Visit.course_id.in_(course_subq)),
    )


async def is_accompaniment_visit_for_staff(
    db: AsyncSession,
    *,
    visit_id: UUID,
    course_id: UUID | None,
    staff_id: UUID,
) -> bool:
    """単体 visit が当該スタッフの同行対象か (set 判定 3 経路の共通ヘルパー).

    visit 直リンク OR course リンク (live JOIN)。GET /visits/{id} と checkin ロードの
    可視性 set 判定に足す (漏れると同行者がカードをタップした瞬間 404)。
    """
    direct = await db.scalar(
        select(Accompaniment.id)
        .where(
            Accompaniment.accompanying_staff_id == staff_id,
            Accompaniment.visit_id == visit_id,
        )
        .limit(1)
    )
    if direct is not None:
        return True
    if course_id is None:
        return False
    course_link = await db.scalar(
        select(Accompaniment.id)
        .join(Course, Accompaniment.course_id == Course.id)
        .where(
            Accompaniment.accompanying_staff_id == staff_id,
            Accompaniment.course_id == course_id,
            Course.deleted_at.is_(None),
        )
        .limit(1)
    )
    return course_link is not None


# ---------------------------------------------------------------------------
# 射影 (VisitRead / モニター) — 同行者の非破壊解決 (複数名対応・決定#5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccompanimentEntry:
    """1 訪問に付く同行者 1 名 (射影用の最小情報)."""

    staff_id: UUID
    staff_name: str | None
    kind: str

    def to_payload(self) -> dict:
        """``VisitRead.accompaniments[]`` の 1 要素へ."""
        return {
            "staff_id": self.staff_id,
            "staff_name": self.staff_name,
            "kind": self.kind,
        }


def _entry_sort_key(entry: AccompanimentEntry) -> tuple[int, str, str]:
    """同行者の決定的順序 (一般化 決定#5): support 優先 → スタッフ名昇順 → id.

    **なぜ support を先に出すか**: 単数フィールド (``VisitRead.accompaniment`` /
    モニターの ``accompaniment_staff_name`` / カイポケ職員名2) は先頭要素を採る。
    一般スタッフの同行は「2 人目の担い手」として実務上の意味が重く、新人同行は
    育成目的なので、代表 1 名に出すなら support 側が妥当 (決定#6/#7 の順序規約と一致)。
    名前 None は空文字として最後寄りに寄せず安定比較にする (id が最終タイブレーク)。
    """
    kind_rank = 0 if entry.kind == ACCOMPANIMENT_KIND_SUPPORT else 1
    return (kind_rank, entry.staff_name or "", str(entry.staff_id))


async def resolve_accompaniment_by_visit(
    db: AsyncSession, visits: list[Visit]
) -> dict[UUID, list[AccompanimentEntry]]:
    """visit_id -> 同行者エントリ**全件** (決定的順序) を解決する.

    直リンク ∪ course リンク (live JOIN) の和集合。同一スタッフが両方で張られて
    いる場合は直リンク側を採る (1 スタッフ 1 エントリ)。同行が無い visit は
    キーを含めない。VisitRead / モニター / カイポケで共用する。

    一般化 決定#5 以前は「代表 1 名 (last-wins)」を返しており、複数同行時に
    表示されるスタッフが非決定的だった (現行不具合)。全件返却 + 決定的順序で解消。
    単数しか表現できない口 (``VisitRead.accompaniment`` / モニターの
    ``accompaniment_staff_name`` / カイポケ職員名2) は**先頭要素**を採る — 代表の
    選び方をこの 1 関数の順序に寄せることで、経路ごとに別人が出る事故を構造的に断つ。
    """
    if not visits:
        return {}

    visit_ids = [v.id for v in visits]
    course_ids = {v.course_id for v in visits if v.course_id is not None}

    # コースリンク: course_id -> [entry] (live JOIN)。
    course_map: dict[UUID, list[AccompanimentEntry]] = defaultdict(list)
    if course_ids:
        rows = (
            await db.execute(
                select(
                    Accompaniment.course_id,
                    Accompaniment.accompanying_staff_id,
                    Accompaniment.kind,
                    Staff.name,
                )
                .join(Course, Accompaniment.course_id == Course.id)
                .join(Staff, Accompaniment.accompanying_staff_id == Staff.id)
                .where(
                    Accompaniment.course_id.in_(course_ids),
                    Course.deleted_at.is_(None),
                )
            )
        ).all()
        for cid, sid, kind, sname in rows:
            course_map[cid].append(AccompanimentEntry(staff_id=sid, staff_name=sname, kind=kind))

    # 直リンク: visit_id -> [entry]。
    direct_map: dict[UUID, list[AccompanimentEntry]] = defaultdict(list)
    rows = (
        await db.execute(
            select(
                Accompaniment.visit_id,
                Accompaniment.accompanying_staff_id,
                Accompaniment.kind,
                Staff.name,
            )
            .join(Staff, Accompaniment.accompanying_staff_id == Staff.id)
            .where(Accompaniment.visit_id.in_(visit_ids))
        )
    ).all()
    for vid, sid, kind, sname in rows:
        direct_map[vid].append(AccompanimentEntry(staff_id=sid, staff_name=sname, kind=kind))

    result: dict[UUID, list[AccompanimentEntry]] = {}
    for v in visits:
        merged: dict[UUID, AccompanimentEntry] = {}
        # 直リンクを先に入れる = 同一スタッフが course/visit 両方に居ても 1 件。
        for e in direct_map.get(v.id, []):
            merged[e.staff_id] = e
        if v.course_id is not None:
            for e in course_map.get(v.course_id, []):
                merged.setdefault(e.staff_id, e)
        if merged:
            result[v.id] = sorted(merged.values(), key=_entry_sort_key)
    return result


async def resolve_accompaniment_staff_by_course(
    db: AsyncSession, course_ids: list[UUID]
) -> dict[UUID, set[UUID]]:
    """course_id -> 同行スタッフ staff_id の集合を解決する (live JOIN).

    逆取込 (inbound) の add 経路が「新設 visit が張り付くコースに同行リンクがあり、
    カイポケ側 staff2 がその同行スタッフと一致するか」を突合するための course-level
    ヘルパー (設計 §9)。同行が無いコースはキーを含めない。

    集合を返すのは、1 コースに複数スタッフが張られている場合 (UNIQUE は
    (staff, course) 単位のため可能) に単一値の last-wins だと突合が非決定的になり、
    CSV が送ったスタッフと別のスタッフを比較して「同行由来なのに secondary へ書き戻す」
    汚染が起き得るため (Phase 3 レビュー MINOR-2)。membership 判定で構造的に防ぐ。

    ``kind`` ではフィルタしない (一般化 §3-5): カイポケ職員名2 には新人同行も一般
    スタッフの同行も等しく載るため、返ってきた staff2 が「同行由来か」は**リンクの
    実在**だけで決まる。
    """
    if not course_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Accompaniment.course_id,
                Accompaniment.accompanying_staff_id,
            )
            .join(Course, Accompaniment.course_id == Course.id)
            .where(
                Accompaniment.course_id.in_(course_ids),
                Course.deleted_at.is_(None),
            )
        )
    ).all()
    result: dict[UUID, set[UUID]] = {}
    for cid, sid in rows:
        result.setdefault(cid, set()).add(sid)
    return result


async def resolve_direct_accompaniment_staff_by_visit(
    db: AsyncSession, visits: list[Visit]
) -> dict[UUID, set[UUID]]:
    """visit_id -> **visit 直リンク**の同行スタッフ集合 (``target_type='visit'`` のみ).

    コースリンクを混ぜない版。逆取込 (inbound) の **復活枠 (revive)** 経路のために
    分けてある: 復活はキャンセル済み visit を再利用しつつ ``course_id`` を
    「カイポケの担当1 から解決した新しいコース」へ**付け替える**ため、その visit が
    今どのコースに居るかは突合の材料にならない。旧コースのリンクまで混ぜると、
    旧コースの同行者と同名の staff2 が判定① へ誤マッチして「本物の 2 人目」が
    secondary へ書かれず消える (2026-08-17 レビュー MEDIUM-2)。

    付け替え**先**のコースリンクは呼び出し側が
    ``resolve_accompaniment_staff_by_course`` から別途 union する。
    UNIQUE (staff, visit_id) の冪等ガードにもこの直リンク集合を使う。
    同行が無い visit はキーを含めない。
    """
    if not visits:
        return {}
    rows = (
        await db.execute(
            select(
                Accompaniment.visit_id,
                Accompaniment.accompanying_staff_id,
            ).where(Accompaniment.visit_id.in_([v.id for v in visits]))
        )
    ).all()
    result: dict[UUID, set[UUID]] = {}
    for vid, sid in rows:
        result.setdefault(vid, set()).add(sid)
    return result


async def resolve_accompaniment_staff_by_visit(
    db: AsyncSession, visits: list[Visit]
) -> dict[UUID, set[UUID]]:
    """visit_id -> その訪問に同行するスタッフ staff_id の集合を解決する (live JOIN).

    逆取込 (inbound) の edit 経路の同行由来 staff2 突合用 (設計 §9)。
    表示用の ``resolve_accompaniment_by_visit`` は名前や kind まで解決するが、突合に
    要るのは staff_id の集合だけなので軽い口を分けてある。直リンク ∪ course リンクの
    **全集合**を返し、membership 判定でラウンドトリップ汚染を構造的に防ぐ
    (Phase 3 レビュー MINOR-2)。kind ではフィルタしない — カイポケ職員名2 には
    新人同行も一般スタッフの同行も等しく載るため (一般化 §3-5)。
    同行が無い visit はキーを含めない。

    コースが**付け替わる**経路 (復活枠) では現在の course_id を混ぜてはいけない —
    ``resolve_direct_accompaniment_staff_by_visit`` を使うこと。
    """
    if not visits:
        return {}

    course_ids = {v.course_id for v in visits if v.course_id is not None}

    course_sets: dict[UUID, set[UUID]] = (
        await resolve_accompaniment_staff_by_course(db, list(course_ids)) if course_ids else {}
    )
    direct_sets = await resolve_direct_accompaniment_staff_by_visit(db, visits)

    result: dict[UUID, set[UUID]] = {}
    for v in visits:
        merged = set(direct_sets.get(v.id, set()))
        if v.course_id is not None:
            merged |= course_sets.get(v.course_id, set())
        if merged:
            result[v.id] = merged
    return result


# ---------------------------------------------------------------------------
# 重複判定 (PUT の確定ブロック) — 設計 §6.2 / §7 / 一般化 決定#1
# ---------------------------------------------------------------------------


async def load_effective_visits(
    db: AsyncSession,
    *,
    course_ids: list[UUID],
    visit_ids: list[UUID],
) -> list[Visit]:
    """実効同行訪問集合を構築する (コースリンク先の planned 訪問 ∪ 個別リンク訪問).

    patient を eager-load して返す (重複排除済み)。soft-delete / cancelled は除外。
    """
    seen: dict[UUID, Visit] = {}

    if course_ids:
        rows = (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.course_id.in_(course_ids),
                    Visit.deleted_at.is_(None),
                    Visit.status != VISIT_STATUS_CANCELLED,
                )
                .options(selectinload(Visit.patient))
            )
        ).all()
        for v in rows:
            seen[v.id] = v
    if visit_ids:
        rows = (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.id.in_(visit_ids),
                    Visit.deleted_at.is_(None),
                    Visit.status != VISIT_STATUS_CANCELLED,
                )
                .options(selectinload(Visit.patient))
            )
        ).all()
        for v in rows:
            seen[v.id] = v
    return list(seen.values())


async def load_own_duty_visits(
    db: AsyncSession,
    staff_id: UUID,
    monday: date,
    sunday: date,
) -> list[Visit]:
    """当該スタッフが**自分の担当として**持つ週内の訪問を集める (一般化 決定#1).

    同行は「他人の訪問に付き添う」ものなので、自分が担当している訪問と時間が
    重なったら物理的に不可能 = ハード 422 の根拠になる。従来は同行選択どうしの
    重複しか見ておらず、一般スタッフへ開放すると「自分の担当と重なる同行」を
    登録できてしまうため、本人担当も検査入力へ合流させる。

    担当の定義 = ``visits.primary_staff_id`` / ``secondary_staff_id`` /
    ``mentor_staff_id`` のいずれか、または VSA (``visit_staff_assignments``・v2 の正典)
    に行があること。soft-delete / cancelled は除外し、patient を eager-load する
    (同住所免除の判定に座標が要る)。
    """
    vsa_subq = select(VisitStaffAssignment.visit_id).where(
        VisitStaffAssignment.staff_id == staff_id
    )
    rows = (
        await db.scalars(
            select(Visit)
            .where(
                Visit.visit_date >= monday,
                Visit.visit_date <= sunday,
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
                or_(
                    Visit.primary_staff_id == staff_id,
                    Visit.secondary_staff_id == staff_id,
                    Visit.mentor_staff_id == staff_id,
                    Visit.id.in_(vsa_subq),
                ),
            )
            .options(selectinload(Visit.patient))
        )
    ).all()
    return list(rows)


def _same_address_key(visit: Visit) -> str | None:
    """患者座標の同住所バケットキー (FE ``buildSameAddressKey`` と同じ .3f 量子化).

    同住所×同時刻ペアは「90分の間に 2 人とも回る」運用ルール
    (``SAME_ADDRESS_PAIR_MIN_OCCUPANCY`` / 同住所ペア占有) が既に存在し、
    同行者も同じ玄関に居られるため時間重複としてブロックしない (PO 2026-07-12)。
    座標が無い患者は None (= 免除しない・保守的にブロック)。
    """
    patient = visit.patient
    if patient is None:
        return None
    lat = patient.lat
    lng = patient.lng
    if lat is None or lng is None:
        return None
    return f"{lat:.3f}:{lng:.3f}"


def _overlaps(a: Visit, b: Visit) -> bool:
    """区間交差 (``a.start < b.end AND b.start < a.end``) かつ同住所免除に該当しない.

    例外: **同住所ペア** (患者座標バケット一致) は 90 分占有ルールの正当な同時刻の
    ため重複扱いしない (FE ``computeAccompanimentOverlaps`` と同一の免除)。
    """
    if a.visit_date != b.visit_date:
        return False
    if not (a.start_time < b.end_time and b.start_time < a.end_time):
        return False
    ka = _same_address_key(a)
    if ka is not None and ka == _same_address_key(b):
        return False
    return True


def find_time_overlaps(visits: list[Visit]) -> list[tuple[Visit, Visit]]:
    """同一日で時間帯 (start〜end) が交差する visit ペアを列挙する.

    同一人物が同時刻に 2 箇所は物理的に不可能 → 確定ブロック (422) の根拠。
    同住所ペアは免除 (``_overlaps`` 参照)。
    """
    by_date: dict[date, list[Visit]] = defaultdict(list)
    for v in visits:
        by_date[v.visit_date].append(v)

    pairs: list[tuple[Visit, Visit]] = []
    for group in by_date.values():
        group.sort(key=lambda v: (v.start_time, v.end_time))
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if _overlaps(a, b):
                    pairs.append((a, b))
    return pairs


# 422 の code (FE はこの文字列で「なぜ登録できないか」ダイアログを出し分ける)。
ACCOMPANIMENT_OVERLAP_CODE = "accompaniment_overlap"

# conflicts[].reason — 'own_duty' = 本人担当と衝突 / 'accompaniment' = 同行選択どうし。
CONFLICT_REASON_OWN_DUTY = "own_duty"
CONFLICT_REASON_ACCOMPANIMENT = "accompaniment"


@dataclass(frozen=True)
class AccompanimentConflict:
    """登録を拒否した理由 1 件 (= 衝突相手の訪問 1 つ) — 一般化 決定#1.

    設計 §1 の「◯月◯日(◯) HH:MM は ◯◯様（◯◯コース・ご自身の担当）と重なるため
    登録できません」を FE がそのまま組めるだけの粒度を持たせる。
    """

    visit_id: UUID
    date: date
    weekday: int
    start: str
    end: str
    patient_name: str | None
    course_label: str | None
    reason: str

    def to_detail(self) -> dict:
        """422 detail の ``conflicts[]`` 1 要素へ."""
        return {
            "visit_id": str(self.visit_id),
            "date": self.date.isoformat(),
            "weekday": self.weekday,
            "start": self.start,
            "end": self.end,
            "patient_name": self.patient_name,
            "course_label": self.course_label,
            "reason": self.reason,
        }


def accompaniment_overlap_detail(conflicts: list[AccompanimentConflict]) -> dict:
    """422 の detail body (一般化 決定#1) を組む — 全経路で同形."""
    return {
        "code": ACCOMPANIMENT_OVERLAP_CODE,
        "message": "時間が重複するため同行を登録できません（同時には行けません）",
        "conflicts": [c.to_detail() for c in conflicts],
    }


async def _course_labels(db: AsyncSession, visits: list[Visit]) -> dict[UUID, str]:
    """course_id -> 表示ラベル。テンプレ label (例「稲毛A」) 優先・無ければ code.

    衝突理由の「◯◯コース」を人が読める粒度にするためだけの解決 (1 クエリ)。
    """
    course_ids = {v.course_id for v in visits if v.course_id is not None}
    if not course_ids:
        return {}
    rows = (
        await db.execute(
            select(Course.id, Course.code, CourseTemplate.label)
            .outerjoin(CourseTemplate, Course.template_id == CourseTemplate.id)
            .where(Course.id.in_(course_ids))
        )
    ).all()
    return {cid: (label or code) for cid, code, label in rows}


async def collect_accompaniment_conflicts(
    db: AsyncSession,
    *,
    effective: list[Visit],
    own_duty: list[Visit],
) -> list[AccompanimentConflict]:
    """同行の実効訪問集合を、本人担当 + 同行どうしの両方向で検査する (決定#1).

    - ``own_duty``: 同行しようとしているスタッフが**自分の担当**として持つ週内の訪問。
      同行選択のどれかと重なったら ``reason='own_duty'`` で**その担当訪問**を返す
      (「◯◯様（◯◯コース・ご自身の担当）と重なる」の材料)。
    - ``effective`` どうしの重複は ``reason='accompaniment'`` で**両方の訪問**を返す
      (どちらが悪いとは言えないため。FE は 2 件を並べて「この 2 つが重なる」と出せる)。

    ``own_duty`` 側に同行選択と同じ visit が含まれることがある (同行対象コースの
    担当が本人 = 通常あり得ないが、手動編集で起こり得る)。自分自身との衝突を
    誤検出しないよう visit_id で除外する。

    返却は決定的順序 (日付 → 開始 → reason → visit_id) で、同一 visit × 同一 reason は
    1 件に畳む。空リスト = 登録して良い。
    """
    if not effective:
        return []

    effective_ids = {v.id for v in effective}
    own_duty_only = [v for v in own_duty if v.id not in effective_ids]

    labels = await _course_labels(db, effective + own_duty_only)

    def _make(v: Visit, reason: str) -> AccompanimentConflict:
        return AccompanimentConflict(
            visit_id=v.id,
            date=v.visit_date,
            weekday=v.visit_date.weekday(),
            start=v.start_time.strftime("%H:%M"),
            end=v.end_time.strftime("%H:%M"),
            patient_name=(getattr(v.patient, "name", None) if v.patient is not None else None),
            course_label=(labels.get(v.course_id) if v.course_id is not None else None),
            reason=reason,
        )

    found: dict[tuple[UUID, str], AccompanimentConflict] = {}

    # 1) 同行選択どうし (従来の検査・reason='accompaniment')。
    for a, b in find_time_overlaps(effective):
        for v in (a, b):
            found.setdefault(
                (v.id, CONFLICT_REASON_ACCOMPANIMENT), _make(v, CONFLICT_REASON_ACCOMPANIMENT)
            )

    # 2) 同行選択 × 本人担当 (決定#1・reason='own_duty')。
    by_date: dict[date, list[Visit]] = defaultdict(list)
    for v in own_duty_only:
        by_date[v.visit_date].append(v)
    for e in effective:
        for own in by_date.get(e.visit_date, []):
            if _overlaps(e, own):
                found.setdefault(
                    (own.id, CONFLICT_REASON_OWN_DUTY), _make(own, CONFLICT_REASON_OWN_DUTY)
                )

    return sorted(found.values(), key=lambda c: (c.date, c.start, c.reason, str(c.visit_id)))


# ---------------------------------------------------------------------------
# ライフサイクル (§7.5 / 一般化 §3-7) — 将来リンク + 既定の一括削除
# ---------------------------------------------------------------------------


async def delete_future_accompaniments(
    db: AsyncSession,
    *,
    staff_id: UUID,
    iso_year: int,
    iso_week: int,
    monday: date,
    include_defaults: bool = True,
) -> tuple[int, int]:
    """今週以降の同行リンク (と、任意で毎週の既定) を削除する (**commit しない**).

    ``DELETE /accompaniments/future`` (is_trainee OFF の確認ダイアログ) と、
    スタッフの退職 / 非 active 化 (``PATCH /staff/{id}`` / ``DELETE /staff/{id}``) の
    両方から呼ぶ単一ソース。過去週のリンクは実績履歴として残す。

    ``include_defaults`` (2026-08-17 レビュー M-2):
        **休職は「消す」ではなく「止める」**。status を非 active にしただけの休職者は
        いずれ復帰するので、毎週の既定 (テンプレ層) を消すと復帰時に人手で組み直す
        羽目になる。週リンクだけ消せば盤面からは即座に消え、復帰後の週生成で既定が
        再展開されて自動的に戻る。

        既定まで消すのは「もう戻らない / 同行者ではなくなる」と確定した経路だけ:
        退職 (論理削除) と is_trainee OFF (``DELETE /accompaniments/future``)。

    返却 = (削除したリンク数, 削除した既定数)。冪等 (対象ゼロでも成功)。
    """
    future_course_ids = select(Course.id).where(
        or_(
            Course.iso_year > iso_year,
            and_(Course.iso_year == iso_year, Course.iso_week >= iso_week),
        ),
    )
    future_visit_ids = select(Visit.id).where(Visit.visit_date >= monday)

    res_links = await db.execute(
        delete(Accompaniment).where(
            Accompaniment.accompanying_staff_id == staff_id,
            or_(
                Accompaniment.course_id.in_(future_course_ids),
                Accompaniment.visit_id.in_(future_visit_ids),
            ),
        )
    )
    deleted_defaults = 0
    if include_defaults:
        res_defaults = await db.execute(
            delete(AccompanimentDefault).where(
                AccompanimentDefault.accompanying_staff_id == staff_id
            )
        )
        deleted_defaults = int(res_defaults.rowcount or 0)
    return int(res_links.rowcount or 0), deleted_defaults


async def has_accompaniment_on_dates(
    db: AsyncSession,
    *,
    staff_id: UUID,
    dates: list[date],
) -> list[Visit]:
    """当該スタッフが指定日に持つ**同行訪問**を返す (逆方向の警告用・決定#1後段).

    コース担当の変更 / apply-staff-review が「そのスタッフには同日に同行が入って
    いる」ことに気づけるようにするための読み取り専用ヘルパー。エンジン本体は
    触らず (意図的スコープ外)、警告 + 管理者通知の材料だけを提供する。

    同行が無ければ空リスト。patient を eager-load して返す (本文に患者名を出すため)。
    """
    if not dates:
        return []
    rows = (
        await db.scalars(
            select(Visit)
            .where(
                Visit.visit_date.in_(list(dict.fromkeys(dates))),
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
                accompaniment_visibility_condition(staff_id),
            )
            .options(selectinload(Visit.patient))
            .order_by(Visit.visit_date, Visit.start_time, Visit.id)
        )
    ).all()
    return list(rows)


# ---------------------------------------------------------------------------
# 逆方向の警告 (決定#1 後段) — 「担当を付けた日に、その人の同行が入っている」
# ---------------------------------------------------------------------------

# notifications.type / reference_type。FE のアイコン選択キーと共用。
NOTIFY_ACCOMPANIMENT_CONFLICT = "accompaniment_conflict"

_NOTIFY_TITLE_MAX_LEN = 200
_WEEKDAY_JA = ("月", "火", "水", "木", "金", "土", "日")


@dataclass(frozen=True)
class AccompanimentDutyWarning:
    """コース担当を付けた日に、その人の同行リンクが入っている 1 件 (警告).

    **ブロックしない** (決定#1 後段): 同行を先に登録してから後で担当を割り当てる
    向きは、エンジンのハード対応が別案件のため警告 + 管理者通知で出荷する。
    """

    staff_id: UUID
    staff_name: str | None
    visit_id: UUID
    date: date
    weekday: int
    start: str
    end: str
    patient_name: str | None

    def to_payload(self) -> dict:
        return {
            "staff_id": str(self.staff_id),
            "staff_name": self.staff_name,
            "visit_id": str(self.visit_id),
            "date": self.date.isoformat(),
            "weekday": self.weekday,
            "start": self.start,
            "end": self.end,
            "patient_name": self.patient_name,
        }


async def collect_accompaniment_duty_warnings(
    db: AsyncSession,
    *,
    staff_id: UUID,
    course_ids: list[UUID],
) -> list[AccompanimentDutyWarning]:
    """担当に付けようとしているコースの訪問と、その人の同行が**時間帯で交差**するか.

    コース担当変更 (``PATCH /courses/{id}``) と ``apply-staff-review`` の両方から呼ぶ。

    **同日ではなく時間帯交差で絞る** (2026-08-17 レビュー M-4):
    「同じ日に同行がある」だけで鳴らすと、午前の担当と夕方の同行のように実務上
    まったく問題ない組み合わせまで警告 + 管理者通知が飛ぶ。オオカミ少年になった
    警告は読まれなくなるので、実際に体が 2 つ要る組み合わせだけを残す。
    交差判定は PUT の重複検査と同じ ``_overlaps`` を使う (= 同住所ペアは免除)。

    同一 visit (担当コースの訪問に自分が同行している) は交差扱いしない — 同じ
    現場に居るだけで物理矛盾ではない。空リスト = 警告なし。
    """
    if not course_ids:
        return []

    # 担当することになる訪問 (= 交差判定の左辺)。
    duty_visits = list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.course_id.in_(course_ids),
                    Visit.deleted_at.is_(None),
                    Visit.status != VISIT_STATUS_CANCELLED,
                )
                .options(selectinload(Visit.patient))
            )
        ).all()
    )
    if not duty_visits:
        return []

    dates = sorted({v.visit_date for v in duty_visits})
    acc_visits = await has_accompaniment_on_dates(db, staff_id=staff_id, dates=dates)
    if not acc_visits:
        return []

    duty_by_date: dict[date, list[Visit]] = defaultdict(list)
    for v in duty_visits:
        duty_by_date[v.visit_date].append(v)
    duty_ids = {v.id for v in duty_visits}

    staff_name = await db.scalar(select(Staff.name).where(Staff.id == staff_id))
    conflicting: dict[UUID, Visit] = {}
    for acc in acc_visits:
        if acc.id in duty_ids:
            continue  # 担当コースの訪問に自分が同行 = 同じ現場・矛盾なし。
        for duty in duty_by_date.get(acc.visit_date, []):
            if _overlaps(acc, duty):
                conflicting[acc.id] = acc
                break

    return [
        AccompanimentDutyWarning(
            staff_id=staff_id,
            staff_name=staff_name,
            visit_id=v.id,
            date=v.visit_date,
            weekday=v.visit_date.weekday(),
            start=v.start_time.strftime("%H:%M"),
            end=v.end_time.strftime("%H:%M"),
            patient_name=(getattr(v.patient, "name", None) if v.patient is not None else None),
        )
        for v in sorted(conflicting.values(), key=lambda v: (v.visit_date, v.start_time, str(v.id)))
    ]


async def notify_accompaniment_duty_conflict(
    db: AsyncSession,
    *,
    warnings: list[AccompanimentDutyWarning],
    actor: User | None,
    op_group_id: UUID | None = None,
) -> int:
    """同行と担当がぶつかった事実を active admin へお知らせする (**commit しない**).

    ``constraint_override_notify.notify_constraint_override`` と同じ据わり:
    呼び出し側が TX 境界を握り、通知は「実際に適用されたもの」とだけ一緒に飛ぶ。

    冪等キー: ``reference_type='accompaniment_conflict'`` + ``reference_id=op_group_id``。
    **op_group_id が None のときは重複検査そのものをスキップする** (= 毎回通知)。
    NULL 同士を突き合わせると ``reference_id IS NULL`` の過去通知全部にヒットして
    以後の通知が永久に止まる — 既存経路で実際に踏んだ罠なので同じ形で避ける。
    """
    if not warnings:
        return 0
    users = list(
        (
            await db.scalars(
                select(User).where(
                    User.role.in_(("admin", "manager")),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
    )
    if not users:
        return 0

    actor_name = "不明"
    if actor is not None:
        if actor.staff_id is not None:
            actor_name = (
                await db.scalar(select(Staff.name).where(Staff.id == actor.staff_id))
            ) or actor_name
        if actor_name == "不明":
            actor_name = actor.username or actor.email or "不明"

    # 1 通に複数スタッフ分が載りうる (apply-staff-review の集約通知)。
    staff_names = list(dict.fromkeys(w.staff_name or "担当者" for w in warnings))
    head_name = staff_names[0]
    if len(staff_names) > 1:
        head_name = f"{head_name} 他{len(staff_names) - 1}名"
    title = f"同行と担当が重なりました: {head_name}"[:_NOTIFY_TITLE_MAX_LEN]

    lines = [f"操作者: {actor_name}"]
    for w in warnings:
        wd = _WEEKDAY_JA[w.weekday] if 0 <= w.weekday < len(_WEEKDAY_JA) else str(w.weekday)
        patient = f"{w.patient_name}様" if w.patient_name else "利用者"
        who = w.staff_name or "担当者"
        lines.append(f"⚠ {who}: {w.date.isoformat()}({wd}) {w.start}-{w.end} 同行あり ({patient})")
    body = "\n".join(lines)

    already: set[UUID] = set()
    if op_group_id is not None:
        already = set(
            (
                await db.scalars(
                    select(Notification.user_id).where(
                        Notification.reference_type == NOTIFY_ACCOMPANIMENT_CONFLICT,
                        Notification.reference_id == op_group_id,
                    )
                )
            ).all()
        )

    created = 0
    for u in users:
        if u.id in already:
            continue
        db.add(
            Notification(
                user_id=u.id,
                type=NOTIFY_ACCOMPANIMENT_CONFLICT,
                title=title,
                body=body,
                reference_type=NOTIFY_ACCOMPANIMENT_CONFLICT,
                reference_id=op_group_id,
            )
        )
        created += 1
    return created
