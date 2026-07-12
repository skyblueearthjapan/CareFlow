"""新人同行 (trainee accompaniment) モデル — 設計 §4.

`docs/plans/trainee-accompaniment-design.md` v1.1:
新人スタッフ (``staff.is_trainee=true``) が先輩の訪問に「同行」として付く運用を
表す専用テーブル 2 本。旧 ``staff_companion_assignments`` (曜日×午前/午後、紐付けの
向きが逆) を置き換える (旧テーブルの撤去は Phase 2)。

- ``trainee_accompaniment_defaults``: 毎週の既定 (テンプレ層。PFV と同じ恒久
  ``course_templates`` アンカー)。新人×曜日で 1 コース。
- ``trainee_accompaniments``: 週の実効リンク (唯一の表示/連携ソース)。コース単位
  (生きた参照) と訪問単位を同一テーブルで混在管理する。

設計原則 §2: 同行リンクは本テーブルが唯一の正典。``visits.primary/secondary/
mentor_staff_id`` や VSA には**書かない** (過去の同期事故の教訓)。読み出し側が
JOIN で解決する。週の特定は JOIN 先 (courses.iso_year/iso_week・visits の日付) から
導出し本テーブルには持たない。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class TraineeAccompanimentDefault(Base, TimestampMixin):
    """毎週の既定 (テンプレ層) — 設計 §4.1.

    「毎週この曜日はこのコースに同行」。恒久テンプレート ``course_templates`` を
    参照 (PFV と同じアンカー。週インスタンスは ``courses.template_id`` 経由で解決)。
    既定はコースのみ (患者個別の毎週既定は初版スコープ外)。

    UNIQUE (trainee_staff_id, weekday) — 1 新人 × 1 曜日 = 1 コース既定
    (同日 2 コースは物理的に不可)。
    """

    __tablename__ = "trainee_accompaniment_defaults"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trainee_staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="0=Mon ... 6=Sun",
    )
    course_template_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("course_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "trainee_staff_id",
            "weekday",
            name="uq_tad_trainee_weekday",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_tad_weekday"),
        Index("ix_tad_trainee", "trainee_staff_id"),
    )


class TraineeAccompaniment(Base, TimestampMixin):
    """週の実効リンク (唯一の表示/連携ソース) — 設計 §4.2.

    - コースリンク (``target_type='course'``) は「生きた参照」: 後からコースに患者が
      追加/移動されても自動で同行対象に含まれる (物質化コピーしない)。
    - 個別リンク (``target_type='visit'``) は visit へ直接。
    - ``source='default'`` = 週生成時の既定展開由来、``'manual'`` = 画面からの手動追加
      (既定の再展開で上書きしない判定に使う。§5.2)。
    """

    __tablename__ = "trainee_accompaniments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trainee_staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="'course' / 'visit'",
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=True,
    )
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="CASCADE"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="manual",
        server_default="manual",
        comment="'default' (既定展開由来) / 'manual' (手動追加)",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "target_type IN ('course', 'visit')",
            name="ck_ta_target_type",
        ),
        # target_type と course_id / visit_id の整合を DB 側で担保する。
        CheckConstraint(
            "(target_type = 'course') = (course_id IS NOT NULL)",
            name="ck_ta_course_presence",
        ),
        CheckConstraint(
            "(target_type = 'visit') = (visit_id IS NOT NULL)",
            name="ck_ta_visit_presence",
        ),
        CheckConstraint(
            "source IN ('default', 'manual')",
            name="ck_ta_source",
        ),
        # 1 新人 × 1 コース / 1 訪問 = 1 リンク (冪等展開・重複防止)。
        UniqueConstraint("trainee_staff_id", "course_id", name="uq_ta_trainee_course"),
        UniqueConstraint("trainee_staff_id", "visit_id", name="uq_ta_trainee_visit"),
        Index("ix_ta_course", "course_id"),
        Index("ix_ta_visit", "visit_id"),
        Index("ix_ta_trainee", "trainee_staff_id"),
    )
