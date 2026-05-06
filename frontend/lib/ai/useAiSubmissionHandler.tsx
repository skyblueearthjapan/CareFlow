'use client';

/**
 * AI 申請ハンドラ hook — W7-FE1 (Codex Must-fix #6).
 *
 * 設計仕様書 v0.9 §3.5 / §3.5.2 / §3.5.3 に従い、AI 解釈結果を
 * `pending_requests` API に送信するための共通 hook。
 *
 * 目的:
 *   W5-FE11 で作成した `<SubmitToPendingHandler />` のロジックを hook として
 *   切り出し、`AiFab` / `MobileAiFab` / `/help/ai` 等の各マウント箇所から
 *   共通利用できるようにする。
 *
 * RBAC マッピング (§3.5.2):
 *
 *   | context_type              | admin/manager  | staff             |
 *   |---------------------------|----------------|-------------------|
 *   | staff_off                 | pending        | pending           |
 *   | staff_event               | pending        | pending           |
 *   | staff_mentor              | pending        | out_of_scope      |
 *   | staff_create              | pending        | out_of_scope      |
 *   | patient_create            | pending        | out_of_scope      |
 *   | patient_cancel            | pending        | pending           |
 *   | patient_reschedule        | pending        | pending           |
 *   | patient_special_week_on   | pending        | out_of_scope      |
 *   | patient_special_week_off  | pending        | out_of_scope      |
 *   | out_of_scope              | out_of_scope   | out_of_scope      |
 */

import { useCallback, useState, type ReactNode } from 'react';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { MissingInfoModal } from '@/components/ai/MissingInfoModal';
import {
  useCreateAndApplyPendingRequest,
  useCreatePendingRequest,
} from '@/lib/queries/pending_requests';
import type { InterpretResponse, InterpretedAction } from '@/lib/schemas/ai';
import type {
  PendingRequestV2Create,
  RequestScope,
  RequestType,
} from '@/lib/schemas/pending_request';
import type { AppRole } from '@/types/auth';

// ─────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────

export type AiSubmissionHandlerMode = 'pending' | 'direct' | 'out_of_scope';

export interface UseAiSubmissionHandlerOptions {
  /** モバイルレイアウトかどうか。true の場合は admin/manager でも即時反映しない。 */
  isMobile?: boolean;
}

export interface UseAiSubmissionHandlerResult {
  /**
   * `AiInputModal` の `onSubmitInterceptor` に渡す関数。
   * context_type と role に応じて pending POST / out_of_scope を分岐する。
   */
  onSubmitInterceptor: (result: InterpretResponse) => Promise<void>;
  /**
   * `AiInputModal` の `missingInfoSlot` に渡す ReactNode。
   * `missing_fields` が存在する場合に `MissingInfoModal` を mount する。
   */
  missingInfoSlot: ReactNode;
  /** 現在の送信モード（prop として AiInputModal に渡す）。 */
  submissionMode: 'pending_request';
}

// ─────────────────────────────────────────────────────────────────────────
// RBAC mapping
// ─────────────────────────────────────────────────────────────────────────

/**
 * staff ロールが pending を送信できる action_type の許可リスト。
 *
 * §3.5.2 の RBAC テーブルに従い、staff は以下の 4 種類のみ許可。
 * それ以外の action_type は out_of_scope として扱う。
 */
const STAFF_ALLOWED_ACTION_TYPES = new Set<string>([
  'staff_off',
  'staff_event',
  'patient_cancel',
  'patient_reschedule',
]);

/**
 * ロールと action_type から送信モードを決定する。
 *
 * @returns 'pending'       — pending_requests に POST する
 * @returns 'out_of_scope'  — 権限なし / 範囲外として表示する
 */
function resolveMode(
  role: AppRole | null | undefined,
  actionType: string,
): 'pending' | 'out_of_scope' {
  if (!actionType || actionType === 'out_of_scope') return 'out_of_scope';

  if (role === 'admin' || role === 'manager') {
    // admin / manager は out_of_scope 以外すべて pending
    return 'pending';
  }

  // staff: 許可リストに含まれるもののみ pending、それ以外は out_of_scope
  return STAFF_ALLOWED_ACTION_TYPES.has(actionType) ? 'pending' : 'out_of_scope';
}

// ─────────────────────────────────────────────────────────────────────────
// action_type → request_type マッピング
// ─────────────────────────────────────────────────────────────────────────

const ACTION_TYPE_TO_REQUEST_TYPE: Record<string, RequestType> = {
  patient_create: 'patient_create',
  staff_create: 'staff_create',
  staff_event: 'staff_event',
  staff_off: 'staff_off',
  staff_mentor: 'staff_mentor',
  patient_cancel: 'patient_cancel',
  patient_reschedule: 'patient_reschedule',
  // patient_special_week は payload.mode で on/off を分岐する。
  patient_special_week: 'patient_special_week_on',
};

/**
 * action / fields から `request_type` を最終決定する。
 *
 * `patient_special_week` のとき `fields.mode === 'off'` なら `patient_special_week_off`、
 * それ以外は `patient_special_week_on`。
 * 未知 / `out_of_scope` の場合は `null` を返す。
 */
function resolveRequestType(action: InterpretedAction): RequestType | null {
  const actionType = action.action_type;
  if (actionType === 'patient_special_week') {
    const mode = (action.fields as Record<string, unknown>).mode;
    return mode === 'off' ? 'patient_special_week_off' : 'patient_special_week_on';
  }
  return ACTION_TYPE_TO_REQUEST_TYPE[actionType] ?? null;
}

// ─────────────────────────────────────────────────────────────────────────
// payload builders
// ─────────────────────────────────────────────────────────────────────────

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISO_DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

function pickUuid(value: unknown): string | undefined {
  return typeof value === 'string' && UUID_REGEX.test(value) ? value : undefined;
}

function pickIsoDate(value: unknown): string | undefined {
  return typeof value === 'string' && ISO_DATE_REGEX.test(value) ? value : undefined;
}

function pickScope(value: unknown): RequestScope | undefined {
  if (value === 'one_time' || value === 'permanent') return value;
  return undefined;
}

function buildCreatePayload(
  result: InterpretResponse,
  action: InterpretedAction,
  requestType: RequestType,
): PendingRequestV2Create {
  const fields = action.fields as Record<string, unknown>;

  const targetStaffId = pickUuid(fields.target_staff_id) ?? pickUuid(fields.staff_id);
  const targetPatientId = pickUuid(fields.target_patient_id) ?? pickUuid(fields.patient_id);
  const targetDate =
    pickIsoDate(fields.target_date) ?? pickIsoDate(fields.visit_date) ?? pickIsoDate(fields.date);
  const scope = requestType === 'patient_reschedule' ? pickScope(fields.scope) : undefined;

  return {
    request_type: requestType,
    payload: fields,
    target_staff_id: targetStaffId ?? null,
    target_patient_id: targetPatientId ?? null,
    target_date: targetDate ?? null,
    scope: scope ?? null,
    ai_interpret_log_id: result.log_id,
  };
}

// ─────────────────────────────────────────────────────────────────────────
// Hook
// ─────────────────────────────────────────────────────────────────────────

/**
 * AI 解釈結果を `pending_requests` に送信するための共通ロジックを提供する hook。
 *
 * `MobileAiFab` / `AiFab` / `/help/ai` 等の各マウント箇所で利用し、
 * 戻り値を `<AiInputModal>` の props に渡す。
 *
 * @example
 * ```tsx
 * function MyComponent() {
 *   const { onSubmitInterceptor, missingInfoSlot, submissionMode } =
 *     useAiSubmissionHandler({ isMobile: false });
 *
 *   return (
 *     <AiInputModal
 *       submissionMode={submissionMode}
 *       onSubmitInterceptor={onSubmitInterceptor}
 *       missingInfoSlot={missingInfoSlot}
 *     />
 *   );
 * }
 * ```
 */
export function useAiSubmissionHandler({
  isMobile = false,
}: UseAiSubmissionHandlerOptions = {}): UseAiSubmissionHandlerResult {
  const { data: session } = useSession();
  const role = session?.user?.role ?? null;
  const create = useCreatePendingRequest();
  const createAndApply = useCreateAndApplyPendingRequest();

  // MissingInfoModal の open 状態と、補完待ちのデータを管理する。
  const [missingInfoOpen, setMissingInfoOpen] = useState(false);
  const [missingFields, setMissingFields] = useState<string[]>([]);
  const [pendingPartialData, setPendingPartialData] = useState<Record<string, unknown>>({});
  // 補完完了後の送信に必要なコンテキストを保持する。
  const [pendingContext, setPendingContext] = useState<{
    result: InterpretResponse;
    requestType: RequestType;
  } | null>(null);

  /**
   * 即時承認するか (PC × admin/manager のみ).
   *
   * §3.5.1: モバイル入力はロール問わず即時反映禁止。
   * §3.5.3: PC でも staff は申請扱い（管理者の承認待ち）。
   */
  const shouldAutoApprove = !isMobile && (role === 'admin' || role === 'manager');

  /**
   * pending_requests に POST する。
   *
   * PC admin/manager の場合は `create-and-apply` (単一 TX) を使い、申請作成と業務反映を
   * アトミックに実行する (W8-FE1 Codex 再レビュー Must-fix #1)。
   * モバイル全員 + PC staff の場合は通常の create のみ。承認は別フロー。
   */
  const postRequest = useCallback(
    async (
      result: InterpretResponse,
      requestType: RequestType,
      fields: Record<string, unknown>,
    ) => {
      const action: InterpretedAction = {
        action_type: result.interpreted.actions?.[0]?.action_type ?? '',
        confidence: result.interpreted.actions?.[0]?.confidence ?? 0,
        fields,
      };

      const body = buildCreatePayload(result, action, requestType);

      if (shouldAutoApprove) {
        // PC admin/manager: 単一 TX で create + apply (applier 失敗時に申請レコードが
        // 残る不整合を防ぐ — W7-BE3 POST /pending-requests/create-and-apply)。
        await createAndApply.mutateAsync(body);
        toast.success(`AI 反映完了 (即時): ${requestType}`);
      } else {
        // モバイル全員 + PC staff: 通常の create のみ (approve は別フロー)。
        await create.mutateAsync(body);
        toast.success(`AI 申請を提出しました: ${requestType}`);
      }
    },
    [create, createAndApply, shouldAutoApprove],
  );

  /**
   * `AiInputModal` の `onSubmitInterceptor` として渡す関数。
   *
   * フロー:
   *   1. AI 解釈結果の action_type + role から送信モードを決定
   *   2. out_of_scope なら toast でガイドし終了
   *   3. missing_fields が存在する場合は MissingInfoModal を開いて補完を促す
   *   4. 補完完了 or missing なしの場合は pending POST を実行
   */
  const onSubmitInterceptor = useCallback(
    async (result: InterpretResponse) => {
      const action = result.interpreted.actions?.[0];
      if (!action) {
        toast.error('AI 解釈にアクションが含まれていません');
        return;
      }

      const mode = resolveMode(role, action.action_type);

      if (mode === 'out_of_scope') {
        // staff が権限外の操作をしようとした場合
        if (role === 'staff') {
          toast.error(
            `この操作はスタッフ権限では実行できません: ${action.action_type}。管理者にお問い合わせください。`,
          );
        } else {
          toast.error(`未対応の操作です: ${action.action_type}`);
        }
        return;
      }

      const requestType = resolveRequestType(action);
      if (!requestType) {
        toast.error(`未対応の操作タイプです: ${action.action_type}`);
        return;
      }

      const fields = action.fields as Record<string, unknown>;

      // missing_fields がある場合は MissingInfoModal を開く
      const missingFieldsFromResult = result.interpreted as unknown as {
        missing_fields?: string[];
      };
      const mf = missingFieldsFromResult.missing_fields ?? [];
      if (mf.length > 0) {
        setMissingFields(mf);
        setPendingPartialData(fields);
        setPendingContext({ result, requestType });
        setMissingInfoOpen(true);
        // MissingInfoModal の onComplete 経由で送信するので、ここでは return する
        return;
      }

      // missing_fields なし: 直接送信
      await postRequest(result, requestType, fields);
    },
    [role, postRequest],
  );

  /**
   * MissingInfoModal での補完完了時のハンドラ。
   * partialData とユーザー入力をマージして pending POST を実行する。
   */
  const handleMissingInfoComplete = useCallback(
    async (completed: Record<string, unknown>) => {
      if (!pendingContext) return;
      try {
        await postRequest(pendingContext.result, pendingContext.requestType, completed);
      } catch (e) {
        toast.error(`AI 申請の提出に失敗しました: ${(e as Error).message}`);
      } finally {
        setPendingContext(null);
        setMissingFields([]);
        setPendingPartialData({});
      }
    },
    [pendingContext, postRequest],
  );

  const missingInfoSlot: ReactNode =
    missingFields.length > 0 ? (
      <MissingInfoModal
        open={missingInfoOpen}
        onOpenChange={setMissingInfoOpen}
        missingFields={missingFields}
        partialData={pendingPartialData}
        onComplete={(data) => {
          void handleMissingInfoComplete(data);
        }}
      />
    ) : null;

  return {
    onSubmitInterceptor,
    missingInfoSlot,
    submissionMode: 'pending_request',
  };
}
