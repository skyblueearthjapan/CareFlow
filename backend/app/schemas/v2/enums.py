"""v2 共有 Enum 定義 (Wave 0-C).

設計仕様書 v0.9:
- §4.5: course_status (`proposed` / `course_fixed` / `staff_assigned`)
- §4.4: pending_requests.request_type / status / scope
- §3.5: AI context_type

これらの enum はフロントエンド `frontend/lib/schemas/v2/enums.ts` と
**完全一致**させる（API 契約の前提）。値を増減する場合は両方同時に更新する。
"""

from __future__ import annotations

from enum import StrEnum


class CourseStatus(StrEnum):
    """コース状態 (§4.5).

    遷移: proposed → course_fixed → staff_assigned → (週終了でアーカイブ)
    """

    PROPOSED = "proposed"
    COURSE_FIXED = "course_fixed"
    STAFF_ASSIGNED = "staff_assigned"


class RequestType(StrEnum):
    """pending_requests.request_type (§4.4).

    9 種類。`patient_special_week_on` / `patient_special_week_off` は別 request_type だが
    AI 側の context_type では `patient_special_week` に集約される
    (`docs/plans/v2-api-contracts.md` §9 対応表参照)。
    """

    STAFF_OFF = "staff_off"
    STAFF_EVENT = "staff_event"
    STAFF_MENTOR = "staff_mentor"
    STAFF_CREATE = "staff_create"
    PATIENT_CREATE = "patient_create"
    PATIENT_CANCEL = "patient_cancel"
    PATIENT_RESCHEDULE = "patient_reschedule"
    PATIENT_SPECIAL_WEEK_ON = "patient_special_week_on"
    PATIENT_SPECIAL_WEEK_OFF = "patient_special_week_off"
    STAFF_STATUS_UPDATE = "staff_status_update"
    PATIENT_STATUS_UPDATE = "patient_status_update"
    # Phase G-84: 現場ボード 空き枠への既存患者直接配置 (normal PFV へ 1 枠追加).
    PATIENT_VISIT_ADD = "patient_visit_add"


class RequestStatus(StrEnum):
    """pending_requests.status (§3.5.4 / §4.4).

    `pending` で作成され、`approved` (即時 / 後追い) または `rejected` に遷移。
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RequestScope(StrEnum):
    """pending_requests.scope (§3.5.6 / §4.4).

    `patient_reschedule` のときのみ必須。それ以外の request_type では NULL。
    """

    ONE_TIME = "one_time"
    PERMANENT = "permanent"


class AiContextType(StrEnum):
    """AI /api/v1/ai/interpret context_type (§3.5.2).

    pending_requests.request_type と 1:1 対応する 9 種類 +
    `patient_special_week` (on/off を AI 側では区別しない) +
    既存の `general` (フォールバック) +
    `out_of_scope` (AI が対応外と判定したときに返す)。

    対応表は `docs/plans/v2-api-contracts.md` §9 を参照。
    """

    STAFF_OFF = "staff_off"
    STAFF_EVENT = "staff_event"
    STAFF_MENTOR = "staff_mentor"
    STAFF_CREATE = "staff_create"
    PATIENT_CREATE = "patient_create"
    PATIENT_CANCEL = "patient_cancel"
    PATIENT_RESCHEDULE = "patient_reschedule"
    PATIENT_SPECIAL_WEEK = "patient_special_week"
    GENERAL = "general"
    OUT_OF_SCOPE = "out_of_scope"
    STAFF_STATUS_UPDATE = "staff_status_update"
    PATIENT_STATUS_UPDATE = "patient_status_update"
