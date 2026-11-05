"""Staff schemas — v2 (W1-BE2: スタッフマスタを §4.2 の 9 項目に整理).

設計仕様書 v0.9 §4.2 / 実装手順書 v0.2 §2 W1-BE2 / API 契約 §2 に準拠。

削除済み (v1 → v2): can_double_team / home_address / home_lat / home_lng /
areas / max_per_day / skill_level / assignment_volume の 6 + 3 項目。

状態 (`status`): 在籍 (active) / 休職 (on_leave) / 退職 (retired) の **3 値 enum**
で厳密化 (DB は文字列のまま、Pydantic で Literal バリデート)。

W10-BE1: mentor_id 廃止、is_trainee 追加。
Phase 2 (新人同行 v1.1 §3): 旧 staff_companion_assignments 機構は撤去済み。
同行は accompaniments (スケジュール画面の同行モード) が正典。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.contacts import (
    ContactValueError,
    classify_email,
    classify_phone,
    is_carrier_email,
    sms_capable_for_kind,
)

# ---------------------------------------------------------------------------
# 列挙値 (§4.2 / Wave 0-C `app.schemas.v2.staff` と同一定義)
# ---------------------------------------------------------------------------

# 業務ロール (二軸分離・2026-08-09): 実運用は staff(スタッフ) / manager(マネージャー) の 2 値。
# manager は自動割当の対象外 (埋まらないコースの救済にのみ入る)。
# 'admin' は旧データ読み取り互換のためだけに残す (新規入力の選択肢には出さない)。
# ログインのアカウント権限 (users.role: admin/staff) とは **無関係**。
RoleV2 = Literal["admin", "manager", "staff"]

# 状態: 在籍 (active) / 休職 (on_leave) / 退職 (retired) の 3 値
#   保存値は英語、UI 表示は ja-JP に変換する
StaffStatusV2 = Literal["active", "on_leave", "retired"]

# 性別: 患者の性別制限とマッチング用
SexV2 = Literal["male", "female", "unknown"]

# 資格 (K-1b カイポケ「職種」列)。`app.schemas.v2.staff.QualificationV2` と同一定義。
# S1 (42bf03e) で FE の編集フォームが送るようになったが v1 schema (extra="forbid") に
# 無く、スタッフ編集ページの保存が 422 extra_forbidden で全滅していた (2026-08-31 是正)。
QualificationV2 = Literal["看護師", "准看護師", "理学療法士", "作業療法士", "言語聴覚士"]

# 休み・勤務表 (mig 0099・docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §2-2・§7-1)。
# 常勤 / 非常勤 (必要休日数は常勤だけ)。
EmploymentTypeV2 = Literal["full_time", "part_time"]
# 勤務表の勤務形態の区分。
WorkFormV2 = Literal["A", "B", "C", "D"]
# お知らせの手段 (mig 0109・docs/plans/contacts-phone-email-design-2026-11-05.md §2)。今は使わない。
StaffNotifyChannelV2 = Literal["sms", "email", "none"]


def _check_phone(v: str | None) -> str | None:
    """電話番号の形を確かめる (保存の形への変換は API がする)."""
    try:
        classify_phone(v)
    except ContactValueError as exc:
        raise ValueError(f"電話番号：{exc.message}") from exc
    return v


def _check_email(v: str | None) -> str | None:
    try:
        return classify_email(v)
    except ContactValueError as exc:
        raise ValueError(f"連絡用メール：{exc.message}") from exc


class StaffBase(BaseModel):
    """スタッフマスタ v2 基底スキーマ (§4.2 残す 9 項目)."""

    model_config = ConfigDict(extra="forbid")

    # 基本情報
    code: str | None = Field(default=None, max_length=64, description="スタッフコード")
    name: str = Field(min_length=1, max_length=120)
    kana: str | None = Field(default=None, max_length=120)
    sex: SexV2 | None = Field(default=None, description="性別 (患者の性別制限と突合)")
    role: RoleV2 = Field(
        default="staff",
        description="業務ロール。manager は自動割当の対象外 (救済のみ)。アカウント権限とは無関係",
    )
    status: StaffStatusV2 = Field(
        default="active",
        description="在籍 (active) 以外 (on_leave / retired) は割当除外",
    )
    primary_office_id: UUID | None = Field(
        default=None,
        description="主拠点 (§4.3). プルダウン: 稲毛 / 都賀。距離計算の起点",
    )
    note: str | None = Field(default=None, description="自由記述")

    # 詳細セクション (§4.2)
    is_trainee: bool = Field(
        default=False,
        description="新人フラグ。True の場合は同行スタッフ (companion) と一緒に訪問",
    )
    qualification: QualificationV2 | None = Field(
        default=None, description="資格 (看護師 / 准看護師 / PT / OT / ST)。請求区分の判定に使う"
    )

    # 休み・勤務表 (mig 0099)
    employment_type: EmploymentTypeV2 = Field(
        default="full_time", description="常勤 / 非常勤。非常勤は必要休日数の対象外"
    )
    work_form: WorkFormV2 | None = Field(default=None, description="勤務表の勤務形態の区分 A〜D")
    admin_hours_per_day: float | None = Field(
        default=None, ge=0, le=24, description="1 日の管理時間。空 = 管理者でない"
    )
    kinmu_title: str | None = Field(
        default=None, max_length=64, description="勤務表の 1 行目に出す役職 (空なら職種)"
    )


class StaffCreate(StaffBase):
    """POST /api/v1/staff リクエスト (W1-BE2)."""

    # 連絡先 (mig 0109)。phone は生の入力 (API で digits・表示・種類に分ける)。
    phone: str | None = Field(default=None, max_length=40, description="電話番号 (生の入力)")
    contact_email: str | None = Field(
        default=None, max_length=255, description="連絡用メール (ログイン用とは別)"
    )
    notify_channel: StaffNotifyChannelV2 = Field(default="none", description="お知らせの手段")

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, v: str | None) -> str | None:
        return _check_phone(v)

    @field_validator("contact_email")
    @classmethod
    def _valid_email(cls, v: str | None) -> str | None:
        return _check_email(v)


class StaffUpdate(BaseModel):
    """PATCH /api/v1/staff/{id} リクエスト (W1-BE2). 全フィールド optional."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kana: str | None = Field(default=None, max_length=120)
    sex: SexV2 | None = None
    role: RoleV2 | None = None
    status: StaffStatusV2 | None = None
    primary_office_id: UUID | None = None
    note: str | None = None
    is_trainee: bool | None = None
    qualification: QualificationV2 | None = None
    employment_type: EmploymentTypeV2 | None = None
    work_form: WorkFormV2 | None = None
    admin_hours_per_day: float | None = Field(default=None, ge=0, le=24)
    kinmu_title: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=40)
    contact_email: str | None = Field(default=None, max_length=255)
    notify_channel: StaffNotifyChannelV2 | None = None

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, v: str | None) -> str | None:
        return _check_phone(v)

    @field_validator("contact_email")
    @classmethod
    def _valid_email(cls, v: str | None) -> str | None:
        return _check_email(v)

    @field_validator("notify_channel")
    @classmethod
    def _notify_channel_not_null(cls, v: StaffNotifyChannelV2 | None) -> StaffNotifyChannelV2:
        # 省略は「変えない」。明示の null は DB の NOT NULL で 500 になるので 422 にする。
        if v is None:
            raise ValueError("notify_channel は sms・email・none のどれかを指定してください")
        return v

    @field_validator("employment_type")
    @classmethod
    def _employment_type_not_null(cls, v: EmploymentTypeV2 | None) -> EmploymentTypeV2:
        # 省略は「変えない」。明示の null は DB の NOT NULL で 500 になるので 422 にする。
        if v is None:
            raise ValueError("employment_type は full_time か part_time を指定してください")
        return v


class StaffRead(StaffBase):
    """GET /api/v1/staff{,/{id}} レスポンス (W1-BE2)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # 資格: DB には入っているのに v1 の一覧/詳細応答に含まれておらず、スタッフ一覧が全員
    # 「資格未設定」と表示されていた (2026-08-31 PO 指摘)。応答側は旧データを落とさないよう
    # 文字列のまま返す (入力側は StaffBase / StaffUpdate の Literal で検証)。
    qualification: str | None = Field(default=None, description="資格")  # type: ignore[assignment]
    # 同行のライフサイクル (docs/plans/general-accompaniment-design.md §3-7):
    # status を active から外した PATCH が畳んだ同行リンク / 既定の件数。
    # ORM に対応する属性は無く、``PATCH`` が model_copy で載せるときだけ非ゼロ
    # (GET/LIST は from_attributes の解決で既定 0)。FE 表示配線は Phase E。
    purged_accompaniment_links: int = 0
    purged_accompaniment_defaults: int = 0
    # 連絡先 (mig 0109)。管理者かご本人 (users.staff_id が一致) でないときは API が null にする。
    phone: str | None = None
    phone_display: str | None = None
    phone_kind: str | None = None
    sms_capable: bool | None = None
    contact_email: str | None = None
    email_carrier: bool | None = None
    notify_channel: str | None = None

    @model_validator(mode="after")
    def _derive_contact_flags(self) -> StaffRead:
        self.sms_capable = sms_capable_for_kind(self.phone_kind)
        self.email_carrier = is_carrier_email(self.contact_email)
        return self

    def masked_contacts(self) -> StaffRead:
        """連絡先を null にした写し (管理者・ご本人以外に返すとき)."""
        return self.model_copy(update=dict.fromkeys(STAFF_CONTACT_READ_FIELDS))


#: 管理者・ご本人以外には null で返す欄。
STAFF_CONTACT_READ_FIELDS: tuple[str, ...] = (
    "phone",
    "phone_display",
    "phone_kind",
    "sms_capable",
    "contact_email",
    "email_carrier",
    "notify_channel",
)
