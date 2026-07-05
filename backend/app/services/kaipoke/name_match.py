"""カイポケ氏名 ↔ CareFlow マスタ名の突合用ユーティリティ (K-1c).

カイポケの氏名は「姓　名」(全角スペース区切り) で、漢字異体字 (髙/高・栁/柳 等) を
含む。CareFlow の staff.name / patient.name との突合には、両側を同じ規則で正規化した
「照合キー」に落としてから比較する。旧 PlaywrightTest1 ``normalize_name`` の異体字マップを
移植し、NFKC (全半角統一) と空白畳み込みを足したもの。

CSV 生成時 (csv_builder) は CareFlow の名前をそのまま出力する。この正規化は
あくまで **名寄せ (どの CareFlow 行がどのカイポケ行か)** の照合に使う。
"""

from __future__ import annotations

import unicodedata

# カイポケで確認された漢字異体字ペア (旧 PlaywrightTest1 commands/auto_apply.py より移植)。
# 照合キー生成時に「旧字体 → 常用字体」へ寄せる。複数文字の姓 (渡邉→渡辺) を先に
# 適用したいので dict の挿入順を維持する (Python 3.7+ は順序保持)。
_VARIANT_MAP: dict[str, str] = {
    "渡邉": "渡辺",
    "渡邊": "渡辺",
    "栁": "柳",
    "﨑": "崎",
    "髙": "高",
    "濵": "浜",
    "邊": "辺",
    "廣": "広",
    "齋": "斎",
    "齊": "斎",
    "澤": "沢",
    "櫻": "桜",
}


def normalize_name_key(name: str) -> str:
    """氏名を照合キーへ正規化する。

    手順: NFKC 正規化 (全角/半角・互換文字統一) → 空白 (全角含む) を全除去 →
    漢字異体字を常用字体へ寄せる。空白を「除去」するのは、カイポケ「姓　名」と
    CareFlow「姓名」/「姓 名」の表記ゆれを吸収するため。
    """
    if not name:
        return ""
    norm = unicodedata.normalize("NFKC", name)
    # NFKC 後は全角スペースも半角化されるため、通常空白の除去で足りる。
    norm = "".join(norm.split())
    for old, new in _VARIANT_MAP.items():
        norm = norm.replace(old, new)
    return norm


def build_name_index(names: dict[str, str]) -> dict[str, list[str]]:
    """{id: name} を {正規化キー: [id, ...]} へ反転する。

    同一正規化キーに複数 id が当たる (同姓同名) 場合は list に積む。呼び出し側で
    曖昧一致を検出できるようにするため、単一 id へ潰さない。
    """
    index: dict[str, list[str]] = {}
    for id_, name in names.items():
        key = normalize_name_key(name)
        if not key:
            continue
        index.setdefault(key, []).append(id_)
    return index


def match_name(kaipoke_name: str, index: dict[str, list[str]]) -> str | None:
    """カイポケ氏名を index に照合し、一意に定まる id を返す。

    見つからない / 複数該当 (曖昧) の場合は ``None`` を返す。曖昧一致を成功扱い
    しないことで、取り違えを防ぐ (呼び出し側で人手確認へ回す)。
    """
    key = normalize_name_key(kaipoke_name)
    hits = index.get(key)
    if hits and len(hits) == 1:
        return hits[0]
    return None
