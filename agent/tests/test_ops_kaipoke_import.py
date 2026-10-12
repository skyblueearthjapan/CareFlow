"""rk/ops/kaipoke_import.py の試験（偽の Api と差し替えた時計だけ・本番にもカイポケにも触らない）。"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk.opsbase import load_all  # noqa: E402
from rk.ops.kaipoke import JST  # noqa: E402
from rk.ops.kaipoke_import import (  # noqa: E402
    NO_UNDO_CHECKINS,
    KaipokeImport,
    KaipokeImportUndo,
    build_import_op,
    run_preview,
    summarize_preview,
)

WEEK = "2026-10-19"
SHEET = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
JOB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
NOON = datetime(2026, 10, 19, 10, 30, tzinfo=JST)

PREVIEW = {
    "weekStart": WEEK, "weekEnd": "2026-10-24", "protectedDays": [], "replaceDays": ["2026-10-20", "2026-10-21"],
    "sheetId": None, "diffSummary": {},
    "replace": {"wiped": 30, "inserted": 31, "skipped": [{"x": 1}], "traineeSolo": [], "ngConflicts": []},
    "rakusuke_adjusted_replaced": [
        {"visit_id": "v1", "date": "2026-10-20", "start_time": "09:30", "patient_label": "試験 花子様",
         "change": "カイポケの内容で作り直されます"},
        {"visit_id": "v2", "date": "2026-10-21", "start_time": "14:00", "patient_label": "試験 太郎様",
         "change": "カイポケに無いため消えます"},
    ],
}


class FakeApi:
    def __init__(self, statuses=None, snapshots=None, jobs=None, has_checkins=False):
        self.statuses = list(statuses or [])
        self.snapshots = snapshots if snapshots is not None else []
        self.jobs = jobs or []
        self.has_checkins = has_checkins
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path.startswith("/integrations/smart-inbound-preview/status/"):
            st = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
            if isinstance(st, Exception):
                raise st
            return st
        if path == "/integrations/inbound-snapshots":
            return {"snapshots": self.snapshots, "hasCheckins": self.has_checkins}
        if path == "/integrations/kaipoke/jobs":
            return {"items": self.jobs, "total": len(self.jobs)}
        raise AssertionError(path)

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/integrations/smart-inbound-preview/start":
            return {"jobId": JOB, "status": "running"}
        if path == "/integrations/smart-inbound-apply":
            return {"weekStart": WEEK, "dryRun": False, "replace": {"wiped": 30, "inserted": 31}}
        if path.endswith("/restore"):
            return {"wiped": 31, "restored": 30, "coursesRestored": 0, "coursesRemoved": 0}
        raise AssertionError(path)


def done(preview=PREVIEW, at="2026-10-19T01:00:00Z"):
    return {"jobId": JOB, "weekStart": WEEK, "status": "completed", "preview": preview, "completedAt": at}


def make(now=NOON):
    impl = KaipokeImport()
    impl.clock = lambda: now
    return impl


def base_op(**kw):
    op = build_import_op(WEEK, {"job_id": JOB, "preview": PREVIEW}, "2026-10-19T10:05:00+09:00")
    op.update(kw)
    return op


def test_registered():
    reg = load_all()
    for name in ("kaipoke-import", "kaipoke-import-undo", "actual-time-adjust", "actual-time-reset"):
        assert reg.get(name) is not None, name


def test_preview_polls_until_completed():
    api = FakeApi(statuses=[{"status": "running"}, RuntimeError("一時的"), done()])
    slept = []
    res = run_preview(api, WEEK, sleep=slept.append)
    assert res["job_id"] == JOB and res["preview"] is PREVIEW
    assert api.calls[0] == ("POST", "/integrations/smart-inbound-preview/start", {"weekStart": WEEK})
    assert len(slept) == 2


def test_preview_failed_and_timeout():
    with pytest.raises(RkError, match="0件"):
        run_preview(FakeApi(statuses=[{"status": "failed", "error": "カイポケの現況が0件でした"}]), WEEK,
                    sleep=lambda s: None)
    ticks = iter(range(0, 10_000, 400))
    with pytest.raises(RkError, match="12 分"):
        run_preview(FakeApi(statuses=[{"status": "running"}]), WEEK, sleep=lambda s: None,
                    clock=lambda: next(ticks))


def test_summary_and_op_carry_counts_and_adjusted_list():
    s = summarize_preview(PREVIEW)
    assert s["replace_wiped"] == 30 and s["replace_inserted"] == 31 and s["replace_skipped"] == 1
    assert s["adjusted_removed"] == 1 and len(s["adjusted_replaced"]) == 2
    op = base_op()
    assert op["expected"]["replace_wiped"] == 30
    assert [a["visit_id"] for a in op["adjusted_replaced"]] == ["v1", "v2"]
    assert make().validate(op) == []
    assert make().request(op) == ("POST", "/integrations/smart-inbound-apply",
                                  {"weekStart": WEEK, "sheetId": None, "dryRun": False})


@pytest.mark.parametrize("missing", ["week_start", "preview_job_id", "preview_reviewed_at", "adjusted_replaced"])
def test_validate_requires_preview_fields(missing):
    op = base_op()
    op.pop(missing)
    assert make().validate(op)


def test_validate_window_monday_staleness_and_sheet():
    assert any("17:00" in e and "取り込み" in e for e in make(datetime(2026, 10, 19, 17, 30, tzinfo=JST)).validate(base_op()))
    assert any("月曜" in e for e in make().validate(base_op(week_start="2026-10-20")))
    assert any("時間を過ぎた" in e for e in make().validate(base_op(preview_reviewed_at="2026-10-19T06:00:00+09:00")))
    assert any("sheet_id" in e for e in make().validate(base_op(protected_days=["2026-10-19"])))
    # 差分だけの週（置換の日が無い）はロボットを使わない → 夕方でも時間の制限なし
    late = make(datetime(2026, 10, 19, 17, 30, tzinfo=JST))
    assert late.validate(base_op(protected_days=["2026-10-19"], replace_days=[], sheet_id=SHEET,
                                 preview_reviewed_at="2026-10-19T17:20:00+09:00")) == []


def test_execute_rechecks_preview_and_later_imports():
    impl = make()
    with pytest.raises(RkError, match="結果が残っていません"):
        impl.execute(FakeApi(statuses=[{"status": "failed"}]), base_op())
    with pytest.raises(RkError, match="差分シート"):
        impl.execute(FakeApi(statuses=[done(dict(PREVIEW, sheetId=SHEET))]), base_op())
    later = {"params": {"op": "smart-apply"}, "status": "completed", "completed_at": "2026-10-19T01:10:00Z"}
    with pytest.raises(RkError, match="もう取り込まれて"):
        impl.execute(FakeApi(statuses=[done()], jobs=[later]), base_op())
    api = FakeApi(statuses=[done()])
    res = impl.execute(api, base_op())
    assert ("POST", "/integrations/smart-inbound-apply", {"weekStart": WEEK, "sheetId": None, "dryRun": False}) in api.calls
    assert impl.check_response(base_op(), res) == []
    assert impl.check_response(base_op(), {"dryRun": False, "diff": {"failed": 2}})
    with pytest.raises(RkError, match="17:00"):
        make(datetime(2026, 10, 19, 17, 30, tzinfo=JST)).execute(FakeApi(statuses=[done()]), base_op())


def test_inverse_is_restore_unless_checkins():
    impl = make()
    before = impl.snapshot(FakeApi(snapshots=[{"id": "old", "kind": "smart"}]), base_op())
    assert before["snapshot_ids"] == ["old"] and before["has_checkins"] is False
    assert impl.undo_note(base_op(), before) is None
    assert impl.inverse(base_op(), before) == [{"op": "kaipoke-import-undo", "week_start": WEEK,
                                                "known_snapshot_ids": ["old"]}]
    # 打刻のある日がある週は戻せない（サーバーが 422）
    op = base_op(protected_days=["2026-10-19"], sheet_id=SHEET)
    assert impl.undo_note(op) == NO_UNDO_CHECKINS and impl.inverse(op, before) is None
    assert impl.inverse(base_op(), dict(before, has_checkins=True)) is None
    assert "打刻" in NO_UNDO_CHECKINS and "画面からも元に戻せません" in NO_UNDO_CHECKINS


def test_verify_reads_job_and_snapshot():
    impl = make()
    job = {"params": {"op": "smart-apply"}, "status": "completed", "created_at": "2026-10-19T01:20:00Z"}
    snap = {"id": "new", "kind": "smart", "createdAt": "2026-10-19T01:20:00Z"}
    assert impl.verify(FakeApi(jobs=[job], snapshots=[snap]), base_op()) == []
    assert len(impl.verify(FakeApi(), base_op())) == 2


def test_undo_picks_first_new_smart_snapshot_and_refuses_checkins():
    undo = KaipokeImportUndo()
    op = {"op": "kaipoke-import-undo", "week_start": WEEK, "known_snapshot_ids": ["old"]}
    assert undo.validate(op) == [] and undo.undoable is False
    snaps = [{"id": "later", "kind": "smart", "createdAt": "2026-10-19T03:00:00Z"},
             {"id": "ours", "kind": "smart", "createdAt": "2026-10-19T01:20:00Z"},
             {"id": "old", "kind": "smart", "createdAt": "2026-10-18T01:00:00Z"},
             {"id": "copy", "kind": "copy_week", "createdAt": "2026-10-19T02:00:00Z"}]
    api = FakeApi(snapshots=snaps)
    assert undo.check_response(op, undo.execute(api, op)) == []
    assert ("POST", "/integrations/inbound-snapshots/ours/restore", None) in api.calls
    with pytest.raises(RkError, match="打刻"):
        undo.execute(FakeApi(snapshots=snaps, has_checkins=True), op)
    with pytest.raises(RkError, match="見つかりません"):
        undo.execute(FakeApi(snapshots=[snaps[2]]), op)
    snaps[1]["restoredAt"] = "2026-10-19T04:00:00Z"
    assert undo.verify(FakeApi(snapshots=snaps), op) == []
    assert undo.verify(FakeApi(snapshots=[snaps[2]]), op)
