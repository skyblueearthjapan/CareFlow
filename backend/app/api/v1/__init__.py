"""v1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1 import (
    acceptance_calendar,
    acceptance_matrix,
    admin,
    admin_checkin,
    admin_geocoding,
    allocate,
    audit_logs,
    auth,
    checkin_settings,
    cities,
    course_templates,
    courses,
    dashboard,
    diff,
    geocoding,
    health,
    integrations,
    notifications,
    office_feature_flags,
    offices,
    op_log,
    patient_fixed_visits,
    patient_qr,
    patient_same_address_links,
    patient_sync,
    patients,
    patients_excel,
    pending_requests,
    schedule,
    schedule_v2,
    scheduling_settings,
    shift_requests,
    staff,
    staff_companion,
    staff_companion_assignments,
    staff_events,
    staff_excel,
    staff_overrides,
    staff_shifts,
    trainee_accompaniments,
    visit_monitor,
    visit_photos,
    visit_review,
    visits,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
# Task #144: lat/lng 整合性 audit (定期 cron 用 admin endpoint).
api_router.include_router(admin_geocoding.router)
# QR チェックイン Phase 5-2 / 5-6: 未訪問通知 + GPS パージ (定期 cron 用 admin endpoint).
api_router.include_router(admin_checkin.router)
# W41+ patient master Excel import / export. Must be registered BEFORE
# patients.router so /patients/import-export/* paths are matched before the
# /patients/{patient_id} catch-all.
api_router.include_router(
    patients_excel.router,
    prefix="/patients/import-export",
    tags=["patients-excel"],
)
# W9-BE1: patient fixed-visit pattern sub-resource.
# Phase G-21 final H3: patients.router の **前** に登録すること.
# FastAPI 現行版は path specificity で fall-through するが、 ルート定義順依存に
# しないため明示的に patient_fixed_visits を先に登録する.
# 例: PATCH /patients/fixed-visits/{pfv_id}/pin が PATCH /patients/{patient_id}
# の path-param に吸われないよう保護する.
api_router.include_router(
    patient_fixed_visits.router, prefix="/patients", tags=["patient-fixed-visits"]
)
# QR 訪問チェックイン Phase 5-1: 患者 QR 発行 / 再発行 sub-resource.
# patients.router の **前** に登録し、/patients/{id}/qr が /patients/{id} の
# catch-all に吸われないようにする (patient_fixed_visits と同方針)。
api_router.include_router(patient_qr.router, prefix="/patients", tags=["patient-qr"])
api_router.include_router(patients.router, prefix="/patients", tags=["patients"])
# 今週 visits → 固定枠 (PFV) 個別反映 sub-resource.
# POST /patients/{patient_id}/sync-week-visits-to-fixed
api_router.include_router(patient_sync.router, prefix="/patients", tags=["patient-sync"])
# Sub-resource routers MUST be included BEFORE staff.router so that the more
# specific paths (/staff/{id}/shifts etc.) are matched before the generic
# /staff/{id} catch-all in staff.router.
# Staff master Excel import / export. Must be registered BEFORE staff.router so
# that /staff/import-export/* paths are matched before the /staff/{staff_id}
# catch-all (mirrors patients_excel.router placement).
api_router.include_router(
    staff_excel.router,
    prefix="/staff/import-export",
    tags=["staff-excel"],
)
# W10-BE1: companion-candidates must be registered BEFORE /{staff_id} sub-resource
# routes to avoid UUID path collision on /staff/companion-candidates.
api_router.include_router(staff_companion.router, prefix="/staff", tags=["staff-companion"])
# W15-FE Phase 5 F-1: course-scoped GET + per-assignment PATCH (pair_role)
# Registered without /staff prefix — uses /staff-companion-assignments root.
api_router.include_router(staff_companion_assignments.router, tags=["staff-companion"])
api_router.include_router(staff_shifts.router, prefix="/staff", tags=["staff-shifts"])
api_router.include_router(staff_overrides.router, prefix="/staff", tags=["staff-overrides"])
api_router.include_router(staff_events.router, prefix="/staff", tags=["staff-events"])
# Wave 4-D: shift-request sub-resource (/staff/{id}/shift-requests).
api_router.include_router(shift_requests.router, prefix="/staff", tags=["shift-requests"])
api_router.include_router(staff.router, prefix="/staff", tags=["staff"])
# Wave 4-D: visit photo sub-resource (/visits/{id}/photos[...]) — registered
# before visits.router so its routes are matched before the generic
# /visits/{visit_id} catch-all.
api_router.include_router(visit_photos.router, prefix="/visits", tags=["visit-photos"])
# /visits/{visit_id}/review (確認済み) — visits.router の前に登録 (Phase 5-3)。
api_router.include_router(visit_review.router, prefix="/visits", tags=["visit-review"])
api_router.include_router(visits.router, prefix="/visits", tags=["visits"])
# QR 訪問チェックイン Phase 3: PC 訪問モニター集計 (admin/manager, read-only).
api_router.include_router(visit_monitor.router, prefix="/monitor", tags=["visit-monitor"])
# W2-BE4: Course CRUD (generate / fix / assign-staff は Wave 4 で追加).
api_router.include_router(courses.router, prefix="/courses", tags=["courses"])
# W15-BE1: 永続コーステンプレート CRUD.
api_router.include_router(
    course_templates.router, prefix="/course-templates", tags=["course-templates"]
)
# W15-BE1: 受入カレンダー (拠点単位の bulk upsert).
api_router.include_router(
    acceptance_calendar.router, prefix="/acceptance-calendar", tags=["acceptance-calendar"]
)
# 受け入れ枠マトリックス (拠点×曜日×時間帯 ○△× 自動算出, read-only).
api_router.include_router(
    acceptance_matrix.router, prefix="/acceptance-matrix", tags=["acceptance-matrix"]
)
api_router.include_router(offices.router, prefix="/offices", tags=["offices"])
api_router.include_router(cities.router, prefix="/cities", tags=["cities"])
api_router.include_router(diff.router, prefix="/diff", tags=["diff"])
api_router.include_router(allocate.router, prefix="/allocate", tags=["allocate"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
# Geocoding relay: POST /geocode + GET /geocoding/cache (Wave 4-C).
api_router.include_router(geocoding.router, tags=["geocoding"])
# W6-MIG2: /special-weeks API は廃止（special_weekly_pattern を patients に統合済）.
# 旧ルーターは削除し、`/api/v1/special-weeks*` は FastAPI の既定 404 で応答する。
# Wave 4-F: HTTP audit-log read API (admin only).
api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["audit-logs"])
# Wave 4-D: notifications inbox + admin-create (W6 will add producer side).
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
# Wave 4-D: cross-staff shift-request status update (admin/manager only).
api_router.include_router(
    shift_requests.status_router, prefix="/shift-requests", tags=["shift-requests"]
)
# W2-BE5: pending_requests (手動申請 + 承認フロー).
api_router.include_router(
    pending_requests.router, prefix="/pending-requests", tags=["pending-requests"]
)
# W3-BE-FIX: POST /schedule/fix (週レイアウト → patients.weekly_pattern).
# W4-BE7 で /schedule/generate-week が同じ router に追加される予定。
# 新人同行 (trainee accompaniment): 週リンク一覧/一括置換 + 既定 GET/PUT.
# ルートは絶対パス (/trainee-accompaniments[-defaults]) のため prefix なしで登録する。
api_router.include_router(trainee_accompaniments.router, tags=["trainee-accompaniments"])
api_router.include_router(schedule.router, prefix="/schedule", tags=["schedule"])
# W41 v2.0: auto-schedule v2 (差分追加 / 全面最適化 / 個別採用 / 固定枠に戻す).
# v1 (``/schedule/auto-allocate`` 等) は schedule.router にそのまま残し、UI 完成後の
# 別 PR で削除する.
api_router.include_router(schedule_v2.router, prefix="/schedule", tags=["schedule-v2"])
# Wave U-3: 操作ジャーナル undo/redo エンドポイント (/schedule/v2/op-log/*)
api_router.include_router(op_log.router, prefix="/schedule", tags=["op-log"])
# Phase G-21 T2: 同住所紐付け CRUD (blocked / required の link 行管理).
api_router.include_router(
    patient_same_address_links.router,
    prefix="/patient-same-address-links",
    tags=["patient-same-address-links"],
)
# Phase G-21 T2: 拠点 feature flag CRUD (canary 3 phase 制御).
api_router.include_router(
    office_feature_flags.router,
    prefix="/office-feature-flags",
    tags=["office-feature-flags"],
)
# Phase G-88 Step2: 自動最適化設定 (事業所単位の単一行) の取得 / 部分更新.
api_router.include_router(
    scheduling_settings.router,
    prefix="/scheduling-settings",
    tags=["scheduling-settings"],
)
# QR 訪問チェックイン Phase 4: しきい値設定 (全社一律・単一行) の取得 / 部分更新 +
# 距離系のみの public 取得 (モバイル到着プレビュー同期).
api_router.include_router(
    checkin_settings.router,
    prefix="/checkin-settings",
    tags=["checkin-settings"],
)

__all__ = ["api_router"]
