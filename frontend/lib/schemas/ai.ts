/**
 * AI interpret zod schemas — D4 Phase E / Wave 4-B.
 *
 * Mirrors backend `app/schemas/ai.py`. Used by `useInterpret()` and the
 * `<AiInputModal />` to validate Gemini's structured response before we
 * render it to the operator.
 */
import { z } from 'zod';

export const AI_CONTEXT_TYPES = [
  'patient_create',
  'event_create',
  'override_create',
  'general',
] as const;

export const AiContextTypeSchema = z.enum(AI_CONTEXT_TYPES);
export type AiContextType = z.infer<typeof AiContextTypeSchema>;

export const AI_CONTEXT_LABELS: Record<AiContextType, string> = {
  patient_create: '患者新規',
  event_create: 'スタッフイベント新規',
  override_create: 'スタッフ休み新規',
  general: '汎用',
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
