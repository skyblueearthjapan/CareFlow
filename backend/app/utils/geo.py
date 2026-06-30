"""Geo helpers (Haversine great-circle distance).

QR 訪問チェックイン (Phase 1) の距離判定で利用する共通ユーティリティ。
``app/services/allocation/utils.py`` 他に既存の ``haversine_km`` があるが、
そちらは allocation エンジン専用 (km 単位) で多数の呼び出し元に密結合して
いるため、本 Phase では置換せず、チェックイン判定が依存する最小限の関数を
``app/utils`` 配下に独立して用意する (設計 R6: distance_m は ``km*1000``)。
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0  # Haversine 用の地球半径 (allocation/utils.py と一致)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2 点間の大円距離を km で返す (Haversine 公式)."""
    to_rad = math.pi / 180.0
    d_lat = (lat2 - lat1) * to_rad
    d_lng = (lng2 - lng1) * to_rad
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1 * to_rad) * math.cos(lat2 * to_rad) * math.sin(d_lng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2 点間の大円距離を m で返す (= ``haversine_km() * 1000``)."""
    return haversine_km(lat1, lng1, lat2, lng2) * 1000.0
