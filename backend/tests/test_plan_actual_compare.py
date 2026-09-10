"""カイポケ 予定×実績 月次突合 (plan_actual_compare / plan_actual_fetch / API).

前半 = 純関数の判定テスト (DB も RPA も触らない)。
後半 = /integrations/plan-actual-compare と /plan-actual-report の API テスト
(``test_integration_kaipoke.py`` と同じ StubKaipokeClient 方式)。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.core.security import create_access_token, hash_password
from app.models import User
from app.services import kaipoke_client as kc_module
from app.services.kaipoke.plan_actual_compare import (
    CAT_ACTUAL_ONLY,
    CAT_BOTH,
    CAT_MATCH,
    CAT_PLAN_ONLY,
    CAT_STAFF,
    CAT_TIME,
    CAVEAT,
    CAVEAT_ACCOMPANY,
    TAG_ACCOMPANY,
    TAG_DUP_ACTUAL,
    TAG_SERVICE,
    build_plan_actual_report,
    render_plan_actual_html,
)

# --- fixtures / helpers ----------------------------------------------------

HEADER = (
    "職員名１,職種１,職員名２,職種２,同行２,職員名３,職種３,同行３,"
    "事業所名,日付,曜日,利用者,業務種別,サービス内容,開始時間,終了時間,提供時間（分）,備考"
)

SERVICE = "精神基本療養費Ⅰ・正看"


def _row(
    *,
    staff1: str,
    day: int,
    patient: str,
    start: str,
    end: str,
    service: str = SERVICE,
    staff2: str = "",
    office: str = "よりより",
    business: str = "医療保険",
) -> str:
    return (
        f"{staff1},看護師,{staff2},,,,,,{office},{day},月,{patient},{business},{service},"
        f"{start},{end},35,"
    )


def _csv(*rows: str) -> str:
    return HEADER + "\n" + "\n".join(rows) + "\n"


def _report(plan: str, actual: str):
    return build_plan_actual_report(month="2026-08", plan_csv_text=plan, actual_csv_text=actual)


def _only(report) -> Any:
    """全日をまとめて 1 件だけの entry を返す (単一ケースのテスト用)。"""
    entries = [e for d in report.days for e in d.entries]
    assert len(entries) == 1, entries
    return entries[0]


# --- 1. 判定カテゴリ -------------------------------------------------------


def test_match_when_staff_and_time_agree() -> None:
    row = _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35")
    report = _report(_csv(row), _csv(row))
    entry = _only(report)
    assert entry.category == CAT_MATCH
    assert entry.tags == []
    assert entry.advice == ""
    assert report.counts[CAT_MATCH] == 1
    assert report.counts["plan_rows"] == 1
    assert report.counts["actual_rows"] == 1


def test_time_shift_when_same_staff_different_time() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="10:00", end="10:35"))
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_TIME
    assert entry.advice == "実績の時刻が正。予定を合わせるか確認"


def test_staff_mismatch_when_same_time_different_staff() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="看護B", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_STAFF
    assert entry.advice == "担当を確認"


def test_both_mismatch_when_neither_staff_nor_time_agree() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="看護B", day=3, patient="山田 太郎", start="13:00", end="13:35"))
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_BOTH
    assert entry.category_label == "相違（時刻・担当）"


def test_plan_only_and_actual_only() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="看護B", day=4, patient="佐藤 花子", start="13:00", end="13:35"))
    report = _report(plan, actual)
    entries = {e.category: e for d in report.days for e in d.entries}
    assert set(entries) == {CAT_PLAN_ONLY, CAT_ACTUAL_ONLY}
    assert entries[CAT_ACTUAL_ONLY].advice == "予定外の実績。誤登録なら実績側を削除"
    assert entries[CAT_PLAN_ONLY].advice.startswith("実績が未登録")
    assert report.counts[CAT_PLAN_ONLY] == 1
    assert report.counts[CAT_ACTUAL_ONLY] == 1


def test_multi_row_key_pairs_exact_match_first() -> None:
    """同キーに複数訪問がある日: 担当も時刻も合う組を先に確定させる (段階マッチの肝)。"""
    plan = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
        _row(staff1="看護B", day=3, patient="山田 太郎", start="15:00", end="15:35"),
    )
    actual = _csv(
        _row(staff1="看護B", day=3, patient="山田 太郎", start="15:00", end="15:35"),
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:30", end="10:05"),
    )
    report = _report(plan, actual)
    cats = sorted(e.category for d in report.days for e in d.entries)
    assert cats == sorted([CAT_MATCH, CAT_TIME])


# --- 2. 重複フラグ ---------------------------------------------------------


def test_same_staff_twice_a_day_at_different_times_is_not_a_duplicate() -> None:
    """同じ人が同じ利用者を午前・午後に訪問するのは正当 (1 日 2 回訪問は実運用)。"""
    rows = (
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
        _row(staff1="看護A", day=3, patient="山田 太郎", start="15:00", end="15:35"),
    )
    report = _report(_csv(*rows), _csv(*rows))
    entries = [e for d in report.days for e in d.entries]
    assert len(entries) == 2
    assert all(e.category == CAT_MATCH for e in entries)
    assert all(e.tags == [] for e in entries)
    assert report.counts["重複"] == 0


def test_duplicate_flag_on_actual_side() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
    )
    report = _report(plan, actual)
    entries = [e for d in report.days for e in d.entries]
    assert len(entries) == 2
    assert all(TAG_DUP_ACTUAL in e.tags for e in entries)
    assert all(e.advice == "実績側の重複。記録Ⅱの無い方を削除" for e in entries)
    # 重複は「内数」— カテゴリ集計 (一致 + 実績のみ) は 2 件のまま。
    assert report.counts["重複"] == 2
    assert report.counts[CAT_MATCH] + report.counts[CAT_ACTUAL_ONLY] == 2


# --- 2b. 同行者 (職員名2) ---------------------------------------------------


def test_swapped_staff_pair_is_a_match_not_a_staff_mismatch() -> None:
    """職員名1/2 が入れ替わっただけの行は「担当違い」にしない (順不同で見る)。"""
    plan = _csv(
        _row(staff1="看護A", staff2="看護B", day=3, patient="山田 太郎", start="09:00", end="09:35")
    )
    actual = _csv(
        _row(staff1="看護B", staff2="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35")
    )
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_MATCH
    assert TAG_ACCOMPANY not in entry.tags


def test_missing_staff2_in_actual_adds_accompany_tag() -> None:
    """主担当は合っているのに同行者が実績側に無い → 同行違いタグ。"""
    plan = _csv(
        _row(staff1="看護A", staff2="看護B", day=3, patient="山田 太郎", start="09:00", end="09:35")
    )
    actual = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_MATCH
    assert TAG_ACCOMPANY in entry.tags
    assert "同行者（職員2）が一致しません" in entry.advice


def test_accompany_tag_not_added_to_staff_mismatch_rows() -> None:
    """担当違いの行には同行違いを重ねない (情報が増えず判定が読みにくくなるだけ)。"""
    plan = _csv(
        _row(staff1="看護A", staff2="看護B", day=3, patient="山田 太郎", start="09:00", end="09:35")
    )
    actual = _csv(_row(staff1="看護C", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_STAFF
    assert TAG_ACCOMPANY not in entry.tags


def test_html_states_the_staff2_limitation() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    html_text = render_plan_actual_html(_report(plan, plan))
    assert CAVEAT_ACCOMPANY in html_text


# --- 3. 正規化 -------------------------------------------------------------


def test_time_zero_padding_is_normalised() -> None:
    """実績CSVの ``9:50`` は ``09:50`` と同じ時刻として扱う。"""
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:50", end="10:25"))
    actual = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="9:50", end="10:25"))
    assert _only(_report(plan, actual)).category == CAT_MATCH


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9:50", "09:50"),
        ("9:5", "09:05"),
        ("9:50:00", "09:50"),
        ("09:50", "09:50"),
        ("", ""),
        ("未定", "未定"),
    ],
)
def test_norm_time_cases(raw: str, expected: str) -> None:
    """``9:50:00`` を 5 文字で切ると分が壊れる — 割ってから前ゼロを詰める。"""
    from app.services.kaipoke.plan_actual_compare import _norm_time

    assert _norm_time(raw) == expected


@pytest.mark.parametrize("token", ["", "-", "－"])
def test_no_staff_tokens_are_one_bucket(token: str) -> None:
    """担当なし表記 (空欄 / - / －) は照合キーも集計バケツも 1 つに寄せる。"""
    plan = _csv(_row(staff1=token, day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="-", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    report = _report(plan, actual)
    entry = _only(report)
    assert entry.category == CAT_MATCH
    assert entry.staff == "（担当なし）"
    assert [s.staff for s in report.by_staff] == ["（担当なし）"]


def test_full_width_space_in_names_is_normalised() -> None:
    """利用者「山田　太郎」×「山田 太郎」/ 担当の全角空白も同一人物に束ねる。"""
    plan = _csv(_row(staff1="看護 A", day=3, patient="山田　太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="看護　A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_MATCH


def test_service_mismatch_adds_tag_but_keeps_category() -> None:
    plan = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35", service="A型")
    )
    actual = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35", service="B型")
    )
    entry = _only(_report(plan, actual))
    assert entry.category == CAT_MATCH
    assert TAG_SERVICE in entry.tags
    assert "サービス内容も相違" in entry.advice


def test_service_prefix_is_not_a_mismatch() -> None:
    plan = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35", service="A型")
    )
    actual = _csv(
        _row(
            staff1="看護A",
            day=3,
            patient="山田 太郎",
            start="09:00",
            end="09:35",
            service="A型・加算",
        )
    )
    assert _only(_report(plan, actual)).tags == []


# --- 4. 集計 / HTML --------------------------------------------------------


def test_by_staff_uses_actual_staff_when_present() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="看護B", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    report = _report(plan, actual)
    assert [s.staff for s in report.by_staff] == ["看護B"]
    assert report.by_staff[0].counts[CAT_STAFF] == 1
    assert report.by_staff[0].total == 1


def test_html_contains_caveat_and_by_staff_table() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(_row(staff1="看護B", day=4, patient="佐藤 花子", start="13:00", end="13:35"))
    html_text = render_plan_actual_html(_report(plan, actual))
    assert CAVEAT in html_text
    assert "担当者別の内訳" in html_text
    assert "この表の読み方" in html_text
    assert "看護A" in html_text and "看護B" in html_text
    assert "8/3" in html_text or "2026/8/3" in html_text


def test_html_escapes_names() -> None:
    plan = _csv(_row(staff1="<script>", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    html_text = render_plan_actual_html(_report(plan, _csv()))
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_event_rows_are_excluded_from_the_comparison() -> None:
    """イベント行 (業務種別が 医療保険/介護保険 以外) は突合しない。

    イベントは予定側にしか出ない性質があるため、混ぜると「予定のみ」が偽陽性で埋まる。
    """
    plan = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
        _row(
            staff1="看護A",
            day=3,
            patient="朝会",
            start="08:30",
            end="08:45",
            service="朝会",
            business="朝会",
        ),
    )
    actual = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    report = _report(plan, actual)

    # 訪問 1 件が一致するだけ。イベントは「予定のみ」に化けない。
    assert _only(report).category == CAT_MATCH
    assert report.counts[CAT_PLAN_ONLY] == 0
    assert report.counts["events_skipped"] == 1
    assert report.events_skipped == 1
    # plan_rows は突合した訪問行の数 (イベント除外後)。
    assert report.counts["plan_rows"] == 1
    assert report.counts["actual_rows"] == 1


def test_event_rows_counted_on_both_sides() -> None:
    plan = _csv(
        _row(staff1="看護A", day=3, patient="朝会", start="08:30", end="08:45", business="朝会")
    )
    actual = _csv(
        _row(staff1="看護A", day=4, patient="研修", start="18:00", end="19:00", business="研修")
    )
    report = _report(plan, actual)
    assert report.counts["events_skipped"] == 2
    assert report.days == []


def test_html_reports_events_skipped() -> None:
    plan = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
        _row(staff1="看護A", day=3, patient="朝会", start="08:30", end="08:45", business="朝会"),
    )
    html_text = render_plan_actual_html(_report(plan, _csv()))
    assert "イベント除外" in html_text


def test_truncated_rows_are_counted_as_malformed() -> None:
    """列数が足りず読めなかった行は黙って捨てず、件数を出す。"""
    plan = (
        HEADER
        + "\n"
        + _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35")
        + "\n看護B,看護師,,,,,,,よりより\n"  # 途中で切れた行 (9 列)
    )
    report = _report(plan, _csv())
    assert report.counts["malformed_rows"] == 1
    assert report.malformed_rows == 1
    # 読めた 1 行だけが突合対象。
    assert report.counts["plan_rows"] == 1
    html_text = render_plan_actual_html(report)
    assert "読めなかった行 1 件" in html_text


def test_no_malformed_note_when_all_rows_parse() -> None:
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    report = _report(plan, plan)
    assert report.counts["malformed_rows"] == 0
    assert "読めなかった行" not in render_plan_actual_html(report)


def test_row_count_divergence_warns_about_a_weekly_plan_csv() -> None:
    """行数が 30% 以上食い違うなら、予定CSVが週単位である可能性を警告する。"""
    plan = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual = _csv(
        *[
            _row(staff1="看護A", day=d, patient="山田 太郎", start="09:00", end="09:35")
            for d in range(1, 11)
        ]
    )
    report = _report(plan, actual)
    assert report.row_counts_diverge is True
    html_text = render_plan_actual_html(report)
    assert "予定 CSV が週単位の可能性があります" in html_text
    assert "予定 1 行 / 実績 10 行" in html_text


def test_no_divergence_warning_when_row_counts_are_close() -> None:
    rows = [
        _row(staff1="看護A", day=d, patient="山田 太郎", start="09:00", end="09:35")
        for d in range(1, 11)
    ]
    report = _report(_csv(*rows), _csv(*rows[:9]))
    assert report.row_counts_diverge is False
    assert "週単位の可能性" not in render_plan_actual_html(report)


def test_index_fallback_parses_headerless_csv() -> None:
    """ヘッダ名が引けない CSV は列位置で読む (18列フォーマット)。"""
    row = _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35")
    report = _report(row + "\n", row + "\n")
    assert _only(report).category == CAT_MATCH


# --- 5. API ----------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter-here"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


class StubPlanActualClient:
    """``/api/export`` を division ごとに撃ち分けるスタブ。"""

    def __init__(self, plan_csv: str, actual_csv: str) -> None:
        self.plan_csv = plan_csv
        self.actual_csv = actual_csv
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.fail_division: str | None = None
        #: 事前チェック (`/api/status`) が返す current_task.running。
        self.rpa_running = False
        self.status_error: Exception | None = None
        #: False = division を返さない旧 RPA (予定CSVをそのまま返してしまう) を再現。
        self.echo_division = True

    async def aclose(self) -> None:  # pragma: no cover — interface stub
        pass

    async def status(self) -> dict[str, Any]:
        if self.status_error is not None:
            raise self.status_error
        return {"current_task": {"running": self.rpa_running}}

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        division = payload.get("division")
        if division == self.fail_division:
            return {"result": {"success": False, "csv_content": None, "error": "export failed"}}
        if not self.echo_division:
            # division 未対応の RPA は知らないキーを無視し、常に予定CSVを返す。
            return {
                "result": {
                    "success": True,
                    "csv_content": self.plan_csv,
                    "row_count": max(0, self.plan_csv.count("\n") - 1),
                }
            }
        csv_text = self.actual_csv if division == "actual" else self.plan_csv
        return {
            "result": {
                "success": True,
                "division": division,
                "csv_content": csv_text,
                "row_count": max(0, csv_text.count("\n") - 1),
            }
        }


@pytest.fixture
def stub_plan_actual():
    plan = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
        _row(staff1="看護B", day=4, patient="佐藤 花子", start="13:00", end="13:35"),
    )
    actual = _csv(
        _row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"),
        _row(staff1="看護C", day=5, patient="鈴木 次郎", start="11:00", end="11:35"),
    )
    stub = StubPlanActualClient(plan, actual)
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


async def _seed_running_job(db, admin, month: str = "2026-08"):
    """``running`` の予実比較ジョブを 1 件作る (バックグラウンド実行の入口を再現)。"""
    from app.models.kaipoke_job import KaipokeJob
    from app.services.kaipoke.plan_actual_job import OP

    job = KaipokeJob(
        job_type="fetch",
        week_start=date(int(month[:4]), int(month[5:7]), 1),
        params={"op": OP, "month": month},
        status="running",
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()
    return job


async def _run_job(db, admin, stub, month: str = "2026-08"):
    """ジョブを立てて ``run_plan_actual_job`` を直接 await し、決着後のジョブを返す。

    バックグラウンド実行の実体をテストから決定的に駆動するための入口。
    エンドポイント経由だと「応答後に走る」ため、完了を待ってから検証できない。
    """
    from app.services.kaipoke.plan_actual_job import run_plan_actual_job

    job = await _seed_running_job(db, admin, month)
    await run_plan_actual_job(
        job_id=job.id,
        month=month,
        office_id=None,
        client=stub,  # type: ignore[arg-type]
    )
    await db.refresh(job)
    return job


@pytest.mark.asyncio
async def test_compare_returns_202_running_without_waiting(client, db, stub_plan_actual) -> None:
    """202 が即返り、ジョブは running で立つ (呼び出し元は ~100s 待たされない)。

    実体の完了検証は ``test_run_plan_actual_job_completes_with_counts`` が行う
    (バックグラウンドは応答後に走るので、ここで完了を待つことはできない)。
    """
    admin = await _make_user(db, "pa-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/integrations/plan-actual-compare",
        headers=_bearer(admin),
        json={"month": "2026-08"},
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "running"
    assert res.json()["jobId"]

    # FE の useKaipokeJobs が叩く一覧に op / month / status が載る (ポーリングの土台)。
    jobs = await client.get("/api/v1/integrations/jobs", headers=_bearer(admin))
    job = jobs.json()["items"][0]
    assert job["id"] == res.json()["jobId"]
    assert job["params"]["op"] == "plan-actual-compare"
    assert job["params"]["month"] == "2026-08"
    assert job["job_type"] == "fetch"
    assert job["status"] in {"running", "completed"}


@pytest.mark.asyncio
async def test_run_plan_actual_job_completes_with_counts(client, db, stub_plan_actual) -> None:
    """バックグラウンド本体: running → completed + counts / by_staff を書き込む。"""
    admin = await _make_user(db, "pa-runner@example.com", "admin")
    job = await _run_job(db, admin, stub_plan_actual)

    # 予定 → 実績 の順に 2 回だけ export した (RPA は単一スロット = 逐次)。
    assert [c["division"] for c in stub_plan_actual.calls] == ["plan", "actual"]
    assert all(c["async"] is False for c in stub_plan_actual.calls)

    assert job.status == "completed"
    assert job.completed_at is not None
    summary = job.result_summary
    assert summary["month"] == "2026-08"
    assert summary["plan_rows"] == 2
    assert summary["actual_rows"] == 2
    assert summary["counts"][CAT_MATCH] == 1
    assert summary["counts"][CAT_PLAN_ONLY] == 1
    assert summary["counts"][CAT_ACTUAL_ONLY] == 1
    assert summary["counts"]["events_skipped"] == 0
    assert summary["plan_snapshot_id"] and summary["actual_snapshot_id"]
    assert summary["plan_snapshot_id"] != summary["actual_snapshot_id"]
    assert {s["staff"] for s in summary["by_staff"]} == {"看護A", "看護B", "看護C"}
    assert "csv_content" not in str(summary)


@pytest.mark.asyncio
async def test_compare_rejects_second_run_while_one_is_running(
    client, db, stub_plan_actual
) -> None:
    """同種ジョブの二重起動は 409 (export を奪い合って両方失敗するのを防ぐ)。"""
    from app.models.kaipoke_job import KaipokeJob
    from app.services.kaipoke.plan_actual_job import OP

    admin = await _make_user(db, "pa-dup@example.com", "admin")
    db.add(
        KaipokeJob(
            job_type="fetch",
            week_start=date(2026, 8, 1),
            params={"op": OP, "month": "2026-08"},
            status="running",
            created_by_user_id=admin.id,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/plan-actual-compare",
        headers=_bearer(admin),
        json={"month": "2026-08"},
    )
    assert res.status_code == 409, res.text
    assert "既に実行中" in res.json()["detail"]
    # 詰まったときの出口 (取消) を文言で案内する。
    assert "取消" in res.json()["detail"]
    # export は 1 回も撃っていない。
    assert stub_plan_actual.calls == []


@pytest.mark.asyncio
async def test_compare_clears_an_orphaned_running_job(client, db, stub_plan_actual) -> None:
    """デプロイ等で消えた実行の残骸は failed に倒して、新しい実行を通す。

    バックグラウンドはプロセスに紐づくので、再起動で running のまま残ると
    以後ずっと 409 になってしまう (実行は ~100s なので 10 分超は残骸)。
    """
    from datetime import UTC, datetime, timedelta

    admin = await _make_user(db, "pa-orphan@example.com", "admin")
    orphan = await _seed_running_job(db, admin)
    orphan.started_at = datetime.now(UTC) - timedelta(minutes=30)
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/plan-actual-compare",
        headers=_bearer(admin),
        json={"month": "2026-08"},
    )
    assert res.status_code == 202, res.text

    await db.refresh(orphan)
    assert orphan.status == "failed"
    assert "中断されました" in orphan.result_summary["error"]
    assert orphan.completed_at is not None
    # 新しいジョブが立っている。
    assert res.json()["jobId"] != str(orphan.id)


@pytest.mark.asyncio
async def test_compare_rejects_when_rpa_is_busy(client, db, stub_plan_actual) -> None:
    """事前チェックで RPA が塞がっていれば起動しない (202 後に失敗させない)。"""
    admin = await _make_user(db, "pa-rpabusy@example.com", "admin")
    stub_plan_actual.rpa_running = True
    res = await client.post(
        "/api/v1/integrations/plan-actual-compare",
        headers=_bearer(admin),
        json={"month": "2026-08"},
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "kaipoke busy"
    assert stub_plan_actual.calls == []

    jobs = await client.get("/api/v1/integrations/jobs", headers=_bearer(admin))
    assert jobs.json()["items"] == []


@pytest.mark.asyncio
async def test_compare_requires_admin(client, db, stub_plan_actual) -> None:
    staff = await _make_user(db, "pa-staff@example.com", "staff")
    res = await client.post(
        "/api/v1/integrations/plan-actual-compare",
        headers=_bearer(staff),
        json={"month": "2026-08"},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_busy_midway_fails_the_job_with_japanese_message(
    client, db, stub_plan_actual
) -> None:
    """起動後に誰かが apply を始めた等で 409 になったら、ジョブを failed で決着させる。"""
    admin = await _make_user(db, "pa-busy@example.com", "admin")
    stub_plan_actual.error = kc_module.KaipokeBusyError({"error": "busy"})

    job = await _run_job(db, admin, stub_plan_actual)
    assert job.status == "failed"
    assert "別の処理を実行中" in job.result_summary["error"]
    # running のまま残さない (「実行中」の表示が消えなくなる)。
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_rpa_without_division_support_fails_closed(client, db, stub_plan_actual) -> None:
    """division を返さない RPA は「予定を実績として保存」する — 1 行も保存しない。

    黙って通すと「予定と実績が完全一致」という嘘のレポートが出て、請求前の確認が
    素通りしてしまう (現行 RPA は知らないキーを無視するので実際に起こりうる)。
    """
    from sqlalchemy import select

    from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot

    admin = await _make_user(db, "pa-nodiv@example.com", "admin")
    stub_plan_actual.echo_division = False

    job = await _run_job(db, admin, stub_plan_actual)
    assert job.status == "failed"
    assert "予実区分" in job.result_summary["error"]
    assert "RPA を更新してください" in job.result_summary["error"]
    assert job.result_summary["division"] == "plan"

    # 実績はもちろん、予定も保存しない (区分を確認できていないため)。
    rows = (await db.scalars(select(KaipokeCsvSnapshot))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_unexpected_error_still_settles_the_job(client, db, stub_plan_actual) -> None:
    """想定外の例外でも running のまま放置しない (黙って死なせない)。"""
    admin = await _make_user(db, "pa-boom@example.com", "admin")
    stub_plan_actual.error = RuntimeError("boom")

    job = await _run_job(db, admin, stub_plan_actual)
    assert job.status == "failed"
    assert "boom" in job.result_summary["error"]
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_actual_export_failure_names_the_division(client, db, stub_plan_actual) -> None:
    admin = await _make_user(db, "pa-fail@example.com", "admin")
    stub_plan_actual.fail_division = "actual"

    job = await _run_job(db, admin, stub_plan_actual)
    assert job.status == "failed"
    assert job.result_summary["division"] == "actual"
    assert "実績" in job.result_summary["error"]

    # 取得できた予定CSVは捨てない (~50s かけた成果・diff-local と同じ「最後に見た姿」)。
    # レポートは足りない方 = 実績 を名指しして 404。
    report = await client.get(
        "/api/v1/integrations/plan-actual-report?month=2026-08", headers=_bearer(admin)
    )
    assert report.status_code == 404, report.text
    assert "実績CSV" in report.json()["detail"]


@pytest.mark.asyncio
async def test_report_json_and_html_after_compare(client, db, stub_plan_actual) -> None:
    admin = await _make_user(db, "pa-report@example.com", "admin")
    await _run_job(db, admin, stub_plan_actual)

    res = await client.get(
        "/api/v1/integrations/plan-actual-report?month=2026-08", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["month"] == "2026-08"
    assert body["counts"][CAT_MATCH] == 1
    assert [d["day"] for d in body["days"]] == [3, 4, 5]
    assert body["plan_fetched_at"] and body["actual_fetched_at"]

    html_res = await client.get(
        "/api/v1/integrations/plan-actual-report?month=2026-08&format=html",
        headers=_bearer(admin),
    )
    assert html_res.status_code == 200
    assert html_res.headers["content-type"].startswith("text/html")
    assert CAVEAT in html_res.text


@pytest.mark.asyncio
async def test_report_404_names_the_missing_division(client, db) -> None:
    from app.services.kaipoke.csv_snapshot import save_snapshot

    admin = await _make_user(db, "pa-404@example.com", "admin")
    res = await client.get(
        "/api/v1/integrations/plan-actual-report?month=2026-08", headers=_bearer(admin)
    )
    assert res.status_code == 404, res.text
    assert "予定CSV" in res.json()["detail"]

    await save_snapshot(
        db,
        office_id=None,
        month="2026-08",
        week_start=None,
        csv_text=_csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35")),
        source_op="plan-actual",
        division="plan",
    )
    await db.commit()

    res2 = await client.get(
        "/api/v1/integrations/plan-actual-report?month=2026-08", headers=_bearer(admin)
    )
    assert res2.status_code == 404, res2.text
    assert "実績CSV" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_report_ignores_a_newer_week_scoped_snapshot(client, db, stub_plan_actual) -> None:
    """あとから保存された **週限定** の予定CSVを月次レポートに掴ませない。

    置換取り込み等は対象週の行しか持たないCSVを保存する。それを月次に使うと
    月の大半が抜けたまま「予定のみ 0 件」の安心が出てしまう。
    """
    from app.services.kaipoke.csv_snapshot import save_snapshot

    admin = await _make_user(db, "pa-weekly@example.com", "admin")
    await _run_job(db, admin, stub_plan_actual)

    # 同じ月に、より新しい週限定スナップショット (1 行だけ) を割り込ませる。
    await save_snapshot(
        db,
        office_id=None,
        month="2026-08",
        week_start=date(2026, 8, 24),
        csv_text=_csv(
            _row(staff1="看護Z", day=24, patient="週限定 太郎", start="09:00", end="09:35")
        ),
        source_op="replace-inbound",
    )
    await db.commit()

    res = await client.get(
        "/api/v1/integrations/plan-actual-report?month=2026-08", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 月まるごとの 2 行が使われている (週限定の 1 行ではない)。
    assert body["plan_rows"] == 2
    assert [d["day"] for d in body["days"]] == [3, 4, 5]
    assert "週限定 太郎" not in res.text


@pytest.mark.asyncio
async def test_report_requires_admin(client, db) -> None:
    staff = await _make_user(db, "pa-report-staff@example.com", "staff")
    res = await client.get(
        "/api/v1/integrations/plan-actual-report?month=2026-08", headers=_bearer(staff)
    )
    assert res.status_code == 403, res.text


# --- 6. スナップショットの後方互換 (mig 0083) ------------------------------


@pytest.mark.asyncio
async def test_division_default_keeps_existing_snapshot_behaviour(db) -> None:
    """既存の呼び出し (division 未指定) は従来どおり「予定」だけを読み書きする。"""
    from app.services.kaipoke.csv_snapshot import drop_snapshots, get_latest, save_snapshot

    plan_csv = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    actual_csv = _csv(_row(staff1="看護B", day=4, patient="佐藤 花子", start="13:00", end="13:35"))

    saved_plan = await save_snapshot(
        db,
        office_id=None,
        month="2026-08",
        week_start=None,
        csv_text=plan_csv,
        source_op="diff-local",
    )
    assert saved_plan is not None and saved_plan.division == "plan"

    # 実績を保存しても予定は消えない (upsert キーに division が入っている)。
    await save_snapshot(
        db,
        office_id=None,
        month="2026-08",
        week_start=None,
        csv_text=actual_csv,
        source_op="plan-actual",
        division="actual",
    )
    latest = await get_latest(db, month="2026-08")
    assert latest is not None and latest.id == saved_plan.id
    actual_latest = await get_latest(db, month="2026-08", division="actual")
    assert actual_latest is not None and actual_latest.csv_text == actual_csv

    # 送信後のフェイルクローズも予定だけを捨てる (実績は予実比較用に残す)。
    assert await drop_snapshots(db, month="2026-08") == 1
    assert await get_latest(db, month="2026-08") is None
    assert await get_latest(db, month="2026-08", division="actual") is not None


@pytest.mark.asyncio
async def test_get_latest_month_only_skips_week_scoped_rows(db) -> None:
    """``month_only`` は週限定CSV (対象週の行しか無い) を締め出す。"""
    from app.services.kaipoke.csv_snapshot import get_latest, save_snapshot

    monthly = _csv(_row(staff1="看護A", day=3, patient="山田 太郎", start="09:00", end="09:35"))
    weekly = _csv(_row(staff1="看護Z", day=24, patient="週 太郎", start="09:00", end="09:35"))

    saved_monthly = await save_snapshot(
        db,
        office_id=None,
        month="2026-08",
        week_start=None,
        csv_text=monthly,
        source_op="plan-actual",
    )
    # あとから (= より新しい) 週限定を保存する。
    await save_snapshot(
        db,
        office_id=None,
        month="2026-08",
        week_start=date(2026, 8, 24),
        csv_text=weekly,
        source_op="replace-inbound",
    )

    # 既定は従来どおり「最新」= 週限定を拾う (未送信計算の挙動は変えない)。
    assert (await get_latest(db, month="2026-08")).csv_text == weekly
    # month_only は月まるごとだけを見る。
    assert (await get_latest(db, month="2026-08", month_only=True)).id == saved_monthly.id
