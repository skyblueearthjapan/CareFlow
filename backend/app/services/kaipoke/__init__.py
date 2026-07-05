"""Kaipoke 連携サービス (K-1): DB → 18列CSV 生成・氏名名寄せ。

- ``name_match``: カイポケ氏名 (全角スペース・漢字異体字あり) と CareFlow
  マスタ名の突合用正規化。
- ``csv_builder``: CareFlow visits → カイポケ18列CSV 生成 (差分適用の心臓部)。
"""
