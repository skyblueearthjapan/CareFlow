"""Patient address → Office 自動紐付けサービス (W1-BE3).

設計仕様書 `docs/plans/v2-allocation-redesign.md` v0.9 §4.3 に対応する
拠点 (Office) 自動判定ロジック。

## 役割

- 患者マスタの住所文字列 (`patients.address`) から該当する拠点を 1 つ返す。
- 突合ルール: 住所に含まれる **市区町村名** を `cities` テーブルから抽出し、
  その city が紐付いている `office_cities` (M2M) の office を取得する。
- 該当が複数ある場合は、最長マッチした city の office を採用し、警告ログを出す。
- 該当なしは `None` を返す。呼び出し側で警告 UI / 運用判断に使う。

## 呼び出し側 (W1-BE1) への引き継ぎ

本サービスは新規ファイルとして提供する。`backend/app/api/v1/patients.py` への
組み込みは **W1-BE1 担当**。以下の方針で組み込む想定:

1. 患者作成・更新時、`address` が変更されたら `OfficeAssigner.resolve_for_address`
   を呼び出して `primary_office_id` を自動更新する。
2. リクエストで `primary_office_id` を **明示的に指定** された場合は自動更新を抑止
   （手動上書きを尊重）。フィールド上書き判定は `payload.model_fields_set` で
   `primary_office_id` が含まれるか否かで判定可能。
3. 自動判定の結果が `None` の場合は `primary_office_id` を NULL にしたまま、
   API レスポンスに警告フラグ（例: `office_resolution: "none"`）を返すか、
   既存値を維持するか、運用上の判断は W1-BE1 に委ねる。

API 契約: `POST /api/v1/offices/resolve` (`docs/plans/v2-api-contracts.md` §3.1) を
本ファイルで提供する `resolve_for_address` の薄いラッパとして `offices.py` 側に追加。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.city import City
from app.models.office import Office, OfficeCity

logger = logging.getLogger(__name__)


# 都道府県名の集合（住所先頭の固定接頭辞除去用）。
# cities テーブルの prefecture と一致する 47 値。
_PREFECTURES: frozenset[str] = frozenset(
    {
        "北海道",
        "青森県",
        "岩手県",
        "宮城県",
        "秋田県",
        "山形県",
        "福島県",
        "茨城県",
        "栃木県",
        "群馬県",
        "埼玉県",
        "千葉県",
        "東京都",
        "神奈川県",
        "新潟県",
        "富山県",
        "石川県",
        "福井県",
        "山梨県",
        "長野県",
        "岐阜県",
        "静岡県",
        "愛知県",
        "三重県",
        "滋賀県",
        "京都府",
        "大阪府",
        "兵庫県",
        "奈良県",
        "和歌山県",
        "鳥取県",
        "島根県",
        "岡山県",
        "広島県",
        "山口県",
        "徳島県",
        "香川県",
        "愛媛県",
        "高知県",
        "福岡県",
        "佐賀県",
        "長崎県",
        "熊本県",
        "大分県",
        "宮崎県",
        "鹿児島県",
        "沖縄県",
    }
)


def _detect_prefecture(address: str) -> str | None:
    """住所先頭の都道府県名を検出して返す。なければ None."""
    for pref in _PREFECTURES:
        if address.startswith(pref):
            return pref
    return None


@dataclass(frozen=True)
class OfficeResolution:
    """`resolve_for_address` の戻り値（詳細情報を含む）.

    - `office`: 該当した Office インスタンス（なければ None）
    - `matched_city_id`: 突合に使った city の UUID（なければ None）
    - `confidence`: `exact` / `fuzzy` / `none`
        - `exact`: 都道府県 + 市区町村名で完全一致
        - `fuzzy`: 都道府県の指定がない／前方一致のみ等の条件付きマッチ
        - `none`: 該当なし
    """

    office: Office | None
    matched_city_id: UUID | None
    confidence: Literal["exact", "fuzzy", "none"]


class OfficeAssigner:
    """患者住所 → 拠点 自動判定 (§4.3 自動紐付けの実装).

    ステートレス。AsyncSession を引数で受けることで API 層 / バッチ / テストの
    どこからでも呼べる単一エントリポイントとして機能する。
    """

    @staticmethod
    async def resolve_for_address(db: AsyncSession, address: str | None) -> Office | None:
        """住所文字列から該当する拠点を返す。

        Args:
            db: AsyncSession
            address: 患者住所（例: "千葉県千葉市稲毛区稲毛東1-1-1"）。
                None / 空文字の場合は None を返す。

        Returns:
            該当 Office インスタンス。該当なしは None。
        """
        result = await OfficeAssigner.resolve_with_details(db, address)
        return result.office

    @staticmethod
    async def resolve_with_details(db: AsyncSession, address: str | None) -> OfficeResolution:
        """`resolve_for_address` の詳細版。API レスポンス用。

        マッチング戦略:
            1. 住所先頭の都道府県名を検出（あれば）
            2. cities テーブルから候補（同都道府県がわかれば絞り込み）を取得
            3. 住所文字列に city.name が含まれる city を全列挙
            4. 市区町村名の **長い順** に並べ、住所のより前方に出現する city を優先
            5. 採用した city と紐付く office を `office_cities` 経由で取得
            6. 同一 city に複数 office がぶら下がっている場合は最初の 1 件
               + WARN ログ
        """
        if not address or not address.strip():
            return OfficeResolution(office=None, matched_city_id=None, confidence="none")

        normalized = address.strip()
        detected_pref = _detect_prefecture(normalized)

        # 候補 city の取得（deleted_at IS NULL）
        stmt = select(City).where(City.deleted_at.is_(None))
        if detected_pref is not None:
            stmt = stmt.where(City.prefecture == detected_pref)
        cities = list((await db.scalars(stmt)).all())

        # 住所文字列に出現する city を抽出
        # 「千葉市」と「千葉市稲毛区」が両方候補に入った場合、後者を優先するため
        # name の長い順にソートする。
        candidates: list[tuple[City, int]] = []
        for city in cities:
            idx = normalized.find(city.name)
            if idx >= 0:
                candidates.append((city, idx))

        if not candidates:
            return OfficeResolution(office=None, matched_city_id=None, confidence="none")

        # 長さ降順 → 出現位置昇順 で並べ、最も具体的なマッチを採用
        candidates.sort(key=lambda c: (-len(c[0].name), c[1]))
        best_city, _ = candidates[0]

        # office_cities 経由で office を取得（deleted_at IS NULL の office のみ）
        office_stmt = (
            select(Office)
            .join(OfficeCity, OfficeCity.office_id == Office.id)
            .where(
                OfficeCity.city_id == best_city.id,
                Office.deleted_at.is_(None),
            )
            .order_by(Office.created_at.asc())
        )
        offices = list((await db.scalars(office_stmt)).all())

        if not offices:
            # city は見つかったが、その city を担当する office がいない
            logger.info(
                "OfficeAssigner: matched city %s (%s%s) but no office covers it",
                best_city.id,
                best_city.prefecture,
                best_city.name,
            )
            return OfficeResolution(
                office=None,
                matched_city_id=best_city.id,
                confidence="none",
            )

        if len(offices) > 1:
            logger.warning(
                "OfficeAssigner: city %s (%s%s) is covered by multiple offices "
                "(%d). Using the first one (%s). Resolve duplicate office_cities "
                "rows in the master.",
                best_city.id,
                best_city.prefecture,
                best_city.name,
                len(offices),
                offices[0].id,
            )

        confidence: Literal["exact", "fuzzy"] = "exact" if detected_pref else "fuzzy"
        return OfficeResolution(
            office=offices[0],
            matched_city_id=best_city.id,
            confidence=confidence,
        )


__all__ = ["OfficeAssigner", "OfficeResolution"]
