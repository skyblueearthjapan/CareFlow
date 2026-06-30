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
  arrival: monitorCheckinSchema.nullable().optional(),
  departure: monitorCheckinSchema.nullable().optional(),
  no_show: monitorCheckinSchema.nullable().optional(),
  stay_minutes: z.number().nullable().optional(),
  arrival_delay_min: z.number().nullable().optional(),
  distance_to_next_m: z.number().nullable().optional(),
  reason: z.string().nullable().optional(),
});

export const monitorStaffRowSchema = z.object({
  staff_id: z.string().uuid().nullable().optional(),
  staff_name: z.string().nullable().optional(),
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
