"""Scheduling services (Wave 4 — Layer 1〜3 アルゴリズム).

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6 / §5 に対応する
3 レイヤー構造の入口となるパッケージ。

| レイヤー | 役割                                              | チケット   |
|---------|---------------------------------------------------|-----------|
| Layer 1 | 患者マスタ → 当該週の visits 展開 / 保留プール構築 | W4-BE7    |
| Layer 2 | コース分け (K-means + 制約後処理)                   | W4-BE8    |
| Layer 3 | スタッフ割付 (ハンガリアン法 + ローテーション)      | W4-BE9    |

本パッケージは ``app.services.allocation`` (v1) と並行運用される。Wave 6
(`W6-FREEZE`) で v1 が凍結された後は本パッケージが正本となる。
"""

from app.services.scheduling.layer1_expander import (
    Layer1Expander,
    Layer1ExpandError,
    Layer1Result,
    PoolEntry,
    VisitCreated,
)
from app.services.scheduling.layer2_clustering import (
    CourseProposal,
    Layer2Clusterer,
    Layer2ClusterError,
    Layer2Result,
    Layer2VisitInput,
)

__all__ = [
    "CourseProposal",
    "Layer1ExpandError",
    "Layer1Expander",
    "Layer1Result",
    "Layer2ClusterError",
    "Layer2Clusterer",
    "Layer2Result",
    "Layer2VisitInput",
    "PoolEntry",
    "VisitCreated",
]
