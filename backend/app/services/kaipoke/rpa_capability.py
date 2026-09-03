"""RPA (auto_apply) が登録できるサービス内容の範囲 — 運用ガード (S3 完了まで).

正典 = ``docs/plans/kaipoke-service-content-design.md`` §3 / §5 (Phase S3)。

## なぜ要るか

S2 でサービス内容の自動判定 (患者の訪問看護区分 × 職員1の資格) が入り、
らく助は 4 通りの文字列を出すようになった::

    精神基本療養費Ⅰ・正看   (既定・従来どおり)
    精神基本療養費Ⅰ・准看
    基本療養費Ⅰ・正看
    基本療養費Ⅰ・准看

一方 **RPA 側 (auto_apply) はまだ分岐していない**。新規登録ダイアログの
サービス区分 (``#inPopupEstimate1``) と職員資格 (``#inPopupEstimate3``) を
固定値で選ぶため、准看 / 一般の行を送ると **カイポケには「精神科訪問看護 ×
看護師等」で登録される**。画面上は成功に見えて中身が違う = 突合しても
「サービス内容が違う」差分が延々残り、実際の請求も狂う。

止めずに送るくらいなら送らない、という判断でここに門番を置く。S3
(option 文言の採取 → 分岐実装 → 実機 1 件テスト) が終わったら
``settings.kaipoke_rpa_service_branch_enabled = True`` で門を開ける。

## 対象は add だけ

カイポケの edit ではサービス内容を変更できない (設計 §3)。差分エンジンの
``correction_before_after`` も before/after 双方に同じ ``service_type`` を
入れるため、サービス内容の違いは常に **delete + add** として現れる。
つまり「RPA が登録できない値」が実際に書き込まれる経路は add のみ。
delete / edit / date_change は既存行を動かすだけなので素通しでよい。

## ただし delete は「ペアなら」道連れに止める

上の理屈は 1 行ずつ見れば正しいが、**除外した add と対になる delete** だけは
別扱いが要る。サービス内容のズレは delete + add の 2 行で表現されるので、
add を落として delete だけ送ると **カイポケから予定が消えたまま作り直されない**。
誤った値で登録されるより悪い事故なので、``rpa_unsupported_item_ids`` が
同一キー ``(日, 開始時刻, 正規化利用者名)`` の delete も一緒に外す。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.core.config import get_settings

# RPA が現状ダイアログで選べる唯一の組み合わせ (精神科訪問看護 × 看護師等)。
RPA_SUPPORTED_SERVICE_CONTENT = "精神基本療養費Ⅰ・正看"

# 除外した理由 (job.result_summary / FE の注記で同じ文言を使う)。
RPA_UNSUPPORTED_REASON = "RPA が准看/一般の登録に未対応(S3)"


def service_branch_enabled() -> bool:
    """RPA がサービス内容の分岐に対応しているか (S3 完了で True)。"""
    return bool(get_settings().kaipoke_rpa_service_branch_enabled)


def is_rpa_unsupported(action: str, after: dict[str, Any] | None) -> bool:
    """この修正項目を RPA へ送ると誤った値で登録されるか。

    True = 送信対象から除外すべき。判定は ``action == 'add'`` かつ
    ``after.service_type`` が ``RPA_SUPPORTED_SERVICE_CONTENT`` 以外のとき。

    * ``service_type`` が空 / 欠落している行は **除外しない** (従来どおり
      送る)。イベント行や旧シートなど、サービス内容を持たない項目まで
      止めてしまうと運用が丸ごと止まるため。
    * 設定 (``kaipoke_rpa_service_branch_enabled``) が True なら常に False
      = 門は開きっぱなし。
    """
    if service_branch_enabled():
        return False
    if action != "add":
        return False
    service_type = str((after or {}).get("service_type") or "").strip()
    if not service_type:
        return False
    return service_type != RPA_SUPPORTED_SERVICE_CONTENT


# --- ペア保護 (delete + add の片肺送信を防ぐ) --------------------------------


def _side(action: str, before: dict[str, Any] | None, after: dict[str, Any] | None):
    """その項目の「実体」が載っている側 (resolve_item_date と同じ規則)。

    delete/edit/date_change は before (現況の位置)、add は after (これから作る位置)。
    """
    return (before if action in ("delete", "edit", "date_change") else after) or {}


def pair_key(
    action: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> str | None:
    """delete と add が **同じ訪問** を指しているか判定するキー。

    ``(日, 開始時刻, 正規化した利用者名)``。CSV の日付列は「日」(1-31) しか
    持たないが、突合は 1 シート = 1 週 (同月) の中で行うので日で足りる。
    氏名の正規化は ``master_reconcile.normalize_person_name`` を借りる
    (異体字・空白のゆれで同じ訪問がペアと認識されないのを防ぐ)。

    解決できない (日付か時刻か氏名が空) 場合は ``None`` = ペア判定をしない。
    """
    from app.services.kaipoke.master_reconcile import normalize_person_name

    src = _side(action, before, after)
    day = str(src.get("date") or "").strip()
    start = str(src.get("start_time") or "").strip()
    name = str(src.get("user_name") or "").strip()
    if not day or not start or not name:
        return None
    return f"{day}|{start[:5]}|{normalize_person_name(name)}"


def rpa_unsupported_item_ids(items: Iterable[Any]) -> set[Any]:
    """送信対象から外すべき item の id 集合 (**ペアを巻き込んで** 外す)。

    ## なぜ delete まで外すのか

    サービス内容だけが違う訪問は、カイポケの edit がサービス内容を触れない
    都合で **必ず delete + add の 2 行**として現れる (設計 §3-1)。ここで add
    だけを除外して delete を送ると、カイポケからその行が消えたまま新しい行が
    作られない = **予定が丸ごと消える**。誤った値で登録されるより悪い。

    そこで、除外する add と同じ ``pair_key`` を持つ delete も道連れに外す。
    「サービス内容のズレは、らく助側で 1 件だけ合わせる (visit-service-override)
    か、カイポケで直接直す」— どちらにせよ RPA には触らせない、という形に揃う。

    Args:
        items: ``id`` / ``action`` / ``before`` / ``after`` を持つオブジェクト
            (``CorrectionSheetItem`` を想定)。

    Returns:
        除外対象の ``item.id`` 集合。門が開いていれば (S3 完了後) 常に空。
    """
    if service_branch_enabled():
        return set()

    rows = list(items)
    skip: set[Any] = set()
    blocked_keys: set[str] = set()
    for it in rows:
        if is_rpa_unsupported(it.action, it.after):
            skip.add(it.id)
            key = pair_key(it.action, it.before, it.after)
            if key is not None:
                blocked_keys.add(key)

    if blocked_keys:
        for it in rows:
            if it.id in skip or it.action != "delete":
                continue
            key = pair_key(it.action, it.before, it.after)
            if key is not None and key in blocked_keys:
                skip.add(it.id)
    return skip


# --- 担当なしガード (2026-09-03 本番事故) -----------------------------------

# 除外した理由 (job.result_summary / FE の注記で同じ文言を使う)。
UNASSIGNED_REASON = "担当なしの予定はカイポケへ送れません（先に担当を付けてください）"


def is_unassigned_item(action: str, after: dict[str, Any] | None) -> bool:
    """この修正項目は「担当なし」= カイポケへ送ってはいけないか。

    True = 送信対象から除外すべき。判定は ``action`` が add/edit/date_change
    (= カイポケに職員1を書き込む操作) かつ ``after.staff1`` が空 / ``'-'`` のとき。
    ``delete`` は職員を書かない (行を消すだけ) ので常に False。

    ## なぜ止めるのか (2026-09-03 W37 の送信で 9 件発生)

    らく助の差分エンジンは ``include_unassigned=True`` で担当なしの訪問も
    ``staff1='-'`` の行として差分に含める。これを RPA へ送ると:

    * RPA は「成功」を返す。カイポケにも担当なしの行として実際に入る。
    * ところがカイポケの「スケジュール表」CSV export は **職員別** で、
      職員未割当の行を 1 行も含まない。
    * = らく助から見ると送ったはずの行が現況CSVに出てこない → 次の差分で
      再び ``add`` として現れる → 送るたびに担当なしの行が増える (二重登録)。
    * さらに ``edit`` で送った分は、カイポケに入っていた実在の職員 (熊澤)
      を ``'-'`` で上書きしてしまう = 予定から担当が消える。

    「送っても確認できず、送るほど壊れる」ので、担当が付くまで送らない。
    """
    if action not in ("add", "edit", "date_change"):
        return False
    return str((after or {}).get("staff1") or "").strip() in ("", "-")


def unassigned_item_ids(items: Iterable[Any]) -> set[Any]:
    """送信対象から外すべき「担当なし」item の id 集合 (**ペアを巻き込んで** 外す)。

    ## なぜ delete まで外すのか

    担当なしは 1 項目で完結する、とは限らない。差分エンジンは **サービス内容も
    突合キーに含める** ため、同じ訪問でも「カイポケ側は准看で担当あり / らく助側は
    担当なし ('-' → 職員 None なので正看)」のようにサービス内容がズレると、
    edit ではなく **delete (カイポケの行) + add (らく助の行)** の 2 行に割れる
    (``rpa_unsupported_item_ids`` と同じ構図・設計 §3-1)。

    ここで add だけを落として delete を送ると **カイポケから予定が丸ごと消えて
    作り直されない** = 担当なしで送るより悪い事故になる。そこで、除外する
    担当なし項目と同じ ``pair_key`` を持つ delete も道連れに外す。

    Args:
        items: ``id`` / ``action`` / ``before`` / ``after`` を持つオブジェクト
            (``CorrectionSheetItem`` を想定)。

    Returns:
        除外対象の ``item.id`` 集合。
    """
    rows = list(items)
    skip: set[Any] = set()
    blocked_keys: set[str] = set()
    for it in rows:
        if is_unassigned_item(it.action, it.after):
            skip.add(it.id)
            key = pair_key(it.action, it.before, it.after)
            if key is not None:
                blocked_keys.add(key)

    if blocked_keys:
        for it in rows:
            if it.id in skip or it.action != "delete":
                continue
            key = pair_key(it.action, it.before, it.after)
            if key is not None and key in blocked_keys:
                skip.add(it.id)
    return skip
