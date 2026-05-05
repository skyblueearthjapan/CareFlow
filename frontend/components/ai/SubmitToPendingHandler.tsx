'use client';

/**
 * AI 解釈→申請履歴フロー — Wave 5 W5-FE11.
 *
 * 設計仕様書 v0.9 §3.5 / §3.5.3 に従い、`<AiInputModal />` の解釈結果を
 * `pending_requests` API に送信するためのラッパーコンポーネント。
 *
 * デバイス・ロールに応じた反映方式（§3.5.3 の表）:
 *
 *   | ロール          | デバイス | 反映方式          | pending_requests |
 *   |---------------- |---------|------------------|------------------|
 *   | admin / manager | PC      | **即時**          | `approved` で記録 |
 *   | admin / manager | モバイル | 履歴経由（即時禁止）| `pending`         |
 *   | staff           | 全て    | 履歴経由（申請）   | `pending`         |
 *
 * 「PC admin/manager の即時反映」は、Backend の POST `/api/v1/pending-requests`
 * が常に `pending` 状態で作成する都合上、**POST → 直後に approve** の 2 ステップで
 * 実現する（§3.5.3 の監査要件「業務反映と同一トランザクションで処理」は applier 側
 * で担保される）。
 *
 * Staff の自分軸チェックは Backend (`_enforce_staff_self_scope`) が 403 で防ぐため、
 * 本コンポーネントでは UI 上の事前ガードは行わない（Backend を Single Source of
 * Truth とする）。
 *
 * ファイル所有: 本ファイルは W5-FE11 が新規所有。`AiInputModal.tsx` 等の所有外
 * ファイルには触れず、W5-FE10 が提供する `onSubmitInterceptor` / `submissionMode`
 * / `voiceFirst` の拡張ポイント経由で統合する。
 */
import { useCallback } from 'react';
import { useSession } from 'next-auth/react';
import { toast } from 'sonner';

import { AiInputModal } from '@/components/AiInputModal';
import { useApproveRequest, useCreatePendingRequest } from '@/lib/queries/pending_requests';
import type { InterpretResponse, InterpretedAction } from '@/lib/schemas/ai';
import type {
  PendingRequestV2Create,
  RequestScope,
  RequestType,
} from '@/lib/schemas/pending_request';

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface SubmitToPendingHandlerProps {
  /**
   * モバイルレイアウトかどうか。
   *
   * `true` の場合:
   *   - `<AiInputModal voiceFirst />` を強制（音声入力主体 UI）
   *   - admin / manager でも即時反映を行わず、`pending` で申請として残す
   *     (§3.5.1 「モバイル入力は即時反映禁止」)
   */
  isMobile?: boolean;
}

// ─────────────────────────────────────────────────────────────────────────
// action_type → request_type マッピング
// ─────────────────────────────────────────────────────────────────────────

/**
 * Gemini が返す `action_type` (`gemini_client.py::_CONTEXT_PROMPT_TEMPLATES`) と
 * `pending_requests.request_type` (`schemas/v2/enums.py::REQUEST_TYPE_VALUES`)
 * の対応表。
 *
 * `patient_special_week` のみ on/off の二系統に分岐するため、本テーブルでは
 * `patient_special_week_on` を既定とし、`fields.mode === 'off'` のときに
 * `patient_special_week_off` へ切り替える（{@link resolveRequestType} 参照）。
 */
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
 * 1. `patient_special_week` のとき:
 *    - `fields.mode === 'off'` → `patient_special_week_off`
 *    - それ以外 (省略 or 'on')  → `patient_special_week_on`
 * 2. それ以外は {@link ACTION_TYPE_TO_REQUEST_TYPE} を引く。
 *
 * 未知 / `out_of_scope` / `unknown` の場合は `null` を返す（呼び出し元で
 * トースト表示のうえ送信を中断する）。
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
// payload helpers
// ─────────────────────────────────────────────────────────────────────────

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISO_DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

/** 値が UUID 文字列なら返す。違えば undefined（API 側の uuid バリデーション失敗を防ぐ）。 */
function pickUuid(value: unknown): string | undefined {
  return typeof value === 'string' && UUID_REGEX.test(value) ? value : undefined;
}

/** 値が `YYYY-MM-DD` なら返す。違えば undefined。 */
function pickIsoDate(value: unknown): string | undefined {
  return typeof value === 'string' && ISO_DATE_REGEX.test(value) ? value : undefined;
}

/**
 * `patient_reschedule` のとき必須となる `scope` を fields から拾う (§3.5.6).
 *
 * 値が 'one_time' / 'permanent' のみ採用。それ以外は undefined を返し、
 * Backend の 422 で気付かせる（フロントで暗黙補完しない）。
 */
function pickScope(value: unknown): RequestScope | undefined {
  if (value === 'one_time' || value === 'permanent') return value;
  return undefined;
}

/**
 * AI 解釈結果から `PendingRequestV2Create` を組み立てる。
 *
 * 設計指針:
 *   - `payload` には interpreter が返した `fields` をそのまま格納する
 *     （applier 側が読み取るキー名を尊重し、勝手な変換を入れない）
 *   - `target_staff_id` / `target_patient_id` / `target_date` / `scope` は
 *     fields から「型が合うものだけ」抜き出す。型不一致は payload 内に残し、
 *     applier の `_coerce_uuid` / `_coerce_date` 側に委ねる
 *   - `ai_interpret_log_id` で AI 解釈ログを参照（監査要件 §3.5）
 */
function buildCreatePayload(
  result: InterpretResponse,
  action: InterpretedAction,
  requestType: RequestType,
): PendingRequestV2Create {
  const fields = action.fields as Record<string, unknown>;

  const targetStaffId =
    pickUuid(fields.target_staff_id) ??
    pickUuid(fields.staff_id) ??
    pickUuid(fields.mentor_staff_id);
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
// Component
// ─────────────────────────────────────────────────────────────────────────

/**
 * `<AiInputModal />` をラップし、解釈結果を `pending_requests` に送信するハンドラ。
 *
 * 単独 mount で動作するので、AppShell やモバイルレイアウト側で
 * `<AiFab />` などのトリガーと一緒に置けばよい（モーダルの open/close は
 * `useUIStore.aiInputOpen` 経由で内部管理されている）。
 *
 * @example
 * ```tsx
 * // PC: AppShell で 1 回だけ mount
 * <SubmitToPendingHandler />
 *
 * // モバイル: 音声主体 UI で
 * <SubmitToPendingHandler isMobile />
 * ```
 */
export function SubmitToPendingHandler({ isMobile = false }: SubmitToPendingHandlerProps = {}) {
  const { data: session } = useSession();
  const role = session?.user?.role ?? null;
  const create = useCreatePendingRequest();
  const approve = useApproveRequest();

  /**
   * 即時反映するか (PC × admin/manager のみ).
   *
   * §3.5.1: モバイル入力はロール問わず即時反映禁止。
   * §3.5.3: PC でも staff は申請扱い（自分軸チェック後に admin/manager が承認）。
   */
  const shouldAutoApprove = !isMobile && (role === 'admin' || role === 'manager');

  const handleSubmit = useCallback(
    async (result: InterpretResponse) => {
      const action = result.interpreted.actions?.[0];
      if (!action) {
        toast.error('AI 解釈にアクションが含まれていません');
        return;
      }

      const requestType = resolveRequestType(action);
      if (!requestType) {
        // out_of_scope は AiInputModal 側で別 UI に切替済みなので通常ここには来ない。
        // unknown / 未対応 action_type のフォールバック。
        toast.error(`未対応の操作です: ${action.action_type}`);
        return;
      }

      const body = buildCreatePayload(result, action, requestType);

      // 1. pending として申請を作成（AI 経由は全件履歴に残す — §3.5.3 監査要件）
      const created = await create.mutateAsync(body);

      // 2. PC admin/manager のみ approve を続けて呼び、即時反映 + approved 履歴へ更新。
      //    approve は applier が業務反映と status 更新を **同一 TX** で処理するため、
      //    ここでは結果を待つだけで原子性が確保される (§3.5.3).
      if (shouldAutoApprove) {
        try {
          await approve.mutateAsync(created.id);
          toast.success('AI 入力を反映しました（履歴に承認済みで記録）');
        } catch (e) {
          // approve 失敗時: 申請レコードは pending で残るので運用上は手動承認できる。
          toast.warning(
            `申請は作成されました。承認に失敗したため履歴から手動で承認してください: ${(e as Error).message}`,
          );
        }
        return;
      }

      toast.success('申請を送信しました（管理者の承認をお待ちください）');
    },
    [create, approve, shouldAutoApprove],
  );

  return (
    <AiInputModal
      submissionMode="pending_request"
      voiceFirst={isMobile}
      onSubmitInterceptor={handleSubmit}
    />
  );
}
