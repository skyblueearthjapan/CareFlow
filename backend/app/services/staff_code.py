"""スタッフコード自動採番ヘルパ (2026-08-20).

新規スタッフ登録でコードが空欄のとき、サーバ側で次の連番コードを採番する。
患者コード自動採番 (``patient_code.py`` / Phase G-86) と同一の設計。

採番仕様:
    - 形式: ``S`` + ゼロ埋め3桁 (``f"S{n:03d}"``)。n≥1000 は自然桁 (``S1000``)。
    - 次番号 n: **全 ``staff`` 行 (deleted_at 問わず)** のうち ``^S(\\d+)$`` に
      一致するコードの数値部の最大 + 1。一致が無ければ ``1`` (=``S001``)。
      ※ deleted も含めるのは、退職者のコードを新採番が再利用して現場が
      混乱する事故 (例: 旧 S008 が soft-delete 済みでも別人に S008 を
      振ってしまう) を避けるため。意図的な再利用は手入力で可能なまま。

本ヘルパは「次コード文字列」を返すだけで INSERT はしない。一意性は
``ix_staff_code_unique_alive`` (生存行の部分 UNIQUE) が最終防衛する — 同時登録で
稀に衝突した場合は 409 が返り、もう一度登録し直せば次の番号で通る。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.staff import Staff

# ``S`` + 数値 (ゼロ埋め有無は問わない) のコードのみを採番対象にする。
# 手入力の変則コード (例 ``S-TMP-1``) は対象外で、数値抽出されない。
_STAFF_CODE_RE = re.compile(r"^S(\d+)$", re.IGNORECASE)


async def generate_next_staff_code(db: AsyncSession) -> str:
    """次のスタッフコード文字列 (``S`` + ゼロ埋め3桁) を採番して返す。"""
    rows = (await db.execute(select(Staff.code).where(Staff.code.is_not(None)))).all()

    max_n = 0
    for (code,) in rows:
        if not code:
            continue
        m = _STAFF_CODE_RE.match(code.strip())
        if m is None:
            continue
        n = int(m.group(1))
        if n > max_n:
            max_n = n

    return f"S{max_n + 1:03d}"
