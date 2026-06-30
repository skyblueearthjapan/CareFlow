"""Pydantic schemas for patient QR 発行 / 再発行 (Phase 5-1).

``GET /patients/{id}/qr`` / ``POST /patients/{id}/qr/regenerate`` の出力。

* ``token`` は患者宅に固定掲示する QR の識別子 (``token_urlsafe(16)``)。
* ``version`` は再発行カウンタ (``patients.qr_version``)。印刷シートに印字して
  物理ステッカーの世代を追跡する。
* URL (``/q/{token}``) はフロントで ``window.location.origin`` を前置して組む
  (発行ドメインをサーバが持たないため)。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PatientQrRead(BaseModel):
    """QR 発行 / 再発行レスポンス (token + version)."""

    model_config = ConfigDict(extra="forbid")

    token: str
    version: int
