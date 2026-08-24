"""GET /api/v1/staff/{id}/events の絞り込み・検索 (staff-event-history-design.md §2 Phase 1).

検証観点:
  1. 後方互換 — パラメータ無しは従来どおり starts_at 昇順・全件
  2. ``q``            — title / note の部分一致 (大小文字を問わない)
  3. ``source``       — 'manual' | 'kaipoke' | 'fixed' の完全一致
  4. ``type``         — event_type の完全一致 ('training' / 'event')
  5. ``order``        — starts_at の昇順 / 降順
  6. ``hide_regular`` — source='fixed' と **staff_event_defaults のタイトル**
     を除外。タイトルのハードコードが無い (= defaults テーブル駆動) ことを
     「defaults 行を足すと同じ API 応答から消える」形で確認する
  7. 併用 — from/to と各フィルタの組み合わせ
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.staff import StaffEvent, StaffEventDefault


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff(db, name: str = "絞込 太郎") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _add_event(
    db,
    staff: Staff,
    *,
    day: str,
    start: str = "09:00",
    end: str = "10:00",
    title: str,
    note: str | None = None,
    event_type: str = "event",
    source: str = "manual",
) -> StaffEvent:
    d = date.fromisoformat(day)
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    row = StaffEvent(
        staff_id=staff.id,
        event_type=event_type,
        starts_at=datetime.combine(d, time(sh, sm)),
        ends_at=datetime.combine(d, time(eh, em)),
        title=title,
        note=note,
        source=source,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _seed(db, staff: Staff) -> None:
    """本番の実データを模した 6 件 (朝会2 / 面談 / 研修 / 会議 / カイポケ取込)."""
    await _add_event(db, staff, day="2026-08-10", start="09:00", end="09:15", title="朝会")
    await _add_event(
        db,
        staff,
        day="2026-08-11",
        start="09:00",
        end="09:15",
        title="朝会",
        source="fixed",
    )
    await _add_event(
        db,
        staff,
        day="2026-08-12",
        start="13:00",
        end="14:00",
        title="カンファレンス",
        source="kaipoke",
    )
    await _add_event(
        db,
        staff,
        day="2026-08-14",
        start="15:00",
        end="16:00",
        title="面談 松岡",
        note="ご自宅にて",
    )
    await _add_event(
        db,
        staff,
        day="2026-08-19",
        start="14:00",
        end="15:30",
        title="研修：基本マナー",
        event_type="training",
    )
    await _add_event(
        db,
        staff,
        day="2026-08-21",
        start="16:00",
        end="17:00",
        title="鈴木乃愛様 担当者会議",
        note="草野中学校",
    )


def _titles(res) -> list[str]:
    return [r["title"] for r in res.json()]


@pytest.mark.asyncio
async def test_no_params_is_backward_compatible(client, db) -> None:
    """1. パラメータ無し = 従来どおり全件 starts_at 昇順."""
    admin = await _make_user(db, "evf-compat@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)

    res = await client.get(f"/api/v1/staff/{staff.id}/events", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 6
    assert [r["date"] for r in body] == sorted(r["date"] for r in body)
    assert body[0]["title"] == "朝会"
    assert body[-1]["title"] == "鈴木乃愛様 担当者会議"


@pytest.mark.asyncio
async def test_q_matches_title_and_note(client, db) -> None:
    """2. q は title と note の両方に部分一致する."""
    admin = await _make_user(db, "evf-q@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)

    res = await client.get(f"/api/v1/staff/{staff.id}/events?q=鈴木", headers=headers)
    assert res.status_code == 200
    assert _titles(res) == ["鈴木乃愛様 担当者会議"]

    # note 側のヒット (title には「自宅」が無い)
    res = await client.get(f"/api/v1/staff/{staff.id}/events?q=ご自宅", headers=headers)
    assert _titles(res) == ["面談 松岡"]

    # 前後の空白は無視 / 0 件は空配列
    res = await client.get(f"/api/v1/staff/{staff.id}/events?q=%20%20", headers=headers)
    assert len(res.json()) == 6
    res = await client.get(f"/api/v1/staff/{staff.id}/events?q=存在しない語", headers=headers)
    assert res.json() == []


@pytest.mark.asyncio
async def test_q_is_case_insensitive(client, db) -> None:
    """2b. ILIKE — 英字は大小文字を問わない."""
    admin = await _make_user(db, "evf-qcase@example.com", "admin")
    staff = await _make_staff(db)
    await _add_event(db, staff, day="2026-08-19", title="Zoom 研修", note=None)

    for needle in ("zoom", "ZOOM", "Zoom"):
        res = await client.get(
            f"/api/v1/staff/{staff.id}/events?q={needle}", headers=_bearer(admin)
        )
        assert _titles(res) == ["Zoom 研修"], needle


@pytest.mark.asyncio
async def test_source_filter(client, db) -> None:
    """3. source は完全一致."""
    admin = await _make_user(db, "evf-src@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)

    res = await client.get(f"/api/v1/staff/{staff.id}/events?source=kaipoke", headers=headers)
    assert _titles(res) == ["カンファレンス"]
    assert res.json()[0]["source"] == "kaipoke"

    res = await client.get(f"/api/v1/staff/{staff.id}/events?source=fixed", headers=headers)
    assert len(res.json()) == 1
    assert res.json()[0]["source"] == "fixed"

    res = await client.get(f"/api/v1/staff/{staff.id}/events?source=manual", headers=headers)
    assert len(res.json()) == 4


@pytest.mark.asyncio
async def test_type_filter(client, db) -> None:
    """4. type は event_type の完全一致 ('training' / 'event')."""
    admin = await _make_user(db, "evf-type@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)

    res = await client.get(f"/api/v1/staff/{staff.id}/events?type=training", headers=headers)
    assert _titles(res) == ["研修：基本マナー"]
    assert res.json()[0]["type"] == "研修"

    res = await client.get(f"/api/v1/staff/{staff.id}/events?type=event", headers=headers)
    assert len(res.json()) == 5


@pytest.mark.asyncio
async def test_order_desc(client, db) -> None:
    """5. order=desc で starts_at 降順 (過去タブの遡り)."""
    admin = await _make_user(db, "evf-order@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)

    asc = await client.get(f"/api/v1/staff/{staff.id}/events?order=asc", headers=headers)
    desc = await client.get(f"/api/v1/staff/{staff.id}/events?order=desc", headers=headers)
    assert [r["id"] for r in desc.json()] == list(reversed([r["id"] for r in asc.json()]))

    bad = await client.get(f"/api/v1/staff/{staff.id}/events?order=sideways", headers=headers)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_hide_regular_excludes_fixed_source(client, db) -> None:
    """6a. hide_regular は source='fixed' を必ず除外する."""
    admin = await _make_user(db, "evf-hide1@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)

    res = await client.get(f"/api/v1/staff/{staff.id}/events?hide_regular=true", headers=headers)
    assert res.status_code == 200
    assert all(r["source"] != "fixed" for r in res.json())
    # defaults 未登録なので manual の「朝会」はまだ残る (= タイトルのハードコード無し)
    assert "朝会" in _titles(res)
    assert len(res.json()) == 5


@pytest.mark.asyncio
async def test_hide_regular_is_driven_by_defaults_table(client, db) -> None:
    """6b. **朝会などのタイトルをコードに持たない**証明.

    同じデータ・同じリクエストでも、``staff_event_defaults`` に「朝会」を
    登録した瞬間に manual の「朝会」が hide_regular で消える。逆に defaults に
    「面談 松岡」を入れればそれも消える = 判定はテーブル駆動。
    """
    admin = await _make_user(db, "evf-hide2@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)
    url = f"/api/v1/staff/{staff.id}/events?hide_regular=true"

    before = _titles(await client.get(url, headers=headers))
    assert "朝会" in before

    db.add(
        StaffEventDefault(
            staff_id=staff.id,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(9, 15),
            title="朝会",
        )
    )
    await db.commit()

    after = _titles(await client.get(url, headers=headers))
    assert "朝会" not in after
    assert set(after) == {
        "カンファレンス",
        "面談 松岡",
        "研修：基本マナー",
        "鈴木乃愛様 担当者会議",
    }

    # 任意のタイトルで同じことが起きる (朝会は特別扱いされていない)
    db.add(
        StaffEventDefault(
            staff_id=staff.id,
            weekday=4,
            start_time=time(15, 0),
            end_time=time(16, 0),
            title="面談 松岡",
        )
    )
    await db.commit()
    after2 = _titles(await client.get(url, headers=headers))
    assert "面談 松岡" not in after2

    # hide_regular なしなら全件のまま (defaults は既定表示に影響しない)
    plain = await client.get(f"/api/v1/staff/{staff.id}/events", headers=headers)
    assert len(plain.json()) == 6


@pytest.mark.asyncio
async def test_hide_regular_default_is_false(client, db) -> None:
    """6c. 既定 False — 明示しなければ何も除外されない."""
    admin = await _make_user(db, "evf-hide3@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    db.add(
        StaffEventDefault(
            staff_id=staff.id,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(9, 15),
            title="朝会",
        )
    )
    await db.commit()

    res = await client.get(f"/api/v1/staff/{staff.id}/events", headers=_bearer(admin))
    assert len(res.json()) == 6


@pytest.mark.asyncio
async def test_filters_combine_with_range(client, db) -> None:
    """7. from/to と各フィルタの併用."""
    admin = await _make_user(db, "evf-combo@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)

    res = await client.get(
        f"/api/v1/staff/{staff.id}/events?from=2026-08-13&to=2026-08-31&order=desc&source=manual",
        headers=headers,
    )
    assert res.status_code == 200
    assert _titles(res) == ["鈴木乃愛様 担当者会議", "研修：基本マナー", "面談 松岡"]

    res = await client.get(
        f"/api/v1/staff/{staff.id}/events?q=会議&hide_regular=true&type=event",
        headers=headers,
    )
    assert _titles(res) == ["鈴木乃愛様 担当者会議"]


@pytest.mark.asyncio
async def test_offset_paginates(client, db) -> None:
    """8. offset — limit と組み合わせてページングできる (レビュー指摘対応)."""
    admin = await _make_user(db, "evf-offset@example.com", "admin")
    staff = await _make_staff(db)
    await _seed(db, staff)
    headers = _bearer(admin)

    full = await client.get(f"/api/v1/staff/{staff.id}/events?order=asc", headers=headers)
    ids = [r["id"] for r in full.json()]
    assert len(ids) >= 3

    page = await client.get(
        f"/api/v1/staff/{staff.id}/events?order=asc&limit=2&offset=1", headers=headers
    )
    assert [r["id"] for r in page.json()] == ids[1:3]

    bad = await client.get(f"/api/v1/staff/{staff.id}/events?offset=-1", headers=headers)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_q_escapes_like_wildcards(client, db) -> None:
    """9. q の % / _ はワイルドカードではなく文字として検索される."""
    admin = await _make_user(db, "evf-esc@example.com", "admin")
    staff = await _make_staff(db)
    await _add_event(db, staff, day="2026-08-20", title="達成率100%報告")
    await _add_event(db, staff, day="2026-08-21", title="達成率1000件報告")
    headers = _bearer(admin)

    res = await client.get(f"/api/v1/staff/{staff.id}/events?q=100%25", headers=headers)
    assert res.status_code == 200
    assert [r["title"] for r in res.json()] == ["達成率100%報告"]
