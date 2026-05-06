/**
 * useAiSubmissionHandler — unit tests (W7-FE1 / W8-FE1).
 *
 * 4 ケース:
 *   1. patient_create + admin (PC) → createAndApply が呼ばれる (create/approve は呼ばれない)
 *   2. staff_create + staff → out_of_scope に切り替わる（POST は呼ばれない）
 *   3. missing_fields があれば missingInfoSlot が render される
 *   4. patient_create + admin (mobile) → create のみ呼ばれ createAndApply は呼ばれない
 *
 * テストランナー: vitest + @testing-library/react
 * モック戦略:
 *   - next-auth の useSession をモック（role を注入）
 *   - useCreatePendingRequest / useApproveRequest / useCreateAndApplyPendingRequest をモック
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
const mockCreateAndApplyMutateAsync = vi.fn();

vi.mock('@/lib/queries/pending_requests', () => ({
  useCreatePendingRequest: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
  useApproveRequest: () => ({
    mutateAsync: mockApproveMutateAsync,
    isPending: false,
  }),
  useCreateAndApplyPendingRequest: () => ({
    mutateAsync: mockCreateAndApplyMutateAsync,
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
    mockMutateAsync.mockResolvedValue({
      id: 'req-001',
      request_type: 'patient_create',
      status: 'pending',
    });
    mockApproveMutateAsync.mockResolvedValue({ id: 'req-001', status: 'approved' });
    mockCreateAndApplyMutateAsync.mockResolvedValue({
      id: 'req-001',
      request_type: 'patient_create',
      status: 'approved',
    });
  });

  /**
   * ケース 1: patient_create + admin (PC) → createAndApply が呼ばれる
   *
   * admin ロール + PC (isMobile: false) で patient_create を送信した場合:
   *   - useCreateAndApplyPendingRequest.mutateAsync が 'patient_create' で呼ばれる
   *   - useCreatePendingRequest.mutateAsync / useApproveRequest.mutateAsync は呼ばれない
   *   - toast.success が呼ばれる
   *
   * W8-FE1 Codex 再レビュー Must-fix #1: 旧来の create + approve の 2 コールから
   * create-and-apply 単一 TX に変更。
   */
  it('patient_create + admin (PC) → createAndApply is called, create/approve are NOT called', async () => {
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

    // PC admin → 単一 TX の createAndApply を使用
    expect(mockCreateAndApplyMutateAsync).toHaveBeenCalledOnce();
    expect(mockCreateAndApplyMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ request_type: 'patient_create' }),
    );
    // create / approve は呼ばれない
    expect(mockMutateAsync).not.toHaveBeenCalled();
    expect(mockApproveMutateAsync).not.toHaveBeenCalled();
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
    expect(mockCreateAndApplyMutateAsync).not.toHaveBeenCalled();
    // missingInfoSlot が null でない
    expect(result.current.missingInfoSlot).not.toBeNull();
  });

  /**
   * ケース 4: patient_create + admin (mobile) → create のみ呼ばれ createAndApply は呼ばれない
   *
   * admin ロールでも isMobile: true の場合は即時反映なし (§3.5.1)。
   * 通常の create のみが呼ばれ、createAndApply / approve は呼ばれないことを確認。
   *
   * W8-FE1 Codex 再レビュー Must-fix #1: モバイル admin は従来通り create のみ。
   */
  it('patient_create + admin (mobile) → create is called, createAndApply is NOT called', async () => {
    mockSession.mockReturnValue({
      data: { user: { role: 'admin' } },
      status: 'authenticated',
    });

    // isMobile: true — モバイル admin
    const { result } = renderHook(() => useAiSubmissionHandler({ isMobile: true }));

    const interpretResult = makeInterpretResponse('patient_create', {
      name: '鈴木一郎',
    });

    await act(async () => {
      await result.current.onSubmitInterceptor(interpretResult);
    });

    // モバイル → create のみ (即時反映なし)
    expect(mockMutateAsync).toHaveBeenCalledOnce();
    expect(mockMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ request_type: 'patient_create' }),
    );
    // createAndApply / approve は呼ばれない
    expect(mockCreateAndApplyMutateAsync).not.toHaveBeenCalled();
    expect(mockApproveMutateAsync).not.toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('patient_create'));
  });

  /**
   * ケース 5: staff_mentor + assignments[] + admin (PC) → pending 経路 (createAndApply)
   *
   * W11-FE: assignments[] を含む staff_mentor payload を admin が送信した場合、
   * BE が payload をそのまま解釈するため FE 側の変換は不要。
   * createAndApply が staff_mentor request_type で呼ばれることを確認。
   */
  it('staff_mentor + assignments[] + admin (PC) → createAndApply called with staff_mentor', async () => {
    mockSession.mockReturnValue({
      data: { user: { role: 'admin' } },
      status: 'authenticated',
    });

    const { result } = renderHook(() => useAiSubmissionHandler({ isMobile: false }));

    const interpretResult = makeInterpretResponse('staff_mentor', {
      target_staff_id: 'aaaaaaaa-0000-0000-0000-000000000001',
      assignments: [
        {
          day_of_week: 1,
          part: 'morning',
          companion_staff_id: 'bbbbbbbb-0000-0000-0000-000000000002',
        },
        {
          day_of_week: 1,
          part: 'afternoon',
          companion_staff_id: 'cccccccc-0000-0000-0000-000000000003',
        },
      ],
    });

    await act(async () => {
      await result.current.onSubmitInterceptor(interpretResult);
    });

    expect(mockCreateAndApplyMutateAsync).toHaveBeenCalledOnce();
    expect(mockCreateAndApplyMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ request_type: 'staff_mentor' }),
    );
    expect(mockMutateAsync).not.toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('staff_mentor'));
  });

  /**
   * ケース 6: staff_mentor + assignments[] + staff → out_of_scope (RBAC)
   *
   * staff ロールは staff_mentor を操作できないため out_of_scope に分岐する。
   * POST は一切呼ばれないことを確認。
   */
  it('staff_mentor + assignments[] + staff → out_of_scope, no POST', async () => {
    mockSession.mockReturnValue({
      data: { user: { role: 'staff' } },
      status: 'authenticated',
    });

    const { result } = renderHook(() => useAiSubmissionHandler({ isMobile: false }));

    const interpretResult = makeInterpretResponse('staff_mentor', {
      target_staff_id: 'aaaaaaaa-0000-0000-0000-000000000001',
      assignments: [
        {
          day_of_week: 2,
          part: 'all_day',
          companion_staff_id: 'bbbbbbbb-0000-0000-0000-000000000002',
        },
      ],
    });

    await act(async () => {
      await result.current.onSubmitInterceptor(interpretResult);
    });

    expect(mockMutateAsync).not.toHaveBeenCalled();
    expect(mockCreateAndApplyMutateAsync).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('staff'));
  });

  /**
   * ケース 7: staff_mentor + assignments=[] (空配列) + admin (PC) → POST OK (同行スタッフ全削除)
   *
   * assignments が空配列でも POST 自体は成功する (BE 側で全削除として扱う)。
   * FE では 422 を事前にブロックせず、BE に素通しする設計。
   */
  it('staff_mentor + assignments=[] (empty) + admin (PC) → POST OK (companion full clear)', async () => {
    mockSession.mockReturnValue({
      data: { user: { role: 'admin' } },
      status: 'authenticated',
    });

    const { result } = renderHook(() => useAiSubmissionHandler({ isMobile: false }));

    const interpretResult = makeInterpretResponse('staff_mentor', {
      target_staff_id: 'aaaaaaaa-0000-0000-0000-000000000001',
      assignments: [],
    });

    await act(async () => {
      await result.current.onSubmitInterceptor(interpretResult);
    });

    expect(mockCreateAndApplyMutateAsync).toHaveBeenCalledOnce();
    expect(mockCreateAndApplyMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        request_type: 'staff_mentor',
        payload: expect.objectContaining({ assignments: [] }),
      }),
    );
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('staff_mentor'));
  });
});
