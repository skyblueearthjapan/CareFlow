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

/**
 * 効いている調整 1 件 (実績の時刻を合わせる・設計 2026-09-30 §6-3)。
 * `kind` は `arrival` / `departure`。
 */
export const monitorAdjustmentSchema = z.object({
  kind: z.string(),
  // 理由コード (intercom_wait / read_later / no_read / other)。表示は `reason_label`。
  reason_code: z.string().nullable().optional(),
  reason_label: z.string().nullable().optional(),
  reason_text: z.string().nullable().optional(),
  by_name: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
});

export const monitorVisitSchema = z.object({
  visit_id: z.string().uuid(),
  // 訪問の担当 (= visits.primary_staff_id)。モバイル「今日の訪問」と同一ソース。
  staff_id: z.string().uuid().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  // 2 名体制のグルーピングキー。同一値の visit が 2 行 (各スタッフ 1 行)。通常は null。
  visit_group_id: z.string().uuid().nullable().optional(),
  // 同行 (§7.3): この訪問に同行するスタッフ名 (単数・後方互換)。null=同行なし。
  // コース担当との食い違い ⚠ (course_staff_mismatch) とは別ラベル「＋◯◯（同行）」で表示する。
  accompaniment_staff_name: z.string().nullable().optional(),
  // 同行スタッフ名の全件 (一般化・確定#5)。旧デプロイは undefined → 単数へ落とす。
  accompaniment_staff_names: z.array(z.string()).nullable().optional(),
  // 実績 (arrival 打刻者)。予定担当 (staff_name) と食い違う = 代行
  // (qr-open-checkin-design.md §6)。予定側は書き換えず「予定/実績」を並記する。
  actual_staff_id: z.string().uuid().nullable().optional(),
  actual_staff_name: z.string().nullable().optional(),
  // 代行した人 = arrival 打刻者のうち担当集合の外だった最新の 1 名。
  // actual_staff_* は「最新の打刻者」なので、代行の後に担当本人が打ち直すと実績名は
  // 担当本人になる。「代行バッジ + 担当本人名」の自己矛盾を防ぐため、UI はバッジの
  // 根拠 (誰が代行したか) をこちらから取る。is_substitute=false のときは常に null。
  substitute_staff_id: z.string().uuid().nullable().optional(),
  substitute_staff_name: z.string().nullable().optional(),
  // 代行 = arrival 打刻者のいずれかが visit の担当集合の外。バーに「代行」バッジ+代行者名。
  is_substitute: z.boolean().default(false),
  // 予定外訪問 (visits.is_unplanned)。読み取った本人 (= 主担当) の行に「予定外」の札つきで入る
  // (専用行は 2026-10-01 廃止・monitor-staff-rows-design-2026-09-30.md §2)。
  is_unplanned: z.boolean().default(false),
  // 訪問のコースと札 (「稲D」= 拠点名の 1 文字目 + コースコード)。コース無し・予定外は null。
  // 旧デプロイは undefined。形が崩れていてもモニター全体は落とさない。
  course_id: z.string().uuid().nullish().catch(null),
  course_tag: z.string().nullish().catch(null),
  // コースの拠点 (拠点の絞り込みと札の色)。コース無しは患者の主担当拠点。
  course_office_id: z.string().uuid().nullish().catch(null),
  course_office_name: z.string().nullish().catch(null),
  // コースの担当とこの訪問の担当が違う (手動の付け替えを除く)。札に ⚠。
  course_staff_mismatch: z.boolean().nullish().catch(null),
  patient_id: z.string().uuid(),
  patient_name: z.string().nullable().optional(),
  patient_code: z.string().nullable().optional(),
  // 入力チャネル (`visits.source`)。'status_cancel' = 患者ステータス連動の取消。
  // BE は取消をモニターに出さないが、表示側でも落とす (二重の安全網・§3-4)。
  source: z.string().nullish().catch(null),
  // 患者マスタの `patients.status`。非稼働のまま予定が残っていたらバッジで見せる。
  patient_status: z.string().nullish().catch(null),
  // ステータスが今の値になった日 (JST `YYYY-MM-DD`)。この日以降だけバッジを出す。
  patient_status_since: z.string().nullish().catch(null),
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
  // 実績時刻 (調整後。無ければ読取時刻・設計 2026-09-30 §6-3)。画面の時刻はこちらで
  // 描く — `arrival` / `departure` (生の打刻) の `scanned_at` を直接読まない。
  // 旧デプロイは undefined → `components/monitor/constants.ts` の `actualArrivalIso`
  // などが打刻の `scanned_at` へ落とす。形が崩れていてもモニター全体は落とさない。
  arrival_at: z.string().nullish().catch(null),
  departure_at: z.string().nullish().catch(null),
  // 読取時刻 (QR を読んだ時刻)。読み取りが無ければ null。
  arrival_read_at: z.string().nullish().catch(null),
  departure_read_at: z.string().nullish().catch(null),
  arrival_adjusted: z.boolean().nullish().catch(null),
  departure_adjusted: z.boolean().nullish().catch(null),
  // 読み取りの無い退出 (手で入れた時刻)。`departure` は null のまま `departure_at` が入る。
  departure_manual: z.boolean().nullish().catch(null),
  // 読み取りの無い到着 (管理者が手で入れた時刻・pc-actual-time-edit-design D2)。
  arrival_manual: z.boolean().nullish().catch(null),
  // 前日以前で到着はあるが退出が無い =「退出未記録」(同設計 Q4)。このとき
  // stay_minutes は null (今の時刻まで数え続けない)。phase は inprogress のまま。
  departure_missing: z.boolean().nullish().catch(null),
  // 見ているユーザーが実績の時刻を合わせられるか / 打刻なしの訪問に到着・退出を
  // 手で入れられるか (どちらも管理者だけ。権限は BE が判定)。古い BE は欠落 = 出さない。
  adjust_allowed: z.boolean().nullish().catch(null),
  manual_arrival_allowed: z.boolean().nullish().catch(null),
  // 圏外で退避して遅れて届いた打刻の受信時刻 (遅れていなければ null)。
  arrival_late_received_at: z.string().nullish().catch(null),
  departure_late_received_at: z.string().nullish().catch(null),
  adjustments: z.array(monitorAdjustmentSchema).nullish().catch(null),
  // stay_minutes / arrival_delay_min / phase / alert_level は BE が実績時刻基準で出す。
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

/** 行ヘッダに並べるコースの札 1 つ (その人がこの日持つコース)。 */
export const monitorCourseTagSchema = z.object({
  label: z.string(),
  course_id: z.string().uuid(),
  office_id: z.string().uuid().nullable().optional(),
  office_name: z.string().nullable().optional(),
});

/** その日の休み (`off`)・時間変更 (`custom_time`)。 */
export const monitorDayOverrideSchema = z.object({
  kind: z.string(),
  start_time: z.string().nullable().optional(),
  end_time: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
});

// 行 = 職員単位 (2026-10-01・monitor-staff-rows-design-2026-09-30.md)。
// staff_id = 行の職員 (「担当なし」行は null)。office_* = 職員の所属拠点。
// course_id / course_label / course_staff_* は互換のため項目だけ残り、常に null。
export const monitorStaffRowSchema = z.object({
  course_id: z.string().uuid().nullable().optional(),
  course_staff_id: z.string().uuid().nullable().optional(),
  course_staff_name: z.string().nullable().optional(),
  staff_id: z.string().uuid().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  staff_ids: z.array(z.string().uuid()).default([]),
  office_id: z.string().uuid().nullable().optional(),
  office_name: z.string().nullable().optional(),
  course_label: z.string().nullable().optional(),
  // その人がこの日持つコースの札 (重複なし・初出順)。
  course_tags: z.array(monitorCourseTagSchema).nullish().catch(null),
  visits: z.array(monitorVisitSchema),
  // 同行・副担当として関わる訪問の id (訪問本体は主担当の行)。画面は薄く描く。
  companion_visit_ids: z.array(z.string().uuid()).nullish().catch(null),
  // その日の休み・時間変更 (無ければ null)。
  day_override: monitorDayOverrideSchema.nullish().catch(null),
});

export const monitorOfficeSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  // 拠点の略称 (札と凡例の 1 文字目・PO 決定 2026-10-01 で short_label に揃える)。
  // 無い応答 (古い BE) は拠点名の 1 文字目で代用する。
  short_label: z.string().nullish().catch(null),
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
  // 札の色の基準 = 拠点マスタの安定した順 (その日の offices の並びに依らない)。
  office_order: z.array(z.string().uuid()).optional(),
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
export type MonitorAdjustment = z.infer<typeof monitorAdjustmentSchema>;
export type MonitorVisit = z.infer<typeof monitorVisitSchema>;
export type MonitorStaffRow = z.infer<typeof monitorStaffRowSchema>;
export type MonitorCourseTag = z.infer<typeof monitorCourseTagSchema>;
export type MonitorDayOverride = z.infer<typeof monitorDayOverrideSchema>;
export type MonitorOffice = z.infer<typeof monitorOfficeSchema>;
export type MonitorThresholds = z.infer<typeof monitorThresholdsSchema>;
export type MonitorResponse = z.infer<typeof monitorResponseSchema>;
export type NearbyPatient = z.infer<typeof nearbyPatientSchema>;
export type NearbyResponse = z.infer<typeof nearbyResponseSchema>;
