/**
 * SameAddressLinksSection vitest tests (Phase G-21 T4).
 *
 * カバーするシナリオ:
 *   1. candidates 表示 (氏名 + コード + 住所 + radio 3 択)
 *   2. radio 選択で API call (preferred → DELETE, blocked → POST)
 *   3. 初期 pair_mode=null → preferred radio が checked
 *   4. 初期 pair_mode=blocked → blocked radio が checked
 *   5. candidates 空時のメッセージ
 *   6. loading 中は Skeleton 表示 (= 空 / no-candidates メッセージなし)
 *   7. readOnly=true → radio が disabled
 *   8. note 入力 + blur で POST 呼び出し (pair_mode 維持で note 更新)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ─── Mock next-auth ───────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

// ─── Mock query hooks ─────────────────────────────────────────────────────────
vi.mock('@/lib/queries/g21', () => ({
  useSameAddressCandidates: vi.fn(),
  useSetSameAddressLink: vi.fn(),
  useDeleteSameAddressLink: vi.fn(),
}));

// ─── Mock toast ───────────────────────────────────────────────────────────────
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { useSession } from 'next-auth/react';
import {
  useDeleteSameAddressLink,
  useSameAddressCandidates,
  useSetSameAddressLink,
} from '@/lib/queries/g21';

import { SameAddressLinksSection } from '../SameAddressLinksSection';

const PATIENT_ID = '00000000-0000-0000-0000-000000000001';
const OTHER_ID = '00000000-0000-0000-0000-000000000002';

function makeSession(role: 'admin' | 'manager' | 'staff') {
  return { data: { user: { role } }, status: 'authenticated' };
}

function makeMutation(fn: Mock = vi.fn().mockResolvedValue(undefined)) {
  return { mutateAsync: fn, isPending: false, isError: false };
}

function setupMocks(opts: {
  role?: 'admin' | 'manager' | 'staff';
  candidates?: unknown[];
  isLoading?: boolean;
  setFn?: Mock;
  deleteFn?: Mock;
}) {
  (useSession as Mock).mockReturnValue(makeSession(opts.role ?? 'admin'));
  (useSameAddressCandidates as Mock).mockReturnValue({
    data: opts.candidates ?? [],
    isLoading: opts.isLoading ?? false,
    isError: false,
    error: null,
  });
  (useSetSameAddressLink as Mock).mockReturnValue(makeMutation(opts.setFn));
  (useDeleteSameAddressLink as Mock).mockReturnValue(makeMutation(opts.deleteFn));
}

describe('SameAddressLinksSection (Phase G-21)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. candidates 表示 (氏名 + コード + 住所 + radio 3 択)', () => {
    setupMocks({
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県千葉市稲毛区 1-2-3',
          pair_mode: null,
          decided_by_user_id: null,
          note: null,
        },
      ],
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    expect(screen.getByText('山田太郎')).toBeInTheDocument();
    expect(screen.getByText('[P-002]')).toBeInTheDocument();
    expect(screen.getByText(/千葉県千葉市稲毛区/)).toBeInTheDocument();

    // 3 radio (preferred / required / blocked)
    expect(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-preferred`)).toBeInTheDocument();
    expect(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-required`)).toBeInTheDocument();
    expect(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-blocked`)).toBeInTheDocument();
  });

  it('2. blocked radio → POST 呼び出し (pair_mode=blocked)', async () => {
    const setFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: null,
          decided_by_user_id: null,
          note: null,
        },
      ],
      setFn,
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    fireEvent.click(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-blocked`));

    await waitFor(() => expect(setFn).toHaveBeenCalledTimes(1));
    expect(setFn).toHaveBeenCalledWith({
      patient_a_id: PATIENT_ID,
      patient_b_id: OTHER_ID,
      pair_mode: 'blocked',
      note: null,
    });
  });

  it('3. 既存 blocked → preferred radio に変更 → DELETE 呼び出し', async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: 'blocked',
          decided_by_user_id: null,
          note: 'AB の関係性なし',
        },
      ],
      deleteFn,
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    // 初期は blocked が checked
    expect(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-blocked`)).toBeChecked();

    // preferred に戻す
    fireEvent.click(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-preferred`));

    await waitFor(() => expect(deleteFn).toHaveBeenCalledTimes(1));
    expect(deleteFn).toHaveBeenCalledWith({
      patient_a_id: PATIENT_ID,
      patient_b_id: OTHER_ID,
    });
  });

  it('4. candidates 空 → 「同住所候補が見つかりません」表示', () => {
    setupMocks({ candidates: [] });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    expect(screen.getByTestId('same-address-no-candidates')).toBeInTheDocument();
    expect(screen.getByText('同住所候補が見つかりません')).toBeInTheDocument();
  });

  it('5. staff role → 閲覧のみバッジ + radio が disabled', () => {
    setupMocks({
      role: 'staff',
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: null,
          decided_by_user_id: null,
          note: null,
        },
      ],
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    expect(screen.getByText('閲覧のみ')).toBeInTheDocument();
    expect(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-preferred`)).toBeDisabled();
    expect(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-blocked`)).toBeDisabled();
  });

  it('6. note 入力 + blur (pair_mode=blocked) → POST 呼び出し (note 更新)', async () => {
    const setFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: 'blocked',
          decided_by_user_id: null,
          note: null,
        },
      ],
      setFn,
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    const noteInput = screen.getByTestId(`pair-mode-note-${OTHER_ID}`) as HTMLInputElement;
    expect(noteInput).not.toBeDisabled();

    fireEvent.change(noteInput, { target: { value: '互いに認知症あり' } });
    fireEvent.blur(noteInput);

    await waitFor(() => expect(setFn).toHaveBeenCalledTimes(1));
    expect(setFn).toHaveBeenCalledWith({
      patient_a_id: PATIENT_ID,
      patient_b_id: OTHER_ID,
      pair_mode: 'blocked',
      note: '互いに認知症あり',
    });
  });

  it('7. pair_mode=preferred 状態 → note 入力は disabled', () => {
    setupMocks({
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: null,
          decided_by_user_id: null,
          note: null,
        },
      ],
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    expect(screen.getByTestId(`pair-mode-note-${OTHER_ID}`)).toBeDisabled();
  });

  it('8. readOnly=true 強制 → admin でも radio disabled', () => {
    setupMocks({
      role: 'admin',
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: null,
          decided_by_user_id: null,
          note: null,
        },
      ],
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} readOnly />);

    expect(screen.getByTestId(`pair-mode-radio-${OTHER_ID}-blocked`)).toBeDisabled();
  });

  // ─── Phase G-21 T4 reviewer fixes ─────────────────────────────────────

  it('H2: radiogroup の aria-label が候補ごとに個別化される', () => {
    setupMocks({
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: null,
          decided_by_user_id: null,
          note: null,
        },
      ],
    });
    render(<SameAddressLinksSection patientId={PATIENT_ID} />);
    // 「山田太郎 の同住所紐付け方針」 という個別 aria-label が radiogroup に付く
    const group = screen.getByRole('radiogroup', { name: '山田太郎 の同住所紐付け方針' });
    expect(group).toBeInTheDocument();
  });

  it('H2: patient_name 欠落時は patient_code に fallback (= ID 化される)', () => {
    setupMocks({
      candidates: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-099',
          patient_name: null, // 氏名なし
          address: null,
          pair_mode: null,
          decided_by_user_id: null,
          note: null,
        },
      ],
    });
    render(<SameAddressLinksSection patientId={PATIENT_ID} />);
    // 氏名が無くても patient_code を含む aria-label が出る
    const group = screen.getByRole('radiogroup', { name: 'P-099 の同住所紐付け方針' });
    expect(group).toBeInTheDocument();
  });

  it('H3: setLink.isPending=true 中の note blur は POST しない (二重送信ガード)', async () => {
    // setLink を「常に pending」状態にして blur しても mutateAsync が呼ばれないことを確認.
    const setFn = vi.fn().mockResolvedValue(undefined);
    (useSession as Mock).mockReturnValue(makeSession('admin'));
    (useSameAddressCandidates as Mock).mockReturnValue({
      data: [
        {
          patient_id: OTHER_ID,
          patient_code: 'P-002',
          patient_name: '山田太郎',
          address: '千葉県',
          pair_mode: 'blocked',
          decided_by_user_id: null,
          note: null,
        },
      ],
      isLoading: false,
      isError: false,
      error: null,
    });
    // isPending=true を返すように mock (= radio click 中の状態を再現).
    (useSetSameAddressLink as Mock).mockReturnValue({
      mutateAsync: setFn,
      isPending: true,
      isError: false,
    });
    (useDeleteSameAddressLink as Mock).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(undefined),
      isPending: false,
      isError: false,
    });

    render(<SameAddressLinksSection patientId={PATIENT_ID} />);

    const noteInput = screen.getByTestId(`pair-mode-note-${OTHER_ID}`) as HTMLInputElement;
    // 入力 → blur しても mutateAsync が呼ばれない (= H3 ガード)
    fireEvent.change(noteInput, { target: { value: '互いに認知症あり' } });
    fireEvent.blur(noteInput);

    // 短い時間待っても call されない
    await new Promise((r) => setTimeout(r, 50));
    expect(setFn).not.toHaveBeenCalled();
  });
});
