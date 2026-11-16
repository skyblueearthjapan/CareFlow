"""予定のお知らせ (SMS) の文面づくりの試験 (設計 §11「文面」).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §1・§4。
事業所名・電話・お名前はすべて見本。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.services.sms import composer as c

STATION = "見本ステーション"
TEL = "000-000-0000"
# 2026-11-25 は水曜日。その週の月曜は 11/23・次の週の月曜は 11/30。
TODAY = date(2026, 11, 25)


def _week(week_start: date, slots, today: date = TODAY) -> str:
    return c.compose_week_notice(
        station_name=STATION,
        contact_tel=TEL,
        surname="見本",
        week_start=week_start,
        today=today,
        slots=slots,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("見本 太郎", "見本"),
        ("見本　太郎", "見本"),
        ("  見本 太郎 ", "見本"),
        ("見本太郎", "見本太郎"),
        ("", ""),
        (None, ""),
    ],
)
def test_surname_of(name, expected) -> None:
    assert c.surname_of(name) == expected


@pytest.mark.parametrize(
    ("week_start", "label"),
    [
        (date(2026, 11, 23), "今週の"),
        (date(2026, 11, 30), "来週の"),
        (date(2026, 12, 7), "12/7からの週の"),
        (date(2026, 11, 16), "11/16からの週の"),
    ],
)
def test_week_label(week_start, label) -> None:
    assert c.week_label(week_start, TODAY) == label


def test_week_label_on_monday_and_sunday() -> None:
    # 月曜・日曜に送っても、同じ週は「今週の」。
    assert c.week_label(date(2026, 11, 23), date(2026, 11, 23)) == "今週の"
    assert c.week_label(date(2026, 11, 23), date(2026, 11, 29)) == "今週の"
    assert c.week_label(date(2026, 11, 30), date(2026, 11, 29)) == "来週の"


def test_week_notice_form() -> None:
    body = _week(
        date(2026, 11, 30),
        [(date(2026, 12, 5), time(9, 30)), (date(2026, 12, 1), time(10, 0))],
    )
    assert body == (
        "見本ステーション です。見本様の来週のご訪問予定をお知らせします。\n"
        "12/1(火)10:00\n"
        "12/5(土)9:30\n"
        "時刻は前後することがあります。ご都合が悪いときは000-000-0000までご連絡ください。"
    )


def test_two_visits_same_day_are_two_lines_and_two_person_visit_is_one() -> None:
    body = _week(
        date(2026, 11, 30),
        [
            (date(2026, 12, 1), time(10, 0)),
            # 旧形式の 2 名体制 (同じ日・同じ時刻の 2 行) は 1 行。
            (date(2026, 12, 1), time(10, 0)),
            (date(2026, 12, 1), time(15, 0)),
        ],
    )
    lines = body.split("\n")
    assert lines[1:3] == ["12/1(火)10:00", "12/1(火)15:00"]
    assert len(lines) == 4


def test_this_week_and_dated_wording() -> None:
    assert "見本様の今週のご訪問予定" in _week(date(2026, 11, 23), [(date(2026, 11, 26), time(9))])
    assert "見本様の12/7からの週のご訪問予定" in _week(
        date(2026, 12, 7), [(date(2026, 12, 8), time(9))]
    )


def test_only_surname_in_body() -> None:
    body = c.compose_week_notice(
        station_name=STATION,
        contact_tel=TEL,
        surname=c.surname_of("見本 花子"),
        week_start=date(2026, 11, 30),
        today=TODAY,
        slots=[(date(2026, 12, 1), time(10))],
    )
    assert "見本様" in body and "花子" not in body


def test_slots_to_json() -> None:
    slots = c.unique_slots([(date(2026, 12, 1), time(9, 5)), (date(2026, 12, 1), time(9, 5))])
    assert c.slots_to_json(slots) == [{"date": "2026-12-01", "start": "09:05"}]


# ---------------------------------------------------------------------------
# 文字数と通数 (改行は 2 文字)
# ---------------------------------------------------------------------------


def test_count_chars_newline_is_two() -> None:
    assert c.count_chars("あ\nい") == 4
    assert c.count_chars("abc") == 3


@pytest.mark.parametrize(
    ("chars", "carrier", "segments"),
    [
        (1, "docomo", 1),
        (70, "docomo", 1),
        (71, "docomo", 2),
        (132, "docomo", 2),
        (133, "docomo", 3),
        (198, "docomo", 3),
        (199, "docomo", 4),
        (134, "au", 2),
        (135, "au", 3),
        (201, "au", 3),
        (202, "au", 4),
        (134, "rakuten", 2),
        # 知らないキャリア・SoftBank は見込みに docomo の表を使う。
        (133, "softbank", 3),
    ],
)
def test_estimate_segments(chars, carrier, segments) -> None:
    assert c.estimate_segments(chars, carrier) == segments


def test_estimate_default_is_docomo() -> None:
    assert c.ESTIMATE_CARRIER == "docomo"
    assert c.estimate_segments(133) == 3


def test_three_visits_is_two_segments() -> None:
    body = _week(
        date(2026, 11, 30),
        [
            (date(2026, 12, 1), time(10)),
            (date(2026, 12, 3), time(14)),
            (date(2026, 12, 5), time(9, 30)),
        ],
    )
    chars = c.count_chars(body)
    assert chars == len(body) + 4
    assert 71 <= chars <= 132
    assert c.estimate_segments(chars) == 2


# ---------------------------------------------------------------------------
# 変更のお知らせ
# ---------------------------------------------------------------------------


def _change(*changes: c.VisitChange) -> str:
    return c.compose_change_notice(
        station_name=STATION, contact_tel=TEL, surname="見本", changes=list(changes)
    )


def test_change_time() -> None:
    body = _change(
        c.VisitChange("time", date(2026, 12, 3), time(14), date(2026, 12, 3), time(15, 30))
    )
    assert body == (
        "【見本ステーション】見本様 ご訪問の変更です。12/3(木)14:00→15:30 "
        "ご都合が悪いときは000-000-0000へ"
    )
    assert c.estimate_segments(c.count_chars(body)) == 1


def test_change_cancel_and_add() -> None:
    assert _change(c.VisitChange("cancel", old_date=date(2026, 12, 3), old_start=time(14))) == (
        "【見本ステーション】見本様 12/3(木)14:00のご訪問はお休みになりました。"
        "ご不明な点は000-000-0000へ"
    )
    assert _change(c.VisitChange("add", new_date=date(2026, 12, 4), new_start=time(10))) == (
        "【見本ステーション】見本様 12/4(金)10:00にご訪問が加わりました。"
        "ご都合が悪いときは000-000-0000へ"
    )


def test_change_date() -> None:
    body = _change(c.VisitChange("date", date(2026, 12, 3), time(14), date(2026, 12, 4), time(10)))
    assert "12/3(木)14:00→12/4(金)10:00" in body


def test_change_several_lines() -> None:
    body = _change(
        c.VisitChange("time", date(2026, 12, 3), time(14), date(2026, 12, 3), time(15, 30)),
        c.VisitChange("cancel", old_date=date(2026, 12, 5), old_start=time(9, 30)),
        c.VisitChange("add", new_date=date(2026, 12, 6), new_start=time(10)),
    )
    assert body.split("\n") == [
        "【見本ステーション】見本様 ご訪問の変更です。",
        "12/3(木)14:00→15:30",
        "12/5(土)9:30 お休み",
        "12/6(日)10:00 追加",
        "ご都合が悪いときは000-000-0000へ",
    ]


def test_change_empty_is_error() -> None:
    with pytest.raises(ValueError):
        _change()
