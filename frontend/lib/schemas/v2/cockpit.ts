/**
 * 「今週の運転席」(Phase E) の zod スキーマ — 契約書 `docs/plans/week-cockpit-design.md` §2。
 *
 * 対応 API:
 *   POST /api/v1/schedule/v2/substitute-candidates            (§2-1 BE-1)
 *   POST /api/v1/schedule/v2/visit-cancel-week                (§2-2 BE-3)
 *   POST /api/v1/staff/{staff_id}/events/{event_id}/cancel-week (§2-3 BE-3)
 *   POST /api/v1/integrations/unsent-summary                  (§2-4 BE-2)
 *   POST /api/v1/integrations/correction-sheets/{id}/reverse   (§2-5 BE-2)
 *
 * BE のフィールドは snake_case (既存 `lib/schemas/integration.ts` の
 * CorrectionItemRead と同じ流儀) で、そのまま写している。
 */
import { z } from 'zod';

import { CorrectionItemReadSchema } from '@/lib/schemas/integration';
import { eventReadSchema } from '@/lib/schemas/staff-events';

const isoDateSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, '日付は YYYY-MM-DD 形式です');
const hhmmSchema = z.string().regex(/^\d{2}:\d{2}(:\d{2})?$/, '時刻は HH:MM 形式です');

// ───────────────────────────────────────────────────────────────────────────
// §2-1 代替候補 (急な休みの付け替え)
// ───────────────────────────────────────────────────────────────────────────

/** 候補が ◎/△/× になった理由コード (契約書 §2-1)。 */
export const SUBSTITUTE_REASON_CODES = [
  'off',
  'ng_staff',
  'gender',
  'trainee',
  'office',
  'event_overlap',
  'time_overlap',
  'not_working_day',
  // 同行 (メンター) で拘束されている (2026-08-22 レビュー追補・BE と同期)。
  'accompaniment',
] as const;
export type SubstituteReasonCode = (typeof SUBSTITUTE_REASON_CODES)[number];

/** 未知コードでも落とさない (BE 先行追加への寛容パース)。 */
export const substituteReasonSchema = z.object({
  code: z.string(),
  message: z.string(),
  visit_id: z.string().uuid().nullable().optional(),
});
export type SubstituteReason = z.infer<typeof substituteReasonSchema>;

/** ◎=ok / △=warn / ×=ng。 */
export const SUBSTITUTE_STATUS_VALUES = ['ok', 'warn', 'ng'] as const;
export const substituteStatusSchema = z.enum(SUBSTITUTE_STATUS_VALUES);
export type SubstituteStatus = z.infer<typeof substituteStatusSchema>;

/** 状態 → 盤面表示の記号 (モックの `.g` バッジと同じ)。 */
export const SUBSTITUTE_STATUS_MARK: Record<SubstituteStatus, string> = {
  ok: '◎',
  warn: '△',
  ng: '×',
};

export const substituteCandidateSchema = z.object({
  staff_id: z.string().uuid(),
  name: z.string(),
  // 契約は male|female|null。BE は str|None のため寛容に受ける (表示に使わない)。
  sex: z.string().nullable().optional(),
  office_name: z.string().nullable().optional(),
  status: substituteStatusSchema,
  reasons: z.array(substituteReasonSchema).default([]),
  /** 高いほど推奨 (継続性+/移動-/負荷-)。 */
  score: z.number(),
  /** その日の既存訪問数。 */
  load_today: z.number().int(),
});
export type SubstituteCandidate = z.infer<typeof substituteCandidateSchema>;

export const substituteGroupVisitSchema = z.object({
  visit_id: z.string().uuid(),
  patient_id: z.string().uuid(),
  patient_name: z.string(),
  start_time: hhmmSchema,
  end_time: hhmmSchema,
  week_pinned: z.boolean().default(false),
});
export type SubstituteGroupVisit = z.infer<typeof substituteGroupVisitSchema>;

/** course ごとの束 (course_id=null は臨時/未所属をまとめたもの)。 */
export const substituteGroupSchema = z.object({
  course_id: z.string().uuid().nullable(),
  course_label: z.string(),
  visits: z.array(substituteGroupVisitSchema).default([]),
  candidates: z.array(substituteCandidateSchema).default([]),
});
export type SubstituteGroup = z.infer<typeof substituteGroupSchema>;

export const substituteCandidatesRequestSchema = z.object({
  staff_id: z.string().uuid(),
  date: isoDateSchema,
  course_id: z.string().uuid().nullable().optional(),
});
export type SubstituteCandidatesRequest = z.input<typeof substituteCandidatesRequestSchema>;

export const substituteCandidatesReadSchema = z.object({
  absent_staff: z.object({ id: z.string().uuid(), name: z.string() }),
  date: isoDateSchema,
  /** 0=月 .. 5=土。 */
  weekday: z.number().int().min(0).max(6),
  groups: z.array(substituteGroupSchema).default([]),
  warnings: z.array(z.string()).default([]),
});
export type SubstituteCandidatesRead = z.infer<typeof substituteCandidatesReadSchema>;

/** SubstitutePanel → 親へ渡す付け替え指示 (契約書 D8: 実行は既存 API)。 */
export interface SubstituteApplyPayload {
  /** 付け替え先。null = （担当なし）へ戻す。 */
  toStaffId: string | null;
  groups: { courseId: string | null; visitIds: string[] }[];
}

// ───────────────────────────────────────────────────────────────────────────
// §2-2 今週だけ取消 (訪問)
// ───────────────────────────────────────────────────────────────────────────

export const visitCancelWeekRequestSchema = z.object({
  visit_id: z.string().uuid(),
  /** true=planned→cancelled / false=cancelled→planned。 */
  cancel: z.boolean(),
  reason: z.string().max(200).nullable().optional(),
  /** Wave U-3: 1 ユーザー操作 = 1 UUID (ツールバー「戻る」で 1 手として扱う)。 */
  op_group_id: z.string().uuid().nullish(),
});
export type VisitCancelWeekRequest = z.input<typeof visitCancelWeekRequestSchema>;

/**
 * res=VisitRead。運転席が読むのは最小限のため寛容に受ける
 * (BE の VisitRead は項目が多く、増減で FE を壊さない)。
 */
export const visitCancelWeekResponseSchema = z
  .object({
    id: z.string().uuid(),
    status: z.string(),
  })
  .passthrough();
export type VisitCancelWeekResponse = z.infer<typeof visitCancelWeekResponseSchema>;

// ───────────────────────────────────────────────────────────────────────────
// §2-3 固定イベントを今週だけ外す (staff_events.cancelled_at)
// ───────────────────────────────────────────────────────────────────────────

/**
 * EventRead 拡張 (BE `schemas/staff_events.py:EventRead` と同期):
 * `cancelled_at`(今週だけ除外) / `source`(manual|fixed|kaipoke) / `external_id`。
 */
export const cockpitEventReadSchema = eventReadSchema.extend({
  /** 非 null = 今週だけ外されている (行は残るので展開で再生成されない)。 */
  cancelled_at: z.string().nullable().optional(),
  /**
   * 出所。'fixed' = 固定イベント(朝会など・型由来)で「全員（固定）」帯が絞り込む。
   * 旧 BE 互換の既定は 'manual' (DB の default と同じ)。
   */
  source: z.string().default('manual'),
  /**
   * カイポケ個別業務の複合キー ("{業務ID}:{職員内部ID}:{YYYY-MM-DD}") /
   * 固定イベント展開キー ("{default_id}:{YYYY-MM-DD}")。未同期は null。
   */
  external_id: z.string().nullable().optional(),
});
export type CockpitEventRead = z.infer<typeof cockpitEventReadSchema>;

export const eventCancelWeekRequestSchema = z.object({
  staff_id: z.string().uuid(),
  event_id: z.string().uuid(),
  cancel: z.boolean(),
});
export type EventCancelWeekRequest = z.input<typeof eventCancelWeekRequestSchema>;

// ───────────────────────────────────────────────────────────────────────────
// §2-4 ●未送信サマリ (RPA を呼ばない・保存済み CSV との差分)
// ───────────────────────────────────────────────────────────────────────────

/**
 * CorrectionItemRead + `date_iso`(当週の実日付) + `rpa_unsupported`。
 *
 * `rpa_unsupported` = らく助では作れるが RPA がカイポケへ正しく登録できない行
 * (S3 完了まで・kaipoke-service-content-design.md §3)。**判定は BE が持つ** ―
 * FE がサービス内容の文字列を自前で見ると、S3 で門を開けたときに片方だけ
 * 古いルールで止め続ける。旧 BE 応答では欠けるので既定 false。
 */
export const cockpitCorrectionItemSchema = CorrectionItemReadSchema.extend({
  date_iso: z.string().nullable().optional(),
  rpa_unsupported: z.boolean().default(false),
});
export type CockpitCorrectionItem = z.infer<typeof cockpitCorrectionItemSchema>;

export const unsentEventSchema = z.object({
  id: z.string().uuid(),
  staff_id: z.string().uuid(),
  staff_name: z.string(),
  date: isoDateSchema,
  start_time: hhmmSchema,
  end_time: hhmmSchema,
  title: z.string(),
  /** add=カイポケへ登録 / delete=カイポケから削除。 */
  kind: z.enum(['add', 'delete']),
});
export type UnsentEvent = z.infer<typeof unsentEventSchema>;

export const unsentSnapshotSchema = z.object({
  fetched_at: z.string().nullable(),
  month: z.string(),
  row_count: z.number().int(),
});
export type UnsentSnapshot = z.infer<typeof unsentSnapshotSchema>;

export const unsentSummaryRequestSchema = z.object({
  week_start: isoDateSchema,
});
export type UnsentSummaryRequest = z.input<typeof unsentSummaryRequestSchema>;

export const unsentSummaryReadSchema = z.object({
  week_start: isoDateSchema,
  /** null = 保存済み CSV が無い → 「🔄突合でカイポケ現況を取得してください」。 */
  snapshot: unsentSnapshotSchema.nullable(),
  /** 生成した outbound シート (direction='outbound', origin='cached')。 */
  sheet_id: z.string().uuid().nullable(),
  items: z.array(cockpitCorrectionItemSchema).default([]),
  events: z.array(unsentEventSchema).default([]),
  sendable_count: z.number().int(),
  /** 当日以前 = 実績保護のため送信対象外。 */
  past_count: z.number().int(),
  /**
   * RPA 未対応 (准看/一般) で送れない件数。**過去日とは二重に数えない**ので
   * `sendable = items+events - past_count - rpa_unsupported_count` が成立する。
   * 旧 BE 応答では欠けるので既定 0。
   */
  rpa_unsupported_count: z.number().int().default(0),
});
export type UnsentSummaryRead = z.infer<typeof unsentSummaryReadSchema>;

// ───────────────────────────────────────────────────────────────────────────
// §2-5 ⇧上書き (inbound シートを反転して outbound シートを作る)
// ───────────────────────────────────────────────────────────────────────────

export const reverseSheetRequestSchema = z.object({
  sheet_id: z.string().uuid(),
});
export type ReverseSheetRequest = z.input<typeof reverseSheetRequestSchema>;

export const reverseSheetResponseSchema = z.object({
  sheet_id: z.string().uuid(),
  item_count: z.number().int(),
});
export type ReverseSheetResponse = z.infer<typeof reverseSheetResponseSchema>;
