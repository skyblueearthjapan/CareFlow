"""rk post-read が通してよい「読むだけの POST」（R1 で種別「読」の POST から、控えめに選んだもの）。

外したもの: RPA を動かす「RPA読」（カイポケのロボットの枠を使う・1〜2 分）・旧 API（/diff/compute）・
重い全面最適化（/schedule/v2/full-optimize）・キャッシュに書く /geocode。
足すときは R1 の「種別=読」と実装（DB を変えないこと）を確かめてから。
"""

from __future__ import annotations

import re

READONLY_POSTS: list[tuple[str, str]] = [
    ("/integrations/unsent-summary", "未送信の変更（カイポケへ送る前）"),
    ("/integrations/events-outbound-preview", "カイポケへ送るイベントの確認"),
    ("/schedule/v2/copy-week/preview", "週のコピーの確認（DB 不変）"),
    ("/schedule/v2/assign-candidates", "訪問を誰に入れられるか"),
    ("/schedule/v2/substitute-candidates", "急に抜けたときの代わりの候補"),
    ("/schedule/v2/diff-add", "保留プールの患者ごとの提案"),
    ("/schedule/v2/pool-overview", "保留プールの最良候補"),
    ("/schedule/v2/pool-bulk-simulate", "プール一括投入の試算"),
    ("/schedule/v2/propose-slots", "希望から空き枠を探す"),
    ("/schedule/v2/propose-unblock", "詰まり解消の手順案"),
    ("/schedule/v2/scope-optimization/simulate", "範囲最適化の試算"),
    ("/schedule/v2/travel-estimate", "移動時間の見積もり"),
    ("/patients/{id}/fixed-visits/validate", "固定訪問の枠の検査（書かない）"),
    ("/offices/resolve", "住所から拠点を判定"),
]


def _pattern(template: str) -> re.Pattern:
    parts = [("[^/?]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg))
             for seg in template.split("/")]
    return re.compile("^" + "/".join(parts) + "$")


_COMPILED = [(_pattern(t), t) for t, _ in READONLY_POSTS]


#: コースの作業場（template_ws）の「下書きの編集」の POST。下書きは本番のマスターでも週の予定でもなく、
#: 1 手ごとに理由つきで履歴に残り人が ↶ で戻せるので、承認番号は要らない（PO 2026-10-12）。
#: 出口（/commit・/week-apply）は含めない＝承認番号つきの案（rk plan）でだけ。
DRAFT_EDIT_POSTS: list[tuple[str, str]] = [
    ("/template-ws/drafts", "下書きを作る（マスターを写すだけ）"),
    ("/template-ws/drafts/{id}/move", "下書きの 1 枠を動かす"),
    ("/template-ws/drafts/{id}/whatif", "下書きにもしもの条件を書く"),
    ("/template-ws/drafts/{id}/add-patient", "下書きに枠の無い方を足す"),
    ("/template-ws/drafts/{id}/remove-patient", "下書きから足した方を外す"),
    ("/template-ws/drafts/{id}/optimise", "下書きを最適化する（計算）"),
    ("/template-ws/drafts/{id}/undo", "下書きの一つ前に戻す"),
    ("/template-ws/drafts/{id}/redo", "下書きをやり直す"),
]
_DRAFT_COMPILED = [_pattern(t) for t, _ in DRAFT_EDIT_POSTS]


def is_readonly_post(path: str) -> bool:
    """読むだけの POST、または下書きの編集の POST（本番のマスター・週の予定には触らない）。"""
    bare = path.split("?", 1)[0].rstrip("/")
    if bare.startswith("/api/v1/"):
        bare = bare[len("/api/v1"):]
    return any(rx.match(bare) for rx, _ in _COMPILED) or any(rx.match(bare) for rx in _DRAFT_COMPILED)


#: 本文が dry_run:true のときだけ「読むだけ」になる POST（週を空にする・型から作る の確認）。
#: dry_run が true でない・無いときは通さない（本番を書き換えるため）。
DRY_RUN_POSTS: list[tuple[str, str]] = [
    ("/schedule/v2/weeks/{y}/{w}/clear", "週を空にしたときに消える件数の確認"),
    ("/schedule/generate-week-only", "型から週を作ったときに増える件数の確認"),
]
_DRY_COMPILED = [_pattern(t) for t, _ in DRY_RUN_POSTS]


def is_dry_run_post(path: str, body: object) -> bool:
    if not isinstance(body, dict) or body.get("dry_run") is not True:
        return False
    bare = path.split("?", 1)[0].rstrip("/")
    if bare.startswith("/api/v1/"):
        bare = bare[len("/api/v1"):]
    return any(rx.match(bare) for rx in _DRY_COMPILED)
