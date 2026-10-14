"""Pydantic schemas for patient QR 発行 / 再発行 (Phase 5-1).

``GET /patients/{id}/qr`` / ``POST /patients/{id}/qr/regenerate`` の出力と、
「QRの管理」用の ``GET /patients/{id}/qr/status`` の出力・再発行の本文 (理由)。

* ``token`` は患者宅に固定掲示する QR の識別子 (``token_urlsafe(16)``)。
* ``version`` は再発行カウンタ (``patients.qr_version``)。印刷シートに印字して
  物理ステッカーの世代を追跡する。
* URL (``/q/{token}``) はフロントで ``window.location.origin`` を前置して組む
  (発行ドメインをサーバが持たないため)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

#: 再発行の理由。``damaged`` (汚れた・読めない) は古い QR を 7 日残す。
#: ``lost`` (なくした) / ``misuse`` (他人に使われた恐れ) は古い QR をすぐ使えなくする。
QrRegenerateReason = Literal["damaged", "lost", "misuse"]


class PatientQrRead(BaseModel):
    """QR 発行 / 再発行レスポンス (token + version)."""

    model_config = ConfigDict(extra="forbid")

    token: str
    version: int


class PatientQrRegenerate(BaseModel):
    """再発行の本文 (理由)。本文なしの再発行 (古い画面) は今と同じ即時無効。"""

    model_config = ConfigDict(extra="forbid")

    reason: QrRegenerateReason


class PatientQrGrace(BaseModel):
    """猶予中の古い QR 1 件 (第何版がいつまで使えるか)。"""

    model_config = ConfigDict(extra="forbid")

    version: int
    valid_until: datetime


class PatientQrStatus(BaseModel):
    """「QRの管理」の表示 (今の版・発行日・最後に読めた日・猶予中の古い QR)。

    * ``issued_at`` は直近の再発行の時刻 (無ければ null = 初回発行のまま)。
    * ``last_scanned_at`` / ``last_scanned_staff_name`` は QR で記録された打刻の最新 1 件。
    """

    model_config = ConfigDict(extra="forbid")

    version: int
    issued_at: datetime | None
    last_scanned_at: datetime | None
    last_scanned_staff_name: str | None
    grace: list[PatientQrGrace]
