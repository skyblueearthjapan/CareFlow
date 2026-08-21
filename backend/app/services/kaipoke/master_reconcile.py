"""マスタ相互突合 (週空間 Phase M・PO発案 2026-08-21).

カイポケの名簿 (現況CSVに現れる利用者/職員) と、らく助のマスタ (patients/staff)
を氏名で突き合わせ、同期の土台のズレを見える化する:

  - kaipoke_only  : カイポケにだけ現れる (らく助に未登録 → 取込時 unresolved になる)
  - rakusuke_only : らく助にだけ居る (カイポケ未登録 or 別表記)
  - notation_diff : 同一人物だが表記が違う (スペース/異体字) — 正規化で吸収済みだが
                    見えるところから直せるように提示する

正規化は診断用に「強め」(NFKC + 空白全除去 + 異体字統一)。同期コード側の実装
(diff/engine._normalize_user_name / RPA name_matches) と同思想で、ここが唯一の
マスタ診断の正典。カイポケ側の名簿 API は存在しないため、現況CSV (スケジュールに
実際に現れた名前) を名簿の近似として使う — スケジュールに載らない人は突合できない
点は仕様 (画面に明記)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# カイポケで確認された異体字ペア (RPA auto_apply.normalize_name と同期)。
_VARIANT_MAP = {
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


def normalize_person_name(name: str) -> str:
    """氏名の突合キー: NFKC → 異体字統一 → 空白(全種)除去。"""
    s = unicodedata.normalize("NFKC", name or "")
    for old, new in _VARIANT_MAP.items():
        s = s.replace(old, new)
    return re.sub(r"\s+", "", s)


@dataclass
class NameReconcileResult:
    matched: int = 0
    kaipoke_only: list[str] = field(default_factory=list)
    rakusuke_only: list[str] = field(default_factory=list)
    # (カイポケ表記, らく助表記) — 正規化キーは一致するが原文が違うペア。
    notation_diff: list[tuple[str, str]] = field(default_factory=list)


def reconcile_names(kaipoke_names: list[str], rakusuke_names: list[str]) -> NameReconcileResult:
    """氏名リスト同士を正規化キーで突合する (純関数・テスト対象)。

    同一正規化キーに複数の原文表記がある場合は初出を代表にする。
    """
    kp: dict[str, str] = {}
    for n in kaipoke_names:
        n = (n or "").strip()
        if not n or n == "-":
            continue
        kp.setdefault(normalize_person_name(n), n)
    rk: dict[str, str] = {}
    for n in rakusuke_names:
        n = (n or "").strip()
        if not n:
            continue
        rk.setdefault(normalize_person_name(n), n)

    result = NameReconcileResult()
    for key, kname in sorted(kp.items()):
        if key in rk:
            if kname == rk[key]:
                result.matched += 1
            else:
                result.notation_diff.append((kname, rk[key]))
        else:
            result.kaipoke_only.append(kname)
    for key, rname in sorted(rk.items()):
        if key not in kp:
            result.rakusuke_only.append(rname)
    return result


def extract_names_from_kaipoke_csv(csv_content: str) -> tuple[list[str], list[str]]:
    """カイポケ18列CSVから (利用者名list, 職員名list) を抽出する。

    列位置は diff/engine._parse_kaipoke_rows と同じ: 職員名1/2/3 = 0/2/5,
    利用者 = 11。ヘッダー行はスキップ。
    """
    import csv as _csv
    import io as _io

    if csv_content and csv_content[0] == "﻿":
        csv_content = csv_content[1:]
    rows = list(_csv.reader(_io.StringIO(csv_content)))
    # dict をorderd-setとして使い重複を除去 (月次CSVは同一人物が数百行現れる)。
    patients: dict[str, None] = {}
    staff: dict[str, None] = {}
    for r in rows[1:]:
        if len(r) < 18:
            continue
        patients.setdefault(r[11].strip(), None)
        for idx in (0, 2, 5):
            staff.setdefault(r[idx].strip(), None)
    return list(patients), list(staff)
