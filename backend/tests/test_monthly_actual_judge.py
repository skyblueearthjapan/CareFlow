"""月の実績の組み方と判定 (純粋関数) — 11/1 に 10 月分で見つかった型を小さく再現する."""

from __future__ import annotations

from datetime import date

from app.services.monthly_actual import judge as rules
from app.services.monthly_actual.service import parse_kp_csv

D1 = date(2026, 10, 1)
D2 = date(2026, 10, 2)
NURSE_A = "佐藤 花子"
NURSE_B = "鈴木 一郎"


def rk(
    vid: str,
    patient: str,
    arr: str | None,
    dep: str | None,
    *,
    day: date = D1,
    staff: tuple[str, ...] = (NURSE_A,),
    plan: str | None = None,
) -> rules.RkVisit:
    return rules.RkVisit(
        visit_id=vid,
        date=day,
        patient=patient,
        staff=staff,
        plan_start=plan or arr,
        arrival=arr,
        departure=dep,
    )


def kp(
    patient: str,
    start: str,
    end: str,
    *,
    day: date = D1,
    staff: tuple[str, ...] = ("佐藤　花子",),
    k2: tuple[str, str] | None = None,
) -> rules.KpRow:
    return rules.KpRow(
        date=day,
        patient=patient,
        staff=staff,
        start=start,
        end=end,
        k2_start=k2[0] if k2 else None,
        k2_end=k2[1] if k2 else None,
    )


def one(rows: list[rules.JudgedRow], patient: str) -> rules.JudgedRow:
    hits = [r for r in rows if rules.name_key(r.patient) == rules.name_key(patient)]
    assert len(hits) == 1, hits
    return hits[0]


def codes(row: rules.JudgedRow) -> set[str]:
    return {f.code for f in row.flags}


def test_match_and_small_gap() -> None:
    rows = rules.judge(
        [rk("1", "青木 さくら", "10:00", "10:40"), rk("2", "石田 健", "11:03", "11:41")],
        [kp("青木　さくら", "10:00", "10:40"), kp("石田　健", "11:00", "11:35")],
    )
    assert one(rows, "青木 さくら").status == rules.STATUS_MATCH
    small = one(rows, "石田 健")
    assert small.status == rules.STATUS_APPLY
    assert (small.new_start, small.new_end) == ("11:03", "11:41")
    assert small.default_selected is True
    assert small.flags == []


def test_short_stay_and_no_departure_end_at_arrival_plus_35() -> None:
    rows = rules.judge(
        [rk("1", "木下 誠", "16:16", "16:34"), rk("2", "岡本 翔", "09:31", None)],
        [kp("木下　誠", "16:15", "16:50"), kp("岡本　翔", "9:30", "10:05")],
    )
    short = one(rows, "木下 誠")
    assert short.status == rules.STATUS_APPLY
    assert (short.new_start, short.new_end) == ("16:16", "16:51")
    assert rules.FLAG_SHORT_STAY in codes(short)
    assert short.rk_stay == 18
    no_dep = one(rows, "岡本 翔")
    assert no_dep.status == rules.STATUS_APPLY
    assert (no_dep.new_start, no_dep.new_end) == ("09:31", "10:06")
    assert rules.FLAG_NO_DEPARTURE in codes(no_dep)


def test_gap_of_an_hour_or_more_goes_to_review() -> None:
    rows = rules.judge(
        [rk("1", "斉藤 大地", "16:04", "16:40")],
        [kp("斉藤　大地", "17:20", "17:55")],
    )
    row = one(rows, "斉藤 大地")
    assert row.status == rules.STATUS_REVIEW
    assert rules.FLAG_BIG_GAP in codes(row)
    assert row.default_selected is False


def test_overlap_with_same_staff_goes_to_review() -> None:
    # 後から手入力した時刻どうしが重なる型と、QR の退出が次のお宅の到着より後の型。
    rows = rules.judge(
        [
            rk("1", "木村 駿", "13:00", "14:10"),
            rk("2", "植田 結", "14:00", "14:40"),
            rk("3", "別の 人", "15:00", "15:40", staff=(NURSE_B,)),
        ],
        [
            kp("木村　駿", "13:00", "13:40"),
            kp("植田　結", "14:00", "14:35"),
            kp("別の　人", "15:00", "15:35", staff=(NURSE_B,)),
        ],
    )
    assert one(rows, "木村 駿").status == rules.STATUS_REVIEW
    assert rules.FLAG_OVERLAP in codes(one(rows, "木村 駿"))
    assert one(rows, "植田 結").status == rules.STATUS_REVIEW
    assert one(rows, "別の 人").status == rules.STATUS_APPLY


def test_missing_in_kaipoke_and_kaipoke_only() -> None:
    rows = rules.judge(
        [rk("1", "武藤 榛那", "15:32", "16:07")],
        [kp("上野　ゆり", "13:00", "13:35")],
    )
    missing = one(rows, "武藤 榛那")
    assert missing.status == rules.STATUS_MISSING
    assert missing.item_key is None and missing.default_selected is False
    assert one(rows, "上野 ゆり").status == rules.STATUS_KAIPOKE_ONLY
    counts = rules.count_statuses(rows)
    assert counts[rules.STATUS_MISSING] == 1 and counts[rules.STATUS_KAIPOKE_ONLY] == 1
    assert counts["total"] == 2


def test_variant_characters_and_spaces_pair_up() -> None:
    rows = rules.judge(
        [
            rk("1", "齋藤 花", "10:00", "10:40"),
            rk("2", "髙橋 﨑子", "11:00", "11:40"),
            rk("3", "青栁 德子", "12:00", "12:40"),
            rk("4", "渡邉 冨美", "13:00", "13:40"),
            rk("5", "濱田 邊", "14:00", "14:40"),
        ],
        [
            kp("齊藤　花", "10:00", "10:40"),
            kp("高橋　崎子", "11:00", "11:40"),
            kp("青柳　徳子", "12:00", "12:40"),
            kp("渡辺　富美", "13:00", "13:40"),
            kp("浜田　辺", "14:00", "14:40"),
        ],
    )
    assert [r.status for r in rows] == [rules.STATUS_MATCH] * 5


def test_sisters_at_the_same_time_do_not_share_a_key() -> None:
    # 同じ日・同じ時刻・同じ職員のご姉妹 2 行。鍵に利用者名が入るので別の行を指す。
    rows = rules.judge(
        [rk("1", "増島 梨紗", "14:02", "14:40"), rk("2", "増島 由里彩", "14:02", "14:40")],
        [kp("増島　梨紗", "14:00", "14:35"), kp("増島　由里彩", "14:00", "14:35")],
    )
    a, b = one(rows, "増島 梨紗"), one(rows, "増島 由里彩")
    assert a.item_key != b.item_key
    assert "増島梨紗" in (a.item_key or "") and "増島由里彩" in (b.item_key or "")
    # まったく同じ時刻の 2 件は重なりとみなさない。
    assert a.status == b.status == rules.STATUS_APPLY
    assert rules.FLAG_OVERLAP not in codes(a) | codes(b)


def test_staff_differs_and_two_person_count_differs_go_to_review() -> None:
    rows = rules.judge(
        [
            rk("1", "小林 あおい", "10:00", "10:40", staff=(NURSE_B,)),
            rk("2", "加藤 みのり", "11:00", "11:40", staff=(NURSE_A,)),
        ],
        [
            kp("小林　あおい", "10:00", "10:35"),
            kp("加藤　みのり", "11:00", "11:35", staff=("佐藤　花子", "鈴木　一郎")),
        ],
    )
    diff = one(rows, "小林 あおい")
    assert diff.status == rules.STATUS_REVIEW and rules.FLAG_STAFF_DIFF in codes(diff)
    count = one(rows, "加藤 みのり")
    assert count.status == rules.STATUS_REVIEW and rules.FLAG_STAFF_COUNT in codes(count)


def test_k2_gap_and_kaipoke_end_before_start_still_apply() -> None:
    rows = rules.judge(
        [rk("1", "青木 さくら", "14:53", "15:33"), rk("2", "石田 健", "09:00", "09:40")],
        [
            kp("青木　さくら", "14:43", "15:33", k2=("14:33", "15:33")),
            kp("石田　健", "09:00", "08:35"),
        ],
    )
    k2 = one(rows, "青木 さくら")
    assert k2.status == rules.STATUS_APPLY and rules.FLAG_K2_GAP in codes(k2)
    backwards = one(rows, "石田 健")
    assert backwards.status == rules.STATUS_APPLY
    assert rules.FLAG_KP_END_BEFORE_START in codes(backwards)
    assert rules.FLAG_BIG_GAP not in codes(backwards)


def test_two_visits_same_day_pair_with_the_nearest_row() -> None:
    rows = rules.judge(
        [
            rk("am", "前川 七海", "09:05", "09:40", plan="09:00"),
            rk("pm", "前川 七海", "16:50", "17:28", plan="16:45"),
        ],
        [kp("前川　七海", "16:45", "17:20"), kp("前川　七海", "09:00", "09:35")],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert by_id["am"].kp is not None and by_id["am"].kp.start == "09:00"
    assert by_id["pm"].kp is not None and by_id["pm"].kp.start == "16:45"


def test_no_arrival_is_review_and_far_rows_do_not_pair() -> None:
    rows = rules.judge(
        [
            rk("1", "上野 ゆり", None, None, plan="10:00"),
            rk("2", "岡本 翔", "08:00", "08:40", day=D2),
        ],
        [kp("上野　ゆり", "10:00", "10:35"), kp("岡本　翔", "13:00", "13:35", day=D2)],
    )
    no_arr = one(rows, "上野 ゆり")
    assert no_arr.status == rules.STATUS_REVIEW and rules.FLAG_NO_ARRIVAL in codes(no_arr)
    statuses = sorted(r.status for r in rows if rules.name_key(r.patient) == "岡本翔")
    assert statuses == [rules.STATUS_KAIPOKE_ONLY, rules.STATUS_MISSING]


def test_duplicate_kaipoke_rows_are_flagged() -> None:
    # 同じ利用者・同じ時刻・同じ職員のカイポケの行が 2 つ → どちらも人が見る。
    rows = rules.judge(
        [rk("1", "青木 さくら", "10:02", "10:40"), rk("2", "青木 さくら", "10:03", "10:41")],
        [kp("青木　さくら", "10:00", "10:35"), kp("青木　さくら", "10:00", "10:35")],
    )
    assert {r.status for r in rows} == {rules.STATUS_REVIEW}
    assert all(rules.FLAG_DUP_TARGET in codes(r) for r in rows)


def test_parse_kp_csv_reads_actual_columns() -> None:
    header = (
        "﻿職員名１,職種１,職員名２,職種２,職員名３,日付,利用者,業務種別,サービス内容,"
        "開始時間,終了時間,提供時間（分）,記録Ⅱ開始時間,記録Ⅱ終了時間"
    )
    body = [
        "佐藤　花子,看護師,-,,,1,青木　さくら,医療保険,訪問看護基本療養費,9:05,9:40,35,9:00,9:40",
        "佐藤　花子,看護師,,,,2,石田　健,朝会,,9:00,9:30,,,",
        ",,,,,x,合計,,,,,,,",
    ]
    rows = parse_kp_csv("\n".join([header, *body]), "2026-10")
    assert len(rows) == 1
    row = rows[0]
    assert row.date == D1 and row.patient == "青木 さくら"
    assert row.staff == ("佐藤 花子",)
    assert (row.start, row.end, row.minutes) == ("09:05", "09:40", 35)
    assert (row.k2_start, row.k2_end) == ("09:00", "09:40")


def test_other_visit_does_not_take_the_row_of_the_exact_visit() -> None:
    # レビューの重大: らく助 10:00〜10:30 と 10:40〜11:15、カイポケ 10:40〜11:15 の 1 行。
    # 近い順の早い者勝ちだと 10:00 の訪問が 10:40 の行を取り「反映する」になっていた。
    rows = rules.judge(
        [
            rk("a", "前川 七海", "10:00", "10:30"),
            rk("b", "前川 七海", "10:40", "11:15"),
        ],
        [kp("前川　七海", "10:40", "11:15")],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert by_id["b"].kp is not None and by_id["b"].status == rules.STATUS_MATCH
    assert by_id["a"].kp is None and by_id["a"].status == rules.STATUS_MISSING
    assert all(r.status != rules.STATUS_APPLY for r in rows)


def test_count_differs_sends_every_pair_of_that_day_to_review() -> None:
    # 件数が違う (らく助 2・カイポケ 1) → 組めた行も人が見る。組めなかった方はカイポケに無い。
    rows = rules.judge(
        [
            rk("a", "前川 七海", "10:00", "10:30"),
            rk("b", "前川 七海", "10:42", "11:15"),
        ],
        [kp("前川　七海", "10:40", "11:15")],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert by_id["b"].kp is not None and by_id["b"].status == rules.STATUS_REVIEW
    assert rules.FLAG_COUNT_DIFF in codes(by_id["b"])
    assert by_id["b"].default_selected is False
    assert by_id["a"].status == rules.STATUS_MISSING


def test_pairs_are_assigned_by_smallest_gap_first() -> None:
    # 予定の順に早い者勝ちだと a (10:30) が 10:40 の行を取り、b (10:40) は 11:30 と組む。
    # 差 0 の組 (b と 10:40) を先に固定する。
    rows = rules.judge(
        [
            rk("a", "前川 七海", "10:30", "11:05", plan="10:30"),
            rk("b", "前川 七海", "10:40", "11:15", plan="10:45"),
        ],
        [kp("前川　七海", "10:40", "11:15"), kp("前川　七海", "11:30", "12:05")],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert by_id["b"].kp is not None and by_id["b"].kp.start == "10:40"
    assert by_id["b"].status == rules.STATUS_MATCH


def test_short_stay_with_long_kaipoke_stay_goes_to_review() -> None:
    rows = rules.judge(
        [
            rk("1", "青木 さくら", "10:00", "10:20"),
            rk("2", "石田 健", "13:00", None),
            rk("3", "木下 誠", "15:00", "15:20"),
        ],
        [
            kp("青木　さくら", "10:00", "11:00"),
            kp("石田　健", "13:00", "14:05"),
            kp("木下　誠", "15:00", "15:50"),
        ],
    )
    for name in ("青木 さくら", "石田 健"):
        row = one(rows, name)
        assert row.status == rules.STATUS_REVIEW and rules.FLAG_KP_LONGER in codes(row)
    short = one(rows, "木下 誠")  # カイポケ 50 分 → 60 分未満なので反映してよい
    assert short.status == rules.STATUS_APPLY and rules.FLAG_KP_LONGER not in codes(short)


def test_same_item_key_with_a_kaipoke_only_twin_goes_to_review() -> None:
    # カイポケに同じ鍵の行が 2 つ (片方はカイポケだけ) → RPA はどちらか決められない。
    rows = rules.judge(
        [rk("1", "青木 さくら", "10:02", "10:40")],
        [kp("青木　さくら", "10:00", "10:35"), kp("青木　さくら", "10:00", "10:35")],
    )
    paired = [r for r in rows if r.rk is not None]
    assert len(paired) == 1 and paired[0].status == rules.STATUS_REVIEW
    assert rules.FLAG_DUP_TARGET in codes(paired[0])


def test_swapped_order_sends_every_pair_of_that_day_to_review() -> None:
    # 再レビューの中: らく助 A 09:30〜10:00・B 10:00〜10:30、カイポケ a 09:45〜10:25・
    # b 10:20〜10:50。差の小さい順だと A→b・B→a と組まれ、両方「反映する」になっていた。
    rows = rules.judge(
        [
            rk("A", "前川 七海", "09:30", "10:00"),
            rk("B", "前川 七海", "10:00", "10:30"),
        ],
        [kp("前川　七海", "09:45", "10:25"), kp("前川　七海", "10:20", "10:50")],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert by_id["A"].kp is not None and by_id["A"].kp.start == "10:20"
    assert by_id["B"].kp is not None and by_id["B"].kp.start == "09:45"
    for row in by_id.values():
        assert row.status == rules.STATUS_REVIEW
        assert rules.FLAG_ORDER_SWAP in codes(row)
        assert row.default_selected is False


def test_same_order_is_not_flagged_as_swapped() -> None:
    rows = rules.judge(
        [
            rk("A", "前川 七海", "09:30", "10:00"),
            rk("B", "前川 七海", "13:00", "13:30"),
        ],
        [kp("前川　七海", "09:32", "10:00"), kp("前川　七海", "13:05", "13:30")],
    )
    assert all(rules.FLAG_ORDER_SWAP not in codes(r) for r in rows)
    assert all(r.status == rules.STATUS_APPLY for r in rows)


def test_two_patient_codes_under_one_name_go_to_review() -> None:
    # 字体をそろえると 髙橋様 と 高橋様 (別の方) が同じ (日・利用者) になる。
    visits = [
        rules.RkVisit(
            visit_id="1",
            date=D1,
            patient="髙橋 一",
            patient_code="P001",
            staff=(NURSE_A,),
            plan_start="09:00",
            arrival="09:00",
            departure="09:40",
        ),
        rules.RkVisit(
            visit_id="2",
            date=D1,
            patient="高橋 一",
            patient_code="P002",
            staff=(NURSE_A,),
            plan_start="14:00",
            arrival="14:02",
            departure="14:40",
        ),
    ]
    rows = rules.judge(visits, [kp("高橋　一", "09:00", "09:30"), kp("高橋　一", "14:00", "14:40")])
    paired = [r for r in rows if r.rk is not None]
    assert len(paired) == 2
    for row in paired:
        assert row.status == rules.STATUS_REVIEW
        assert rules.FLAG_CODE_VARIANT in codes(row)


def test_same_patient_code_is_not_flagged() -> None:
    visits = [
        rules.RkVisit(
            visit_id="1",
            date=D1,
            patient="高橋 一",
            patient_code="P001",
            staff=(NURSE_A,),
            plan_start="09:00",
            arrival="09:00",
            departure="09:40",
        ),
    ]
    rows = rules.judge(visits, [kp("高橋　一", "09:00", "09:30")])
    assert rules.FLAG_CODE_VARIANT not in codes(rows[0])
    assert rows[0].status == rules.STATUS_APPLY


def test_equal_gap_prefers_the_row_with_the_same_staff() -> None:
    # 差が同じ (どちらも 10 分) なら、職員1 が担当と一致する行を先に選ぶ。
    rows = rules.judge(
        [rk("1", "青木 さくら", "10:00", "10:40", staff=(NURSE_B,))],
        [
            kp("青木　さくら", "09:50", "10:30", staff=("佐藤　花子",)),
            kp("青木　さくら", "10:10", "10:50", staff=("鈴木　一郎",)),
        ],
    )
    paired = [r for r in rows if r.rk is not None]
    assert len(paired) == 1 and paired[0].kp is not None
    assert paired[0].kp.start == "10:10"


def test_swap_in_a_day_of_three_visits_sends_all_three_to_review() -> None:
    # 3 件の日: A・B が入れ替わって組まれると、入れ替わっていない C も人が見る。
    rows = rules.judge(
        [
            rk("A", "前川 七海", "09:30", "10:00"),
            rk("B", "前川 七海", "10:00", "10:30"),
            rk("C", "前川 七海", "13:00", "13:40"),
        ],
        [
            kp("前川　七海", "09:45", "10:25"),
            kp("前川　七海", "10:20", "10:50"),
            kp("前川　七海", "13:05", "13:40"),
        ],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert len(by_id) == 3
    assert by_id["C"].kp is not None and by_id["C"].kp.start == "13:05"
    for row in by_id.values():
        assert row.status == rules.STATUS_REVIEW
        assert rules.FLAG_ORDER_SWAP in codes(row)
        assert row.default_selected is False


def test_pairs_at_the_same_time_are_not_flagged_as_swapped() -> None:
    # 同じ時刻の 2 件 (別の職員) は、順番の入れ替わりとみなさない。担当が一致する行と組む。
    rows = rules.judge(
        [
            rk("A", "前川 七海", "09:00", "09:40", staff=(NURSE_A,)),
            rk("B", "前川 七海", "09:00", "09:40", staff=(NURSE_B,)),
        ],
        [
            kp("前川　七海", "09:00", "09:35", staff=("佐藤　花子",)),
            kp("前川　七海", "09:00", "09:35", staff=("鈴木　一郎",)),
        ],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert by_id["A"].kp is not None and by_id["A"].kp.staff1 == "佐藤　花子"
    assert by_id["B"].kp is not None and by_id["B"].kp.staff1 == "鈴木　一郎"
    for row in by_id.values():
        assert rules.FLAG_ORDER_SWAP not in codes(row)
        assert row.status == rules.STATUS_APPLY


def test_same_count_with_unpaired_rows_sends_the_pair_to_review() -> None:
    # 件数は同じ 2 件でも、09:00 と 13:30 が組めない日は、組めた 11:00→10:30 も人が見る。
    rows = rules.judge(
        [
            rk("A", "前川 七海", "09:00", "09:40"),
            rk("B", "前川 七海", "11:00", "11:40"),
        ],
        [kp("前川　七海", "10:30", "11:10"), kp("前川　七海", "13:30", "14:10")],
    )
    by_id = {r.rk.visit_id: r for r in rows if r.rk}
    assert by_id["A"].status == rules.STATUS_MISSING
    assert by_id["B"].kp is not None and by_id["B"].kp.start == "10:30"
    assert by_id["B"].status == rules.STATUS_REVIEW
    assert rules.FLAG_COUNT_DIFF in codes(by_id["B"])
    assert by_id["B"].default_selected is False
    assert [r.kp.start for r in rows if r.rk is None and r.kp] == ["13:30"]


def test_empty_patient_code_mixed_in_goes_to_review() -> None:
    # 同じ (日・利用者) に利用者コードが空の訪問が混ざると、別の方か決められない。
    visits = [
        rules.RkVisit(
            visit_id="1",
            date=D1,
            patient="髙橋 一",
            patient_code="P001",
            staff=(NURSE_A,),
            plan_start="09:00",
            arrival="09:00",
            departure="09:40",
        ),
        rules.RkVisit(
            visit_id="2",
            date=D1,
            patient="高橋 一",
            patient_code="",
            staff=(NURSE_A,),
            plan_start="14:00",
            arrival="14:02",
            departure="14:40",
        ),
    ]
    rows = rules.judge(visits, [kp("高橋　一", "09:00", "09:30"), kp("高橋　一", "14:00", "14:40")])
    paired = [r for r in rows if r.rk is not None]
    assert len(paired) == 2
    for row in paired:
        assert row.status == rules.STATUS_REVIEW
        assert rules.FLAG_CODE_VARIANT in codes(row)
