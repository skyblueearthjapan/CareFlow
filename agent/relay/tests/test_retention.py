"""保存期間の選び方（relay/retention.py）と作業の記録（rk/journal.py）の試験。時刻はすべて UTC。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from relay import retention as R
from rk import journal

UTC = timezone.utc
NOW = datetime(2026, 10, 12, 18, 30, tzinfo=UTC)


def item(name: str, ts: datetime, kind: str = "") -> R.Item:
    return R.Item(name, ts, 100, kind)


# ---- 時刻・境目 ----
def test_parse_stamp_utc():
    assert R.parse_stamp("daily-20261011-1730.sql.gz") == datetime(2026, 10, 11, 17, 30, tzinfo=UTC)
    assert R.parse_stamp("agent-w43-20261012-064455.sql.gz") == datetime(2026, 10, 12, 6, 44, 55, tzinfo=UTC)
    assert R.parse_stamp("pre-0041-20260630-1250.sql.gz") == datetime(2026, 6, 30, 12, 50, tzinfo=UTC)
    assert R.parse_stamp("no-stamp.sql.gz") is None


def test_boundary_exactly_n_days_is_kept():
    assert not R.is_expired(NOW - timedelta(days=14), NOW, 14)
    assert R.is_expired(NOW - timedelta(days=14, seconds=1), NOW, 14)


def test_timezone_naive_is_utc_and_other_offsets_normalized():
    jst = timezone(timedelta(hours=9))
    # 同じ瞬間を JST で表しても結果は同じ
    t = NOW - timedelta(days=7)
    assert R.is_expired(t.astimezone(jst), NOW, 7) is False
    assert R.is_expired((t - timedelta(minutes=1)).astimezone(jst), NOW, 7) is True
    assert R.is_expired((t - timedelta(minutes=1)).replace(tzinfo=None), NOW, 7) is True


# ---- 新しい 5 個は必ず残す ----
def test_keep_newest_five_even_if_all_old():
    items = [item(f"agent-x-{i}", NOW - timedelta(days=100 + i)) for i in range(8)]
    sel = R.select_keep_newest(items, NOW, 14, 5)
    assert sorted(i.path for i in sel) == ["agent-x-5", "agent-x-6", "agent-x-7"]


def test_keep_newest_only_deletes_older_than_14_days():
    items = [item(f"a{i}", NOW - timedelta(days=i)) for i in range(20)]  # a0..a19
    sel = R.select_keep_newest(items, NOW, 14, 5)
    assert sorted(int(i.path[1:]) for i in sel) == [15, 16, 17, 18, 19]  # 14 日ちょうどは残る


def test_keep_newest_fewer_than_five():
    items = [item(f"a{i}", NOW - timedelta(days=300)) for i in range(3)]
    assert R.select_keep_newest(items, NOW, 14, 5) == []


# ---- アプリの既存バックアップ ----
def test_classify_app_backup():
    c = R.classify_app_backup
    assert c("daily-20261011-1730.sql.gz") == "daily"
    assert c("pre-deploy-20260615-0302.sql.gz") == "pre-deploy"
    assert c("pre-deploy-0059-20260709-210011.sql") == "pre-deploy"
    assert c("pre-aso-cancel-20261009-1319.sql.gz") == "pre"
    for n in ("agent-x-20261001-1200.sql.gz", "pre_g59_20260601.sql.gz", "predeploy_g110_x.sql.gz",
              "carelink_pre_g57_x.sql.gz", "w42-apply.py", "env-backup-20260101", "daily-x.log"):
        assert c(n) is None, n


def test_app_daily_30_days_plus_first_of_month_12_months():
    items = []
    d = NOW - timedelta(days=500)
    while d <= NOW:
        items.append(R.Item(f"daily-{d:%Y%m%d}-1730.sql.gz", d, 1, "daily"))
        d += timedelta(days=1)
    sel = {i.path for i in R.select_app_backups(items, NOW)}
    kept = [i for i in items if i.path not in sel]
    recent = [i for i in kept if not R.is_expired(i.ts, NOW, 30)]
    monthly = [i for i in kept if R.is_expired(i.ts, NOW, 30)]
    assert len(recent) == 31  # 30 日前ちょうど〜今日
    # 30 日より古くて残るのは「各月の最初の 1 個」（今月を含め 12 か月 = 2025-11〜2026-10 のうち 30 日より古いもの）
    assert all(i.ts.day == 1 or i.ts == min(x.ts for x in items if (x.ts.year, x.ts.month) == (i.ts.year, i.ts.month))
               for i in monthly)
    months = sorted({(i.ts.year, i.ts.month) for i in monthly})
    assert months[0] == (2025, 11) and months[-1] <= (2026, 9)
    assert all((i.ts.year, i.ts.month) >= (2025, 11) for i in monthly)


def test_app_newest_three_per_kind_always_kept():
    old = NOW - timedelta(days=400)
    items = [R.Item(f"pre-deploy-{k}", old - timedelta(days=k), 1, "pre-deploy") for k in range(5)]
    items += [R.Item(f"pre-{k}", old - timedelta(days=k), 1, "pre") for k in range(2)]
    items += [R.Item(f"daily-{k}", old - timedelta(days=k), 1, "daily") for k in range(4)]
    sel = {i.path for i in R.select_app_backups(items, NOW)}
    assert sel == {"pre-deploy-3", "pre-deploy-4", "daily-3"}


def test_app_pre_60_days_boundary():
    items = [R.Item("pre-a", NOW - timedelta(days=60), 1, "pre"), R.Item("pre-b", NOW - timedelta(days=61), 1, "pre")]
    items += [R.Item(f"pre-new{k}", NOW - timedelta(hours=k), 1, "pre") for k in range(3)]
    assert [i.path for i in R.select_app_backups(items, NOW)] == ["pre-b"]


def test_app_backups_scan_ignores_dirs_and_other_files(tmp_path):
    (tmp_path / "visit_photos").mkdir()
    (tmp_path / "visit_photos" / "daily-20200101-0000.sql.gz").write_bytes(b"x")
    for n in ("daily-20200101-1730.sql.gz", "daily-20200102-1730.sql.gz", "daily-20200103-1730.sql.gz",
              "daily-20200104-1730.sql.gz", "pre_g59_20200101.sql.gz", "agent-a-20200101-0000.sql.gz"):
        (tmp_path / n).write_bytes(b"x" * 10)
    items = R.scan_files(tmp_path, R.classify_app_backup)
    assert sorted(Path(i.path).name for i in items) == [
        "daily-20200101-1730.sql.gz", "daily-20200102-1730.sql.gz", "daily-20200103-1730.sql.gz",
        "daily-20200104-1730.sql.gz"]
    sel = R.select_app_backups(items, NOW)
    assert [Path(i.path).name for i in sel] == ["daily-20200101-1730.sql.gz"]  # 古いが月初は 12 か月より前


# ---- ファイルの片づけ（dry-run と apply） ----
def _cli(args: list[str]) -> int:
    return R.main(args)


def test_agent_backups_dry_run_then_apply_writes_journal(tmp_path, capsys):
    d = tmp_path / "agent"
    d.mkdir()
    for k in range(8):
        t = NOW - timedelta(days=20 + k)
        (d / f"agent-s1-v{k}-{t:%Y%m%d-%H%M%S}.sql.gz").write_bytes(b"x" * 1000)
    j = tmp_path / "journal" / "work-journal.jsonl"
    now = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _cli(["agent-backups", "--dir", str(d), "--now", now, "--journal", str(j)]) == 0
    out = capsys.readouterr().out
    assert out.count("WOULD DELETE") == 3 and len(list(d.iterdir())) == 8 and not j.exists()
    assert _cli(["agent-backups", "--dir", str(d), "--now", now, "--journal", str(j), "--apply"]) == 0
    assert len(list(d.iterdir())) == 5
    rec = json.loads(j.read_text().splitlines()[-1])
    assert rec["kind"] == "retention" and rec["scope"] == "agent-backups"
    assert rec["deleted"]["agent"] == {"count": 3, "bytes": 3000, "errors": 0}


def _age(p: Path, days: float) -> None:
    t = (NOW - timedelta(days=days)).timestamp()
    os.utime(p, (t, t))


def test_sandbox_transcripts_and_scratch_7_days(tmp_path):
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-srv-agent"
    proj.mkdir(parents=True)
    old, new = proj / "old.jsonl", proj / "new.jsonl"
    old.write_text("x"), new.write_text("y")
    _age(old, 8), _age(new, 6)
    cred = home / ".claude" / ".credentials.json"
    cred.write_text("{}")
    _age(cred, 400)
    codex = home / ".codex" / "sessions" / "2026" / "09" / "01"
    codex.mkdir(parents=True)
    r = codex / "rollout.jsonl"
    r.write_text("z")
    _age(r, 30)
    scratch = tmp_path / "sessions"
    s_old, s_new = scratch / "sid-old", scratch / "sid-new"
    s_old.mkdir(parents=True), s_new.mkdir()
    (s_old / "a.txt").write_text("a"), (s_new / "b.txt").write_text("b")
    _age(s_old / "a.txt", 9), _age(s_old, 9), _age(s_new / "b.txt", 1)
    now = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _cli(["sandbox", "--home", str(home), "--scratch", str(scratch), "--now", now, "--apply"]) == 0
    assert not old.exists() and new.exists() and cred.exists()
    assert not r.exists() and not (home / ".codex" / "sessions" / "2026").exists()
    assert (home / ".codex" / "sessions").exists()
    assert not s_old.exists() and s_new.exists()



def test_sandbox_transcripts_in_per_user_seats(tmp_path):
    """人ごとの席（home/u/<鍵>/）の会話の写しも 7 日で消す。ログイン情報は消さない。"""
    home = tmp_path / "home"
    seat = home / "u" / ("a" * 32)
    proj = seat / ".claude" / "projects" / "-srv-agent"
    proj.mkdir(parents=True)
    old, new = proj / "old.jsonl", proj / "new.jsonl"
    old.write_text("x"), new.write_text("y")
    _age(old, 8), _age(new, 6)
    roll = seat / ".codex" / "sessions" / "2026" / "10" / "01" / "rollout.jsonl"
    roll.parent.mkdir(parents=True)
    roll.write_text("z")
    _age(roll, 10)
    auth = seat / ".codex" / "auth.json"
    auth.write_text("{}")
    _age(auth, 400)
    now = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _cli(["sandbox", "--home", str(home), "--scratch", str(tmp_path / "none"), "--now", now, "--apply"]) == 0
    assert not old.exists() and new.exists() and not roll.exists() and auth.exists()

def test_run_records_one_year(tmp_path):
    runs = tmp_path / "runs"
    (runs / "20251012-183000-week-leveling").mkdir(parents=True)   # ちょうど 1 年（365 日）前 → 残す
    (runs / "20251011-120000-week-leveling").mkdir()               # 366 日前 → 消す
    (runs / "journal").mkdir()
    (runs / "journal" / "work-journal.jsonl").write_text("{}\n")
    (runs / "backups.jsonl").write_text("{}\n")
    sel = R.select_older_than(R.scan_run_records(runs), NOW, R.PLAN_RUN_DAYS)
    assert [Path(i.path).name for i in sel] == ["20251011-120000-week-leveling"]


def test_relay_db_prune(tmp_path):
    from relay import store as store_mod
    from relay.store import SqlStore

    url = f"sqlite:///{tmp_path / 'relay.db'}"
    st = SqlStore(url, create=True)
    t = {"now": NOW - timedelta(days=100)}
    orig = store_mod.utcnow
    store_mod.utcnow = lambda: t["now"]
    try:
        old_chat = st.create_session("1", "old chat")             # 100 日前・案なし → 会話ごと消す
        st.append_event(old_chat["id"], 1, {"type": "text"})
        old_plan = st.create_session("1", "old plan")             # 100 日前・案あり → 会話は残し、イベントだけ消す
        st.append_event(old_plan["id"], 1, {"type": "text"})
        st.add_plan(old_plan["id"], kind="k", title="t", plan_json={}, plan_sha256="x", check_result={})
        t["now"] = NOW - timedelta(days=400)
        ancient = st.create_session("1", "ancient")               # 400 日前 → 全部消す
        st.add_plan(ancient["id"], kind="k", title="t", plan_json={}, plan_sha256="x", check_result={})
        t["now"] = NOW - timedelta(days=1)
        fresh = st.create_session("1", "fresh")
        st.append_event(fresh["id"], 1, {"type": "text"})
    finally:
        store_mod.utcnow = orig
    dry = R.prune_store(url, NOW, apply=False)
    assert dry["db-sessions"]["count"] == 2 and dry["db-events"]["count"] == 2 and dry["db-plans"]["count"] == 1
    assert st.get_session(old_chat["id"]) is not None  # dry-run は消さない
    R.prune_store(url, NOW, apply=True)
    assert st.get_session(old_chat["id"]) is None and st.get_session(ancient["id"]) is None
    assert st.get_session(old_plan["id"]) is not None and st.latest_plan(old_plan["id"]) is not None
    assert st.list_events(old_plan["id"]) == [] and len(st.list_events(fresh["id"])) == 1


# ---- ディスクの見張り ----
def test_disk_status_low_and_ok():
    gb = 1024 ** 3
    low = R.disk_status("/", usage=lambda p: SimpleNamespace(free=9 * gb, total=100 * gb))
    ok = R.disk_status("/", usage=lambda p: SimpleNamespace(free=10 * gb, total=100 * gb))
    assert low["low"] and "空き容量" in low["warning"]
    assert not ok["low"] and ok["warning"] is None


# ---- 作業の記録（名前を入れない） ----
def test_journal_apply_record_has_no_names(tmp_path):
    plan = {"kind": "week-leveling", "title": "山田花子さんを佐藤さんへ",
            "ops": [{"op": "visit.reassign", "label": "山田花子 10/13"}, {"op": "visit.reassign"},
                    {"op": "visit.move-week"}]}
    rec = journal.apply_record(plan, "画面の承認 ap_x（user 42・pc・2026-10-12）",
                               {"relay_session": "3f2a-uuid", "plan_version": 2}, "20261012-1830-week-leveling",
                               applied=2, unknown=0, error="2 件目（山田花子）で止めました")
    p = tmp_path / "j.jsonl"
    assert journal.append(rec, p, now=NOW)
    line = p.read_text(encoding="utf-8").strip()
    assert "山田" not in line and "佐藤" not in line and "\\u" not in line
    d = json.loads(line)
    assert d["user_id"] == "42" and d["session_id"] == "3f2a-uuid" and d["approved"] is True
    assert d["ops"] == {"visit.reassign": 2, "visit.move-week": 1} and d["result"] == "stopped"
    assert d["ts"] == "2026-10-12T18:30:00Z" and d["plan_id"].startswith("sha256:")


def test_journal_sanitize_drops_unknown_keys_and_unsafe_values():
    out = journal.sanitize({"kind": "apply", "title": "x", "user_id": "田中", "deleted": {"名前": {"count": 1},
                                                                                         "daily": {"count": 2}}})
    assert out == {"kind": "apply", "deleted": {"daily": {"count": 2}}}


def test_journal_never_raises(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    assert journal.append({"kind": "apply"}, f / "under-a-file.jsonl") is False


def test_journal_path_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RK_JOURNAL_PATH", raising=False)
    monkeypatch.setenv("AGENT_RELAY_DATA_DIR", str(tmp_path))
    assert journal.journal_path() == tmp_path / "journal" / "work-journal.jsonl"
    monkeypatch.setenv("RK_JOURNAL_PATH", str(tmp_path / "x.jsonl"))
    assert journal.journal_path() == tmp_path / "x.jsonl"


@pytest.mark.parametrize("cmd", [["app-backups"], ["agent-backups"]])
def test_dir_required(cmd):
    with pytest.raises(SystemExit):
        R.main(cmd)
