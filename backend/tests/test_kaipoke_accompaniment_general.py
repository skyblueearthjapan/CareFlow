"""同行一般化 Phase D (カイポケ連携) — 設計 `general-accompaniment-design.md` §3-5 / 決定#6.

Phase 3 (新人同行) のテストが「新人だから同行扱い」を固めているのに対し、ここでは
**kind を問わない**汎化後の仕様を固める。

観点:
- 逆取込 staff2 の 3 段階判定
  * ① 既存の同行リンクと一致 → 取り込まない (kind='support' の一般スタッフでも成立)
  * ② リンク無しの自動同行化は **is_trainee 限定を維持** (一般開放しない・意図的見送り)
  * ③ リンク無しの一般スタッフ → 従来どおり 2 名体制 (secondary + required=2)
- csv_builder の職員名2/3 配分 (決定#6): 複数同行の決定的順序 + staff_id デデュープ
- replace-inbound (置換取込) の追随
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.models.accompaniment import (
    ACCOMPANIMENT_KIND_SUPPORT,
    ACCOMPANIMENT_KIND_TRAINEE,
    Accompaniment,
)
from app.models.correction_sheet import CorrectionSheetItem
from app.models.visit import Visit
from app.services.diff.engine import ScheduleEntry
from app.services.kaipoke.csv_builder import BuildOptions, resolve_month_rows
from app.services.kaipoke.inbound import apply_inbound_items
from app.services.kaipoke.replace_inbound import replace_week_from_kaipoke
from tests.test_kaipoke_accompaniment_phase3 import (  # 共有シード (同じ週・同じ火曜)
    TUE,
    WEEK_END,
    WEEK_START,
    _course,
    _office,
    _patient,
    _staff,
    _visit,
)

JST = ZoneInfo("Asia/Tokyo")
SERVICE = "精神基本療養費Ⅰ・正看"


def _now() -> datetime:
    return datetime(2026, 7, 8, 12, 0, tzinfo=JST)


def _link(db, staff, *, course=None, visit=None, kind: str = ACCOMPANIMENT_KIND_SUPPORT) -> None:
    """同行リンクを 1 本張る (kind 既定 = 一般スタッフの同行)."""
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course" if course is not None else "visit",
            course_id=course.id if course is not None else None,
            visit_id=visit.id if visit is not None else None,
            source="manual",
            kind=kind,
        )
    )


def _edit_item(patient, visit, *, staff1: str, staff2: str) -> CorrectionSheetItem:
    common = {"user_name": patient.name, "date": "7", "start_time": "10:00", "end_time": "10:35"}
    return CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        visit_id=visit.id,
        action="edit",
        before={**common, "staff1": staff1, "staff2": ""},
        after={**common, "staff1": staff1, "staff2": staff2},
        include=True,
    )


def _add_item(patient, *, staff1: str, staff2: str, start: str = "15:00") -> CorrectionSheetItem:
    return CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        action="add",
        before=None,
        after={
            "user_name": patient.name,
            "date": "7",
            "start_time": start,
            "end_time": "15:35",
            "staff1": staff1,
            "staff2": staff2,
        },
        include=True,
    )


async def _apply(db, items) -> object:
    return await apply_inbound_items(
        db,
        items=items,
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )


# --- A. 判定① — kind を問わないリンクベース判定 ------------------------------


@pytest.mark.asyncio
async def test_inbound_edit_support_course_link_skipped(db) -> None:
    """判定①: 一般スタッフ (is_trainee=False) のコース同行リンク × staff2 一致 → 取り込まない.

    新人限定だった頃の集合判定のままなら、この一般スタッフは判定③ へ落ちて
    secondary_staff_id へ書き戻される (= 楽スケが 2 名体制に化けるラウンドトリップ汚染)。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)  # 一般スタッフ (新人ではない)。
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    _link(db, helper, course=course)
    await db.commit()

    summary = await _apply(db, [_edit_item(patient, visit, staff1="先輩太郎", staff2="応援次郎")])
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id is None  # 楽スケは 1 人のまま (= 正常形)。
    assert visit.required_staff_count == 1
    assert summary.skipped == 1  # 同行以外に変更点なし。


@pytest.mark.asyncio
async def test_inbound_edit_support_visit_link_skipped(db) -> None:
    """判定①: 一般スタッフの **visit 単位** 同行リンクでも取り込まない (患者様ごとの紐付け)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    _link(db, helper, visit=visit)
    await db.commit()

    summary = await _apply(db, [_edit_item(patient, visit, staff1="先輩太郎", staff2="応援次郎")])
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id is None
    assert visit.required_staff_count == 1
    assert summary.skipped == 1


@pytest.mark.asyncio
async def test_inbound_add_support_course_link_skipped(db) -> None:
    """判定① (add): 新設 visit が張り付くコースの一般同行と staff2 一致 → 要2名化しない."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    _link(db, helper, course=course)
    await db.commit()

    summary = await _apply(db, [_add_item(patient, staff1="先輩太郎", staff2="応援次郎")])
    await db.commit()

    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.start_time == time(15, 0))
    )
    assert summary.added == 1
    assert new_visit is not None
    assert new_visit.secondary_staff_id is None
    assert new_visit.required_staff_count == 1


@pytest.mark.asyncio
async def test_inbound_add_revive_support_visit_link_skipped(db) -> None:
    """判定① (add・復活枠): キャンセル枠を復活させる経路でも **直リンク** を突合する.

    add は「新設 visit にコースリンクだけ突合」で足りていたが、同時刻のキャンセル枠が
    あると INSERT せず既存 visit を復活させる (= visit 単位の同行リンクが生き残る)。
    コースリンクしか見ないと、一般スタッフの visit 単位同行が判定③ へ落ちて
    secondary へ書き戻される — 一般化で初めて露出する経路。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    # 15:00 の枠をキャンセル済みで用意 → add はこれを復活させる。
    cancelled = await _visit(
        db, patient, mentor, course=course, start=time(15, 0), end=time(15, 35)
    )
    cancelled.status = "cancelled"
    _link(db, helper, visit=cancelled)  # コースには張らず visit 単位のみ。
    await db.commit()

    summary = await _apply(db, [_add_item(patient, staff1="先輩太郎", staff2="応援次郎")])
    await db.commit()
    await db.refresh(cancelled)

    assert summary.added == 1
    assert cancelled.status == "planned"  # 復活している。
    assert cancelled.secondary_staff_id is None  # 同行は secondary へ書かない。
    assert cancelled.required_staff_count == 1


# --- B. 判定② — is_trainee 限定の維持 (一般開放しない) ------------------------


@pytest.mark.asyncio
async def test_inbound_edit_unlinked_support_promotes_two_staff(db) -> None:
    """判定③: リンク無しの一般スタッフ staff2 は従来どおり 2 名体制へ昇格する.

    判定② (自動同行化) を誤って一般開放すると、本当に 2 名で入った訪問の
    required_staff_count=2 が消えてしまう。ここが ③ に落ち続けることを固定する
    (設計 §3-5: 一般の自動同行化は意図的に見送り)。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)  # 同行リンクは張らない。
    await db.commit()
    helper_id = helper.id

    summary = await _apply(db, [_edit_item(patient, visit, staff1="先輩太郎", staff2="応援次郎")])
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id == helper_id  # ③ = 2 名体制。
    assert visit.required_staff_count == 2
    assert summary.updated == 1

    # 自動同行化されていない (リンクが増えていない)。
    count = await db.scalar(
        select(func.count())
        .select_from(Accompaniment)
        .where(Accompaniment.accompanying_staff_id == helper_id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_inbound_add_unlinked_support_promotes_two_staff(db) -> None:
    """判定③ (add): リンク無しの一般スタッフ staff2 も従来どおり required=2 で新設."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    await _course(db, office, mentor, weekday=1, code="A")
    await db.commit()
    helper_id = helper.id

    summary = await _apply(db, [_add_item(patient, staff1="先輩太郎", staff2="応援次郎")])
    await db.commit()

    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.start_time == time(15, 0))
    )
    assert summary.added == 1
    assert new_visit is not None
    assert new_visit.secondary_staff_id == helper_id
    assert new_visit.required_staff_count == 2


@pytest.mark.asyncio
async def test_inbound_edit_unlinked_trainee_still_auto_accompaniment(db) -> None:
    """判定②の維持: リンク無しの **新人** staff2 は今も自動同行化される (kind='trainee')."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    await db.commit()
    trainee_id = trainee.id

    summary = await _apply(db, [_edit_item(patient, visit, staff1="先輩太郎", staff2="新人花子")])
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id is None
    assert visit.required_staff_count == 1
    assert summary.updated == 1

    link = await db.scalar(
        select(Accompaniment).where(
            Accompaniment.accompanying_staff_id == trainee_id,
            Accompaniment.visit_id == visit.id,
        )
    )
    assert link is not None
    assert link.kind == ACCOMPANIMENT_KIND_TRAINEE  # 判定② は新人限定 = 常に trainee。


# --- C. csv_builder — 職員名2/3 の決定的配分 (決定#6) --------------------------


@pytest.mark.asyncio
async def test_csv_multiple_accompaniments_support_first(db) -> None:
    """複数同行: 職員名2=support・職員名3=trainee (support 優先の決定的順序).

    新人の名前 (井上花子) は名前昇順だけなら support (応援次郎) より先に来る
    (井 U+4E95 < 応 U+5FDC) — kind ランクが名前より先に効いていることを固定する。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "井上花子", office, is_trainee=True)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course)
    # 新人を先に張る = 挿入順が結果へ漏れないこと (旧 last-wins の回帰) も同時に確認。
    _link(db, trainee, course=course, kind=ACCOMPANIMENT_KIND_TRAINEE)
    _link(db, helper, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert len(rows) == 1
    assert rows[0].secondary is not None and rows[0].secondary.name == "応援次郎"
    assert rows[0].tertiary is not None and rows[0].tertiary.name == "井上花子"


@pytest.mark.asyncio
async def test_csv_multiple_support_accompaniments_name_order(db) -> None:
    """同 kind の複数同行はスタッフ名昇順 (安定・再現する順序).

    昇順はコードポイント比較 (「山」U+5C71 <「青」U+9752) — 日本語の読み順ではない。
    重要なのは五十音順であることではなく、**挿入順に依存せず毎回同じ人が職員名2 に
    載る**ことなので、リンクは名前順と逆に張って挿入順の影響を排除して確認する。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    yamamoto = await _staff(db, "山本三郎", office)
    aoki = await _staff(db, "青木一郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course)
    _link(db, aoki, course=course)  # 昇順で後ろに来る方を先に張る。
    _link(db, yamamoto, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert rows[0].secondary is not None and rows[0].secondary.name == "山本三郎"
    assert rows[0].tertiary is not None and rows[0].tertiary.name == "青木一郎"


@pytest.mark.asyncio
async def test_csv_secondary_plus_two_accompaniments_drops_third(db) -> None:
    """secondary + 同行2名 = 4人目相当は落ちる (現行踏襲・カイポケ枠は職員名3 まで)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    second = await _staff(db, "看護次郎", office)
    helper_a = await _staff(db, "応援一郎", office)
    helper_b = await _staff(db, "応援四郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course, secondary_staff_id=second.id)
    _link(db, helper_a, course=course)
    _link(db, helper_b, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert rows[0].secondary is not None and rows[0].secondary.name == "看護次郎"  # 職員名2
    assert rows[0].tertiary is not None and rows[0].tertiary.name == "応援一郎"  # 職員名3
    # 応援四郎 (4人目相当) は枠が無く落ちる。誰が落ちるかは順序が決定的なので再現する。


@pytest.mark.asyncio
async def test_csv_dedupe_secondary_and_accompaniment_same_staff(db) -> None:
    """同一人物が secondary と同行の両方に居ても職員名2/3 に二重掲載しない."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    both = await _staff(db, "看護次郎", office)
    helper = await _staff(db, "応援一郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course, secondary_staff_id=both.id)
    _link(db, both, course=course)  # secondary と同一人物の同行リンク。
    _link(db, helper, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert rows[0].secondary is not None and rows[0].secondary.name == "看護次郎"
    assert rows[0].tertiary is not None and rows[0].tertiary.name == "応援一郎"  # 重複せず次点。


@pytest.mark.asyncio
async def test_csv_dedupe_primary_same_as_accompaniment(db) -> None:
    """primary と同一人物の同行リンクは職員名2 に載せない (職員名1 との二重掲載防止)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course)
    _link(db, mentor, course=course)  # 担当者自身の同行リンク (手動編集で起こり得る)。
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert rows[0].primary.name == "先輩太郎"
    assert rows[0].secondary is None
    assert rows[0].tertiary is None


# --- D. replace-inbound (置換取込) の追随 -------------------------------------


def _entry(patient_name: str, *, staff1: str, staff2: str, start: str = "10:00") -> ScheduleEntry:
    return ScheduleEntry(
        user_name=patient_name,
        date=str(TUE.day),
        weekday="火",
        business_type="医療保険",
        service_type=SERVICE,
        start_time=start,
        end_time="10:35",
        staff1_name=staff1,
        staff1_type="看護師",
        staff2_name=staff2,
        staff2_type="看護師" if staff2 else "",
    )


@pytest.mark.asyncio
async def test_replace_support_course_link_skipped(db) -> None:
    """置換取込: 一般スタッフのコース同行リンクと staff2 一致 → secondary にしない."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    _link(db, helper, course=course)
    await db.commit()

    result = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=[_entry(patient.name, staff1="先輩太郎", staff2="応援次郎")],
        dry_run=False,
        now=_now(),
    )
    await db.commit()

    assert result.inserted == 1
    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
    )
    assert new_visit is not None
    assert new_visit.secondary_staff_id is None  # 同行由来は secondary へ書かない。
    assert new_visit.required_staff_count == 1


@pytest.mark.asyncio
async def test_replace_unlinked_support_promotes_two_staff(db) -> None:
    """置換取込: リンク無しの一般スタッフ staff2 は従来どおり 2 名体制 (判定③)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    await _course(db, office, mentor, weekday=1, code="A")  # 同行リンク無し。
    await db.commit()
    helper_id = helper.id

    result = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=[_entry(patient.name, staff1="先輩太郎", staff2="応援次郎")],
        dry_run=False,
        now=_now(),
    )
    await db.commit()

    assert result.inserted == 1
    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
    )
    assert new_visit is not None
    assert new_visit.secondary_staff_id == helper_id
    assert new_visit.required_staff_count == 2


@pytest.mark.asyncio
async def test_replace_unlinked_trainee_auto_accompaniment_kept(db) -> None:
    """置換取込: リンク無しの新人 staff2 は判定② で自動同行化 (kind='trainee') のまま."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    await _course(db, office, mentor, weekday=1, code="A")
    await db.commit()
    trainee_id = trainee.id

    result = await replace_week_from_kaipoke(
        db,
        week_start=WEEK_START,
        entries=[_entry(patient.name, staff1="先輩太郎", staff2="新人花子")],
        dry_run=False,
        now=_now(),
    )
    await db.commit()

    assert result.inserted == 1
    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
    )
    assert new_visit is not None
    assert new_visit.secondary_staff_id is None
    link = await db.scalar(
        select(Accompaniment).where(
            Accompaniment.accompanying_staff_id == trainee_id,
            Accompaniment.visit_id == new_visit.id,
        )
    )
    assert link is not None
    assert link.kind == ACCOMPANIMENT_KIND_TRAINEE


# --- E. ラウンドトリップ (CSV 送出 → 逆取込) の閉ループ -----------------------


@pytest.mark.asyncio
async def test_general_accompaniment_roundtrip_stays_one_staff(db) -> None:
    """一般同行の往復: 職員名2 として送出 → staff2 で返ってきても楽スケは 1 人のまま.

    決定#6 (送出) と §3-5 判定① (取込) が同じリンクを見ていることの閉ループ確認。
    片方だけ汎化すると「送っては 2 名体制に化ける」無限往復になる。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    helper = await _staff(db, "応援次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    _link(db, helper, course=course)
    await db.commit()

    # 送出: 職員名2 に一般同行者が載る (新人と同格・決定#6)。
    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert rows[0].secondary is not None and rows[0].secondary.name == "応援次郎"

    # 取込: その職員名2 が staff2 として返ってきても要2名化しない。
    await _apply(db, [_edit_item(patient, visit, staff1="先輩太郎", staff2=rows[0].secondary.name)])
    await db.commit()
    await db.refresh(visit)
    assert visit.secondary_staff_id is None
    assert visit.required_staff_count == 1
