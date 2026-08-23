"""マスタ相互突合 (Phase M・PO発案 2026-08-21) — 氏名突合の純関数 + API.

weekly-space-design.md Phase M。スペース/異体字のズレを吸収した突合で
①カイポケのみ ②らく助のみ ③表記ズレ を返す。
"""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User
from app.services.kaipoke.master_reconcile import (
    extract_names_from_kaipoke_csv,
    extract_staff_qualifications_from_kaipoke_csv,
    normalize_person_name,
    reconcile_names,
    reconcile_staff_qualifications,
)

_HEADER = (
    "職員名1,職種1,職員名2,職種2,同行2,職員名3,職種3,同行3,"
    "事業所名,日付,曜日,利用者,業務種別,サービス内容,"
    "開始時間,終了時間,提供時間,備考\n"
)


def test_normalize_person_name_absorbs_space_and_variants() -> None:
    assert normalize_person_name("髙梨　桂子") == normalize_person_name("髙梨桂子")
    assert normalize_person_name("今井 康敦") == normalize_person_name("今井　康敦")
    assert normalize_person_name("髙梨桂子") == "高梨桂子"  # 異体字 髙→高


def test_reconcile_names_categorizes() -> None:
    res = reconcile_names(
        kaipoke_names=["今井　康敦", "髙梨　桂子", "カイポケのみ 太郎", "-", ""],
        rakusuke_names=["今井 康敦", "髙梨桂子", "らく助のみ 花子"],
    )
    # 完全一致は無し・正規化一致だが表記違い = notation_diff 2件
    assert res.matched == 0
    assert ("今井　康敦", "今井 康敦") in res.notation_diff
    assert ("髙梨　桂子", "髙梨桂子") in res.notation_diff
    assert res.kaipoke_only == ["カイポケのみ 太郎"]
    assert res.rakusuke_only == ["らく助のみ 花子"]


def test_reconcile_names_exact_match_counts() -> None:
    res = reconcile_names(["田中　大河"], ["田中　大河"])
    assert res.matched == 1
    assert res.notation_diff == [] and res.kaipoke_only == [] and res.rakusuke_only == []


def test_extract_names_from_csv() -> None:
    csv = (
        _HEADER
        + "川名　千恵,看護師,髙梨　桂子,,,,,,より,3,月,患者A,医療保険,正看,09:00,09:35,35,\n"
        + "-,,,,,,,,より,4,火,患者B,医療保険,正看,10:00,10:35,35,\n"
    )
    patients, staff = extract_names_from_kaipoke_csv(csv)
    assert patients == ["患者A", "患者B"]
    assert "川名　千恵" in staff and "髙梨　桂子" in staff


# ---------------------------------------------------------------------------
# 資格のズレ / 未設定 (設計 kaipoke-service-content-design.md §1-2 / §4)
# ---------------------------------------------------------------------------


def test_extract_staff_qualifications_uses_staff1_columns() -> None:
    """職員名1 / 職種1 (列 0/1) だけを見る・職種空と '-' は落とす・初出優先。"""
    csv = (
        _HEADER
        + "川名　千恵,看護師,髙梨　桂子,准看護師,,,,,より,3,月,患者A,医療保険,正看,09:00,09:35,35,\n"
        + "川名　千恵,准看護師,,,,,,,より,4,火,患者B,医療保険,正看,10:00,10:35,35,\n"
        + "-,,,,,,,,より,5,水,患者C,医療保険,正看,11:00,11:35,35,\n"
        + "今井 康敦,,,,,,,,より,6,木,患者D,医療保険,正看,12:00,12:35,35,\n"
    )
    got = extract_staff_qualifications_from_kaipoke_csv(csv)
    # 職員1 のみ = 髙梨(職員名2)は入らない。川名は初出の「看護師」。
    assert got == {"川名千恵": ("川名　千恵", "看護師")}


def test_reconcile_staff_qualifications_statuses() -> None:
    kaipoke = {
        "川名千恵": ("川名　千恵", "看護師"),
        "高梨桂子": ("髙梨　桂子", "准看護師"),
        "今井康敦": ("今井 康敦", "看護師"),
        "知らない人": ("知らない 人", "看護師"),
    }
    rakusuke = [
        ("11111111-1111-1111-1111-111111111111", "川名 千恵", "看護師", "active"),  # 一致
        ("22222222-2222-2222-2222-222222222222", "髙梨桂子", "看護師", "active"),  # ズレ
        ("33333333-3333-3333-3333-333333333333", "今井　康敦", None, "active"),  # 未設定
    ]
    by_name = {d.name: d for d in reconcile_staff_qualifications(kaipoke, rakusuke)}

    assert by_name["川名 千恵"].status == "match"
    assert by_name["髙梨桂子"].status == "mismatch"
    assert by_name["髙梨桂子"].kaipoke_qualification == "准看護師"
    assert by_name["髙梨桂子"].rakusuke_qualification == "看護師"
    assert by_name["今井　康敦"].status == "missing_in_rakusuke"
    assert by_name["今井　康敦"].rakusuke_qualification is None
    # らく助に居ない人はカイポケ側の原文氏名で出す (正規化キーは表示に使わない)。
    assert by_name["知らない 人"].status == "unknown_staff"
    assert by_name["知らない 人"].staff_id is None


def test_reconcile_staff_qualifications_treats_blank_as_missing() -> None:
    """らく助側が空文字は「未設定」扱い (mismatch にしない)。"""
    got = reconcile_staff_qualifications(
        {"川名千恵": ("川名　千恵", "看護師")},
        [("11111111-1111-1111-1111-111111111111", "川名 千恵", "   ", "active")],
    )
    assert [d.status for d in got] == ["missing_in_rakusuke"]


def test_reconcile_staff_qualifications_normalizes_with_nfkc() -> None:
    """全角/半角・前後空白のゆれは同じ資格として扱う (偽の mismatch を出さない)。"""
    got = reconcile_staff_qualifications(
        {"川名千恵": ("川名　千恵", "准看護師 ")},
        [("11111111-1111-1111-1111-111111111111", "川名 千恵", "准看護師", "active")],
    )
    assert [d.status for d in got] == ["match"]


def test_reconcile_staff_qualifications_prefers_active_staff() -> None:
    """同名でも在職者が 1 人に決まるなら、その人を代表にする (退職者は無視)。"""
    got = reconcile_staff_qualifications(
        {"川名千恵": ("川名　千恵", "准看護師")},
        [
            ("11111111-1111-1111-1111-111111111111", "川名 千恵", "看護師", "inactive"),
            ("22222222-2222-2222-2222-222222222222", "川名千恵", None, "active"),
        ],
    )
    assert [(d.status, d.staff_id) for d in got] == [
        ("missing_in_rakusuke", "22222222-2222-2222-2222-222222222222")
    ]


def test_reconcile_staff_qualifications_ambiguous_when_two_active_same_name() -> None:
    """在職者が 2 人以上なら誰の資格か決められない = ambiguous (採用不可)。"""
    got = reconcile_staff_qualifications(
        {"川名千恵": ("川名　千恵", "准看護師")},
        [
            ("11111111-1111-1111-1111-111111111111", "川名 千恵", None, "active"),
            ("22222222-2222-2222-2222-222222222222", "川名　千恵", "看護師", "active"),
        ],
    )
    assert [d.status for d in got] == ["ambiguous"]
    # 採用ボタンを出させないため staff_id は返さない。
    assert got[0].staff_id is None


class _StubKaipokeClient:
    """export だけ差し替える最小スタブ (test_integration_kaipoke と同型)."""

    def __init__(self) -> None:
        self.responses: dict = {}
        self.calls: list = []

    async def aclose(self) -> None:  # pragma: no cover
        pass

    async def export(self, payload, *, timeout=None):
        self.calls.append(("export", payload))
        return self.responses.get("export", {})


@pytest.fixture
def stub_kaipoke():
    from app.services import kaipoke_client as kc_module

    stub = _StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


async def _make_admin(db) -> User:
    u = User(email="mr-admin@example.com", password_hash=hash_password("pw"), role="admin")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_master_reconcile_endpoint(client, db, stub_kaipoke) -> None:
    """API: export CSV × らく助マスタ → 3分類のレスポンス (read-only)."""
    admin = await _make_admin(db)
    db.add_all(
        [
            Patient(code="MR-1", name="患者A", status="active", special_week_active=[]),
            Patient(code="MR-2", name="らく助のみ患者", status="active", special_week_active=[]),
            Staff(name="髙梨桂子"),  # カイポケ側は「髙梨　桂子」→ 表記ズレ
        ]
    )
    await db.commit()

    csv = (
        _HEADER
        + "髙梨　桂子,看護師,,,,,,,より,3,月,患者A,医療保険,正看,09:00,09:35,35,\n"
        + "髙梨　桂子,看護師,,,,,,,より,4,火,カイポケのみ患者,医療保険,正看,10:00,10:35,35,\n"
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": csv}}

    res = await client.post(
        "/api/v1/integrations/master-reconcile",
        headers=_bearer(admin),
        json={"month": "2026-08"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["month"] == "2026-08"
    assert body["patients"]["matched"] == 1  # 患者A
    assert body["patients"]["kaipokeOnly"] == ["カイポケのみ患者"]
    assert "らく助のみ患者" in body["patients"]["rakusukeOnly"]
    assert body["staff"]["notationDiff"] == [{"kaipoke": "髙梨　桂子", "rakusuke": "髙梨桂子"}]


@pytest.mark.asyncio
async def test_master_reconcile_returns_staff_qualifications(client, db, stub_kaipoke) -> None:
    """API: 資格のズレ / 未設定 / 未知スタッフ を staffQualifications に載せる。"""
    admin = await _make_admin(db)
    db.add_all(
        [
            Staff(name="川名 千恵", qualification="看護師"),  # 一致
            Staff(name="髙梨桂子", qualification="看護師"),  # カイポケは准看護師 = ズレ
            Staff(name="今井　康敦"),  # らく助未設定
        ]
    )
    await db.commit()

    csv = (
        _HEADER
        + "川名　千恵,看護師,,,,,,,より,3,月,患者A,医療保険,正看,09:00,09:35,35,\n"
        + "髙梨　桂子,准看護師,,,,,,,より,4,火,患者B,医療保険,准看,10:00,10:35,35,\n"
        + "今井 康敦,看護師,,,,,,,より,5,水,患者C,医療保険,正看,11:00,11:35,35,\n"
        + "新人　太郎,准看護師,,,,,,,より,6,木,患者D,医療保険,准看,12:00,12:35,35,\n"
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": csv}}

    res = await client.post(
        "/api/v1/integrations/master-reconcile",
        headers=_bearer(admin),
        json={"month": "2026-08"},
    )
    assert res.status_code == 200, res.text
    rows = {r["name"]: r for r in res.json()["staffQualifications"]}

    assert rows["川名 千恵"]["status"] == "match"
    assert rows["髙梨桂子"]["status"] == "mismatch"
    assert rows["髙梨桂子"]["kaipokeQualification"] == "准看護師"
    assert rows["髙梨桂子"]["rakusukeQualification"] == "看護師"
    assert rows["今井　康敦"]["status"] == "missing_in_rakusuke"
    # 「カイポケの職種を採用」ボタンが叩けるよう staffId が要る。
    assert rows["今井　康敦"]["staffId"] is not None
    assert rows["新人　太郎"]["status"] == "unknown_staff"
    assert rows["新人　太郎"]["staffId"] is None
