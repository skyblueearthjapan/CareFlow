"""予定のお知らせ (SMS) の画面の言葉 (状態・届かなかった理由・種類).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-4・§7-2、
``docs/plans/sms-notify-api-contract-2026-11-16.md`` §11。

届かなかった理由は KDDI の照会が返す値 (``reason``) をそのまま ``fail_reason_raw`` に残し、
画面ではここで言いかえる。知らない値は「理由は分かりません（KDDI の値: …）」。
言いかえは、この 1 か所だけに置く (画面・Excel・月のまとめで同じ言葉にするため)。
"""

from __future__ import annotations

#: 1 通の状態 (契約 §11)。
STATUS_LABEL: dict[str, str] = {
    "queued": "送る順番を待っています",
    "sending": "送っています",
    "accepted": "受け付けられました（まだ届いていません）",
    "delivered": "届きました",
    "failed": "届きませんでした",
    "unknown": "分かりません",
    "skipped": "送っていません",
}

#: 送っていない理由 (``skip_reason``・契約 §13-5)。
SKIP_REASON_LABEL: dict[str, str] = {
    "test_mode": "試験の状態のため",
    "not_in_test_allowlist": "試験の状態のため",
    "expired": "送る前に時間が過ぎたため",
    "superseded": "新しいお知らせを作ったため",
    "contact_changed": "連絡先が変わったため",
    "patient_inactive": "稼働中でなくなったため",
}

#: 送る直前の確かめで送らなかった理由 (ベル「送れなかったお知らせが N 件あります」に数える)。
#: 試験の状態のため送らなかった分 (test_mode) は数えない。
UNSENT_SKIP_REASONS: frozenset[str] = frozenset(
    {"expired", "superseded", "contact_changed", "patient_inactive"}
)

#: お知らせの種類。
KIND_LABEL: dict[str, str] = {
    "week": "週のお知らせ",
    "change": "変更のお知らせ",
}

#: 届かなかった理由 (KDDI の値 → ふつうの言葉)。
#: SoftBank は SMPP の配信状態、au は SMPP のエラーの名前で返る (仕様書 ver3.1)。
#: 送るときに受け付けられなかったもの (らく助の印) も同じ表で言いかえる。
FAIL_REASON_LABEL: dict[str, str] = {
    # SoftBank
    "DELIVRD": "届きました",
    "EXPIRED": "時間内に届きませんでした（電源が切れている・圏外など）",
    "DELETED": "送る途中で取り消されました",
    "UNDELIV": "届けられませんでした（番号が使われていない・受け取れない端末など）",
    "REJECTD": "受信の設定で受け取れませんでした",
    # au
    "ESME_REGULATED": "迷惑 SMS の設定などで受け取れませんでした",
    "ESME_RINVDSTADR": "番号が正しくありません",
    "ESME_RMSGQFUL": "相手の携帯の受け取りがいっぱいで届きませんでした",
    "ESME_RSYSERR": "携帯会社の仕組みの都合で届きませんでした",
    "SMPP_TIMEOUT": "携帯会社からの返事が時間内にありませんでした",
    # 送るとき (らく助の印)
    "config": "送る設定が足りません（管理者が設定を確かめてください）",
    "bad_phone": "番号の形が正しくありません",
    "timeout": "送ったあと返事が途切れました（届いたか分かりません）",
    "unsupported_char": "SMS で送れない文字がありました",
}

#: 受信の設定で受け取れない (気になる番号の「受け取れない」)。
BLOCKED_REASONS: frozenset[str] = frozenset({"REJECTD", "ESME_REGULATED"})

#: キャリアが「sms」= 使われていない番号 (仕様書 ver3.1)。
NOT_IN_USE_CARRIER = "sms"

#: 気になる番号の種類。
ATTENTION_KIND_LABEL: dict[str, str] = {
    "repeated_failure": "続けて届いていません",
    "not_in_use": "使われていない番号のようです",
    "blocked": "受信の設定で受け取れません",
}

#: お伝えした手段・送らずに済ませた理由。
CHANNEL_LABEL: dict[str, str] = {
    "sms": "SMS",
    "phone": "お電話",
    "none": "お知らせしない",
}
RESOLVE_REASON_LABEL: dict[str, str] = {
    "phoned": "お電話でお伝えした",
    "patient_request": "ご本人・ご家族からのお申し出",
    "other": "その他",
}


def fail_reason_label(raw: str | None) -> str:
    """KDDI の値 → 画面の言葉 (知らない値は値を添える)."""
    value = (raw or "").strip()
    if not value:
        return "理由は記録されていません"
    known = FAIL_REASON_LABEL.get(value) or FAIL_REASON_LABEL.get(value.upper())
    if known:
        return known
    return f"理由は分かりません（KDDI の値: {value}）"


def status_label(status: str, skip_reason: str | None = None) -> str:
    """状態の言葉 (送っていないときは理由を添える)."""
    base = STATUS_LABEL.get(status, "分かりません")
    if status == "skipped":
        extra = SKIP_REASON_LABEL.get(skip_reason or "", "ほか")
        return f"{base}（{extra}）"
    return base
