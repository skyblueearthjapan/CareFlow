"""らく助×カイポケ 週突合レポート (reconcile-report) のテスト。

* 純関数: 時刻の前ゼロ正規化 / (日,患者) ペアリングと相違分類
* API: スナップショット + visits を種まきして一致/相違/片側のみが数えられること
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.core.security import hash_password
from app.models import Staff, User
from app.services.kaipoke.reconcile_report_html import (
    ReconRow,
    _norm_time,
    _pair_group,
    render_reconcile_html,
)

WEEK_START = date(2026, 9, 7)  # 月曜


def _row(start: str, end: str, staff1: str, service: str = "精神基本療養費Ⅰ・正看") -> ReconRow:
    return ReconRow(start=start, end=end, staff1=staff1, staff2="", service=service)


def test_norm_time_zero_pads_single_digit_hour():
    assert _norm_time("9:50") == "09:50"
    assert _norm_time("09:50") == "09:50"
    assert _norm_time("") == ""


def test_pair_group_classifies_diffs_and_one_sided_rows():
    day = WEEK_START
    pairs = _pair_group(
        day,
        "山田 太郎",
        [_row("09:00", "09:35", "看護A"), _row("14:00", "14:35", "看護A")],
        [_row("09:00", "09:35", "看護B"), _row("16:00", "16:35", "看護A")],
    )
    cats = sorted(p.category for p in pairs)
    # 09:00 は担当違い / 14:00 は最寄り 16:00 とペアになり時刻違い
    assert cats == ["担当", "時刻"]

    pairs2 = _pair_group(day, "山田 太郎", [_row("09:00", "09:35", "A")], [])
    assert [p.category for p in pairs2] == ["らく助のみ"]
    pairs3 = _pair_group(day, "山田 太郎", [], [_row("09:00", "09:35", "A")])
    assert [p.category for p in pairs3] == ["カイポケのみ"]


SNAPSHOT_HEADER = (
    "職員名１,職種１,職員名２,職種２,同行２,職員名３,職種３,同行３,事業所名,日付,曜日,利用者,"
    "業務種別,サービス内容,開始時間,終了時間,提供時間（分）,備考"
)


def _snap_line(day: int, patient: str, start: str, end: str, staff1: str) -> str:
    return (
        f"{staff1},看護師,,,,,,,テスト事業所,{day},月,{patient},医療保険,"
        f"精神基本療養費Ⅰ・正看,{start},{end},35,"
    )


@pytest.mark.asyncio
async def test_reconcile_report_endpoint_counts_and_html(client, db):
    from app.models.patient import Patient
    from app.models.visit import Visit
    from app.services.kaipoke.csv_snapshot import save_snapshot

    staff = Staff(name="看護A")
    pat = Patient(code="RR-1", name="山田　太郎")
    db.add_all([staff, pat])
    await db.flush()
    # らく助側: 9/7 09:00 看護A
    db.add(
        Visit(
            patient_id=pat.id,
            primary_staff_id=staff.id,
            visit_date=WEEK_START,
            start_time=time(9, 0),
            end_time=time(9, 35),
            type="regular",
            status="planned",
        )
    )
    # カイポケ側スナップショット: 同時刻だが担当が別名 (相違=担当) + らく助に無い行
    csv_text = "\n".join(
        [
            SNAPSHOT_HEADER,
            _snap_line(7, "山田　太郎", "09:00", "09:35", "看護B"),
            _snap_line(8, "別人　花子", "10:00", "10:35", "看護A"),
        ]
    )
    await save_snapshot(
        db, office_id=None, month="2026-09", week_start=None, csv_text=csv_text, source_op="test"
    )
    await db.commit()

    admin = User(email="rr-admin@example.com", password_hash=hash_password("x"), role="admin")
    db.add(admin)
    await db.commit()
    from app.core.security import create_access_token

    headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(admin.id), role=admin.role)}"
    }

    res = await client.get(
        "/api/v1/integrations/reconcile-report",
        params={"weekStart": WEEK_START.isoformat(), "days": 7},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["counts"]["相違"] == 1  # 9/7 担当違い
    assert body["counts"]["カイポケのみ"] == 1  # 9/8 別人
    assert body["counts"]["一致"] == 0
    assert body["snapshots"][0]["month"] == "2026-09"
    assert body["html"] and "突合一覧" in body["html"]

    # format=html は text/html
    res2 = await client.get(
        "/api/v1/integrations/reconcile-report",
        params={"weekStart": WEEK_START.isoformat(), "format": "html"},
        headers=headers,
    )
    assert res2.status_code == 200
    assert res2.headers["content-type"].startswith("text/html")


def test_render_html_escapes_and_contains_summary():
    from datetime import UTC, datetime

    from app.services.kaipoke.reconcile_report_html import (
        ReconcileReport,
        ReconPair,
        SnapshotInfo,
    )

    rep = ReconcileReport(
        week_start=WEEK_START,
        week_end=WEEK_START,
        generated_at=datetime.now(UTC),
        snapshots=[SnapshotInfo(month="2026-09", fetched_at=None, row_count=0, source_op="なし")],
        pairs=[
            ReconPair(
                day=WEEK_START,
                patient="<script>x</script>",
                local=_row("09:00", "09:35", "A"),
                remote=None,
            )
        ],
    )
    html_text = render_reconcile_html(rep)
    assert "<script>x</script>" not in html_text
    assert "&lt;script&gt;" in html_text
    assert "らく助のみ" in html_text


def test_pair_group_absorbs_name_spacing_and_service_suffix():
    """空白/異体字ゆれの担当名・接尾が伸びたサービス内容は一致扱い (エンジンと同じ)。"""
    day = WEEK_START
    pairs = _pair_group(
        day,
        "山田 太郎",
        [
            ReconRow(
                start="09:00",
                end="09:35",
                staff1="小西彩稀",
                staff2="",
                service="精神基本療養費Ⅰ・正看",
            )
        ],
        [
            ReconRow(
                start="09:00",
                end="09:35",
                staff1="小西　彩稀",
                staff2="",
                service="精神基本療養費Ⅰ・正看・複数名",
            )
        ],
    )
    assert [p.category for p in pairs] == ["一致"]
