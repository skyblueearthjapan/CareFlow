/**
 * AI interpret zod schemas — D4 Phase E / Wave 4-B.
 *
 * Mirrors backend `app/schemas/ai.py`. Used by `useInterpret()` and the
 * `<AiInputModal />` to validate Gemini's structured response before we
 * render it to the operator.
 */
import { z } from 'zod';

// Wave 11 で v2 (10 種類) に揃える。BE `AiContextType` (v2/enums.py) と完全一致。
// 旧 `event_create` / `override_create` は v2 で `staff_event` / `staff_off` に置換。
export const AI_CONTEXT_TYPES = [
  'staff_off',
  'staff_event',
  'staff_mentor',
  'staff_create',
  'patient_create',
  'patient_cancel',
  'patient_reschedule',
  'patient_special_week',
  'staff_status_update',
  'patient_status_update',
  'general',
  'out_of_scope',
] as const;

export const AiContextTypeSchema = z.enum(AI_CONTEXT_TYPES);
export type AiContextType = z.infer<typeof AiContextTypeSchema>;

export const AI_CONTEXT_LABELS: Record<AiContextType, string> = {
  staff_off: 'スタッフ休み (その週だけ)',
  staff_event: 'スタッフイベント (会議・研修)',
  staff_mentor: '新人フラグ + 同行スタッフ割付',
  staff_create: 'スタッフ新規登録',
  patient_create: '患者新規登録',
  patient_cancel: '患者訪問キャンセル',
  patient_reschedule: '患者訪問日時変更',
  patient_special_week: '特別訪問週間 ON/OFF',
  staff_status_update: 'スタッフ状態変更 (在籍/休職/退職)',
  patient_status_update: '患者状態変更 (稼働中/一時休止/入院中/開始前/解約済み)',
  general: '汎用 (自動判定)',
  out_of_scope: '範囲外',
};

export const InterpretRequestSchema = z.object({
  prompt: z.string().min(1, '入力してください').max(4000),
  context_type: AiContextTypeSchema.default('general'),
  context: z.record(z.unknown()).default({}),
});
export type InterpretRequest = z.infer<typeof InterpretRequestSchema>;

/** Single action returned inside `interpreted.actions[]`. The fields object
 * is intentionally left as a free-form record because each action_type has
 * a different shape (see design 09-9 / D4 plan §6). */
export const InterpretedActionSchema = z.object({
  action_type: z.string(),
  confidence: z.number().min(0).max(1).default(0),
  fields: z.record(z.unknown()).default({}),
});
export type InterpretedAction = z.infer<typeof InterpretedActionSchema>;

export const InterpretedPayloadSchema = z
  .object({
    actions: z.array(InterpretedActionSchema).default([]),
  })
  .passthrough();
export type InterpretedPayload = z.infer<typeof InterpretedPayloadSchema>;

export const InterpretResponseSchema = z.object({
  interpreted: InterpretedPayloadSchema,
  confidence: z.number().min(0).max(1),
  raw_response: z.string(),
  log_id: z.string().uuid(),
  model: z.string(),
  latency_ms: z.number().int(),
  cost_usd: z.number(),
  context_type: AiContextTypeSchema,
});
export type InterpretResponse = z.infer<typeof InterpretResponseSchema>;

export const AiLogReadSchema = z.object({
  id: z.string().uuid(),
  prompt: z.string(),
  response: z.record(z.unknown()).default({}),
  model: z.string(),
  latency_ms: z.number().int(),
  user_id: z.string().uuid().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  context_type: z.string().nullable().optional(),
  cost_usd: z.number().nullable().optional(),
  confidence: z.number().nullable().optional(),
});
export type AiLogRead = z.infer<typeof AiLogReadSchema>;
