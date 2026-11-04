"""月の実績から反映するとき、記録書Ⅱの「実際の訪問時間」も同じ時刻にそろえる (PO 決定 2026-11-04).

正典: ``docs/plans/kaipoke-kiroku2-time-design-2026-11-04.md`` §2-2・§3 の 3〜4。
実績も書き換える行は ``/api/actual-time/apply`` の項目に ``care_record`` を付け、実績はもう
QR どおりで記録書Ⅱだけ違う行は ``/api/care-record-time/apply`` で送る (実績 → 記録書Ⅱだけ の順)。
RPA (KaipokeClient) はモックに差し替える (本物のカイポケにはつながない)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot
from app.models.monthly_actual import MonthlyActualRow
from app.services import kaipoke_client
from app.services.kaipoke import sources
from app.services.monthly_actual import judge as rules
from app.services.monthly_actual import service as svc
from tests.test_monthly_actual_api import HEADER, MONTH, URL, _bearer, _by_patient, _seed, _user
from tests.test_monthly_actual_judge import codes, kp, one, rk

#: (職員, 日, 利用者, 開始, 終了, 記録Ⅱ開始, 記録Ⅱ終了)
Line = tuple[str, int, str, str, str, str, str]

#: カイポケの実績 (反映前・記録書Ⅱつき)。
KP_K2: list[Line] = [
    # 実績は QR どおり・記録書Ⅱだけ違う → 記録書Ⅱだけ
    ("佐藤　花子", 1, "青木　さくら", "10:00", "10:40", "09:55", "10:40"),
    # 実績も記録書Ⅱも違う → 実績と続けて記録書Ⅱも
    ("佐藤　花子", 1, "石田　健", "11:00", "11:35", "11:00", "11:35"),
    # 記録書Ⅱが無い → 実績だけ
    ("佐藤　花子", 2, "木下　誠", "16:15", "16:50", "", ""),
    ("佐藤　花子", 3, "斉藤　大地", "17:20", "17:55", "17:20", "17:55"),
    ("佐藤　花子", 6, "上野　ゆり", "13:00", "13:35", "", ""),
]
#: 全部そろった後。
KP_K2_AFTER: list[Line] = [
    ("佐藤　花子", 1, "青木　さくら", "10:00", "10:40", "10:00", "10:40"),
    ("佐藤　花子", 1, "石田　健", "11:03", "11:41", "11:03", "11:41"),
    ("佐藤　花子", 2, "木下　誠", "16:16", "16:51", "", ""),
    ("佐藤　花子", 3, "斉藤　大地", "17:20", "17:55", "17:20", "17:55"),
    ("佐藤　花子", 6, "上野　ゆり", "13:00", "13:35", "", ""),
]


def _csv(lines: list[Line]) -> str:
    body = []
    for staff, day, patient, start, end, k2s, k2e in lines:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        minutes = (eh * 60 + em) - (sh * 60 + sm)
        body.append(
            f"{staff},看護師,,,,{day},{patient},医療保険,訪問看護基本療養費,"
            f"{start},{end},{minutes},{k2s},{k2e}"
        )
    return "\n".join([HEADER, *body])


class FakeKaipoke:
    """RPA のモック。実績の口と記録書Ⅱだけの口の両方を覚える (呼ばれた順も)。"""

    def __init__(
        self,
        *,
        export_lines: list[Line] | None = None,
        care_results: dict[str, tuple[str, str | None]] | None = None,
    ) -> None:
        self.export_lines = export_lines or KP_K2_AFTER
        #: 本番のときだけ、利用者ごとに記録書Ⅱの (status, reason) を差し替える。
        self.care_results = care_results or {}
        self.calls: list[str] = []
        self.actual_payloads: list[dict[str, Any]] = []
        self.care_payloads: list[dict[str, Any]] = []
        self.exports: list[dict[str, Any]] = []
        self._slots: dict[str, dict[str, Any]] = {}

    async def status(self) -> dict[str, Any]:
        return {"current_task": {"running": False}}

    async def actual_time_apply(self, payload, *, timeout=None) -> dict[str, Any]:
        self.calls.append("actual")
        self.actual_payloads.append(dict(payload))
        self._slots["actual"] = dict(payload)
        return {"success": True, "async": True, "job_id": payload["job_id"]}

    async def care_record_time_apply(self, payload, *, timeout=None) -> dict[str, Any]:
        self.calls.append("care_record")
        self.care_payloads.append(dict(payload))
        self._slots["care_record"] = dict(payload)
        return {"success": True, "async": True, "job_id": payload["job_id"]}

    def _care(self, patient: str, dry_run: bool) -> tuple[str, str | None]:
        if not dry_run and patient in self.care_results:
            return self.care_results[patient]
        return ("dry_run_ok" if dry_run else "success", None)

    def _result(self, details: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": True,
            "status": "completed",
            "job_id": payload["job_id"],
            "result": {"month": MONTH, "dry_run": payload["dry_run"], "details": details},
        }

    async def actual_time_result(self) -> dict[str, Any]:
        payload = self._slots["actual"]
        dry = payload["dry_run"]
        details = []
        for item in payload["items"]:
            d = {**item, "user": item["user_name"], "status": "dry_run_ok" if dry else "success"}
            if "care_record" in item:
                st, reason = self._care(item["user_name"], dry)
                d["care_record_status"], d["care_record_reason"] = st, reason
            details.append(d)
        return self._result(details, payload)

    async def care_record_time_result(self) -> dict[str, Any]:
        payload = self._slots["care_record"]
        details = []
        for item in payload["items"]:
            st, reason = self._care(item["patient"], payload["dry_run"])
            details.append({**item, "user": item["patient"], "status": st, "reason": reason})
        return self._result(details, payload)

    async def export(self, payload, *, timeout=None) -> dict[str, Any]:
        self.exports.append(dict(payload))
        return {
            "result": {
                "success": True,
                "division": payload["division"],
                "csv_content": _csv(self.export_lines),
                "row_count": len(self.export_lines),
            }
        }


@pytest.fixture(autouse=True)
def _fast(monkeypatch) -> None:
    monkeypatch.setattr(svc, "APPLY_POLL_SECONDS", 0.0)
    monkeypatch.setattr(sources, "RETRY_INTERVAL_SECONDS", 0.0)

    async def no_background(*, job_id, client):
        return None

    monkeypatch.setattr(svc, "run_apply_job", no_background)
    yield
    kaipoke_client.set_test_client(None)


async def _snapshot(db, lines: list[Line], *, fetched_at=None) -> None:
    db.add(
        KaipokeCsvSnapshot(
            office_id=None,
            month=MONTH,
            week_start=None,
            fetched_at=fetched_at or datetime(2026, 11, 2, 12, 52, tzinfo=UTC),
            csv_text=_csv(lines),
            row_count=len(lines),
            source_op="plan-actual",
            division="actual",
        )
    )
    await db.commit()


async def _setup(client, db, fake: FakeKaipoke):
    admin = await _user(db, "admin")
    await _seed(db, with_snapshot=False)
    await _snapshot(db, KP_K2)
    kaipoke_client.set_test_client(fake)  # type: ignore[arg-type]
    body = (await client.get(URL, headers=_bearer(admin))).json()
    return admin, body


async def _apply(client, db, admin, fake, **payload):
    res = await client.post(f"{URL}/apply", json=payload, headers=_bearer(admin))
    assert res.status_code == 202, res.text
    job = await svc.run_apply(db, fake, uuid.UUID(res.json()["job_id"]))  # type: ignore[arg-type]
    assert job is not None and job.status == "completed", job and job.result_summary
    return job


@pytest.mark.asyncio
async def test_judge_shows_k2_columns_and_k2_only_rows(client, db) -> None:
    _admin, body = await _setup(client, db, FakeKaipoke())
    # 斉藤様は記録書Ⅱとも 1 時間以上ずれるので記録書Ⅱは送らない (数えない)。
    assert body["counts"]["k2_only"] == 1 and body["counts"]["care_record"] == 2
    rows = _by_patient(body["rows"])
    aoki = rows["青木 さくら"]
    assert aoki["status"] == "k2_only" and aoki["status_label"] == "記録書Ⅱだけ"
    assert aoki["selected"] is True and aoki["selectable"] is True
    assert aoki["needs_actual"] is False and aoki["needs_care_record"] is True
    assert (aoki["k2_start"], aoki["k2_end"]) == ("09:55", "10:40")
    ishida = rows["石田 健"]
    assert ishida["status"] == "apply" and ishida["needs_care_record"] is True
    assert rows["木下 誠"]["needs_care_record"] is False  # 記録書Ⅱが無い行は扱わない
    assert rows["斉藤 大地"]["needs_care_record"] is False
    assert body["selected_count"] == 3


@pytest.mark.asyncio
async def test_dry_run_sends_actual_with_care_record_then_care_record_only(client, db) -> None:
    fake = FakeKaipoke()
    admin, _ = await _setup(client, db, fake)
    job = await _apply(client, db, admin, fake, dry_run=True)

    # 実績の口 → 記録書Ⅱだけの口 の順。
    assert fake.calls == ["actual", "care_record"]
    actual = fake.actual_payloads[0]
    assert actual["dry_run"] is True and actual["job_id"] == f"careflow-actual-{job.id}"
    items = {i["user_name"]: i for i in actual["items"]}
    assert set(items) == {"石田 健", "木下 誠"}
    assert items["石田 健"]["care_record"] == {"old_start": "11:00", "old_end": "11:35"}
    assert "care_record" not in items["木下 誠"]
    care = fake.care_payloads[0]
    assert care["dry_run"] is True and care["job_id"] == f"careflow-k2-{job.id}"
    assert care["month"] == MONTH and care["headed"] is True
    assert [{k: v for k, v in i.items() if k != "item_id"} for i in care["items"]] == [
        {
            "date": "2026-10-01",
            "patient": "青木 さくら",
            "staff1": "佐藤 花子",
            "actual_start": "10:00",
            "actual_end": "10:40",
            "old_start": "09:55",
            "old_end": "10:40",
            "new_start": "10:00",
            "new_end": "10:40",
        }
    ]

    status = (await client.get(f"{URL}/apply/status", headers=_bearer(admin))).json()
    assert status["counts"]["dry_run_ok"] == 2  # 実績の口へ送った行だけ
    assert status["care_record_counts"]["dry_run_ok"] == 2
    rows = _by_patient(status["rows"])
    assert rows["石田 健"]["care_record_status"] == "dry_run_ok"
    assert rows["青木 さくら"]["apply_status"] is None  # 記録書Ⅱだけの行は実績を触らない
    assert rows["青木 さくら"]["care_record_status"] == "dry_run_ok"
    assert all(r["dry_run_passed"] for r in status["rows"])
    assert status["last_apply"]["care_record_counts"]["dry_run_ok"] == 2


@pytest.mark.asyncio
async def test_real_apply_aligns_both_and_verifies(client, db) -> None:
    fake = FakeKaipoke(export_lines=KP_K2_AFTER)
    admin, _ = await _setup(client, db, fake)
    res = await client.post(f"{URL}/apply", json={"dry_run": False}, headers=_bearer(admin))
    assert res.status_code == 422  # 試しを通っていない
    await _apply(client, db, admin, fake, dry_run=True)
    job = await _apply(client, db, admin, fake, dry_run=False)

    assert fake.calls == ["actual", "care_record", "actual", "care_record"]
    sent = {s["patient"]: s for s in job.params["sent"]}
    assert sent["石田 健"]["actual"] is True
    assert sent["石田 健"]["care_record"] == {"old_start": "11:00", "old_end": "11:35"}
    assert sent["青木 さくら"]["actual"] is False
    assert sent["木下 誠"]["care_record"] is None
    results = job.result_summary["sent_results"]
    assert {r["care_record_status"] for r in results.values()} == {"success", None}

    view = (await client.get(URL, headers=_bearer(admin))).json()
    verify = view["run"]["verify_result"]
    assert view["run"]["state"] == "verified", verify
    assert verify["applied"] == 3 and verify["matched"] == 3
    assert verify["actual_applied"] == 2 and verify["actual_matched"] == 2
    assert verify["care_record_applied"] == 2 and verify["care_record_matched"] == 2
    assert view["counts"]["k2_only"] == 0 and view["counts"]["care_record"] == 0
    res = await client.post(f"{URL}/apply", json={"dry_run": False}, headers=_bearer(admin))
    assert res.status_code == 422  # もう送る行が無い


@pytest.mark.asyncio
async def test_care_record_failed_after_actual_success_is_sent_to_care_record_only(
    client, db
) -> None:
    # 実績は書けたが記録書Ⅱは一覧の時刻が違って失敗 → 読み取り直すと「記録書Ⅱだけ」の行になる。
    after = [
        line
        if line[2] != "石田　健"
        else ("佐藤　花子", 1, "石田　健", "11:03", "11:41", "11:00", "11:35")
        for line in KP_K2_AFTER
    ]
    fake = FakeKaipoke(
        export_lines=after, care_results={"石田 健": ("failed", "record_time_mismatch")}
    )
    admin, _ = await _setup(client, db, fake)
    await _apply(client, db, admin, fake, dry_run=True)
    await _apply(client, db, admin, fake, dry_run=False)

    view = (await client.get(URL, headers=_bearer(admin))).json()
    verify = view["run"]["verify_result"]
    assert view["run"]["state"] == "applied"
    assert verify["care_record_applied"] == 1 and verify["actual_matched"] == 2
    miss = {m["patient"]: m for m in verify["not_matched"]}
    assert miss["石田 健"]["actual_matched"] is True
    assert miss["石田 健"]["care_record_matched"] is False
    ishida = _by_patient(view["rows"])["石田 健"]
    assert ishida["status"] == "k2_only" and ishida["selected"] is True

    fake.care_results = {}
    fake.calls.clear()
    await _apply(client, db, admin, fake, dry_run=True)
    assert fake.calls == ["care_record"]
    item = fake.care_payloads[-1]["items"][0]
    assert (item["actual_start"], item["old_start"], item["new_start"]) == (
        "11:03",
        "11:00",
        "11:03",
    )


@pytest.mark.asyncio
async def test_error_marks_care_record_unknown_only_for_the_started_phase(client, db) -> None:
    fake = FakeKaipoke()
    admin, _ = await _setup(client, db, fake)
    res = await client.post(f"{URL}/apply", json={}, headers=_bearer(admin))
    job_id = uuid.UUID(res.json()["job_id"])
    # 実績の口を始めた後に止まった (記録書Ⅱだけの口はまだ)。
    assert await svc._touch(db, job_id, rpa_started=True)
    job = await svc.finish_apply(db, job_id, error="カイポケ側で結果が失われました")
    assert job is not None and job.status == "failed"
    assert job.result_summary["care_record_counts"]["unknown"] == 1

    rows = {r.patient: r for r in (await db.scalars(select(MonthlyActualRow))).all()}
    assert rows["石田 健"].apply_status == "unknown"
    assert rows["石田 健"].care_record_status == "unknown"
    assert svc.needs_kaipoke_check(rows["石田 健"])
    assert rows["青木 さくら"].care_record_status is None  # 送っていない
    assert rows["青木 さくら"].apply_status is None


@pytest.mark.asyncio
async def test_status_polls_the_care_record_phase(client, db) -> None:
    fake = FakeKaipoke()
    admin, _ = await _setup(client, db, fake)
    res = await client.post(f"{URL}/apply", json={}, headers=_bearer(admin))
    job_id = uuid.UUID(res.json()["job_id"])
    rows = (await db.scalars(select(MonthlyActualRow))).all()
    k2_rows = [r for r in rows if r.patient == "青木 さくら"]
    await fake.care_record_time_apply(
        {
            "job_id": svc.rpa_job_id(job_id, svc.PHASE_CARE_RECORD),
            "dry_run": True,
            "items": svc.build_care_record_items(k2_rows),
        }
    )
    assert await svc._touch(db, job_id, rpa_started=True, rpa_completed=True, k2_started=True)
    body = (await client.get(f"{URL}/apply/status", headers=_bearer(admin))).json()
    assert body["running"] is True and body["job"]["phase"] == "care_record"
    aoki = _by_patient(body["rows"])["青木 さくら"]
    assert aoki["care_record_status"] == "dry_run_ok"


# --- 判定 (純粋関数) ------------------------------------------------------------------


def test_judge_k2_differs_with_actual_apply_aligns_k2_too() -> None:
    rows = rules.judge(
        [rk("1", "石田 健", "11:03", "11:41")],
        [kp("石田　健", "11:00", "11:35", k2=("11:00", "11:35"))],
    )
    row = one(rows, "石田 健")
    assert row.status == rules.STATUS_APPLY and row.k2_apply is True
    assert row.default_selected is True
    assert rules.count_k2(rows) == 1


def test_judge_k2_only_when_actual_already_matches_qr() -> None:
    # 11/1 に実績だけ QR へ合わせた行 (青栁様 10/1 の型): 実績は QR どおり・記録書Ⅱだけ違う。
    rows = rules.judge(
        [rk("1", "青木 さくら", "14:53", "15:33")],
        [kp("青木　さくら", "14:53", "15:33", k2=("14:33", "15:33"))],
    )
    row = one(rows, "青木 さくら")
    assert row.status == rules.STATUS_K2_ONLY and row.k2_apply is True
    assert row.default_selected is True
    assert rules.count_statuses(rows)[rules.STATUS_K2_ONLY] == 1


def test_judge_k2_same_as_new_time_or_missing_is_left_alone() -> None:
    rows = rules.judge(
        [rk("1", "青木 さくら", "10:00", "10:40"), rk("2", "石田 健", "11:00", "11:40")],
        [
            kp("青木　さくら", "10:00", "10:40", k2=("10:00", "10:40")),
            kp("石田　健", "11:00", "11:40"),  # 記録書Ⅱが無い
        ],
    )
    for name in ("青木 さくら", "石田 健"):
        row = one(rows, name)
        assert row.status == rules.STATUS_MATCH and row.k2_apply is False
    assert rules.count_k2(rows) == 0


def test_judge_k2_gap_of_an_hour_or_more_holds_only_k2() -> None:
    # PO 決定 案B: 実績は今までどおり反映し、記録書Ⅱだけ送らない。
    # 実績はもう QR どおりで記録書Ⅱだけが 60 分以上違う行は、送るものが無いので人が見る。
    rows = rules.judge(
        [rk("1", "青木 さくら", "14:53", "15:33"), rk("2", "石田 健", "11:03", "11:41")],
        [
            kp("青木　さくら", "14:53", "15:33", k2=("13:50", "14:30")),
            kp("石田　健", "11:00", "11:35", k2=("09:30", "10:10")),
        ],
    )
    for name in ("青木 さくら", "石田 健"):
        row = one(rows, name)
        assert row.k2_apply is False
        assert rules.FLAG_K2_BIG_GAP in codes(row) and rules.FLAG_K2_GAP not in codes(row)
        flag = next(f for f in row.flags if f.code == rules.FLAG_K2_BIG_GAP)
        assert flag.review is False
        assert flag.label == "記録書Ⅱと 60 分以上ずれる・記録書Ⅱは人が見る"
    ishida = one(rows, "石田 健")
    assert ishida.status == rules.STATUS_APPLY and ishida.default_selected is True
    aoki = one(rows, "青木 さくら")
    assert aoki.status == rules.STATUS_REVIEW and aoki.default_selected is False
    assert rules.count_k2(rows) == 0


@pytest.mark.parametrize("k2", [("11:35", "11:00"), ("11:00", "11:00"), ("11:00", "--")])
def test_judge_k2_reversed_or_unreadable_holds_only_k2(k2: tuple[str, str]) -> None:
    rows = rules.judge(
        [rk("1", "青木 さくら", "14:53", "15:33"), rk("2", "石田 健", "11:03", "11:41")],
        [
            kp("青木　さくら", "14:53", "15:33", k2=k2),
            kp("石田　健", "11:00", "11:35", k2=k2),
        ],
    )
    for name in ("青木 さくら", "石田 健"):
        row = one(rows, name)
        assert row.k2_apply is False and rules.FLAG_K2_ORDER in codes(row)
        flag = next(f for f in row.flags if f.code == rules.FLAG_K2_ORDER)
        assert flag.review is False and flag.label == "記録書Ⅱの時刻の順が逆・人が見る"
    assert one(rows, "石田 健").status == rules.STATUS_APPLY  # 実績は送る
    assert one(rows, "青木 さくら").status == rules.STATUS_REVIEW  # 送るものが無い


def _service_row(**kw: Any) -> MonthlyActualRow:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": rules.STATUS_K2_ONLY,
        "flags": [],
        "item_key": "2026-10-01|青木さくら|10:00-10:40|佐藤花子",
        "kp_start": "10:00",
        "kp_end": "10:40",
        "kp_staff1": "佐藤 花子",
        "new_start": "10:00",
        "new_end": "10:40",
        "k2_start": "09:55",
        "k2_end": "10:40",
        "selected": True,
    }
    return MonthlyActualRow(**{**base, **kw})


def test_needs_care_record_uses_the_same_rule_as_judge() -> None:
    assert svc.needs_care_record(_service_row()) is True
    assert svc.needs_care_record(_service_row(k2_start="10:40", k2_end="09:55")) is False
    assert svc.needs_care_record(_service_row(k2_start="xx", k2_end="10:40")) is False
    held = [{"code": rules.FLAG_K2_BIG_GAP, "label": "", "review": False}]
    assert svc.needs_care_record(_service_row(flags=held)) is False
    order = [{"code": rules.FLAG_K2_ORDER, "label": "", "review": False}]
    assert svc.needs_care_record(_service_row(flags=order)) is False


@pytest.mark.parametrize("reason", ["save_maybe_saved", "verify_other_field_changed"])
def test_care_record_maybe_saved_is_not_sent_again(reason: str) -> None:
    # 本番で「保存したかもしれない」記録書Ⅱは、本番でも流し直し (only_failed) でも選ばない。
    row = _service_row(
        care_record_status="failed", care_record_reason=reason, care_record_dry_run=False
    )
    assert svc.care_record_needs_check(row) and svc.needs_kaipoke_check(row)
    assert svc.choose_rows([row]) == []
    assert svc.choose_rows([row], only_failed=True) == []
    plain = _service_row(
        care_record_status="failed",
        care_record_reason="record_time_mismatch",
        care_record_dry_run=False,
    )
    assert svc.choose_rows([plain], only_failed=True) == [plain]


@pytest.mark.asyncio
async def test_k2_result_is_not_carried_over_when_k2_was_rewritten(client, db) -> None:
    # そろえた後に記録書Ⅱが書き換えられた → 結果を引き継がず、また送る対象にする。
    fake = FakeKaipoke(export_lines=KP_K2_AFTER)
    admin, _ = await _setup(client, db, fake)
    await _apply(client, db, admin, fake, dry_run=True)
    await _apply(client, db, admin, fake, dry_run=False)
    body = (await client.get(URL, headers=_bearer(admin))).json()
    aoki = _by_patient(body["rows"])["青木 さくら"]
    assert aoki["care_record_status"] == "success" and aoki["needs_care_record"] is False

    rewritten = [
        line
        if line[2] != "青木　さくら"
        else ("佐藤　花子", 1, "青木　さくら", "10:00", "10:40", "09:50", "10:40")
        for line in KP_K2_AFTER
    ]
    await _snapshot(db, rewritten, fetched_at=datetime.now(UTC) + timedelta(minutes=5))
    rows = _by_patient((await client.get(URL, headers=_bearer(admin))).json()["rows"])
    aoki = rows["青木 さくら"]
    assert aoki["care_record_status"] is None and aoki["needs_care_record"] is True
    assert aoki["status"] == "k2_only"


@pytest.mark.asyncio
async def test_k2_check_result_is_carried_over_and_not_sent(client, db) -> None:
    # 保存したかもしれない (カイポケで確かめる待ち) は、記録書Ⅱが旧のままでも引き継ぎ、送らない。
    still = [
        line
        if line[2] != "青木　さくら"
        else ("佐藤　花子", 1, "青木　さくら", "10:00", "10:40", "09:55", "10:40")
        for line in KP_K2_AFTER
    ]
    fake = FakeKaipoke(
        export_lines=still, care_results={"青木 さくら": ("failed", "save_maybe_saved")}
    )
    admin, _ = await _setup(client, db, fake)
    await _apply(client, db, admin, fake, dry_run=True)
    await _apply(client, db, admin, fake, dry_run=False)

    body = (await client.get(URL, headers=_bearer(admin))).json()
    aoki_view = _by_patient(body["rows"])["青木 さくら"]
    assert aoki_view["care_record_reason"] == "save_maybe_saved"
    rows = list(
        (await db.scalars(select(MonthlyActualRow).execution_options(populate_existing=True))).all()
    )
    aoki = next(r for r in rows if r.patient == "青木 さくら")
    assert aoki.care_record_reason == "save_maybe_saved" and svc.needs_kaipoke_check(aoki)
    assert aoki not in svc.choose_rows(rows)
    assert aoki not in svc.choose_rows(rows, only_failed=True)
