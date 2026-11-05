"""連絡先の共通の決まり (``app/services/contacts.py``) の試験.

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §2・§3・§8。
電話の表は frontend の ``lib/contacts/phone.ts`` の試験と同じ例を使う。番号はすべて見本。
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.services.contacts import (
    ContactRow,
    ContactValueError,
    PreviousContact,
    build_contact_row,
    classify_email,
    classify_phone,
    db_error_label,
    effective_channel,
    is_carrier_email,
    normalize_patient_contacts,
    sms_capable_for_kind,
)

TODAY = date(2026, 11, 5)


# ---------------------------------------------------------------------------
# 電話の見分け (§3 の表)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "digits", "kind", "sms", "display"),
    [
        ("090-0000-0000", "09000000000", "mobile", True, "090-0000-0000"),
        ("08010000000", "08010000000", "mobile", True, "080-1000-0000"),
        ("070 0000 0000", "07000000000", "mobile", True, "070-0000-0000"),
        ("060-0000-0000", "06000000000", "mobile_new", None, "060-0000-0000"),
        ("050-0000-0000", "05000000000", "ip", False, "050-0000-0000"),
        ("020-0000-0000", "02000000000", "data", False, "020-0000-0000"),
        ("0120-000-000", "0120000000", "freedial", False, "0120-000-000"),
        ("0800-000-0000", "08000000000", "freedial", False, "0800-000-0000"),
        ("043-000-0000", "0430000000", "landline", False, "043-000-0000"),
        ("0300000000", "0300000000", "landline", False, "0300000000"),
        ("0436-00-0000", "0436000000", "landline", False, "0436-00-0000"),
        # 全角・全角ハイフン・長音
        ("０９０－００００－００００", "09000000000", "mobile", True, "090-0000-0000"),
        ("０４３ー０００ー００００", "0430000000", "landline", False, "043-000-0000"),
        # +81
        ("+81 90 0000 0000", "09000000000", "mobile", True, "090-0000-0000"),
        ("+81-43-000-0000", "0430000000", "landline", False, "043-000-0000"),
        ("＋８１９０００００００００", "09000000000", "mobile", True, "090-0000-0000"),
        # 前後の空白
        ("  090-0000-0000  ", "09000000000", "mobile", True, "090-0000-0000"),
    ],
)
def test_classify_phone_table(raw, digits, kind, sms, display) -> None:
    info = classify_phone(raw)
    assert info is not None
    assert (info.digits, info.kind, info.sms_capable, info.display) == (
        digits,
        kind,
        sms,
        display,
    )


def test_classify_phone_bom_and_zero_width() -> None:
    # BOM・幅の無い文字は除く (frontend の試験と同じ例)。
    info = classify_phone("\ufeff+81 90-1234-5678")
    assert info is not None and (info.digits, info.kind) == ("09012345678", "mobile")
    info = classify_phone("090\u200b-1234-5678\u2060")
    assert info is not None and info.digits == "09012345678"


def test_classify_phone_non_ascii_digits_are_not_digits() -> None:
    # アラビア・インドの数字は NFKC でも 0-9 にならない。frontend と同じく数えない。
    with pytest.raises(ContactValueError) as exc:
        classify_phone("090" + "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667")
    assert exc.value.kind == "too_short"


def test_freedial_0800_is_checked_before_mobile() -> None:
    # 0800 + 7 桁は 080 の携帯と同じ 11 桁。0800 はフリーダイヤルなので先に当てる。
    info = classify_phone("0800-000-0000")
    assert info is not None and info.kind == "freedial" and info.sms_capable is False
    mobile = classify_phone("080-1000-0000")
    assert mobile is not None and mobile.kind == "mobile"


@pytest.mark.parametrize("raw", [None, "", "   ", "　"])
def test_classify_phone_blank(raw) -> None:
    assert classify_phone(raw) is None


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("090-000-000", "too_short"),
        ("090-0000-00000", "too_long"),
        ("090000000000000", "too_long"),
        ("abc", "bad_format"),
        ("12345678901", "bad_format"),
        ("1234567890", "bad_format"),
    ],
)
def test_classify_phone_errors(raw, kind) -> None:
    with pytest.raises(ContactValueError) as exc:
        classify_phone(raw)
    assert exc.value.kind == kind
    assert exc.value.message


def test_sms_capable_for_kind() -> None:
    assert sms_capable_for_kind("mobile") is True
    assert sms_capable_for_kind("mobile_new") is None
    assert sms_capable_for_kind("landline") is False
    assert sms_capable_for_kind(None) is None


# ---------------------------------------------------------------------------
# メール
# ---------------------------------------------------------------------------


def test_classify_email() -> None:
    assert classify_email("  Taro@Example.COM ") == "taro@example.com"
    assert classify_email("") is None
    assert classify_email(None) is None
    assert classify_email("\ufeffTaro@Example.com\u200b") == "taro@example.com"
    assert classify_email("\ufeff") is None
    for bad in ("taro", "taro@", "taro@example", "ta ro@example.com"):
        with pytest.raises(ContactValueError) as exc:
            classify_email(bad)
        assert exc.value.kind == "email"


@pytest.mark.parametrize(
    ("email", "carrier"),
    [
        ("a@docomo.ne.jp", True),
        ("a@ezweb.ne.jp", True),
        ("a@au.com", True),
        ("a@softbank.ne.jp", True),
        ("a@i.softbank.jp", True),
        ("a@ymobile.ne.jp", True),
        ("a@example.com", False),
        (None, None),
    ],
)
def test_carrier_email(email, carrier) -> None:
    assert is_carrier_email(email) is carrier


def test_effective_channel() -> None:
    assert effective_channel("sms", "mobile", None) == "sms"
    assert effective_channel("sms", "mobile_new", None) == "sms"
    assert effective_channel("sms", "landline", "a@example.com") == "email"
    assert effective_channel("sms", "landline", None) == "none"
    assert effective_channel("email", "mobile", "a@example.com") == "email"
    assert effective_channel("email", "mobile", None) == "sms"
    assert effective_channel("email", None, None) == "none"


def test_build_contact_row_self_has_no_name() -> None:
    row = build_contact_row(
        relation="self",
        name="見本 太郎",
        relation_note="本人の携帯",
        phone="090-0000-0000",
        email=None,
        is_notify_target=True,
        notify_channel="sms",
        consent_status="unconfirmed",
    )
    assert row.name is None
    assert row.relation_note is None
    assert row.phone == "09000000000"
    assert row.phone_display == "090-0000-0000"


# ---------------------------------------------------------------------------
# normalize (§2・§8)
# ---------------------------------------------------------------------------


def _row(relation: str, **kw) -> ContactRow:
    base = {
        "relation": relation,
        "phone": None,
        "phone_kind": None,
        "email": None,
        "is_notify_target": False,
        "notify_channel": "sms",
        "consent_status": "unconfirmed",
    }
    base.update(kw)
    return ContactRow(**base)


def test_last_target_removed_moves_to_self() -> None:
    fam_id = uuid.uuid4()
    rows = [
        _row("family", id=fam_id, name="見本 花子", phone="09000000000", phone_kind="mobile"),
        _row("self", phone="0430000000", phone_kind="landline"),
    ]
    prev = {fam_id: PreviousContact("unconfirmed", None, is_notify_target=True)}
    res = normalize_patient_contacts(rows, prev, today=TODAY)
    assert [r.is_notify_target for r in res.rows] == [False, True]
    assert res.notices and "ご本人" in res.notices[0]


def test_self_ng_moves_to_next_with_means() -> None:
    rows = [
        _row("self", consent_status="ng", phone="09000000000", phone_kind="mobile"),
        _row("facility", name="見本の家"),  # 手段なし
        _row("family", name="見本 花子", email="a@example.com"),
    ]
    res = normalize_patient_contacts(rows, {}, today=TODAY)
    assert [r.is_notify_target for r in res.rows] == [False, False, True]


def test_no_other_candidate_keeps_the_only_one() -> None:
    cid = uuid.uuid4()
    rows = [_row("family", id=cid, name="見本 花子", phone="09000000000", phone_kind="mobile")]
    prev = {cid: PreviousContact("unconfirmed", None, is_notify_target=True)}
    res = normalize_patient_contacts(rows, prev, today=TODAY)
    assert res.rows[0].is_notify_target is True


def test_ng_is_forced_off() -> None:
    rows = [
        _row("self", is_notify_target=True, consent_status="ng"),
        _row("family", is_notify_target=True, name="見本 花子"),
    ]
    res = normalize_patient_contacts(rows, {}, today=TODAY)
    assert [r.is_notify_target for r in res.rows] == [False, True]
    assert any("NG 回答" in n for n in res.notices)


def test_all_ng_has_no_target() -> None:
    rows = [_row("self", consent_status="ng"), _row("family", consent_status="ng")]
    res = normalize_patient_contacts(rows, {}, today=TODAY)
    assert not any(r.is_notify_target for r in res.rows)


def test_empty_rows() -> None:
    res = normalize_patient_contacts([], {}, today=TODAY)
    assert res.rows == [] and res.notices == []


def test_targets_kept_as_is() -> None:
    rows = [_row("self", is_notify_target=True), _row("family", is_notify_target=True)]
    res = normalize_patient_contacts(rows, {}, today=TODAY)
    assert [r.is_notify_target for r in res.rows] == [True, True]
    assert res.notices == []


def test_consent_on_rules() -> None:
    old_day = date(2026, 10, 1)
    same, changed, back, unknown = (uuid.uuid4() for _ in range(4))
    prev = {
        same: PreviousContact("confirmed", old_day, True),
        changed: PreviousContact("unconfirmed", None, False),
        back: PreviousContact("confirmed", old_day, False),
    }
    rows = [
        _row("self", id=same, consent_status="confirmed", is_notify_target=True),
        _row("family", id=changed, consent_status="ng"),
        _row("facility", id=back, consent_status="unconfirmed"),
    ]
    res = normalize_patient_contacts(rows, prev, today=TODAY)
    assert [r.consent_on for r in res.rows] == [old_day, TODAY, None]

    # 新しい行 (id なし・知らない id) は confirmed なら今日、未確認なら None。
    res2 = normalize_patient_contacts(
        [_row("self", id=unknown, consent_status="confirmed"), _row("family")],
        prev,
        today=TODAY,
    )
    assert [r.consent_on for r in res2.rows] == [TODAY, None]


def test_normalize_does_not_mutate_input() -> None:
    rows = [_row("self", consent_status="ng", is_notify_target=True)]
    normalize_patient_contacts(rows, {}, today=TODAY)
    assert rows[0].is_notify_target is True


def test_db_error_label_has_no_values() -> None:
    from sqlalchemy.exc import IntegrityError

    class _OrigError(Exception):
        constraint_name = "uq_patient_contacts_self"

    exc = IntegrityError(
        "INSERT INTO patient_contacts (phone, email) VALUES (?, ?)",
        ("09000000000", "a@example.com"),
        _OrigError("duplicate 09000000000"),
    )
    label = db_error_label(exc)
    assert label == "IntegrityError (uq_patient_contacts_self)"
    assert db_error_label(ValueError("a@example.com")) == "ValueError"
