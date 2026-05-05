"""W4-BE8: Layer 2 アルゴリズム / コース分け のテスト.

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.3 / §4.5 / §5.3 と
API 契約 ``docs/plans/v2-api-contracts.md`` §4.4 に対応。

検証観点 (受入基準):
  1. 24 名 → 4 コース × 6 名以内 (fixture: layer2_24patients.json)
  2. random_state=0 で総直線距離が naive round-robin の 70% 以下
  3. 時間衝突 (移動時間込み) が発生しない
  4. 24 名 → 4 コース 2-2 配分 (= 各コース 6 名)
  5. RBAC: admin/manager は 200, staff は 403, 認証なしは 401
  6. Layer1 → Layer2 統合: visits を DB から取得してクラスタリング
  7. 入力範囲外 / 空入力 / 単一クラスタなど境界ケース
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, time
from pathlib import Path
from uuid import UUID

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer2_clustering import (
    COURSE_CODES,
    MAX_PATIENTS_PER_COURSE,
    Layer2Clusterer,
    Layer2VisitInput,
    from_fixture_dicts,
    haversine_km,
    naive_round_robin,
)

# fixture 用の ISO 週. 月曜 = 2026-05-04.
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 19
TEST_WEEKDAY = 0
TEST_WEEK_MONDAY = date(2026, 5, 4)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "layer2_24patients.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _load_fixture() -> list[Layer2VisitInput]:
    """fixture JSON を読み込んで Layer2VisitInput リストに変換."""
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return from_fixture_dicts(data["patients"])


def _times_overlap(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


# ---------------------------------------------------------------------------
# 1) 受入基準: fixture 24 名で K-means 結果を評価
# ---------------------------------------------------------------------------


def test_fixture_loads_24_patients() -> None:
    """fixture が 24 件を含む."""
    visits = _load_fixture()
    assert len(visits) == 24


def test_kmeans_reduces_total_distance_below_70pct() -> None:
    """random_state=0 で総直線距離が naive round-robin の 70% 以下 (受入基準 2)."""
    visits = _load_fixture()
    baseline = naive_round_robin(visits, staff_count=4)
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)

    assert baseline.total_distance_km > 0
    ratio = result.total_distance_km / baseline.total_distance_km
    # 受入基準: <= 0.70 (= 30% 以上削減)
    assert ratio <= 0.70, (
        f"K-means ratio {ratio:.4f} > 0.70 "
        f"(kmeans={result.total_distance_km:.4f} km, "
        f"baseline={baseline.total_distance_km:.4f} km)"
    )


def test_kmeans_each_course_within_6_patients() -> None:
    """24 名 → 4 コース × 6 名以内 (受入基準 1)."""
    visits = _load_fixture()
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)

    assert len(result.proposals) == 4
    for p in result.proposals:
        assert len(p.patient_ids) <= MAX_PATIENTS_PER_COURSE, (
            f"Course {p.course_code} has {len(p.patient_ids)} patients (>6)"
        )


def test_kmeans_distributes_24_evenly_across_4_courses() -> None:
    """24 名 → 4 コース 2-2 配分 (= 各コース 6 名 = 完全 even)."""
    visits = _load_fixture()
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)

    sizes = [len(p.patient_ids) for p in result.proposals]
    # チケット要件: 24 名 → 4 コース 2-2 配分
    # fixture は 4 つの自然クラスタに 6 名ずつ配置されているため、
    # K-means は完全に 6/6/6/6 を返すはず。
    assert sorted(sizes) == [6, 6, 6, 6], f"Course sizes {sizes} != [6, 6, 6, 6]"
    # 全患者が漏れなく配置されている
    total = sum(sizes)
    assert total == len(visits)


def test_kmeans_no_time_conflicts_within_course() -> None:
    """各コース内で時間衝突 (start, end+5min) が発生しない (受入基準 3)."""
    visits = _load_fixture()
    by_pid = {v.patient_id: v for v in visits}
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)

    for proposal in result.proposals:
        assigned = [by_pid[pid] for pid in proposal.patient_ids]
        # 移動時間込みで重なりが無いこと
        n = len(assigned)
        for i in range(n):
            a = assigned[i]
            a_end_with_travel = time(
                min(23, a.end_time.hour + (a.end_time.minute + 5) // 60),
                (a.end_time.minute + 5) % 60,
            )
            for j in range(i + 1, n):
                b = assigned[j]
                b_end_with_travel = time(
                    min(23, b.end_time.hour + (b.end_time.minute + 5) // 60),
                    (b.end_time.minute + 5) % 60,
                )
                assert not _times_overlap(
                    a.start_time, a_end_with_travel, b.start_time, b_end_with_travel
                ), (
                    f"Time conflict in course {proposal.course_code}: "
                    f"{a.patient_id} ({a.start_time}-{a.end_time}) and "
                    f"{b.patient_id} ({b.start_time}-{b.end_time})"
                )


def test_kmeans_validity_score_high_on_balanced_fixture() -> None:
    """バランス良い fixture では各 proposal の validity_score が 1.0."""
    visits = _load_fixture()
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)

    for p in result.proposals:
        assert p.validity_score == 1.0, (
            f"Course {p.course_code} score={p.validity_score} (expected 1.0)"
        )
    # 平均 score
    assert result.validity_score == 1.0


def test_kmeans_all_patients_assigned_uniquely() -> None:
    """全患者が 1 コースだけに配置される (重複なし、欠落なし)."""
    visits = _load_fixture()
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)

    all_ids: list[UUID] = []
    for p in result.proposals:
        all_ids.extend(p.patient_ids)
    assert len(all_ids) == 24
    counts = Counter(all_ids)
    assert all(c == 1 for c in counts.values()), "Some patient assigned multiple times"
    expected_ids = {v.patient_id for v in visits}
    assert set(all_ids) == expected_ids


# ---------------------------------------------------------------------------
# 2) Haversine 距離関数の健全性
# ---------------------------------------------------------------------------


def test_haversine_zero_distance() -> None:
    """同一点の Haversine は 0."""
    assert haversine_km(35.65, 140.10, 35.65, 140.10) == 0.0


def test_haversine_known_distance() -> None:
    """千葉駅 (35.6131, 140.1133) → 稲毛駅 (35.6383, 140.1041) ≈ 2.92 km."""
    d = haversine_km(35.6131, 140.1133, 35.6383, 140.1041)
    # 概ね 2.5〜3.5 km の範囲に収まる
    assert 2.0 < d < 4.0, f"unexpected haversine {d}"


# ---------------------------------------------------------------------------
# 3) 純粋関数 cluster() のエッジケース
# ---------------------------------------------------------------------------


def test_cluster_empty_input_returns_empty_courses() -> None:
    """空入力 → 全コースが空 (各 score=1.0) で返る."""
    clusterer = Layer2Clusterer()
    result = clusterer.cluster([], staff_count=4, random_state=0)
    assert len(result.proposals) == 4
    for p in result.proposals:
        assert p.patient_ids == []
        assert p.total_distance_km == 0.0
        assert p.validity_score == 1.0
    assert result.total_distance_km == 0.0


def test_cluster_single_patient_assigned_to_one_course() -> None:
    """1 患者入力 → 1 コースだけ 1 名、他は空."""
    visit = Layer2VisitInput(
        patient_id=UUID(int=1),
        visit_id=UUID(int=1),
        start_time=time(9, 0),
        end_time=time(10, 0),
        service_min=60,
        lat=35.65,
        lng=140.10,
    )
    clusterer = Layer2Clusterer()
    result = clusterer.cluster([visit], staff_count=4, random_state=0)
    sizes = [len(p.patient_ids) for p in result.proposals]
    assert sorted(sizes) == [0, 0, 0, 1]


def test_cluster_invalid_staff_count_raises() -> None:
    """staff_count が範囲外なら Layer2ClusterError."""
    from app.services.scheduling.layer2_clustering import Layer2ClusterError

    clusterer = Layer2Clusterer()
    with pytest.raises(Layer2ClusterError):
        clusterer.cluster([], staff_count=0)
    with pytest.raises(Layer2ClusterError):
        clusterer.cluster([], staff_count=5)


def test_cluster_random_state_is_reproducible() -> None:
    """同じ random_state は同じ結果を返す (再現性)."""
    visits = _load_fixture()
    clusterer = Layer2Clusterer()
    r1 = clusterer.cluster(visits, staff_count=4, random_state=0)
    r2 = clusterer.cluster(visits, staff_count=4, random_state=0)
    assert r1.total_distance_km == r2.total_distance_km
    for p1, p2 in zip(r1.proposals, r2.proposals, strict=True):
        assert sorted(str(x) for x in p1.patient_ids) == sorted(str(x) for x in p2.patient_ids)


# ---------------------------------------------------------------------------
# 4) 容量超過時のオーバーフロー処理 (Q1)
# ---------------------------------------------------------------------------


def test_cluster_overflow_redistributes_to_least_loaded() -> None:
    """同一座標に 7 名集めると 6 名上限を超えるので近傍コースへ移動.

    全員同一クラスタに固まる入力なので K-means は 1 つのクラスタに 7 名を
    入れようとするが、容量制約後処理で別コースへ分配される。
    """
    visits: list[Layer2VisitInput] = []
    for i in range(7):
        visits.append(
            Layer2VisitInput(
                patient_id=UUID(int=i + 1),
                visit_id=UUID(int=i + 1),
                # 時間帯を 1 時間ずつずらして衝突を回避
                start_time=time(9 + i, 0),
                end_time=time(9 + i, 30),
                service_min=30,
                lat=35.65 + 0.0001 * i,  # ほぼ同一座標
                lng=140.10 + 0.0001 * i,
            )
        )
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)
    # 全 visit が配置されること
    total = sum(len(p.patient_ids) for p in result.proposals)
    assert total == 7
    # 6 名以下が守られること
    for p in result.proposals:
        assert len(p.patient_ids) <= MAX_PATIENTS_PER_COURSE


# ---------------------------------------------------------------------------
# 5) 時間衝突解消後処理
# ---------------------------------------------------------------------------


def test_cluster_resolves_time_conflict_in_close_cluster() -> None:
    """時刻が完全に重なる 2 件が近接座標にあった場合、別コースへ移動される."""
    visits = [
        Layer2VisitInput(
            patient_id=UUID(int=1),
            visit_id=UUID(int=1),
            start_time=time(10, 0),
            end_time=time(11, 0),
            service_min=60,
            lat=35.65,
            lng=140.10,
        ),
        Layer2VisitInput(
            patient_id=UUID(int=2),
            visit_id=UUID(int=2),
            start_time=time(10, 0),  # 完全衝突
            end_time=time(11, 0),
            service_min=60,
            lat=35.6501,
            lng=140.1001,
        ),
    ]
    clusterer = Layer2Clusterer()
    result = clusterer.cluster(visits, staff_count=4, random_state=0)
    # 2 名が別コースに配置される (= 2 つのコースで non-empty)
    non_empty = [p for p in result.proposals if p.patient_ids]
    assert len(non_empty) == 2


# ---------------------------------------------------------------------------
# 6) HTTP API: POST /api/v1/courses/generate (RBAC + 統合)
# ---------------------------------------------------------------------------


async def _seed_24_patients_with_visits(db, *, weekday: int = TEST_WEEKDAY) -> None:
    """fixture をベースに 24 名の Patient + 24 件の Visit を seed する.

    Layer 2 が DB から visits を取り出すルートを統合検証するためのヘルパー。
    """
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    visit_date = date.fromordinal(TEST_WEEK_MONDAY.toordinal() + weekday)
    for i, item in enumerate(data["patients"]):
        patient = Patient(
            code=f"L2-{i + 1:03d}",
            name=f"L2 患者 {i + 1}",
            status="active",
            lat=item["lat"],
            lng=item["lng"],
        )
        db.add(patient)
        await db.flush()
        sh, sm = (int(x) for x in item["start_time"].split(":"))
        eh, em = (int(x) for x in item["end_time"].split(":"))
        visit = Visit(
            patient_id=patient.id,
            visit_date=visit_date,
            start_time=time(sh, sm),
            end_time=time(eh, em),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
        db.add(visit)
    await db.commit()


@pytest.mark.asyncio
async def test_generate_returns_200_with_4_proposals(client, db) -> None:
    """POST /generate が 200 + 4 案 を返す."""
    admin = await _make_user(db, "l2-1-admin@example.com", "admin")
    await _seed_24_patients_with_visits(db)

    res = await client.post(
        "/api/v1/courses/generate",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "weekday": TEST_WEEKDAY,
            "staff_count": 4,
            "random_state": 0,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "proposals" in body
    assert len(body["proposals"]) == 4
    codes = sorted(p["course_code"] for p in body["proposals"])
    assert codes == sorted(COURSE_CODES)
    # 24 名全員が配置される
    total = sum(len(p["patient_ids"]) for p in body["proposals"])
    assert total == 24


@pytest.mark.asyncio
async def test_generate_no_token_returns_401(client) -> None:
    res = await client.post(
        "/api/v1/courses/generate",
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "weekday": TEST_WEEKDAY,
        },
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_generate_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "l2-2-staff@example.com", "staff")
    res = await client.post(
        "/api/v1/courses/generate",
        headers=_bearer(staff),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "weekday": TEST_WEEKDAY,
        },
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_generate_manager_allowed(client, db) -> None:
    manager = await _make_user(db, "l2-3-mgr@example.com", "manager")
    res = await client.post(
        "/api/v1/courses/generate",
        headers=_bearer(manager),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "weekday": TEST_WEEKDAY,
        },
    )
    # データなしでも 200 (空コース 4 つ)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["proposals"]) == 4
    for p in body["proposals"]:
        assert p["patient_ids"] == []


@pytest.mark.asyncio
async def test_generate_invalid_weekday_returns_422(client, db) -> None:
    admin = await _make_user(db, "l2-4-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/courses/generate",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "weekday": 7,  # 範囲外
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_generate_invalid_iso_week_returns_422(client, db) -> None:
    admin = await _make_user(db, "l2-5-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/courses/generate",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": 0,  # 範囲外
            "weekday": 0,
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_generate_does_not_persist_courses(client, db) -> None:
    """generate は案を返すのみ. DB に Course レコードは作られない."""
    from sqlalchemy import select

    from app.models import Course

    admin = await _make_user(db, "l2-6-admin@example.com", "admin")
    await _seed_24_patients_with_visits(db)

    res = await client.post(
        "/api/v1/courses/generate",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "weekday": TEST_WEEKDAY,
            "staff_count": 4,
            "random_state": 0,
        },
    )
    assert res.status_code == 200, res.text

    # DB に Course レコードが無い
    rows = (await db.scalars(select(Course))).all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_generate_skips_patient_without_coordinates(client, db) -> None:
    """lat/lng が NULL の患者は K-means に乗らずスキップされる."""
    admin = await _make_user(db, "l2-7-admin@example.com", "admin")

    # 座標ありの patient + visit を 4 件
    visit_date = date.fromordinal(TEST_WEEK_MONDAY.toordinal() + TEST_WEEKDAY)
    for i in range(4):
        p = Patient(
            code=f"L2-OK-{i:02d}",
            name=f"OK {i}",
            status="active",
            lat=35.65 + 0.001 * i,
            lng=140.10 + 0.001 * i,
        )
        db.add(p)
        await db.flush()
        v = Visit(
            patient_id=p.id,
            visit_date=visit_date,
            start_time=time(9 + i, 0),
            end_time=time(9 + i, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
        db.add(v)

    # 座標なしの patient + visit を 1 件 (= スキップされるべき)
    p_no_coord = Patient(
        code="L2-NO-COORD",
        name="座標なし",
        status="active",
        lat=None,
        lng=None,
    )
    db.add(p_no_coord)
    await db.flush()
    v_no_coord = Visit(
        patient_id=p_no_coord.id,
        visit_date=visit_date,
        start_time=time(13, 0),
        end_time=time(14, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
    )
    db.add(v_no_coord)
    await db.commit()

    res = await client.post(
        "/api/v1/courses/generate",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "weekday": TEST_WEEKDAY,
            "random_state": 0,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    total = sum(len(p["patient_ids"]) for p in body["proposals"])
    # 座標なしを除いた 4 名のみ
    assert total == 4


# ---------------------------------------------------------------------------
# 7) Service direct invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_load_visits_excludes_other_weekdays(db) -> None:
    """指定曜日以外の visit は Layer 2 入力に含まれない."""
    clusterer = Layer2Clusterer()
    week_monday = TEST_WEEK_MONDAY  # Monday

    # 月曜の visit
    p1 = Patient(code="L2-WD-01", name="月曜", status="active", lat=35.65, lng=140.10)
    db.add(p1)
    await db.flush()
    db.add(
        Visit(
            patient_id=p1.id,
            visit_date=week_monday,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )

    # 火曜の visit (別曜日 = 除外)
    p2 = Patient(code="L2-WD-02", name="火曜", status="active", lat=35.66, lng=140.11)
    db.add(p2)
    await db.flush()
    db.add(
        Visit(
            patient_id=p2.id,
            visit_date=date.fromordinal(week_monday.toordinal() + 1),
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()

    # 月曜 (weekday=0) でクラスタリング
    result = await clusterer.generate_proposals(
        db,
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        staff_count=4,
        random_state=0,
    )
    total = sum(len(p.patient_ids) for p in result.proposals)
    assert total == 1  # 月曜の 1 件のみ


@pytest.mark.asyncio
async def test_service_load_visits_excludes_cancelled(db) -> None:
    """status != 'planned' の visit は除外される."""
    clusterer = Layer2Clusterer()

    p1 = Patient(code="L2-ST-01", name="planned", status="active", lat=35.65, lng=140.10)
    db.add(p1)
    await db.flush()
    db.add(
        Visit(
            patient_id=p1.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    p2 = Patient(code="L2-ST-02", name="cancelled", status="active", lat=35.66, lng=140.11)
    db.add(p2)
    await db.flush()
    db.add(
        Visit(
            patient_id=p2.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(11, 0),
            end_time=time(12, 0),
            type="regular",
            status="cancelled",
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await clusterer.generate_proposals(
        db,
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        staff_count=4,
        random_state=0,
    )
    total = sum(len(p.patient_ids) for p in result.proposals)
    assert total == 1  # planned のみ
