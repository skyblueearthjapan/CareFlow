import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk import cli  # noqa: E402
from rk.config import Config  # noqa: E402
from rk.report import FOOTER, render_html, report_path, write_report  # noqa: E402

PLAN = {
    "kind": "staff-absence", "title": "10/22（木）田中さん お休みの振り分け <x>",
    "target": {"iso_year": 2026, "iso_week": 43, "date": "2026-10-22"},
    "summary": ["5 件を 鈴木・高橋 へ"], "ops": [{"op": "noop", "label": "9:30 伊東様 田中→鈴木"}],
    "checks": ["NG", "女性限定"], "warnings": ["移動が 10 分増える"],
    "timeline": {"date": "2026-10-22", "rows": [
        {"name": "鈴木", "blocks": [{"start": "9:30", "end": "10:20", "label": "伊東様", "changed": True},
                                    {"start": "13:00", "end": "13:45", "label": "佐藤様"}]}]},
}


def test_render_a4():
    h = render_html(PLAN)
    assert "size: A4 portrait" in h and "#e15a7f" in h and "Noto Sans JP" in h
    assert "data:image/png;base64," in h
    assert "9:30 伊東様 田中→鈴木" in h and "何もしない（見本）" in h
    assert "&lt;x&gt;" in h and "<x>" not in h  # エスケープ
    assert FOOTER in h and "女性限定" in h and "移動が 10 分増える" in h
    assert "class='tl-row'" not in h


def test_render_a3_timeline():
    h = render_html(PLAN, a3=True)
    assert "size: A3 landscape" in h and "blk chg" in h and "佐藤様" in h
    p = dict(PLAN)
    del p["timeline"]
    with pytest.raises(RkError):
        render_html(p, a3=True)


def test_render_a3_timelines_one_page_per_day():
    def day(d, who):
        return {"date": d, "rows": [{"name": who, "blocks": [
            {"start": "9:00", "end": "9:45", "label": f"{d} の訪問", "changed": True}]}]}
    p = {**PLAN, "timelines": [day("2026-10-21", "高橋"), day("2026-10-20", "鈴木"), {"date": "2026-10-25", "rows": []}],
         "timeline": day("2026-10-20", "鈴木")}
    h = render_html(p, a3=True)
    assert "size: A3 landscape" in h
    assert h.count("<section class='tl-day'>") == 2  # 空の日は描かない
    assert "page-break-before: always" in h
    assert h.index("時間割 2026-10-20") < h.index("時間割 2026-10-21")  # 日付順
    # timelines が無ければ timeline（1 日分）に戻る
    h1 = render_html({**PLAN, "timelines": []}, a3=True)
    assert "<section class='tl-day'>" not in h1 and "時間割 2026-10-22" in h1


def test_report_path_and_write(tmp_path):
    cfg = Config(reports_dir=tmp_path)
    assert report_path(cfg, PLAN, today=date(2026, 10, 12)) == tmp_path / "2026-10-12" / "staff-absence-2026-10-22.html"
    assert report_path(cfg, PLAN, a3=True, today=date(2026, 10, 12)).name == "staff-absence-2026-10-22-a3.html"
    out = write_report(cfg, PLAN)
    assert out.exists() and out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_parser_has_all_commands():
    ap = cli.build_parser()
    sub = next(a for a in ap._actions if a.__class__.__name__ == "_SubParsersAction")
    for c in ("whoami", "get", "post-read", "sql", "plan", "backup", "apply", "verify", "undo"):
        assert c in sub.choices
    args = ap.parse_args(["sql", "visits", "-p", "d=2026-10-12"])
    assert args.func is cli.cmd_sql and args.param == ["d=2026-10-12"]
    args = ap.parse_args(["plan", "report", "x.json", "--a3"])
    assert args.func is cli.cmd_plan_report and args.a3


def test_plugin_discovery(tmp_path, monkeypatch, capsys):
    import rk.commands as pkg
    (tmp_path / "zz_hello.py").write_text(
        "def register(sub):\n"
        "    p = sub.add_parser('zz-hello')\n"
        "    p.set_defaults(func=lambda a: print('hello') or 0)\n", encoding="utf-8")
    (tmp_path / "zz_broken.py").write_text("raise ImportError('x')\n", encoding="utf-8")
    monkeypatch.setattr(pkg, "__path__", list(pkg.__path__) + [str(tmp_path)])
    for k in ("rk.commands.zz_hello", "rk.commands.zz_broken"):
        sys.modules.pop(k, None)
    ap = cli.build_parser()
    assert "読み込めません" in capsys.readouterr().err  # 壊れた 1 つは警告だけ
    args = ap.parse_args(["zz-hello"])
    assert args.func(args) == 0


def test_main_plan_check_exit_codes(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RK_MODE", raising=False)
    good = tmp_path / "p.json"
    good.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["plan", "check", str(good)]) == 0
    assert "正しい形" in capsys.readouterr().out
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"kind": "x"}), encoding="utf-8")
    assert cli.main(["plan", "check", str(bad)]) == 1
    assert cli.main(["plan", "check", str(tmp_path / "none.json")]) == 1


def test_main_post_read_rejects_write_endpoint(tmp_path, capsys):
    body = tmp_path / "b.json"
    body.write_text("{}")
    assert cli.main(["post-read", "/integrations/apply", "--json", str(body)]) == 1
    assert "許可リスト" in capsys.readouterr().err


def test_main_apply_without_backup_never_connects(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RK_RUNS_DIR", str(tmp_path / "runs"))
    p = tmp_path / "p.json"
    p.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")

    def boom(args):
        raise AssertionError("つないではいけない")
    monkeypatch.setattr(cli, "_open_api", boom)
    assert cli.main(["apply", str(p), "--approved", "今泉 10/12"]) == 1
    assert "バックアップ" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["get", "C:/Program Files/Git/patients/abc"],
    ["post-read", "C:/Program Files/Git/integrations/unsent-summary", "--json", "-"],
])
def test_main_rejects_git_bash_rewritten_path(argv, monkeypatch, capsys):
    def boom(args):
        raise AssertionError("つないではいけない")
    monkeypatch.setattr(cli, "_open_api", boom)
    assert cli.main(argv) == 1
    err = capsys.readouterr().err
    assert "Git Bash がパスを書き換えました" in err and "MSYS2_ARG_CONV_EXCL='*'" in err and "agent/bin/rk" in err


def test_main_plan_check_prints_checks(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RK_MODE", raising=False)
    p = tmp_path / "p.json"
    p.write_text(json.dumps({**PLAN, "checks": ["○ NG", "× 全部の訪問が入る（問題 2 件）: a／b"]}, ensure_ascii=False),
                 encoding="utf-8")
    assert cli.main(["plan", "check", str(p)]) == 0
    out = capsys.readouterr().out
    assert "○ NG" in out and "a／b" in out
