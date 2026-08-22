"""カイポケ現況CSVのスナップショット保存/取得 (week-cockpit §1 D3・mig 0076).

export に成功した経路が ``save_snapshot`` を呼び、「最後に見たカイポケの姿」を
1 箇所に集める。これにより「●未送信」は

    build_local_diff(..., current_csv=get_latest(...).csv_text)

という **RPA を呼ばない** ローカル計算 1 本で求まる (調査報告 §3-4 案 b)。

保存は同 ``(office_id, month)`` につき最新 1 件のみ (古い行は削除)。履歴を持たない
のは「最後に同期した状態」が 2 つあると未送信の定義が割れるため。
commit は呼び出し側 (既存の ``_commit_or_409`` / 取り込みトランザクション) の責務。
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class _AnyOffice:
    """``get_latest(office_id=...)`` の哨兵型: 「事業所を問わず最新」。

    ``None`` は「office_id IS NULL の行 (拠点無指定 export)」という別の意味を持つ
    ため、UUID / None / 無指定 の 3 状態を型で区別する。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — デバッグ表示のみ
        return "ANY_OFFICE"


ANY_OFFICE = _AnyOffice()


def count_csv_rows(csv_text: str) -> int:
    """CSV のデータ行数 (ヘッダー除く)。改行を含むフィールドがあっても数え違えない。"""
    if not csv_text.strip():
        return 0
    rows = list(csv.reader(io.StringIO(csv_text)))
    return max(0, len(rows) - 1)


async def save_snapshot(
    db: AsyncSession,
    *,
    office_id: uuid.UUID | None,
    month: str,
    week_start: date | None,
    csv_text: str,
    source_op: str,
) -> KaipokeCsvSnapshot | None:
    """現況CSVを保存する (同 ``(office_id, month, week_start)`` は最新 1 件に置換)。

    upsert キーに ``week_start`` を含めるのは、**月まるごとの export と週限定の
    週マージ export を共存させる** ため。片方が片方を消すと、
    「7/27〜8/2 の週CSVを保存したせいで 7 月の月CSVが消え、7月の他の週の
    未送信が出せなくなる」という取りこぼしが起きる。どれを使うかは
    ``get_latest`` の週カバー判定に選ばせる。

    ``csv_text`` が空 (取得失敗/0件) の場合は **保存しない** — 空を「最後に見た姿」
    にすると、次の未送信計算でらく助側の全訪問が add に化ける (週全滅の見た目)。
    Returns 保存した行 (保存しなかったときは None)。commit は呼び出し側。
    """
    if not (csv_text or "").strip():
        return None

    office_cond = (
        KaipokeCsvSnapshot.office_id.is_(None)
        if office_id is None
        else KaipokeCsvSnapshot.office_id == office_id
    )
    week_cond = (
        KaipokeCsvSnapshot.week_start.is_(None)
        if week_start is None
        else KaipokeCsvSnapshot.week_start == week_start
    )
    await db.execute(
        delete(KaipokeCsvSnapshot).where(office_cond, week_cond, KaipokeCsvSnapshot.month == month)
    )

    row = KaipokeCsvSnapshot(
        office_id=office_id,
        month=month,
        week_start=week_start,
        fetched_at=datetime.now(UTC),
        csv_text=csv_text,
        row_count=count_csv_rows(csv_text),
        source_op=source_op[:32],
    )
    db.add(row)
    await db.flush()
    return row


async def get_latest(
    db: AsyncSession,
    *,
    month: str,
    office_id: uuid.UUID | None | Any = ANY_OFFICE,
    week_start: date | None = None,
) -> KaipokeCsvSnapshot | None:
    """対象月の最新スナップショットを返す。

    * ``office_id`` — UUID: その事業所 / ``None``: 拠点無指定 (NULL) の行 /
      既定 ``ANY_OFFICE``: 事業所を問わず最新 (単一事業所運用の実態に合わせた既定)。
    * ``week_start`` — 指定時は「その週をカバーし得る」行だけに絞る。すなわち
      月まるごと (``week_start IS NULL``) か、**同じ週** の週マージCSV。別の週の
      週マージCSVを現況として使うと、対象週が丸ごと空 (= 全 add) に見えてしまう。
    """
    stmt = select(KaipokeCsvSnapshot).where(KaipokeCsvSnapshot.month == month)
    if not isinstance(office_id, _AnyOffice):
        stmt = stmt.where(
            KaipokeCsvSnapshot.office_id.is_(None)
            if office_id is None
            else KaipokeCsvSnapshot.office_id == office_id
        )
    if week_start is not None:
        stmt = stmt.where(
            (KaipokeCsvSnapshot.week_start.is_(None))
            | (KaipokeCsvSnapshot.week_start == week_start)
        )
    stmt = stmt.order_by(KaipokeCsvSnapshot.fetched_at.desc()).limit(1)
    return await db.scalar(stmt)


async def drop_snapshots(db: AsyncSession, *, month: str, week_start: date | None = None) -> int:
    """対象月 (と週) のスナップショットを捨てる = 「要🔄突合」へフェイルクローズ。

    カイポケへ送信した直後は、保存CSV(送信前の姿)ともう一致しない。残したまま
    未送信を計算すると **送ったばかりの変更がもう一度未送信に見える** ので、
    送信の決着時に捨てて「🔄突合でカイポケ現況を取り直してください」に倒す。
    Returns 削除件数。commit は呼び出し側。
    """
    stmt = delete(KaipokeCsvSnapshot).where(KaipokeCsvSnapshot.month == month)
    if week_start is not None:
        stmt = stmt.where(
            (KaipokeCsvSnapshot.week_start.is_(None))
            | (KaipokeCsvSnapshot.week_start == week_start)
        )
    res = await db.execute(stmt)
    return int(res.rowcount or 0)
