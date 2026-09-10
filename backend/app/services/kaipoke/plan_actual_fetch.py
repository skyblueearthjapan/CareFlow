"""月次 予実比較のための CSV 取得 (予定 → 実績 の順に 2 回 export・read-only).

PO 要望 (2026-09-10): 請求前に「らく助の予定」ではなく **カイポケの予定 × カイポケの実績**
を突き合わせて、実績側の取りこぼし/予定外/重複を掃除したい。

方針は **非破壊**: RPA には ``/api/export`` (読み取りのみ) しか頼まず、取得した CSV を
既存の ``kaipoke_csv_snapshots`` に ``division='plan' / 'actual'`` として並べて置くだけ。
差分計算 (diff-local) / 反映 (apply) / 取り込み (inbound) の挙動には一切触らない。

``build_local_diff`` の同期 export と同じ作法を踏襲する:
  * payload は ``{"month", "division", "async": False}`` (+ 認証情報)
  * timeout は ``_SYNC_EXPORT_TIMEOUT`` (同期 export は ~50s ブロックする)
  * ``office_id`` 指定時は現況CSVを事業所名で絞ってから保存する
    (保存CSVの中身と office_id キーを一致させる)

**空/失敗の CSV は絶対に保存しない** — 空を「最後に見た姿」にすると予定側の未送信計算が
壊れるし、実績側なら「実績が全部消えた」レポートになって現場を混乱させる。
検証は共通ガード ``export_guard.ensure_export_ok`` に任せ、失敗時はどちらの区分
(予定/実績) で失敗したかを名指しした ``KaipokeExportError`` を投げる。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.office import Office
from app.services.kaipoke.csv_snapshot import save_snapshot
from app.services.kaipoke.export_guard import KaipokeExportError, ensure_export_ok
from app.services.kaipoke.local_diff import _SYNC_EXPORT_TIMEOUT, _filter_current_by_office

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot
    from app.services.kaipoke_client import KaipokeClient

#: スナップショットの ``source_op`` (32 文字以内)。
SOURCE_OP = "plan-actual"


async def _export_one(
    *,
    client: KaipokeClient,
    month: str,
    division: str,
    credentials: dict[str, str] | None,
) -> str:
    """1 区分ぶんの同期 export → CSV 本文。失敗は ``KaipokeExportError``。"""
    payload: dict[str, Any] = {"month": month, "division": division, "async": False}
    if credentials:
        # アプリ内設定の認証情報 (C-1)。HTTP body のみに載せ、永続化はしない。
        payload["credentials"] = credentials

    resp = await client.export(payload, timeout=_SYNC_EXPORT_TIMEOUT)
    result = resp.get("result") or {}
    csv_text = ensure_export_ok(result, division=division)

    # division のエコーバックを **両区分とも** 必須にする (フェイルクローズ)。
    # 現在稼働中の RPA は知らないキーを黙って無視するため、division 未対応のまま
    # actual を頼むと **予定CSV がそのまま返る**。それを実績として保存すると
    # 「予定と実績が完全一致」という嘘のレポートが出て、請求前の確認が素通りする。
    # 一致を確認できない限り 1 行も保存しない。
    if result.get("division") != division:
        raise KaipokeExportError(
            division, "RPA が予実区分 (division) に未対応です。RPA を更新してください"
        )
    return csv_text


async def fetch_month_snapshots(
    db: AsyncSession,
    *,
    office_id: uuid.UUID | None,
    month: str,
    client: KaipokeClient,
    credentials: dict[str, str] | None = None,
) -> tuple[KaipokeCsvSnapshot, KaipokeCsvSnapshot]:
    """対象月の 予定CSV と 実績CSV を順に取得し、スナップショットへ保存する。

    RPA は単一スロットなので **必ず逐次** (予定 → 実績) に呼ぶ。並列にすると
    2 本目が 409 (busy) で弾かれる。

    **区分ごとに commit する** (この関数だけは例外的に commit を持つ)。理由は 2 つ:

    * 取得できた 予定CSV を 実績 の失敗で捨てない。予定の export だけで ~50s
      かかっており、中身は diff-local が保存するものと同じ「最後に見たカイポケの姿」
      として単体で価値がある (捨てると ●未送信 の鮮度も戻ってしまう)。
    * 1 本の長いトランザクションを RPA 呼び出し (~100s) をまたいで開けっ放しに
      しない。バックグラウンド実行中は他のリクエストのセッションが行き来するため、
      未コミットのまま抱えると巻き添えを食う余地が増える (テストの SQLite
      StaticPool 構成では監査ミドルウェアの後処理で実際に消えた)。

    Returns ``(plan_snapshot, actual_snapshot)``。
    """
    office_name: str | None = None
    if office_id is not None:
        office = await db.scalar(select(Office).where(Office.id == office_id))
        if office is not None:
            office_name = office.kaipoke_name or office.name

    saved: list[KaipokeCsvSnapshot] = []
    for division in ("plan", "actual"):
        csv_text = await _export_one(
            client=client, month=month, division=division, credentials=credentials
        )
        if office_name:
            csv_text = _filter_current_by_office(csv_text, office_name)
        snapshot = await save_snapshot(
            db,
            office_id=office_id,
            month=month,
            week_start=None,
            csv_text=csv_text,
            source_op=SOURCE_OP,
            division=division,
        )
        if snapshot is None:
            # save_snapshot が None を返す = 中身が空。RPA が row_count 0 を明示した
            # 「当月データなし」(export_guard が "" を通す経路) と、拠点フィルタで
            # 全行落ちた場合がここに来る。突合するものが無いので取得失敗として扱う。
            raise KaipokeExportError(division, "カイポケに当月のデータがありません")
        await db.commit()
        saved.append(snapshot)

    return saved[0], saved[1]
