"""新人同行 (trainee accompaniment) API スキーマ — 設計 §6.

`docs/plans/trainee-accompaniment-design.md` v1.1:
- 週一覧 (GET) / 週一括置換 (PUT)
- 既定一覧 (GET) / 既定一括置換 (PUT)
- ``AccompanimentRef``: VisitRead / モバイル射影に非破壊追加する同行者参照
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AccompanimentRef(BaseModel):
    """訪問に紐付く同行新人の最小参照 (VisitRead 射影用・非破壊追加)."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    staff_name: str | None = None


# ---------------------------------------------------------------------------
# GET /trainee-accompaniments (週一覧・解決済み情報つき)
# ---------------------------------------------------------------------------


class AccompanimentCourseInfo(BaseModel):
    """コースリンクの解決済みコース情報."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    weekday: int
    code: str
    office_id: UUID
    template_id: UUID | None = None


class AccompanimentVisitInfo(BaseModel):
    """個別リンクの解決済み訪問情報."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    date: date
    start: time
    patient_name: str | None = None


class TraineeAccompanimentRead(BaseModel):
    """GET /trainee-accompaniments の 1 リンク."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    trainee_staff_id: UUID
    trainee_staff_name: str | None = None
    target_type: str
    source: str
    course: AccompanimentCourseInfo | None = None
    visit: AccompanimentVisitInfo | None = None


class TraineeAccompanimentsListResponse(BaseModel):
    """GET /trainee-accompaniments レスポンス."""

    model_config = ConfigDict(extra="forbid")

    items: list[TraineeAccompanimentRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# PUT /trainee-accompaniments (週一括置換・確定操作)
# ---------------------------------------------------------------------------


class AccompanimentDefaultItem(BaseModel):
    """「毎週の既定にする」1 件 (曜日 × テンプレ)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="曜日 (0=月 ... 6=日)")
    course_template_id: UUID


class TraineeAccompanimentsPut(BaseModel):
    """PUT /trainee-accompaniments リクエスト (週単位の一括置換).

    ``defaults`` の扱い (曖昧性排除・設計 §6.2):
      キー省略 / null / [] = 既定に一切触れない。非空配列が来た場合のみ
      「含まれた曜日の upsert」。既定の削除は PUT /trainee-accompaniment-defaults のみ。
    """

    model_config = ConfigDict(extra="forbid")

    trainee_staff_id: UUID
    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    course_ids: list[UUID] = Field(default_factory=list)
    visit_ids: list[UUID] = Field(default_factory=list)
    defaults: list[AccompanimentDefaultItem] | None = None


class OverlapPatient(BaseModel):
    """重複ペアの片側 (422 詳細)."""

    model_config = ConfigDict(extra="forbid")

    visit_id: UUID
    patient_name: str | None = None
    start: time
    end: time
    course_code: str | None = None


class OverlapPair(BaseModel):
    """時間重複ペア詳細 (FE がそのまま表示できる形・設計 §6.2)."""

    model_config = ConfigDict(extra="forbid")

    date: date
    a: OverlapPatient
    b: OverlapPatient


# ---------------------------------------------------------------------------
# GET/PUT /trainee-accompaniment-defaults
# ---------------------------------------------------------------------------


class TraineeAccompanimentDefaultRead(BaseModel):
    """既定 1 件.

    ``course_template_label`` / ``office_id`` は §7.5 の閲覧サマリ用に解決済みの
    テンプレ情報を非破壊で載せる (Phase 1 契約への追加・任意)。
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    trainee_staff_id: UUID
    weekday: int
    course_template_id: UUID
    course_template_label: str | None = None
    office_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class TraineeAccompanimentDefaultsPut(BaseModel):
    """PUT /trainee-accompaniment-defaults リクエスト (曜日×テンプレの全置換)."""

    model_config = ConfigDict(extra="forbid")

    trainee_staff_id: UUID
    items: list[AccompanimentDefaultItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §8-4: is_trainee ON 警告用の「今週以降のコース担当」ガードチェック
# ---------------------------------------------------------------------------


class TraineeCourseGuardCourse(BaseModel):
    """新人が今週以降に担当として残っているコース 1 件."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    iso_year: int
    iso_week: int
    weekday: int
    code: str


class TraineeCourseGuardResponse(BaseModel):
    """GET /trainee-accompaniments/course-guard レスポンス (§8-4 警告主義)."""

    model_config = ConfigDict(extra="forbid")

    trainee_staff_id: UUID
    count: int
    courses: list[TraineeCourseGuardCourse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §7.5: is_trainee OFF 時の将来リンク + 既定の一括削除
# ---------------------------------------------------------------------------


class TraineeAccompanimentFutureDeleteResponse(BaseModel):
    """DELETE /trainee-accompaniments/future レスポンス (冪等)."""

    model_config = ConfigDict(extra="forbid")

    trainee_staff_id: UUID
    deleted_links: int
    deleted_defaults: int


__all__ = [
    "AccompanimentCourseInfo",
    "AccompanimentDefaultItem",
    "AccompanimentRef",
    "AccompanimentVisitInfo",
    "OverlapPair",
    "OverlapPatient",
    "TraineeAccompanimentDefaultRead",
    "TraineeAccompanimentDefaultsPut",
    "TraineeAccompanimentFutureDeleteResponse",
    "TraineeAccompanimentRead",
    "TraineeAccompanimentsListResponse",
    "TraineeAccompanimentsPut",
    "TraineeCourseGuardCourse",
    "TraineeCourseGuardResponse",
]
