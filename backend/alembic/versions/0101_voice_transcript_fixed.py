"""訪問の音声記録に「直した文字起こし」の列を足す (音声記録 v2 の ② 補正)。

Revision ID: 0101_voice_transcript_fixed
Revises: 0100_patient_insurance_address
Create Date: 2026-10-27

正典: ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3 P2。

* ``visit_recordings.transcript_fixed_json`` JSONB NULL — ② 補正で直した行
  (``[{start, end, speaker, text}]``・``transcript_json`` と同じ形)。
* ``transcript_json`` は ① の **直す前** の行のまま (列は変えない)。

データ: 何も入れない (今までの記録 = v1 は NULL のまま。画面は ``transcript_json`` を出す)。

downgrade は列を drop する。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0101_voice_transcript_fixed"
down_revision: str | Sequence[str] | None = "0100_patient_insurance_address"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_pg else sa.JSON()
    op.add_column(
        "visit_recordings",
        sa.Column(
            "transcript_fixed_json",
            json_type,
            nullable=True,
            comment="② 補正で直した行 (transcript_json は直す前)",
        ),
    )


def downgrade() -> None:
    op.drop_column("visit_recordings", "transcript_fixed_json")
