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
"""

from __future__ import annotations

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
