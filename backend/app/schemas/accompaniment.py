"""同行 (accompaniment) API スキーマ — 設計 §6 / 一般化 §3.

正典設計書:
- `docs/plans/trainee-accompaniment-design.md` v1.1 (基盤)
- `docs/plans/general-accompaniment-design.md` (一般化・mig 0072)

- 週一覧 (GET) / 週一括置換 (PUT)
- 既定一覧 (GET) / 既定一括置換 (PUT)
- ``AccompanimentRef``: VisitRead / モバイル射影に非破壊追加する同行者参照

``kind`` は**読み取りにのみ**現れる (書き込みではサーバが ``staff.is_trainee``
から自動判定する = 一般化 §2)。入力スキーマに ``kind`` を足してはいけない。
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AccompanimentRef(BaseModel):
    """訪問に紐付く同行者 1 名の最小参照 (VisitRead 射影用・非破壊追加).

    ``kind`` は一般化 (mig 0072) で追加。既存クライアントは無視できる
    (既定 'trainee' = 従来の新人同行)。
    """

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    staff_name: str | None = None
    kind: str = "trainee"


# ---------------------------------------------------------------------------
# GET /accompaniments (週一覧・解決済み情報つき)
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


class AccompanimentRead(BaseModel):
    """GET /accompaniments の 1 リンク.

    ``staff_id`` / ``staff_name`` が一般化後の正式名。旧 FE 互換のため
    ``trainee_staff_id`` / ``trainee_staff_name`` も同じ値で併記する
    (FE 移行後に削除。設計 §8 の互換エイリアス方針と同じ据わり)。
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    staff_id: UUID
    staff_name: str | None = None
    kind: str = "trainee"
    target_type: str
    source: str
    course: AccompanimentCourseInfo | None = None
    visit: AccompanimentVisitInfo | None = None
    # --- 後方互換 (旧 /trainee-accompaniments クライアント) ---
    trainee_staff_id: UUID
    trainee_staff_name: str | None = None


class AccompanimentsListResponse(BaseModel):
    """GET /accompaniments レスポンス."""

    model_config = ConfigDict(extra="forbid")

    items: list[AccompanimentRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# PUT /accompaniments (週一括置換・確定操作)
# ---------------------------------------------------------------------------


class AccompanimentDefaultItem(BaseModel):
    """「毎週の既定にする」1 件 (曜日 × テンプレ)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="曜日 (0=月 ... 6=日)")
    course_template_id: UUID


class AccompanimentsPut(BaseModel):
    """PUT /accompaniments リクエスト (週単位の一括置換).

    ``defaults`` の扱い (曖昧性排除・設計 §6.2):
      キー省略 / null / [] = 既定に一切触れない。非空配列が来た場合のみ
      「含まれた曜日の upsert」。既定の削除は PUT /accompaniment-defaults のみ。

    ``staff_id`` が正式名。旧 FE 互換のため ``trainee_staff_id`` でも受ける
    (どちらか一方は必須・両方来たら ``staff_id`` を優先)。
    ``acknowledge_constraint_warnings`` は NG スタッフ / 性別制限の 422 確認フロー
    (一般化 決定#4) の再送フラグ。``kind`` は**受け取らない** (サーバ自動判定)。
    """

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID | None = None
    trainee_staff_id: UUID | None = None
    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    course_ids: list[UUID] = Field(default_factory=list)
    visit_ids: list[UUID] = Field(default_factory=list)
    defaults: list[AccompanimentDefaultItem] | None = None
    acknowledge_constraint_warnings: bool = False

    @model_validator(mode="after")
    def _require_staff_id(self) -> AccompanimentsPut:
        if self.staff_id is None and self.trainee_staff_id is None:
            raise ValueError("staff_id (or legacy trainee_staff_id) is required")
        return self

    @property
    def effective_staff_id(self) -> UUID:
        """新旧どちらのキーで来ても 1 つに解決する (旧 FE 互換・validator 済み)."""
        resolved = self.staff_id or self.trainee_staff_id
        assert resolved is not None  # noqa: S101 — _require_staff_id で保証済み
        return resolved


class AccompanimentConflictDetail(BaseModel):
    """422 ``accompaniment_overlap`` の ``conflicts[]`` 1 要素 (一般化 決定#1).

    ``reason`` = 'own_duty' (ご自身の担当と重なる) / 'accompaniment' (同行選択どうし)。
    FE はこれだけで「◯月◯日(◯) HH:MM は ◯◯様（◯◯コース・ご自身の担当）と
    重なるため登録できません」を組める。
    """

    model_config = ConfigDict(extra="forbid")

    visit_id: UUID
    date: date
    weekday: int
    start: str
    end: str
    patient_name: str | None = None
    course_label: str | None = None
    reason: str


# ---------------------------------------------------------------------------
# GET/PUT /accompaniment-defaults
# ---------------------------------------------------------------------------


class AccompanimentDefaultRead(BaseModel):
    """既定 1 件.

    ``course_template_label`` / ``office_id`` は §7.5 の閲覧サマリ用に解決済みの
    テンプレ情報を非破壊で載せる (Phase 1 契約への追加・任意)。
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    staff_id: UUID
    kind: str = "trainee"
    weekday: int
    course_template_id: UUID
    course_template_label: str | None = None
    office_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    # --- 後方互換 (旧 /trainee-accompaniment-defaults クライアント) ---
    trainee_staff_id: UUID


class AccompanimentDefaultsPut(BaseModel):
    """PUT /accompaniment-defaults リクエスト (曜日×テンプレの全置換)."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID | None = None
    trainee_staff_id: UUID | None = None
    items: list[AccompanimentDefaultItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_staff_id(self) -> AccompanimentDefaultsPut:
        if self.staff_id is None and self.trainee_staff_id is None:
            raise ValueError("staff_id (or legacy trainee_staff_id) is required")
        return self

    @property
    def effective_staff_id(self) -> UUID:
        """新旧どちらのキーで来ても 1 つに解決する (旧 FE 互換・validator 済み)."""
        resolved = self.staff_id or self.trainee_staff_id
        assert resolved is not None  # noqa: S101 — _require_staff_id で保証済み
        return resolved


# ---------------------------------------------------------------------------
# §8-4: is_trainee ON 警告用の「今週以降のコース担当」ガードチェック
# ---------------------------------------------------------------------------


class CourseGuardCourse(BaseModel):
    """スタッフが今週以降に担当として残っているコース 1 件."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    iso_year: int
    iso_week: int
    weekday: int
    code: str


class CourseGuardResponse(BaseModel):
    """GET /accompaniments/course-guard レスポンス (§8-4 警告主義).

    ``applicable`` は **常に true** (互換のため残しているフィールド)。
    かつて kind='trainee' 限定にしようとしたが、このEPは「これから is_trainee を
    ON にする人」への事前警告で、FE は保存前のフォーム値で発火する。保存済み kind で
    ゲートすると警告が恒久的に出なくなる (2026-08-17 レビュー HIGH で撤廃)。
    """

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    applicable: bool = True
    count: int
    courses: list[CourseGuardCourse] = Field(default_factory=list)
    # --- 後方互換 (旧 /trainee-accompaniments/course-guard クライアント) ---
    trainee_staff_id: UUID


# ---------------------------------------------------------------------------
# §7.5: 将来リンク + 既定の一括削除
# ---------------------------------------------------------------------------


class AccompanimentFutureDeleteResponse(BaseModel):
    """DELETE /accompaniments/future レスポンス (冪等)."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    deleted_links: int
    deleted_defaults: int
    # --- 後方互換 (旧 /trainee-accompaniments/future クライアント) ---
    trainee_staff_id: UUID


__all__ = [
    "AccompanimentConflictDetail",
    "AccompanimentCourseInfo",
    "AccompanimentDefaultItem",
    "AccompanimentDefaultRead",
    "AccompanimentDefaultsPut",
    "AccompanimentFutureDeleteResponse",
    "AccompanimentRead",
    "AccompanimentRef",
    "AccompanimentVisitInfo",
    "AccompanimentsListResponse",
    "AccompanimentsPut",
    "CourseGuardCourse",
    "CourseGuardResponse",
]
