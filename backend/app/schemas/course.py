"""Course schemas — v2 thin wrapper over ``app.schemas.v2.course``.

W2-BE4 (`docs/plans/v2-allocation-redesign.md` v0.9 §4.5).

* リクエスト/レスポンスの正本は ``app.schemas.v2.course``。
* 旧 v1 互換のための alias は無い（Course は v2 で新規導入）。
"""

from __future__ import annotations

from app.schemas.v2.course import (
    CourseCodeV2,
    CourseV2Base,
    CourseV2Create,
    CourseV2Read,
    CourseV2Update,
)

# 短縮 alias (router 側で from app.schemas.course import CourseCreate と書けるように)
CourseBase = CourseV2Base
CourseCreate = CourseV2Create
CourseRead = CourseV2Read
CourseUpdate = CourseV2Update
CourseCode = CourseCodeV2

__all__ = [
    "CourseBase",
    "CourseCode",
    "CourseCodeV2",
    "CourseCreate",
    "CourseRead",
    "CourseUpdate",
    "CourseV2Base",
    "CourseV2Create",
    "CourseV2Read",
    "CourseV2Update",
]
