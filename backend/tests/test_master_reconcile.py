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
    normalize_person_name,
    reconcile_names,
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
