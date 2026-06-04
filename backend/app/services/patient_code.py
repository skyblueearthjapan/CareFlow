"""患者コード自動採番ヘルパ (Phase G-86 A-1).

新規患者登録で患者コードが空欄のとき、サーバ側で次の連番コードを採番する。

採番仕様 (`docs/plans/phase-g86-patient-code-autonumber.md` §採番仕様):
    - 形式: ``P`` + ゼロ埋め3桁 (``f"P{n:03d}"``)。n≥1000 は自然桁 (``P1000``)。
    - 次番号 n: **全 ``patients`` 行 (deleted_at 問わず)** のうち ``^P(\\d+)$`` に
      一致するコードの数値部の最大 + 1。一致が無ければ ``1`` (=``P001``)。
      ※ deleted も含めるのは UNIQUE 制約が soft-delete 行にも効くため (衝突回避)。

本ヘルパは「次コード文字列」を返すだけの純関数的ヘルパで、INSERT は行わない。
一意性/並行性 (UNIQUE 衝突時のリトライ) は呼び出し側 (applier / API) の責務。
``importer.py:_load_patient_codes`` の lookup を参考にしている。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient

# ``P`` + 数値 (ゼロ埋め有無は問わない) のコードのみを採番対象にする。
# 既存の手入力コード (例 ``P-NEW-001``) は対象外で、数値抽出されない。
_PATIENT_CODE_RE = re.compile(r"^P(\d+)$")


async def generate_next_patient_code(db: AsyncSession) -> str:
    """次の患者コード文字列 (``P`` + ゼロ埋め3桁) を採番して返す。

    全 ``patients`` 行 (deleted_at フィルタ無し) の ``code`` から ``^P(\\d+)$`` に
    一致するものの数値部最大 + 1 を ``f"P{n:03d}"`` 形式で返す。一致が無ければ
    ``P001``。1000 以上は自然桁 (``P1000`` 等)。

    INSERT はしない。UNIQUE 衝突時のリトライは呼び出し側で行う。
    """
    rows = (await db.execute(select(Patient.code).where(Patient.code.is_not(None)))).all()

    max_n = 0
    for (code,) in rows:
        if not code:
            continue
        m = _PATIENT_CODE_RE.match(code)
        if m is None:
            continue
        n = int(m.group(1))
        if n > max_n:
            max_n = n

    return f"P{max_n + 1:03d}"
