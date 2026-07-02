"""Tests for P3-④ 効率優先の代替枠 (include_efficiency_alternatives).

POST /api/v1/schedule/v2/propose-slots の ``include_efficiency_alternatives`` を検証する.

確定仕様 (docs/plans/schedule-advisor-design.md §3 Phase 3):
    - 既定 (False) では出力・順位とも従来と完全に不変 (効率代替は一切付かない).
    - True では time_type / preferred_weekdays のハード制約を外して再列挙し、通常候補と
      重複しない slot (office×weekday×course×start) のうち、proximity+balance 合計が
      通常候補の最高スコアを上回るものを「効率代替」として最大 3 件、通常候補の後ろに
      ``is_efficiency_alternative=True`` を付けて追加する (通常候補の順位は不変).
    - 効率代替には reason「希望外だが効率的（近接/余裕）」を付け、希望曜日 / 希望時間帯一致の
      理由は付けない. P0-1 のスタッフ実態警告は通常候補と同様に適用する.

座標メモ (test_propose_slots_api.py と整合):
    BASE = (35.6000, 140.1000)
    NEAR = (35.6010, 140.1010)  → BASE から ~0.14km (近接 → proximity 高)
    FAR  = (35.6300, 140.1400)  → BASE から ~4.5km (遠い → proximity ~0)

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

from datetime import time
from typing import Any

import pytest

from tests.test_propose_slots_api import (
    BASE,
    FAR,
    ISO_WEEK,
    ISO_YEAR,
    NEAR,
    _bearer,
    _make_user,
    _seed_course,
    _seed_office_staff,
    _seed_patient,
    _seed_shift,
    _seed_visit,
)

REASON_EFFICIENCY = "希望外だが効率的（近接/余裕）"
PREF_REASONS = {"希望曜日", "希望時間帯一致"}


def _payload(office, **overrides: Any) -> dict[str, Any]:
    """候補 = BASE 近傍・希望曜日 Mon・午後希望 (効率代替を出しやすい既定)."""
    body: dict[str, Any] = {
        "lat": BASE[0],
        "lng": BASE[1],
        "service_minutes": 30,
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_ids": [str(office.id)],
        "preferred_weekdays": ["Mon"],
        "time_type": "午後",
    }
    body.update(overrides)
    return body


async def _seed_efficiency_scenario(db):
    """Mon (希望・遠い) + Tue (希望外・近い空きコース) を作る.

    - Mon course A: 候補から FAR の既存訪問 1 件 (午後). 希望適合だが proximity ~0.
      → 通常候補 (午後) が出るがスコア低め.
    - Tue course A: 候補に NEAR の既存訪問 1 件 (午前). 希望外 (Tue・午前) だが
      proximity 高 + 余裕大 → 効率代替の供給源.
    """
    office, staff = await _seed_office_staff(db)
    mon_course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    tue_course = await _seed_course(db, office=office, staff=staff, weekday=1, code="A")
    # 希望曜日 Mon: 遠い既存訪問 (午後) → 通常候補は proximity 低.
    pf = await _seed_patient(db, office=office, code="MONFAR", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=pf, course=mon_course, start=time(13, 0), end=time(13, 30))
    # 希望外 Tue: 近い既存訪問 (午前) → 効率代替 (proximity 高).
    pn = await _seed_patient(db, office=office, code="TUENEAR", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pn, course=tue_course, start=time(9, 30), end=time(10, 0))
    # 在番シフトを入れて staff_absent 降格を無効化する (閾値=通常候補スコアを素の状態にする).
    await _seed_shift(db, staff=staff, weekday=0, is_on=True)
    await _seed_shift(db, staff=staff, weekday=1, is_on=True)
    return office, staff


# ---------------------------------------------------------------------------
# 既定 False: 完全不変
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_efficiency_default_false_is_unchanged(client, db) -> None:
    """flag 省略 (= 既定 False) では効率代替は一切付かず、従来出力と完全一致する."""
    admin = await _make_user(db, email="eff-def@example.com", role="admin")
    office, _staff = await _seed_efficiency_scenario(db)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body
    # 希望 Mon・午後のみ (Tue は希望外なので通常候補には出ない).
    for s in body["slots"]:
        assert s["weekday_code"] == "Mon"
        assert s["is_efficiency_alternative"] is False
        assert REASON_EFFICIENCY not in s["reasons"]


@pytest.mark.asyncio
async def test_efficiency_false_equals_omitted(client, db) -> None:
    """include_efficiency_alternatives=False は flag 省略時と slots が完全一致."""
    admin = await _make_user(db, email="eff-false@example.com", role="admin")
    office, _staff = await _seed_efficiency_scenario(db)
    await db.commit()

    res_omit = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office),
    )
    res_false = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office, include_efficiency_alternatives=False),
    )
    assert res_omit.status_code == 200
    assert res_false.status_code == 200
    assert res_omit.json()["slots"] == res_false.json()["slots"]


# ---------------------------------------------------------------------------
# True: 効率代替の後置・採択基準・順位不変
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_efficiency_true_appends_alternatives(client, db) -> None:
    """True で効率代替 (Tue) が通常候補 (Mon) の後ろに付き、通常候補の順位は不変."""
    admin = await _make_user(db, email="eff-true@example.com", role="admin")
    office, _staff = await _seed_efficiency_scenario(db)
    await db.commit()

    res_false = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office, include_efficiency_alternatives=False),
    )
    res_true = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office, include_efficiency_alternatives=True),
    )
    assert res_false.status_code == 200
    assert res_true.status_code == 200
    slots_false = res_false.json()["slots"]
    slots_true = res_true.json()["slots"]

    normal_true = [s for s in slots_true if not s["is_efficiency_alternative"]]
    alt_true = [s for s in slots_true if s["is_efficiency_alternative"]]

    # 通常候補 (Mon) の並び・内容は False 時と完全一致 (順位不変).
    assert normal_true == slots_false
    # 効率代替が 1 件以上・最大 3 件で付く.
    assert 1 <= len(alt_true) <= 3
    # 効率代替は通常候補すべてより後ろにある (末尾に付加).
    first_alt_index = next(i for i, s in enumerate(slots_true) if s["is_efficiency_alternative"])
    assert all(not s["is_efficiency_alternative"] for s in slots_true[:first_alt_index])
    assert all(s["is_efficiency_alternative"] for s in slots_true[first_alt_index:])

    # 効率代替は希望外 Tue、理由に効率ラベルを持ち希望適合ラベルは持たない.
    for s in alt_true:
        assert s["weekday_code"] == "Tue"
        assert REASON_EFFICIENCY in s["reasons"]
        assert not (PREF_REASONS & set(s["reasons"]))

    # 重複除外: 効率代替は通常候補と (office×weekday×course×start) が重複しない.
    normal_keys = {
        (s["office_id"], s["weekday"], s["course_code"], s["start_time"]) for s in normal_true
    }
    for s in alt_true:
        key = (s["office_id"], s["weekday"], s["course_code"], s["start_time"])
        assert key not in normal_keys


@pytest.mark.asyncio
async def test_efficiency_not_added_when_below_threshold(client, db) -> None:
    """効率代替候補の proximity+balance が通常候補の最高スコア以下なら付かない.

    希望外コース (Tue) も候補から FAR にすると効率 (proximity) が低く、通常候補 (Mon,
    希望適合ボーナス込み) の最高スコアを超えないため、True でも効率代替は出ない.
    """
    admin = await _make_user(db, email="eff-below@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    mon_course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    tue_course = await _seed_course(db, office=office, staff=staff, weekday=1, code="A")
    # Mon: 近い既存訪問 (午後) → 通常候補は proximity 高 + 希望適合ボーナス.
    pm = await _seed_patient(db, office=office, code="MONNEAR", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pm, course=mon_course, start=time(13, 0), end=time(13, 30))
    # Tue: 遠い既存訪問 → 効率 (proximity) 低.
    pt = await _seed_patient(db, office=office, code="TUEFAR", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=pt, course=tue_course, start=time(9, 30), end=time(10, 0))
    await _seed_shift(db, staff=staff, weekday=0, is_on=True)
    await _seed_shift(db, staff=staff, weekday=1, is_on=True)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office, include_efficiency_alternatives=True),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body
    assert all(not s["is_efficiency_alternative"] for s in body["slots"]), body["slots"]


@pytest.mark.asyncio
async def test_efficiency_capped_at_three(client, db) -> None:
    """効率代替の供給源が 4 曜日あっても、付加されるのは最大 3 件."""
    admin = await _make_user(db, email="eff-cap@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # Mon (希望・遠い) → 通常候補.
    mon_course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    pf = await _seed_patient(db, office=office, code="MONFAR", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=pf, course=mon_course, start=time(13, 0), end=time(13, 30))
    await _seed_shift(db, staff=staff, weekday=0, is_on=True)
    # Tue..Fri (希望外・近い) → 効率代替の供給源 4 コース.
    for wd, code in [(1, "TUE"), (2, "WED"), (3, "THU"), (4, "FRI")]:
        c = await _seed_course(db, office=office, staff=staff, weekday=wd, code="A")
        p = await _seed_patient(db, office=office, code=f"{code}NEAR", lat=NEAR[0], lng=NEAR[1])
        await _seed_visit(db, patient=p, course=c, start=time(9, 30), end=time(10, 0))
        await _seed_shift(db, staff=staff, weekday=wd, is_on=True)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office, include_efficiency_alternatives=True, limit=50),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    alt = [s for s in body["slots"] if s["is_efficiency_alternative"]]
    assert len(alt) == 3, [s["weekday_code"] for s in alt]


@pytest.mark.asyncio
async def test_efficiency_alternative_carries_staff_warning(client, db) -> None:
    """P0-1: 効率代替コースの割付スタッフが非番なら staff_absent 警告が付く."""
    admin = await _make_user(db, email="eff-warn@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    mon_course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    tue_course = await _seed_course(db, office=office, staff=staff, weekday=1, code="A")
    pf = await _seed_patient(db, office=office, code="MONFAR", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=pf, course=mon_course, start=time(13, 0), end=time(13, 30))
    pn = await _seed_patient(db, office=office, code="TUENEAR", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pn, course=tue_course, start=time(9, 30), end=time(10, 0))
    # Mon は在番、Tue (効率代替コース) は非番 (staff_absent) にする.
    await _seed_shift(db, staff=staff, weekday=0, is_on=True)
    await _seed_shift(db, staff=staff, weekday=1, is_on=False)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_payload(office, include_efficiency_alternatives=True),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    alt = [s for s in body["slots"] if s["is_efficiency_alternative"]]
    assert alt, body["slots"]
    for s in alt:
        assert "staff_absent" in s["warnings"], s
