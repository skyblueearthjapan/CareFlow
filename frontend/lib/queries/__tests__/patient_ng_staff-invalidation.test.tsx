/**
 * NG スタッフ upsert / delete のキャッシュ失効テスト (2026-08-11 の欠陥修正).
 *
 * 欠陥: 当該患者の `['patients', id, 'ng-staff']` しか無効化しておらず、
 *   - 改善提案 (staleTime 5 分) が NG 追加前の結果を返し続ける
 *   - プール投入提案 (diff-add) / 現場ボード (field-board) も同様
 *   - 患者一覧の `ng_staff_count` バッジ・逆引き (ng-patients) も更新されない
 * ため「NG を足したのに警告が出ない」ように見えていた。
 *
 * 同じ構造の失効漏れがあった同住所リンク (g21) も併せて検証する。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn().mockResolvedValue({}) }));

import { useDeleteNgStaff, useUpsertNgStaff } from '../patient_ng_staff';
import { useDeleteSameAddressLink, useSetSameAddressLink } from '../g21';
import { IMPROVEMENT_SUGGESTIONS_KEY } from '../improvementSuggestions';

const PATIENT_ID = '22222222-2222-4222-8222-222222222222';
const STAFF_ID = '33333333-3333-4333-8333-333333333333';
const PATIENT_B_ID = '44444444-4444-4444-8444-444444444444';

let qc: QueryClient;
let invalidateSpy: ReturnType<typeof vi.fn>;

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

/** invalidateQueries に渡された queryKey 一覧 (順不同で照合する)。 */
function invalidatedKeys(): unknown[][] {
  return invalidateSpy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey);
}

function expectKey(keys: unknown[][], expected: unknown[]) {
  expect(keys).toContainEqual(expected);
}

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  invalidateSpy = vi.fn();
  qc.invalidateQueries = invalidateSpy as unknown as QueryClient['invalidateQueries'];
});

describe('useUpsertNgStaff / useDeleteNgStaff の失効対象', () => {
  it('upsert 成功で提案系・患者一覧・逆引きまで失効する', async () => {
    const { result } = renderHook(() => useUpsertNgStaff(), { wrapper });
    result.current.mutate({ patientId: PATIENT_ID, staffId: STAFF_ID, note: null });
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());

    const keys = invalidatedKeys();
    expectKey(keys, ['patients', PATIENT_ID, 'ng-staff']);
    expectKey(keys, ['staff', STAFF_ID, 'ng-patients']);
    expectKey(keys, ['patients']);
    // これが今回の本丸 (改善提案は staleTime 5 分でキャッシュされる)。
    expectKey(keys, [IMPROVEMENT_SUGGESTIONS_KEY]);
    expectKey(keys, ['diff-add']);
    expectKey(keys, ['field-board']);
  });

  it('delete 成功でも同じ対象を失効する', async () => {
    const { result } = renderHook(() => useDeleteNgStaff(), { wrapper });
    result.current.mutate({ patientId: PATIENT_ID, staffId: STAFF_ID });
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());

    const keys = invalidatedKeys();
    expectKey(keys, ['patients', PATIENT_ID, 'ng-staff']);
    expectKey(keys, ['staff', STAFF_ID, 'ng-patients']);
    expectKey(keys, [IMPROVEMENT_SUGGESTIONS_KEY]);
  });
});

describe('同住所リンク (g21) の失効対象', () => {
  it('リンク作成で提案系キャッシュも失効する', async () => {
    const { result } = renderHook(() => useSetSameAddressLink(), { wrapper });
    result.current.mutate({
      patient_a_id: PATIENT_ID,
      patient_b_id: PATIENT_B_ID,
      pair_mode: 'preferred',
    } as Parameters<typeof result.current.mutate>[0]);
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());

    const keys = invalidatedKeys();
    expectKey(keys, ['patients', PATIENT_ID, 'same-address-candidates']);
    expectKey(keys, [IMPROVEMENT_SUGGESTIONS_KEY]);
    expectKey(keys, ['diff-add']);
    expectKey(keys, ['field-board']);
  });

  it('リンク削除でも提案系キャッシュを失効する', async () => {
    const { result } = renderHook(() => useDeleteSameAddressLink(), { wrapper });
    result.current.mutate({ patient_a_id: PATIENT_ID, patient_b_id: PATIENT_B_ID });
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());

    const keys = invalidatedKeys();
    expectKey(keys, [IMPROVEMENT_SUGGESTIONS_KEY]);
    expectKey(keys, ['diff-add']);
  });
});
