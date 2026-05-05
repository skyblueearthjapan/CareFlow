/**
 * useAiSubmissionHandler — unit tests (W7-FE1).
 *
 * 3 ケース:
 *   1. patient_create + admin → pending POST が呼ばれる
 *   2. staff_create + staff → out_of_scope に切り替わる（POST は呼ばれない）
 *   3. missing_fields があれば missingInfoSlot が render される
 *
 * テストランナー: vitest + @testing-library/react
 * モック戦略:
 *   - next-auth の useSession をモック（role を注入）
 *   - useCreatePendingRequest / useApproveRequest をモック
 *   - sonner の toast をスパイ
 *
 * NOTE: このファイルは vitest + @testing-library/react を想定している。
 * 現時点でプロジェクトに vitest が追加されていない場合、jest 等に移植する際は
 * `vi.` → `jest.` の置換と、`@testing-library/react` の renderHook 利用方法を
 * 各テストランナーの API に合わせること。
 */

import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { toast } from 'sonner';

// ─── Mocks ──────────────────────────────────────────────────────────────────

// next-auth useSession
const mockSession = vi.fn();
vi.mock('next-auth/react', () => ({
  useSession: () => mockSession(),
}));

// pending_requests query hooks
const mockMutateAsync = vi.fn();
const mockApproveMutateAsync = vi.fn();

vi.mock('@/lib/queries/pending_requests', () => ({
  useCreatePendingRequest: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
  useApproveRequest: () => ({
    mutateAsync: mockApproveMutateAsync,
    isPending: false,
  }),
}));

// sonner toast
vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

import { useAiSubmissionHandler } from '../useAiSubmissionHandler';
import type { InterpretResponse } from '@/lib/schemas/ai';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeInterpretResponse(
  actionType: string,
  fields: Record<string, unknown> = {},
): InterpretResponse {
  return {
    interpreted: {
      actions: [
        {
          action_type: actionType,
          confidence: 0.95,
          fields,
        },
      ],
    },
    confidence: 0.95,
    raw_response: '{}',
    log_id: '00000000-0000-0000-0000-000000000001',
    model: 'gemini-1.5-flash',
    latency_ms: 100,
    cost_usd: 0.001,
    context_type: 'general',
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('useAiSubmissionHandler', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockMutateAsync.mockResolvedValue({ id: 'req-001' });
    mockApproveMutateAsync.mockResolvedValue({ id: 'req-001', status: 'approved' });
  });

  /**
   * ケース 1: patient_create + admin → pending POST が呼ばれる
   *
   * admin ロール + PC (isMobile: false) で patient_create を送信した場合:
   *   - useCreatePendingRequest.mutateAsync が 'patient_create' で呼ばれる
   *   - useApproveRequest.mutateAsync が続いて呼ばれる（即時承認）
   *   - toast.success が呼ばれる
   */
  it('patient_create + admin → pending POST and approve are called', async () => {
    mockSession.mockReturnValue({
      data: { user: { role: 'admin' } },
      status: 'authenticated',
    });

    const { result } = renderHook(() => useAiSubmissionHandler({ isMobile: false }));

    const interpretResult = makeInterpretResponse('patient_create', {
      name: '田中太郎',
    });

    await act(async () => {
      await result.current.onSubmitInterceptor(interpretResult);
    });

    expect(mockMutateAsync).toHaveBeenCalledOnce();
    expect(mockMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ request_type: 'patient_create' }),
    );
    // PC admin → 即時承認 (approve)
    expect(mockApproveMutateAsync).toHaveBeenCalledOnce();
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('patient_create'));
  });

  /**
   * ケース 2: staff_create + staff → out_of_scope に切り替わる
   *
   * staff ロールで staff_create を送信した場合:
   *   - 権限外のため POST は呼ばれない
   *   - toast.error が呼ばれる
   */
  it('staff_create + staff → out_of_scope, POST is NOT called', async () => {
    mockSession.mockReturnValue({
      data: { user: { role: 'staff' } },
      status: 'authenticated',
    });

    const { result } = renderHook(() => useAiSubmissionHandler({ isMobile: false }));

    const interpretResult = makeInterpretResponse('staff_create', {
      name: '佐藤花子',
    });

    await act(async () => {
      await result.current.onSubmitInterceptor(interpretResult);
    });

    expect(mockMutateAsync).not.toHaveBeenCalled();
    expect(mockApproveMutateAsync).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('staff'));
  });

  /**
   * ケース 3: missing_fields があれば missingInfoSlot が render される
   *
   * interpreted に missing_fields が含まれる場合:
   *   - POST はすぐに呼ばれない（補完待ち）
   *   - missingInfoSlot が null でない（MissingInfoModal がマウントされる）
   */
  it('missing_fields present → missingInfoSlot is rendered, POST is deferred', async () => {
    mockSession.mockReturnValue({
      data: { user: { role: 'admin' } },
      status: 'authenticated',
    });

    const { result } = renderHook(() => useAiSubmissionHandler({ isMobile: false }));

    // missing_fields を interpreted に埋め込む
    const interpretResult: InterpretResponse = {
      ...makeInterpretResponse('patient_create', { code: 'P001' }),
      interpreted: {
        actions: [
          {
            action_type: 'patient_create',
            confidence: 0.9,
            fields: { code: 'P001' },
          },
        ],
        // missing_fields は passthrough で zod が許容する
        missing_fields: ['name', 'address'],
      } as InterpretResponse['interpreted'],
    };

    await act(async () => {
      await result.current.onSubmitInterceptor(interpretResult);
    });

    // POST はまだ呼ばれていない（補完待ち）
    expect(mockMutateAsync).not.toHaveBeenCalled();
    // missingInfoSlot が null でない
    expect(result.current.missingInfoSlot).not.toBeNull();
  });
});
