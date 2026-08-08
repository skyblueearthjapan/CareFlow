"""DB ユーティリティ — PostgreSQL advisory lock などの横断ヘルパ.

``pg_try_advisory_xact_lock`` は transaction 単位の lock で commit / rollback
時に自動解放されるため lock leak のリスクが無い。checkin の定期ジョブ
(``services/checkin/notify.py`` / ``purge.py``) や geocoding/audit.py が
多重実行排他に用いる。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def try_advisory_xact_lock(db: AsyncSession, key: int) -> bool:
    """``pg_try_advisory_xact_lock(key)`` を取得する (= transaction-level lock).

    取得できれば True、他ジョブが保持中なら False を返す。lock は当該
    transaction の commit / rollback で自動解放される。PostgreSQL 以外
    (= sqlite test) では排他不要のため常に True を返す。``key`` はパラメータ
    バインドで渡す (SQL インジェクション回避)。
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    row = (
        await db.execute(text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(key=key))
    ).scalar()
    return bool(row)
