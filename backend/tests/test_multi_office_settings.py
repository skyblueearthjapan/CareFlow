"""別の事業所へ提供する準備 (mig 0089・multi-office-readiness-audit #1〜#4 / #7 / #9) のテスト.

いまのお客様 (よりより様) で見た目・帳票・上限が変わらないことを、今までコードにあった値
(旧実装) と、設定・拠点マスタから作る新しい実装の出力を突き合わせて確かめる。

* #4 略称: 旧 ``OFFICE_CODE_TO_SHORT`` (INAGE→稲 / TSUGA→津) と ``office_short`` が、
  mig 0059 / 0089 が入れた short_label で同じ値になる。新しい拠点は拠点名の 1 文字目。
* #2 / #3 Excel: 拠点のプルダウン・拠点コードの選択肢・コースの選択肢が今までと同じ並び。
  新しい拠点も選択肢に出て、カルテの拠点名から office_code に戻せる (住所の自動割当に
  黙って落ちない)。
* #9 住所の分解: 今までの千葉の住所は旧実装と同じ結果。別の都道府県の住所も取れる。
* #1 事業所の情報 API、#7 時刻を合わせる上限と予定外訪問の既定 (設定から読む)。
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.core.security import create_access_token, hash_password
from app.models import Office, User
from app.models.checkin_settings import CheckinSettings
from app.models.course_template import CourseTemplate
from app.models.patient import Patient
from app.services.office_labels import (
    office_code_short_pairs,
    office_short,
    ordered_office_codes,
    sorted_offices,
)
from app.services.patient_excel.exporter import _course_token_dropdown_values, build_workbook
from app.services.patient_excel.karte import (
    SHEET_KARTE,
    _office_label_to_code,
    build_karte_workbook,
    office_label_values,
)
from app.services.patient_excel.schema import (
    SHEET_PATIENTS,
    build_office_code_short_maps,
    course_token,
    parse_course_token,
)
from app.services.scheduling.auto_allocator_v2 import _extract_area_label
from app.services.staff_excel.exporter import build_workbook as build_staff_workbook
from app.services.staff_excel.schema import SHEET_STAFF

# ---------------------------------------------------------------------------
# 今までコードにあった値 (旧実装の写し。0088 以前の board_service / propose_slots_service /
# patient_excel / karte / staff_excel / CourseDayTablePanel)
# ---------------------------------------------------------------------------
LEGACY_OFFICE_CODE_TO_SHORT = {"INAGE": "稲", "TSUGA": "津"}
LEGACY_OFFICE_CODE_VALUES = ("INAGE", "TSUGA")
LEGACY_KARTE_OFFICE_LABELS = ("稲毛", "都賀")
LEGACY_GRID_OFFICE_ORDER = ("INAGE", "TSUGA")


def _legacy_course_label(office_code: str | None, course_code: str) -> str:
    """0088 以前の ``propose_slots_service._course_label`` / ``board_service._office_short``."""
    if not office_code:
        return course_code
    return f"{LEGACY_OFFICE_CODE_TO_SHORT.get(office_code, office_code)}{course_code}"


def _current_offices() -> list[Office]:
    """よりより様の本番の拠点 (mig 0059 / 0089 の後の状態)。"""
    return [
        Office(id=uuid4(), name="都賀", code="TSUGA", short_label="津", sort_order=2),
        Office(id=uuid4(), name="稲毛", code="INAGE", short_label="稲", sort_order=1),
    ]


def _dv_formulas(ws) -> dict[str, str]:
    """セル範囲 → プルダウンの式 ("A,B,...")。"""
    out: dict[str, str] = {}
    for dv in ws.data_validations.dataValidation:
        for rng in str(dv.sqref).split():
            out[rng] = dv.formula1
    return out


# ---------------------------------------------------------------------------
# #4 略称
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["INAGE", "TSUGA"])
@pytest.mark.parametrize("course_code", ["A", "B", "M", "臨2"])
def test_short_label_matches_legacy_for_current_offices(code: str, course_code: str) -> None:
    office = next(o for o in _current_offices() if o.code == code)
    from app.services.scheduling.propose_slots_service import _course_label

    assert _course_label(office_short(office.short_label, office.name), course_code) == (
        _legacy_course_label(code, course_code)
    )


def test_office_short_falls_back_to_first_char_of_name() -> None:
    assert office_short(None, "幕張") == "幕"
    assert office_short("  ", "幕張") == "幕"
    assert office_short("まく", "幕張") == "まく"
    assert office_short(None, None) == ""


def test_short_maps_from_master_match_legacy_for_current_offices() -> None:
    code_to_short, short_to_code = build_office_code_short_maps(
        office_code_short_pairs(_current_offices())
    )
    assert code_to_short == LEGACY_OFFICE_CODE_TO_SHORT
    assert short_to_code == {v: k for k, v in LEGACY_OFFICE_CODE_TO_SHORT.items()}
    for code in LEGACY_OFFICE_CODE_VALUES:
        for label in ("A", "M"):
            token = course_token(code, label, code_to_short)
            assert token == f"{LEGACY_OFFICE_CODE_TO_SHORT[code]}{label}"
            assert parse_course_token(token, short_to_code) == (code, label)
            # 後方互換: コードそのもの始まり。
            assert parse_course_token(f"{code}{label}", short_to_code) == (code, label)


def test_new_office_without_short_label_uses_first_char_and_parses() -> None:
    offices = [*_current_offices(), Office(id=uuid4(), name="幕張", code="MAKUHARI")]
    code_to_short, short_to_code = build_office_code_short_maps(office_code_short_pairs(offices))
    assert course_token("MAKUHARI", "A", code_to_short) == "幕A"
    assert parse_course_token("幕A", short_to_code) == ("MAKUHARI", "A")
    # 2 文字の略称も取り違えない。
    _, s2c = build_office_code_short_maps([("KAIHIN", "海浜"), ("INAGE", "稲")])
    assert parse_course_token("海浜B", s2c) == ("KAIHIN", "B")


# ---------------------------------------------------------------------------
# #9 拠点の並び / #2 #3 Excel の選択肢
# ---------------------------------------------------------------------------


def test_office_order_matches_legacy_grid_order() -> None:
    assert tuple(ordered_office_codes(_current_offices())) == LEGACY_GRID_OFFICE_ORDER
    # sort_order の無い拠点は名前順で後ろ。
    offices = [*_current_offices(), Office(id=uuid4(), name="幕張", code="MAKUHARI")]
    assert ordered_office_codes(offices) == ["INAGE", "TSUGA", "MAKUHARI"]
    assert [o.name for o in sorted_offices(offices)] == ["稲毛", "都賀", "幕張"]


def test_course_token_dropdown_matches_legacy_order() -> None:
    offices = _current_offices()
    by_code = {o.code: o for o in offices}
    templates = [
        CourseTemplate(id=uuid4(), office_id=by_code[code].id, label=label)
        for code in ("TSUGA", "INAGE")
        for label in ("M", "B", "A")
    ]
    tokens = _course_token_dropdown_values(offices, templates)
    assert tokens == ["稲A", "稲B", "稲M", "津A", "津B", "津M"]


def test_patient_and_staff_excel_office_code_dropdown_unchanged() -> None:
    offices = _current_offices()
    wb = build_workbook(patients=[], pfvs=[], offices=offices, course_templates=[])
    formulas = _dv_formulas(wb[SHEET_PATIENTS])
    assert '"INAGE,TSUGA"' in formulas.values()

    swb = build_staff_workbook(staff_list=[], shifts=[], offices=offices)
    assert '"INAGE,TSUGA"' in _dv_formulas(swb[SHEET_STAFF]).values()


def test_excel_office_code_dropdown_includes_new_office() -> None:
    offices = [*_current_offices(), Office(id=uuid4(), name="幕張", code="MAKUHARI")]
    wb = build_workbook(patients=[], pfvs=[], offices=offices, course_templates=[])
    assert '"INAGE,TSUGA,MAKUHARI"' in _dv_formulas(wb[SHEET_PATIENTS]).values()


def test_karte_office_dropdown_and_import_match_legacy() -> None:
    offices = _current_offices()
    assert office_label_values(offices) == LEGACY_KARTE_OFFICE_LABELS
    for label, code in zip(LEGACY_KARTE_OFFICE_LABELS, LEGACY_OFFICE_CODE_VALUES, strict=True):
        assert _office_label_to_code(label, offices) == code
        assert _office_label_to_code(f"{label}（自動）", offices) == code
        # 後方互換: コードそのもの。
        assert _office_label_to_code(code, offices) == code
    assert _office_label_to_code("不明", offices) is None
    assert _office_label_to_code(None, offices) is None

    patient = Patient(id=uuid4(), code="P1", name="t", status="active", primary_office_id=None)
    ws = build_karte_workbook(
        patient=patient, fixed_visits=[], offices=offices, course_templates=[]
    )[SHEET_KARTE]
    assert _dv_formulas(ws)["B6"] == '"稲毛,都賀"'


def test_karte_new_office_resolves_instead_of_auto_allocation() -> None:
    offices = [*_current_offices(), Office(id=uuid4(), name="幕張", code="MAKUHARI")]
    assert office_label_values(offices) == ("稲毛", "都賀", "幕張")
    # 以前は稲毛・都賀の決め打ちで、新しい拠点は None (= 住所からの自動割当) に落ちていた。
    assert _office_label_to_code("幕張（自動）", offices) == "MAKUHARI"


# ---------------------------------------------------------------------------
# #9 住所の分解
# ---------------------------------------------------------------------------

# 0088 以前の正規表現 (旧実装の写し)。
_LEGACY_WARD = re.compile(r"千葉県?千葉市?(?P<ward>[^区]+区)(?P<town>[^0-9０-９\s\-]+)")
_LEGACY_CITY = re.compile(r"(?P<city>[^市県\s]+市)(?P<town>[^0-9０-９\s\-]+)")
_LEGACY_TRAIL = re.compile(r"(町|丁目|番地|番).*$")


def _legacy_extract_area_label(address: str | None) -> str | None:
    if not address:
        return None
    m = _LEGACY_WARD.search(address)
    if m:
        town = m.group("town")
        return _LEGACY_TRAIL.sub("", town) or town[:6]
    m2 = _LEGACY_CITY.search(address)
    if m2:
        town = m2.group("town")
        return _LEGACY_TRAIL.sub("", town) or town[:8]
    return None


# いまのお客様の住所の形 (千葉市の各区・県名あり/なし・近隣市・空白入り・全角数字)。
CURRENT_CLIENT_ADDRESSES = [
    "千葉県千葉市稲毛区宮野木町818-2",
    "千葉県千葉市花見川区幕張本郷3-21-29",
    "千葉県千葉市美浜区磯辺4-175棟402",
    "千葉県千葉市若葉区都賀3-1-1",
    "千葉県千葉市若葉区若松町2100",
    "千葉県千葉市中央区中央1丁目1-1",
    "千葉県千葉市緑区おゆみ野3-1",
    "千葉県千葉市稲毛区小仲台６－２－１",
    "千葉県千葉市稲毛区 小仲台6-2-1",
    "千葉市稲毛区小仲台6-2-1",
    "千葉市若葉区桜木北1-2",
    "千葉県四街道市大日27-18",
    "千葉県佐倉市王子台1-1",
    "千葉県船橋市前原西2-1-1",
    "千葉県習志野市谷津1-1",
    "千葉県八千代市大和田新田100",
    "四街道市鷹の台1-2",
    "住所未登録",
    "",
    # 旧実装で None だった住所 (県名・市名の無い千葉市の区、千葉県の郡)。変えない。
    "若葉区都賀3-1-2",
    "稲毛区園生町1-2",
    "中央区新町",
    "千葉県印旛郡酒々井町中央台1-1",
]


@pytest.mark.parametrize(
    "address",
    ["若葉区都賀3-1-2", "稲毛区園生町1-2", "中央区新町", "千葉県印旛郡酒々井町中央台1-1"],
)
def test_area_label_stays_none_for_chiba_addresses_that_were_none(address: str) -> None:
    assert _legacy_extract_area_label(address) is None
    assert _extract_area_label(address) is None


@pytest.mark.parametrize("address", CURRENT_CLIENT_ADDRESSES)
def test_area_label_unchanged_for_current_client_addresses(address: str) -> None:
    assert _extract_area_label(address) == _legacy_extract_area_label(address)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("東京都新宿区西新宿2-8-1", "西新宿"),
        ("東京都世田谷区三軒茶屋1-2-3", "三軒茶屋"),
        ("神奈川県横浜市港北区日吉4-1-1", "日吉"),
        ("大阪府大阪市北区梅田1-1", "梅田"),
        ("北海道札幌市中央区北一条西2-1", "北一条西"),
        ("埼玉県入間郡三芳町藤久保1-2", "藤久保"),
        ("神奈川県藤沢市鵠沼海岸1-1", "鵠沼海岸"),
    ],
)
def test_area_label_other_prefectures(address: str, expected: str) -> None:
    assert _extract_area_label(address) == expected


# ---------------------------------------------------------------------------
# API: #1 事業所の情報 / #7 上限 / #4 拠点の編集
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_business_profile_get_put(client, db) -> None:
    admin = await _make_user(db, "bp-admin@example.com", "admin")
    staff = await _make_user(db, "bp-staff@example.com", "staff")

    # 行が無い = 全項目未設定 (カードに何も載らない)。
    res = await client.get("/api/v1/business-profile", headers=_bearer(staff))
    assert res.status_code == 200, res.text
    assert res.json() == {
        "station_name": None,
        "contact_tel": None,
        "contact_hours": None,
        "contact_days": None,
        "logo_url": None,
    }

    # staff は保存できない。
    res = await client.put(
        "/api/v1/business-profile", headers=_bearer(staff), json={"contact_tel": "0"}
    )
    assert res.status_code == 403

    seeded = {
        "station_name": "訪問看護ステーション よりより",
        "contact_tel": "043-215-8991",
        "contact_hours": "9:00〜18:00",
        "contact_days": "日曜・年末年始休暇を除く",
        "logo_url": "/brand/yoriyori-logo-h.svg",
    }
    res = await client.put("/api/v1/business-profile", headers=_bearer(admin), json=seeded)
    assert res.status_code == 200, res.text
    assert res.json() == seeded

    # 部分更新: 省略は不変、空文字は未設定に戻す。
    res = await client.put(
        "/api/v1/business-profile", headers=_bearer(admin), json={"logo_url": "  "}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {**seeded, "logo_url": None}

    # ロゴは「/」始まりか https の URL だけ。
    for bad in (
        "javascript:alert(1)",
        "http://example.com/a.svg",
        "//evil.example/a.svg",
        "/\\evil.example/x.svg",
        "/brand/a\x01.svg",
    ):
        res = await client.put(
            "/api/v1/business-profile", headers=_bearer(admin), json={"logo_url": bad}
        )
        assert res.status_code == 422, bad
    res = await client.put(
        "/api/v1/business-profile",
        headers=_bearer(admin),
        json={"logo_url": "https://cdn.example.com/logo.png"},
    )
    assert res.status_code == 200


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("/brand/yoriyori-logo-h.svg", True),
        ("/brand/logo_v2.png", True),
        ("https://cdn.example.com/a/b.svg?v=1", True),
        ("//evil.example/x.svg", False),
        ("/\\evil.example/x.svg", False),
        ("/brand/logo .svg", False),
        ("/brand/ロゴ.svg", False),
        ("/brand/a\x00.svg", False),
        ("https://evil.example\\x.svg", False),
        ("https:///evil", False),
        ("http://example.com/a.svg", False),
        ("brand/logo.svg", False),
    ],
)
def test_logo_url_rule(value: str, ok: bool) -> None:
    from app.schemas.business_profile import is_allowed_logo_url

    assert is_allowed_logo_url(value) is ok


@pytest.mark.asyncio
async def test_business_profile_first_put_race_updates_instead_of_500(
    client, db, monkeypatch
) -> None:
    """最初の PUT が同時に 2 本来た場合 (相手が先に行を作った) も 500 にせず更新として扱う.

    相手の INSERT が見えなかった状況を、1 回目の読み込みだけ「行なし」にして再現する。
    """
    from app.api.v1 import business_profile as api
    from app.models.business_profile import BusinessProfile

    admin = await _make_user(db, "bp-race@example.com", "admin")
    db.add(BusinessProfile(is_singleton=True, contact_tel="先に作られた行"))
    await db.commit()

    real_load = api._load_singleton
    calls = {"n": 0}

    async def first_miss(session):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real_load(session)

    monkeypatch.setattr(api, "_load_singleton", first_miss)
    res = await client.put(
        "/api/v1/business-profile", headers=_bearer(admin), json={"contact_tel": "2"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["contact_tel"] == "2"
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_checkin_settings_limits_defaults_and_public(client, db) -> None:
    admin = await _make_user(db, "lim-admin@example.com", "admin")
    staff = await _make_user(db, "lim-staff@example.com", "staff")

    res = await client.get("/api/v1/checkin-settings", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    values = res.json()["values"]
    assert values["arrival_max_back_min"] == 90
    assert values["departure_max_ahead_min"] == 30
    assert values["staff_adjust_window_days"] == 7
    assert values["unplanned_default_minutes"] == 60

    res = await client.put(
        "/api/v1/checkin-settings",
        headers=_bearer(admin),
        json={"arrival_max_back_min": 60, "staff_adjust_window_days": 3},
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_default"]["arrival_max_back_min"] is False

    res = await client.get("/api/v1/checkin-settings/public", headers=_bearer(staff))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["arrival_max_back_min"] == 60
    assert body["departure_max_ahead_min"] == 30
    assert body["staff_adjust_window_days"] == 3
    # 予定外訪問の仮の所要時間・判定の時間系は public に出さない。
    assert "unplanned_default_minutes" not in body
    assert "late_min" not in body

    for field, bad in (
        ("arrival_max_back_min", 9),
        ("departure_max_ahead_min", 181),
        ("staff_adjust_window_days", 32),
        ("unplanned_default_minutes", 9),
    ):
        res = await client.put(
            "/api/v1/checkin-settings", headers=_bearer(admin), json={field: bad}
        )
        assert res.status_code == 422, field


@pytest.mark.asyncio
async def test_can_adjust_window_reads_setting(db) -> None:
    from datetime import date, timedelta

    from app.services.checkin.adjust import can_adjust_actual_time, out_of_window_detail
    from app.services.checkin.judge import load_thresholds

    today = date(2026, 10, 1)
    kwargs = dict(is_admin=False, deleted=False, related=True, today=today, has_arrival_read=True)
    # 既定 (行なし) = 7 日。今までの STAFF_ADJUST_WINDOW_DAYS と同じ。
    days = (await load_thresholds(db))["staff_adjust_window_days"]
    assert days == 7
    assert can_adjust_actual_time(visit_date=today - timedelta(days=7), window_days=days, **kwargs)
    assert not can_adjust_actual_time(
        visit_date=today - timedelta(days=8), window_days=days, **kwargs
    )
    assert out_of_window_detail(days) == (
        "合わせられるのは 7 日前までの訪問です。管理者に依頼してください"
    )

    db.add(CheckinSettings(is_singleton=True, staff_adjust_window_days=2))
    await db.commit()
    days = (await load_thresholds(db))["staff_adjust_window_days"]
    assert days == 2
    assert not can_adjust_actual_time(
        visit_date=today - timedelta(days=3), window_days=days, **kwargs
    )


@pytest.mark.asyncio
async def test_office_edit_accepts_short_label_sort_order_kaipoke_name(client, db) -> None:
    admin = await _make_user(db, "of-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/offices",
        headers=_bearer(admin),
        json={
            "name": "幕張",
            "code": "MAKUHARI",
            "short_label": "幕",
            "sort_order": 3,
            "kaipoke_name": "訪問看護ステーション 幕張",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["short_label"], body["sort_order"], body["kaipoke_name"]) == (
        "幕",
        3,
        "訪問看護ステーション 幕張",
    )

    res = await client.patch(
        f"/api/v1/offices/{body['id']}",
        headers=_bearer(admin),
        json={"short_label": "張", "sort_order": None, "kaipoke_name": None},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["short_label"], body["sort_order"], body["kaipoke_name"]) == ("張", None, None)

    res = await client.get("/api/v1/offices", headers=_bearer(admin))
    assert res.status_code == 200
    assert any(o["short_label"] == "張" for o in res.json())


def test_karte_workbook_roundtrip_offices_is_loadable() -> None:
    """カルテの拠点プルダウンが Excel として読める (新しい拠点を含めても壊れない)."""
    from io import BytesIO

    from app.services.patient_excel.exporter import workbook_to_bytes

    offices = [*_current_offices(), Office(id=uuid4(), name="幕張", code="MAKUHARI")]
    patient = Patient(id=uuid4(), code="P1", name="t", status="active", primary_office_id=None)
    wb = build_karte_workbook(
        patient=patient, fixed_visits=[], offices=offices, course_templates=[]
    )
    reloaded = load_workbook(BytesIO(workbook_to_bytes(wb)))
    assert _dv_formulas(reloaded[SHEET_KARTE])["B6"] == '"稲毛,都賀,幕張"'
