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


def is_readonly_post(path: str) -> bool:
    bare = path.split("?", 1)[0].rstrip("/")
    if bare.startswith("/api/v1/"):
        bare = bare[len("/api/v1"):]
    return any(rx.match(bare) for rx, _ in _COMPILED)
