/** 訪問モニター テスト用のサンプルデータ生成。 */
import type { MonitorStaffRow, MonitorVisit } from '@/lib/schemas/monitor';

let seq = 0;
function uid(): string {
  seq += 1;
  return `00000000-0000-0000-0000-${String(seq).padStart(12, '0')}`;
}

export function makeVisit(overrides: Partial<MonitorVisit> = {}): MonitorVisit {
  return {
    visit_id: uid(),
    patient_id: uid(),
    patient_name: '山田 花子',
    patient_code: 'P-1',
    patient_lat: 35.6,
    patient_lng: 140.11,
    start_time: '09:00',
    end_time: '10:00',
    // 代行 / 予定外 (qr-open-checkin-design.md §6)。既定は通常訪問。
    actual_staff_id: null,
    actual_staff_name: null,
    substitute_staff_id: null,
    substitute_staff_name: null,
    is_substitute: false,
    is_unplanned: false,
    phase: 'done',
    alert_level: 'none',
    pair_waiting: false,
    arrival: null,
    departure: null,
    no_show: null,
    stay_minutes: null,
    arrival_delay_min: null,
    distance_to_next_m: null,
    reason: null,
    reviewed: false,
    reviewed_by_name: null,
    reviewed_at: null,
    review_comment: null,
    ...overrides,
  };
}

export function makeRow(overrides: Partial<MonitorStaffRow> = {}): MonitorStaffRow {
  const staffId = overrides.staff_id !== undefined ? overrides.staff_id : uid();
  return {
    course_id: null,
    course_staff_id: null,
    course_staff_name: null,
    staff_id: staffId,
    staff_name: '田中 太郎',
    staff_ids: staffId ? [staffId] : [],
    office_id: uid(),
    office_name: '稲毛',
    course_label: 'Aコース',
    visits: [],
    ...overrides,
  };
}
