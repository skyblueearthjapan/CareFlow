"""案の帳票 v2（結論・理由・気になる点・今回だけの条件・確かめの問い）。"""
from __future__ import annotations

from rk.plan import check_plan
from rk.report_v2 import proposal_warnings, render_applied, render_html


def fixture() -> dict:
    blk = lambda s, e, l, ch=False, fr=None: {"start": s, "end": e, "label": l, "changed": ch, **({"from": fr} if fr else {})}
    return {
        "kind": "week-leveling", "title": "週43 ならし案（見本）", "target": {"iso_year": 2026, "iso_week": 43},
        "summary": ["訪問 20 件を見直しました"], "checks": ["重なり 0 件"], "warnings": ["見本の注意"],
        "headline": ["8 件を動かし、全員の件数の差が 4 件から 1 件になります", "移動は合計 1 時間 20 分減ります", "木曜の夕方だけ 1 人足りません"],
        "ops": [{"op": "move-visit-week", "label": "10/22 見本様 14:00 → 15:00", "reason": "午前が空いていて移動 8 分で行けます"},
                {"op": "move-visit-week", "label": "10/22 例様 13:00 → 稲B"}],
        "issues": [{"severity": "warn", "text": "木曜 16:00 に見本様の訪問が入る人がいません（予定外の患者のため）",
                    "options": ["時刻を 30 分ずらす", "応援を 1 名お願いする", "保留にする"]},
                   {"severity": "info", "text": "新人さんは同行だけです", "options": []}],
        "conditions": ["1 人 8 件までを今回だけ 9 件まで（管理者承認 10/12）"],
        "compare_to": "案 1", "diff": ["見本様の時刻を 15:00 に変えました"],
        "timelines": [{"date": "2026-10-22", "rows": [
            {"name": "山川", "course": "稲D", "blocks": [blk("09:00", "09:30", "朝会"), blk("10:35", "11:10", "見本様", True, "海野さん 10:35"),
                                                      blk("10:50", "11:20", "同時刻様"), blk("13:00", "13:35", "例様")]},
            {"name": "鈴木", "blocks": [blk("14:00", "14:35", "例様", True, "山川さん")]}]}],
    }


def test_sections_in_order_and_content():
    h = render_html(fixture(), registry=_Reg())
    pos = [h.index(x) for x in ("結論", "気になる点・人が足りない所", "変わるところと理由", "今回だけの条件", "案 1 との違い", "確かめの問い")]
    assert pos == sorted(pos)
    for t in ("午前が空いていて移動 8 分", "<b>①</b> 時刻を 30 分ずらす", "<b>②</b> 応援を 1 名", "<b>③</b> 保留", "この案で反映する", "ここを直してほしい", "やめる", "9 件まで"):
        assert t in h
    assert "<script" not in h


def test_a3_changed_and_origin_labels():
    h = render_html(fixture(), a3=True, registry=_Reg())
    assert "blk chg" in h and "←元の担当 海野さん 10:35" in h and "size: A3 landscape" in h and "時間割 2026-10-22" in h


def test_missing_fields_fall_back_and_warn():
    p = fixture()
    for k in ("headline", "issues", "conditions", "compare_to", "diff"):
        p.pop(k)
    p["ops"] = [{"op": "x", "label": "a"}]
    p["unplaced"] = [{"visit_id": "v1", "label": "例様", "reason": "空きなし", "hint": ""}]
    h = render_html(p, registry=_Reg())
    assert "訪問 20 件を見直しました" in h and "例様 が入りません" in h and "いつもの決まりのまま" in h and "理由は書かれていません" in h
    w = " ".join(proposal_warnings(p))
    assert "headline" in w and "reason" in w and "issues" in w


def test_check_plan_validates_optional_fields():
    p = fixture()
    p["headline"] = ["a", "b", "c", "d"]
    p["issues"] = [{"severity": "bad", "text": "x"}]
    errs = check_plan(p)
    assert any("headline" in e for e in errs) and any("issues[0]" in e for e in errs)


def test_applied_report():
    h = render_applied(fixture(), {"applied": 8, "failed": 0, "verified": True, "run_id": "RUNXYZ"})
    assert "8 件を反映しました" in h and "確かめました" in h and "RUNXYZ" not in h and "まだ送っていません" in h


class _Reg:
    def get(self, name):
        return None


def test_no_internals_for_admin():
    class R2:
        def get(self, n):
            class I:
                summary = ""
                def undo_note(self, op): return "戻すにはバックアップ（DB 復元）から"
            return I()
    h = render_html(fixture(), registry=R2())
    assert "画面からは元に戻せません" in h and "バックアップ" not in h
