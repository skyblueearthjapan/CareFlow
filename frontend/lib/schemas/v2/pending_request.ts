/**
 * PendingRequestV2 zod schemas (Wave 0-C).
 *
 * 設計仕様書 v0.9 §3.5 / §4.4 に基づく申請履歴 (`pending_requests`) v2。
 * 業務上の承認ワークフロー記録。
 *
 * Backend `backend/app/schemas/v2/pending_request.py` の Pydantic schema と
 * **完全一致** させる。
 */
import { z } from 'zod';
import { requestScopeEnum, requestStatusEnum, requestTypeEnum } from './enums';

/**
 * 申請 (pending_request) v2 (§4.4).
 *
 * `payload` JSONB に request_type ごとの依頼内容を構造化して格納する。
 */
export const pendingRequestV2BaseSchema = z.object({
  request_type: requestTypeEnum,
  payload: z.record(z.unknown()).default({}),

  /** 影響先スタッフ (該当するもの) */
  target_staff_id: z.string().uuid().nullable().optional(),
  /** 影響先患者 (該当するもの) */
  target_patient_id: z.string().uuid().nullable().optional(),
  /** 影響日 (スケジュール画面の日付別フィルタ用) */
  target_date: z.string().nullable().optional(),
  /**
   * 適用範囲. patient_reschedule のときのみ必須 (§3.5.6).
   * それ以外の request_type では null。
   */
  scope: requestScopeEnum.nullable().optional(),
});
export type PendingRequestV2Base = z.infer<typeof pendingRequestV2BaseSchema>;

/**
 * POST /api/v1/pending-requests リクエスト (W2-BE5).
 *
 * 要求者 (`requester_user_id`) はサーバ側で `request.state.user` から自動付与する。
 */
export const pendingRequestV2CreateSchema = pendingRequestV2BaseSchema;
export type PendingRequestV2Create = z.infer<typeof pendingRequestV2CreateSchema>;

/**
 * PATCH /api/v1/pending-requests/{id} 用 (内部利用).
 *
 * 通常はクライアントは approve / reject エンドポイントを使う。
 * 本 schema は applier の内部状態更新用。
 */
export const pendingRequestV2UpdateSchema = z.object({
  payload: z.record(z.unknown()).optional(),
  edited_payload: z.record(z.unknown()).nullable().optional(),
  target_staff_id: z.string().uuid().nullable().optional(),
  target_patient_id: z.string().uuid().nullable().optional(),
  target_date: z.string().nullable().optional(),
  scope: requestScopeEnum.nullable().optional(),
  status: requestStatusEnum.optional(),
  rejection_reason: z.string().nullable().optional(),
});
export type PendingRequestV2Update = z.infer<typeof pendingRequestV2UpdateSchema>;

/** PATCH /api/v1/pending-requests/{id}/approve リクエスト (W2-BE5). */
export const pendingRequestApproveSchema = z.object({
  /**
   * 編集して承認の場合の編集後内容 (§3.5.4).
   * null = 内容そのまま承認。
   */
  edited_payload: z.record(z.unknown()).nullable().optional(),
});
export type PendingRequestApprove = z.infer<typeof pendingRequestApproveSchema>;

/** PATCH /api/v1/pending-requests/{id}/reject リクエスト (W2-BE5). */
export const pendingRequestRejectSchema = z.object({
  /** 却下理由 (§3.5.4 で必須化) */
  rejection_reason: z.string().min(1, '却下理由は必須です').max(2000),
});
export type PendingRequestReject = z.infer<typeof pendingRequestRejectSchema>;

/** GET /api/v1/pending-requests/{id} レスポンス (W2-BE5). */
export const pendingRequestV2ReadSchema = pendingRequestV2BaseSchema.extend({
  id: z.string().uuid(),
  /** 入力者 (§4.4) */
  requester_user_id: z.string().uuid(),
  /** 申請日時 (§4.4) */
  created_at: z.string(),
  updated_at: z.string(),
  status: requestStatusEnum.default('pending'),

  /** 編集して承認した場合の編集後内容 (§4.4) */
  edited_payload: z.record(z.unknown()).nullable().optional(),

  /** 承認者 (§4.4) */
  approved_by: z.string().uuid().nullable().optional(),
  approved_at: z.string().nullable().optional(),

  /** 却下者 (§4.4) */
  rejected_by: z.string().uuid().nullable().optional(),
  rejected_at: z.string().nullable().optional(),
  /** 却下理由 (rejected の場合必須, §4.4) */
  rejection_reason: z.string().nullable().optional(),
});
export type PendingRequestV2Read = z.infer<typeof pendingRequestV2ReadSchema>;
