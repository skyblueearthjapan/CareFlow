"""RPA ``/api/export`` の結果を検証する共通ガード (2026-09-10).

## なぜ要るか

RPA は「CSV が古い / ダウンロードに失敗した」場合に **``success: False`` かつ
``csv_content: None``** を返すよう改修された (それまでは古いファイルの中身を
返していた)。ところが CareFlow 側の 3 経路は ``success`` を見ずに
``(result or {}).get("csv_content") or ""`` と書いていたため、失敗が
**「カイポケが空っぽ」** に化ける:

* ``build_local_diff`` — カイポケ現況が空 → らく助の全訪問が ``add`` 差分になり、
  そのまま ⇧送信すると全件二重登録。しかも空CSVが「最後に見た姿」として
  保存されかけ、以降の ●未送信 も全滅表示になる。
* ``export_current_week_csv`` — 置換取り込みで対象週が空扱い。
* ``master-reconcile`` — カイポケ名簿が空 = 「らく助のみ」大量表示。

いずれも **静かに間違う** 種類の事故なので、ここで例外に倒して
ジョブを failed にし、人に取り直させる (フェイルクローズ)。

保存側の ``save_snapshot`` も空CSVを弾くが、それは最後の砦であって、
「失敗を空として扱ったまま差分計算まで進む」ことを止められない。

**「本文が空」だけでは倒さない** — カイポケにその週の入力がまだ無い、という
正常系と区別が付かないため。そちらは呼び出し側の「0件での置換は拒否します」等の
422 で既に守られており、現場に伝わる文言もそちらが持っている。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.kaipoke_client import KaipokeApiError

#: ``division`` → 日本語表記 (エラーメッセージ / レポート見出し用)。
DIVISION_LABELS: dict[str, str] = {"plan": "予定", "actual": "実績"}


class KaipokeExportError(KaipokeApiError):
    """export が失敗した / CSV が空だった (どちらの区分かを ``division`` に持つ)。

    ``KaipokeApiError`` を継承するのは意図的 — export を呼ぶ既存エンドポイントは
    すべて ``except KaipokeApiError`` で「ジョブを failed にして 502」を実装済みで、
    継承しておけばその決着処理がそのまま効く (呼び出し側を触らずに済む)。
    ``KaipokeBusyError`` (409) は別の except 節で先に捕まるので影響しない。
    """

    def __init__(self, division: str, detail: str) -> None:
        self.division = division
        self.label = DIVISION_LABELS.get(division, division)
        super().__init__(
            502,
            {"error": detail, "division": division},
            message=f"カイポケの{self.label} CSV を取得できませんでした（RPA: {detail}）",
        )


def ensure_export_ok(
    result: Mapping[str, Any] | None,
    *,
    division: str = "plan",
) -> str:
    """export の ``result`` を検証して CSV 本文を返す。駄目なら ``KaipokeExportError``。

    倒すのは **RPA が明示した失敗** だけ:

    * ``success`` が **明示的に False** — 新 RPA の失敗/古いファイル検知の signal。
      このとき ``csv_content`` は None で来る。
    * ``success`` が True なのに本文が空 — 契約違反 (成功なら最低でもヘッダー行が来る)。
      ただし ``row_count`` が **明示的に 0** なら「その月に 1 件も無い」という
      正常な報告なので倒さず ``""`` を返す。月跨ぎ週の片方の月が空、という
      実在ケースを潰さないため。

    逆に「``success`` キーが無い」「本文が空」だけでは倒さない:

    * ``success`` を返さない旧 RPA / 既存スタブとの後方互換を壊さないため。
    * **本文が空 = カイポケにその週の入力が無い** は正常系でもあり得る (未来週の
      先行入力待ちなど)。この場合の安全弁は呼び出し側が既に持っており
      (置換取り込みの「0件での置換は拒否します」422 など)、そちらの方が
      現場に伝わる文言になる。ここで 502 に倒すとその案内を潰してしまう。

    ``division`` は既定 ``"plan"`` (= 従来の現況/予定CSV)。予実比較の実績取得だけが
    ``"actual"`` を渡す。
    """
    data = result or {}
    success = data.get("success")
    error = data.get("error") or "詳細不明"

    if success is False:
        raise KaipokeExportError(division, str(error))

    csv_text = data.get("csv_content") or ""
    if success is True and not csv_text.strip():
        # RPA が「0 件でした」と明示しているなら、空は取得失敗ではなく事実。
        row_count = data.get("row_count")
        if row_count == 0:
            return csv_text
        raise KaipokeExportError(division, f"success=true なのに CSV 本文が空でした（{error}）")
    return csv_text
