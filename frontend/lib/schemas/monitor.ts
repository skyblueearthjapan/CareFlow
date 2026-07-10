/**
 * Visit monitor zod schemas — mirrors `backend/app/schemas/visit_monitor.py`.
 *
 * QR 訪問チェックイン Phase 3 (PC 訪問モニター)。
 *
 *   GET /api/v1/monitor?date=YYYY-MM-DD&office_id= → MonitorResponse
 *   GET /api/v1/monitor/nearby?lat=&lng=&radius_m=&limit= → NearbyResponse
 */
import { z } from 'zod';

export const monitorCheckinSchema = z.object({
  kind: z.string(),
  scanned_at: z.string(),
  device_time: z.string().nullable().optional(),
  lat: z.number().nullable().optional(),
  lng: z.number().nullable().optional(),
  distance_m: z.number().nullable().optional(),
  accuracy_m: z.number().nullable().optional(),
  match_status: z.string(),
  reason: z.string().nullable().optional(),
  is_override: z.boolean().default(false),
});

export const monitorVisitSchema = z.object({
  visit_id: z.string().uuid(),
  // 訪問の担当 (= visits.primary_staff_id)。モバイル「今日の訪問」と同一ソース。
  staff_id: z.string().uuid().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  // 2 名体制のグルーピングキー。同一値の visit が 2 行 (各スタッフ 1 行)。通常は null。
  visit_group_id: z.string().uuid().nullable().optional(),
  patient_id: z.string().uuid(),
  patient_name: z.string().nullable().optional(),
  patient_code: z.string().nullable().optional(),
  patient_lat: z.number().nullable().optional(),
  patient_lng: z.number().nullable().optional(),
  start_time: z.string(),
  end_time: z.string(),
  phase: z.string(),
  alert_level: z.string(),
  // 同住所・同時刻ペアの後攻が相方の完了を待っている間 (欠落は false 扱い)。
  pair_waiting: z.boolean().default(false),
  arrival: monitorCheckinSchema.nullable().optional(),
  departure: monitorCheckinSchema.nullable().optional(),
  no_show: monitorCheckinSchema.nullable().optional(),
  stay_minutes: z.number().nullable().optional(),
  arrival_delay_min: z.number().nullable().optional(),
  distance_to_next_m: z.number().nullable().optional(),
  reason: z.string().nullable().optional(),
  // 「確認済み」(visit 単位の review)。reviewed なら要対応トレイから外れ、
  // タイムラインに「確認済」印が付く (Phase 5-3)。
  reviewed: z.boolean().default(false),
  reviewed_by_name: z.string().nullable().optional(),
  reviewed_at: z.string().nullable().optional(),
  review_comment: z.string().nullable().optional(),
});

// 行 = コース単位 (2026-07-10 PO要望。旧: スタッフ単位)。
// staff_id は行内の担当が 1 名のときのみ。staff_name は「・」連結の表示用。
export const monitorStaffRowSchema = z.object({
  course_id: z.string().uuid().nullable().optional(),
  staff_id: z.string().uuid().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  staff_ids: z.array(z.string().uuid()).default([]),
  office_id: z.string().uuid().nullable().optional(),
  office_name: z.string().nullable().optional(),
  course_label: z.string().nullable().optional(),
  visits: z.array(monitorVisitSchema),
});

export const monitorOfficeSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
});

export const monitorThresholdsSchema = z.object({
  match_m: z.number(),
  review_m: z.number(),
  accuracy_m: z.number(),
  no_show_grace_min: z.number(),
  late_min: z.number(),
  // 退出忘れ (長時間 inprogress) しきい値 (分)。Phase 4 で設定化。
  max_inprogress_min: z.number(),
});

export const monitorResponseSchema = z.object({
  date: z.string(),
  now: z.string(),
  thresholds: monitorThresholdsSchema,
  offices: z.array(monitorOfficeSchema),
  staff: z.array(monitorStaffRowSchema),
});

export const nearbyPatientSchema = z.object({
  patient_id: z.string().uuid(),
  name: z.string(),
  code: z.string().nullable().optional(),
  lat: z.number(),
  lng: z.number(),
  distance_m: z.number(),
});

export const nearbyResponseSchema = z.object({
  items: z.array(nearbyPatientSchema),
});

export type MonitorCheckin = z.infer<typeof monitorCheckinSchema>;
export type MonitorVisit = z.infer<typeof monitorVisitSchema>;
export type MonitorStaffRow = z.infer<typeof monitorStaffRowSchema>;
export type MonitorOffice = z.infer<typeof monitorOfficeSchema>;
export type MonitorThresholds = z.infer<typeof monitorThresholdsSchema>;
export type MonitorResponse = z.infer<typeof monitorResponseSchema>;
export type NearbyPatient = z.infer<typeof nearbyPatientSchema>;
export type NearbyResponse = z.infer<typeof nearbyResponseSchema>;
