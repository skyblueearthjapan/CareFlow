"""患者様・スタッフの連絡先 (電話番号・メール) の共通の決まり.

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §2・§3 (PO 了承 2026-11-05)。
API (``app/api/v1/patient_contacts.py``・``staff.py``) と患者マスターの Excel の両方から使う。
画面の ``frontend/lib/contacts/phone.ts`` と同じ見分けにする (同じ表の試験を両方に持つ)。

* :func:`classify_phone` — 電話番号の見分け (§3 の表)。
* :func:`classify_email` — メールの見分け (携帯会社のドメインの印)。
* :func:`normalize_patient_contacts` — 送る先を 0 件にしない・NG 回答は送る先から外す・
  同意の日 (``consent_on``) を決める。
* :func:`replace_patient_contacts` — 患者の連絡先を全部置きかえる (DB)。
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_contact import PatientContact

JST = ZoneInfo("Asia/Tokyo")

RELATIONS: tuple[str, ...] = ("self", "family", "facility", "care_manager", "other")
NOTIFY_CHANNELS: tuple[str, ...] = ("sms", "email")
STAFF_NOTIFY_CHANNELS: tuple[str, ...] = ("sms", "email", "none")
CONSENT_STATUSES: tuple[str, ...] = ("unconfirmed", "confirmed", "ng")
SOURCES: tuple[str, ...] = ("manual", "excel")

#: 患者 1 人の連絡先の上限。
MAX_PATIENT_CONTACTS = 3

RELATION_LABEL: dict[str, str] = {
    "self": "ご本人",
    "family": "ご家族",
    "facility": "施設",
    "care_manager": "ケアマネ",
    "other": "その他",
}

PhoneKind = Literal["mobile", "mobile_new", "ip", "data", "freedial", "landline"]
PhoneErrorKind = Literal["too_short", "too_long", "bad_format"]

PHONE_ERROR_LABEL: dict[str, str] = {
    "too_short": "桁が足りません",
    "too_long": "桁が多すぎます",
    "bad_format": "番号の形が違います",
}

EMAIL_ERROR_LABEL = "メールアドレスの形が違います"


def today_jst() -> date:
    """JST の今日 (本番のコンテナは UTC のため ``date.today()`` を使わない)."""
    return datetime.now(JST).date()


# ---------------------------------------------------------------------------
# 電話番号 (§3)
# ---------------------------------------------------------------------------

# ハイフンに見える文字 (NFKC の後に残るもの)。frontend の HYPHEN_LIKE と同じ。
_HYPHEN_LIKE = re.compile("[‐-―−ーｰ﹣－]")
_PLUS81 = re.compile(r"^\+81[\s-]*")


@dataclass(frozen=True)
class PhoneInfo:
    """正しい番号の見分けの結果."""

    digits: str
    kind: PhoneKind
    #: True=送れる / False=送れない / None=未確認 (060)。
    sms_capable: bool | None
    display: str


class ContactValueError(ValueError):
    """電話・メールの形が違う (``kind`` は電話の誤りの種類・メールは ``email``)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _normalize_phone_input(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw)
    s = _HYPHEN_LIKE.sub("-", s).strip()
    return _PLUS81.sub("0", s)


def _format_344(d: str) -> str:
    return f"{d[:3]}-{d[3:7]}-{d[7:]}"


def classify_phone(raw: str | None) -> PhoneInfo | None:
    """電話番号を見分ける. 空文字・None は None (番号なし). 形が違えば :class:`ContactValueError`."""
    if raw is None:
        return None
    s = _normalize_phone_input(str(raw))
    if s == "":
        return None
    digits = re.sub(r"\D", "", s)

    # 0800 + 7 桁 (11 桁) は 080 の携帯と同じ並びになるので、フリーダイヤルを先に見る
    # (0800 はフリーダイヤルの番号で、携帯の 080 番号は 0800 で始まらない)。
    if re.fullmatch(r"0120\d{6}|0800\d{7}", digits):
        return PhoneInfo(digits, "freedial", False, f"{digits[:4]}-{digits[4:7]}-{digits[7:]}")
    if re.fullmatch(r"0[789]0\d{8}", digits):
        return PhoneInfo(digits, "mobile", True, _format_344(digits))
    if re.fullmatch(r"060\d{8}", digits):
        return PhoneInfo(digits, "mobile_new", None, _format_344(digits))
    if re.fullmatch(r"050\d{8}", digits):
        return PhoneInfo(digits, "ip", False, _format_344(digits))
    if re.fullmatch(r"020\d{8}", digits):
        return PhoneInfo(digits, "data", False, _format_344(digits))
    if re.fullmatch(r"0[1-9]\d{8}", digits):
        # 固定電話: 入力にハイフンがあればその位置 (区切りを除いた並びが digits と一致するとき)。
        kept = re.sub(r"[^\d-]", "", s)
        kept = re.sub(r"-+", "-", kept).strip("-")
        display = kept if "-" in kept and kept.replace("-", "") == digits else digits
        return PhoneInfo(digits, "landline", False, display)

    if digits == "":
        kind: PhoneErrorKind = "bad_format"
    elif len(digits) < 10:
        kind = "too_short"
    elif len(digits) > 11:
        kind = "too_long"
    else:
        kind = "bad_format"
    raise ContactValueError(kind, PHONE_ERROR_LABEL[kind])


def sms_capable_for_kind(kind: str | None) -> bool | None:
    """保存した ``phone_kind`` から SMS が使えるか (番号なしは None)."""
    if kind is None:
        return None
    if kind == "mobile":
        return True
    if kind == "mobile_new":
        return None
    return False


def phone_can_sms(kind: str | None) -> bool:
    """番号があり、SMS が使える (060 は送れる扱い・印は「確認中」)."""
    return kind is not None and sms_capable_for_kind(kind) is not False


# ---------------------------------------------------------------------------
# メール (§3)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_CARRIER_DOMAINS: tuple[str, ...] = (
    "docomo.ne.jp",
    "ezweb.ne.jp",
    "au.com",
    "softbank.ne.jp",
    "i.softbank.jp",
    "ymobile.ne.jp",
)


def classify_email(raw: str | None) -> str | None:
    """メールを小文字・前後空白除去で返す. 空は None. 形が違えば :class:`ContactValueError`."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s == "":
        return None
    if not _EMAIL_RE.match(s):
        raise ContactValueError("email", EMAIL_ERROR_LABEL)
    return s


def is_carrier_email(email: str | None) -> bool | None:
    """携帯会社のドメインなら True (迷惑メールの設定で届かないことがある). メールなしは None."""
    if not email:
        return None
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in _CARRIER_DOMAINS


def effective_channel(notify_channel: str, phone_kind: str | None, email: str | None) -> str:
    """選んだ手段が使えなければ使える方へ落とす (sms / email / none)."""
    can_sms = phone_can_sms(phone_kind)
    has_email = bool(email)
    if notify_channel == "email":
        if has_email:
            return "email"
        return "sms" if can_sms else "none"
    if can_sms:
        return "sms"
    return "email" if has_email else "none"


# ---------------------------------------------------------------------------
# 正規化 (§2)
# ---------------------------------------------------------------------------


@dataclass
class ContactRow:
    """見分けの済んだ連絡先 1 件 (API・Excel の共通の形)."""

    relation: str
    name: str | None = None
    relation_note: str | None = None
    phone: str | None = None
    phone_display: str | None = None
    phone_kind: str | None = None
    email: str | None = None
    is_notify_target: bool = False
    notify_channel: str = "sms"
    consent_status: str = "unconfirmed"
    consent_on: date | None = None
    #: 既存の連絡先なら id (同意の日を引き継ぐため)。新しい行は None。
    id: uuid.UUID | None = None

    @property
    def effective_channel(self) -> str:
        return effective_channel(self.notify_channel, self.phone_kind, self.email)


@dataclass(frozen=True)
class PreviousContact:
    """保存してある連絡先の、正規化に要る値."""

    consent_status: str
    consent_on: date | None
    is_notify_target: bool


@dataclass
class NormalizeResult:
    rows: list[ContactRow]
    notices: list[str] = field(default_factory=list)


def build_contact_row(
    *,
    relation: str,
    name: str | None,
    relation_note: str | None,
    phone: str | None,
    email: str | None,
    is_notify_target: bool,
    notify_channel: str,
    consent_status: str,
    id: uuid.UUID | None = None,
) -> ContactRow:
    """生の入力から :class:`ContactRow` を作る. 電話・メールの形が違えば :class:`ContactValueError`.

    ``ContactValueError.kind`` が ``email`` ならメール、それ以外は電話の誤り。
    """
    info = classify_phone(phone)
    email_n = classify_email(email)
    name_n = (name or "").strip() or None
    note_n = (relation_note or "").strip() or None
    return ContactRow(
        id=id,
        relation=relation,
        # ご本人はお名前を持たない (画面は「本人（〇〇 様）」と自動で出す)。
        name=None if relation == "self" else name_n,
        relation_note=note_n,
        phone=info.digits if info else None,
        phone_display=info.display if info else None,
        phone_kind=info.kind if info else None,
        email=email_n,
        is_notify_target=is_notify_target,
        notify_channel=notify_channel,
        consent_status=consent_status,
    )


def _row_label(row: ContactRow) -> str:
    label = RELATION_LABEL.get(row.relation, row.relation)
    if row.name:
        return f"{label}（{row.name}）"
    return label


def normalize_patient_contacts(
    rows: Sequence[ContactRow],
    previous: Mapping[uuid.UUID, PreviousContact] | None = None,
    *,
    today: date | None = None,
) -> NormalizeResult:
    """送る先を 0 件にしない・NG 回答を送る先から外す・同意の日を決める (§2).

    * NG 回答の連絡先は ``is_notify_target=False`` に強制する。
    * 連絡先が 1 件以上あって送る先が 0 件なら、NG 回答でない連絡先のうち
      ご本人 → 送れる手段 (SMS かメール) がある → 並び順 の順で 1 件を送る先にする。
      今回外した連絡先 (前は送る先だった) は、ほかに移せる先が無いときだけ選ぶ。
    * ``consent_on``: 前と同じ状態ならそのまま、confirmed / ng に変わったら今日 (JST)、
      unconfirmed なら None。
    """
    prev = previous or {}
    day = today or today_jst()
    out: list[ContactRow] = []
    notices: list[str] = []

    for idx, src in enumerate(rows, start=1):
        row = ContactRow(**{k: getattr(src, k) for k in src.__dataclass_fields__})
        before = prev.get(row.id) if row.id is not None else None
        if before is not None and before.consent_status == row.consent_status:
            row.consent_on = before.consent_on
        elif row.consent_status in ("confirmed", "ng"):
            row.consent_on = day
        else:
            row.consent_on = None

        if row.consent_status == "ng" and row.is_notify_target:
            row.is_notify_target = False
            notices.append(f"{idx} 件目（{_row_label(row)}）は NG 回答のため、送る先から外しました")
        out.append(row)

    if out and not any(r.is_notify_target for r in out):
        candidates = [(i, r) for i, r in enumerate(out) if r.consent_status != "ng"]
        if candidates:

            def _key(item: tuple[int, ContactRow]) -> tuple[int, int, int, int]:
                i, r = item
                before = prev.get(r.id) if r.id is not None else None
                just_removed = 1 if (before is not None and before.is_notify_target) else 0
                return (
                    just_removed,
                    0 if r.relation == "self" else 1,
                    0 if r.effective_channel != "none" else 1,
                    i,
                )

            _, chosen = min(candidates, key=_key)
            chosen.is_notify_target = True
            notices.append(f"送る先が無くならないよう、{_row_label(chosen)}を送る先にしました")

    return NormalizeResult(rows=out, notices=notices)


def previous_from_models(
    contacts: Sequence[PatientContact],
) -> dict[uuid.UUID, PreviousContact]:
    return {
        c.id: PreviousContact(
            consent_status=c.consent_status,
            consent_on=c.consent_on,
            is_notify_target=c.is_notify_target,
        )
        for c in contacts
    }


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

#: 変わったかを比べる欄 (監査ログには欄名だけを書く)。
COMPARED_FIELDS: tuple[str, ...] = (
    "relation",
    "name",
    "relation_note",
    "phone",
    "email",
    "is_notify_target",
    "notify_channel",
    "consent_status",
)


async def load_patient_contacts(db: AsyncSession, patient_id: uuid.UUID) -> list[PatientContact]:
    return list(
        (
            await db.scalars(
                select(PatientContact)
                .where(PatientContact.patient_id == patient_id)
                .order_by(PatientContact.sort_order)
            )
        ).all()
    )


def contacts_equal(existing: Sequence[PatientContact], rows: Sequence[ContactRow]) -> bool:
    """並び順を含めて、比べる欄がすべて同じか."""
    if len(existing) != len(rows):
        return False
    return all(
        getattr(e, f) == getattr(r, f)
        for e, r in zip(existing, rows, strict=True)
        for f in COMPARED_FIELDS
    )


def changed_field_names(
    existing: Sequence[PatientContact], rows: Sequence[ContactRow]
) -> list[str]:
    """並び順ごとに比べて、変わった欄名 (値は返さない)."""
    names: set[str] = set()
    for i in range(max(len(existing), len(rows))):
        e = existing[i] if i < len(existing) else None
        r = rows[i] if i < len(rows) else None
        for f in COMPARED_FIELDS:
            if (getattr(e, f) if e is not None else None) != (
                getattr(r, f) if r is not None else None
            ):
                names.add(f)
    return [f for f in COMPARED_FIELDS if f in names]


async def replace_patient_contacts(
    db: AsyncSession,
    patient_id: uuid.UUID,
    rows: Sequence[ContactRow],
    *,
    source: str,
    existing: Sequence[PatientContact] | None = None,
) -> list[PatientContact]:
    """患者の連絡先を ``rows`` に置きかえる (今の行を消し、同じ id を引き継いで入れ直す).

    commit しない (呼び出し側の TX に乗る)。``rows`` は :func:`normalize_patient_contacts`
    を通したもの。行の更新ではなく消して入れ直すのは、送り先の入れ替え (ご本人 ↔ ご家族) で
    ご本人の部分 UNIQUE が途中でぶつからないようにするため。
    """
    current = (
        list(existing) if existing is not None else await load_patient_contacts(db, patient_id)
    )
    known_ids = {c.id for c in current}
    for c in current:
        await db.delete(c)
    await db.flush()

    result: list[PatientContact] = []
    for order, r in enumerate(rows):
        model = PatientContact(
            # 既存の id は引き継ぐ (画面が次の保存で同じ id を送れるように)。
            id=r.id if r.id is not None and r.id in known_ids else uuid.uuid4(),
            patient_id=patient_id,
            sort_order=order,
            relation=r.relation,
            name=r.name,
            relation_note=r.relation_note,
            phone=r.phone,
            phone_display=r.phone_display,
            phone_kind=r.phone_kind,
            email=r.email,
            is_notify_target=r.is_notify_target,
            notify_channel=r.notify_channel,
            consent_status=r.consent_status,
            consent_on=r.consent_on,
            source=source,
        )
        db.add(model)
        result.append(model)
    await db.flush()
    return result
