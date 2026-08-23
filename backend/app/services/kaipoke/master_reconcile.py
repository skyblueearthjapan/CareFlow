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
    "槇": "槙",  # 2026-08-23 実データ (槇 恵 / 槙　恵)
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


@dataclass
class StaffQualificationDiff:
    """スタッフ 1 名ぶんの資格突合結果 (設計 §1-2 / §4 の「資格未設定 N 名」)。

    ``status``:
      * ``match``               — カイポケの職種とらく助の資格が一致
      * ``mismatch``            — 両方あるが違う (どちらが正かは人が判断する)
      * ``missing_in_rakusuke`` — らく助が未設定 (カイポケの職種を採用できる)
      * ``unknown_staff``       — カイポケに居るがらく助に該当スタッフが無い
      * ``ambiguous``           — 正規化名が同じスタッフがらく助に複数居る。
        誰の資格か決められないので **採用不可** (人が名寄せしてから)
    """

    staff_id: str | None
    name: str
    kaipoke_qualification: str | None
    rakusuke_qualification: str | None
    status: str


def extract_staff_qualifications_from_kaipoke_csv(
    csv_content: str,
) -> dict[str, tuple[str, str]]:
    """カイポケ18列CSVから **正規化した職員名1 → (原文氏名, 職種1)** のマップを作る。

    値に原文氏名も持たせるのは、らく助に該当スタッフが居ない (``unknown_staff``)
    ときに画面へ出す名前が必要なため。正規化キー (空白除去・異体字統一) を
    そのまま表示すると「誰のことか」が分かりにくい。

    見るのは職員名1 / 職種1 (列 0 / 1) だけ。サービス内容の分岐 (正看/准看) を
    決めるのは職員1 の資格であり (設計 §2)、資格ズレを潰したい動機もそこにある。
    職員名2/3 の職種は同じ人物が別の行で職員1 としても現れるため、拾わなくても
    実質的な取りこぼしにならない。

    同一人物が複数行に現れた場合は **初出を採用** する (reconcile_names と同流儀)。
    職種が空の行はマップに入れない (「カイポケにも職種が無い」は診断対象外)。
    """
    import csv as _csv
    import io as _io

    if csv_content and csv_content[0] == "﻿":
        csv_content = csv_content[1:]
    rows = list(_csv.reader(_io.StringIO(csv_content)))
    result: dict[str, tuple[str, str]] = {}
    for r in rows[1:]:
        if len(r) < 18:
            continue
        name = r[0].strip()
        qualification = r[1].strip()
        if not name or name == "-" or not qualification:
            continue
        result.setdefault(normalize_person_name(name), (name, qualification))
    return result


def normalize_qualification(value: str | None) -> str | None:
    """資格文字列の比較キー: NFKC → 前後空白除去。空は None。

    カイポケ側は全角/半角や記号のゆれが混ざる (「准看護師」と「准看護師 」等)。
    NFKC を通さずに比較すると、実際は同じ資格なのに ``mismatch`` として
    毎回画面に出続ける = 診断がノイズになる。**表示は原文** のまま行い、
    比較のときだけこのキーを使う。
    """
    if value is None:
        return None
    return unicodedata.normalize("NFKC", value).strip() or None


def reconcile_staff_qualifications(
    kaipoke_qualifications: dict[str, tuple[str, str]],
    rakusuke_staff: list[tuple[str, str, str | None, str | None]],
) -> list[StaffQualificationDiff]:
    """カイポケの職種とらく助の ``staff.qualification`` を突合する (純関数)。

    比較は **NFKC 正規化した文字列の完全一致** (``normalize_qualification``)。
    カイポケの「看護師」→ らく助「看護師」、「准看護師」→「准看護師」が
    そのまま対応するので、変換表は挟まない (挟むと将来カイポケ側に新しい
    職種が出たとき黙って握り潰すことになる)。氏名の正規化は
    ``normalize_person_name`` (NFKC + 異体字 + 空白除去)。

    同じ正規化名のスタッフがらく助に複数居る場合は ``ambiguous``:
    どちらの資格を直せばよいか決められないので採用ボタンを出さない
    (当てずっぽうで片方を書き換えると、もう一方のサービス内容が黙って狂う)。

    Args:
        kaipoke_qualifications: 正規化名 → (原文氏名, 職種)
            (``extract_staff_qualifications_from_kaipoke_csv`` の戻り値)。
        rakusuke_staff: ``(staff_id, name, qualification, status)`` のリスト。
            ``status`` は ``staff.status`` ('active' 等)。同名が複数居るときの
            代表選びに使う (在職者を優先 — 退職者の資格を直しても意味が無い)。

    Returns:
        カイポケ側に現れたスタッフぶんの突合結果 (カイポケ名の正規化キー昇順)。
        らく助にしか居ないスタッフは対象外 — カイポケの職種が無い以上、
        比較対象が存在しないため (氏名側の ``rakusuke_only`` で既に見えている)。
    """
    # 正規化名 → 候補全件。代表は「在職者を優先し、その中の初出」。
    candidates: dict[str, list[tuple[str, str, str | None, str | None]]] = {}
    for row in rakusuke_staff:
        candidates.setdefault(normalize_person_name(row[1]), []).append(row)

    result: list[StaffQualificationDiff] = []
    for key, (kaipoke_name, kaipoke_qualification) in sorted(kaipoke_qualifications.items()):
        found = candidates.get(key, [])
        if not found:
            result.append(
                StaffQualificationDiff(
                    staff_id=None,
                    name=kaipoke_name,
                    kaipoke_qualification=kaipoke_qualification,
                    rakusuke_qualification=None,
                    status="unknown_staff",
                )
            )
            continue

        active = [r for r in found if (r[3] or "active") == "active"]
        # 在職者が 2 人以上いるときだけ「決められない」。退職者が混ざっている
        # だけなら在職者 1 人に決まる (実データで頻出する形)。
        if len(active) > 1:
            result.append(
                StaffQualificationDiff(
                    staff_id=None,
                    name=kaipoke_name,
                    kaipoke_qualification=kaipoke_qualification,
                    rakusuke_qualification=None,
                    status="ambiguous",
                )
            )
            continue

        staff_id, name, rakusuke_qualification, _status = active[0] if active else found[0]
        rk = normalize_qualification(rakusuke_qualification)
        if rk is None:
            status = "missing_in_rakusuke"
        elif rk == normalize_qualification(kaipoke_qualification):
            status = "match"
        else:
            status = "mismatch"
        result.append(
            StaffQualificationDiff(
                staff_id=staff_id,
                name=name,
                kaipoke_qualification=kaipoke_qualification,
                # 表示は原文 (何が入っているかを見せる)。比較だけ正規化キー。
                rakusuke_qualification=(rakusuke_qualification or "").strip() or None,
                status=status,
            )
        )
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
