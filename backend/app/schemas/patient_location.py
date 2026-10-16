"""Pydantic schemas for 登録の位置のずれ (2026-10-17).

``docs/plans/location-drift-align-2026-10-17.md`` §3-4。

* ``LocationDrift`` = 登録座標と、職員が実際に QR を読んだ場所のまとまりとのずれ。
  ``GET /patients/{id}/location-drift`` と ``MonitorVisit.location_drift`` (admin だけ)
  が同じ形で返す。
* ``PatientLocationRead`` = 合わせた / 元に戻した後の登録座標。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LocationDrift(BaseModel):
    """登録の位置のずれ (提案の位置・打刻の回数と日数)."""

    model_config = ConfigDict(extra="forbid")

    # 登録座標から提案の位置までの距離 (m)。登録座標が無い患者は None。
    distance_m: float | None = None
    suggested_lat: float
    suggested_lng: float
    # まとまりに入った打刻の数と、その打刻が分かれている日数 (JST)。
    scans: int
    days: int


class PatientLocationRead(BaseModel):
    """合わせた / 元に戻した後の登録座標."""

    model_config = ConfigDict(extra="forbid")

    lat: float | None = None
    lng: float | None = None
