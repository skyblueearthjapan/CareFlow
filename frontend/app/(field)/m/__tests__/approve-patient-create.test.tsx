/**
 * ApprovePanel patient_create プレビュー (StageB) — 振る舞いテスト.
 *
 * カバー:
 *   1. patient_create 申請でカルテ概要 (コード/カナ/性別/保険/住所) を出す
 *   2. proposed_visits を 曜日/時刻/コース のプレビューで出す
 *   3. 承認 (useApproveRequest) が呼ばれる
 *   4. patient_create 以外ではプレビューを出さない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { PendingRequestV2Read } from '@/lib/schemas/pending_request';

vi.mock('@/lib/queries/pending_requests', () => ({
  usePendingRequests: vi.fn(),
  useApproveRequest: vi.fn(),
  useRejectRequest: vi.fn(),
}));

import {
  usePendingRequests,
  useApproveRequest,
  useRejectRequest,
} from '@/lib/queries/pending_requests';
import { ApprovePanel } from '@/components/field/ApprovePanel';

const approveMutate = vi.fn();

function makeReq(over: Partial<PendingRequestV2Read> = {}): PendingRequestV2Read {
  return {
    id: '12121212-1212-1212-1212-121212121212',
    request_type: 'patient_create',
    payload: {
      name: '青柳 あい',
      patient_name: '青柳 あい',
      code: 'P-1042',
      kana: 'アオヤギ アイ',
      sex: 'female',
      insurance: 'medical',
      address: '千葉市稲毛区小仲台6-2-1',
      status: 'active',
      weekly_pattern: { frequency_per_week: 2 },
      proposed_visits: [
        { weekday: 0, start_time: '10:00', duration_min: 60, course_code: 'A' },
        { weekday: 4, start_time: '14:00', duration_min: 45, course_code: 'B' },
      ],
    },
    requester_user_id: '13131313-1313-1313-1313-131313131313',
    created_at: '2026-06-02T09:30:00Z',
    updated_at: '2026-06-02T09:30:00Z',
    status: 'pending',
    ...over,
  } as unknown as PendingRequestV2Read;
}

function setPending(items: PendingRequestV2Read[]) {
  (usePendingRequests as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items, total: items.length, limit: 100, offset: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  approveMutate.mockReset();
  setPending([makeReq()]);
  (useApproveRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: approveMutate,
    isPending: false,
    isError: false,
  });
  (useRejectRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
  });
});

describe('ApprovePanel patient_create プレビュー', () => {
  it('カルテ概要 (コード/カナ/性別/保険/住所) を表示する', () => {
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.getByText('患者新規')).toBeInTheDocument();
    expect(screen.getByText('カルテ概要')).toBeInTheDocument();
    expect(screen.getByText('P-1042')).toBeInTheDocument();
    expect(screen.getByText('アオヤギ アイ')).toBeInTheDocument();
    expect(screen.getByText('女性')).toBeInTheDocument();
    expect(screen.getByText('医療保険')).toBeInTheDocument();
    expect(screen.getByText('住所: 千葉市稲毛区小仲台6-2-1')).toBeInTheDocument();
  });

  it('proposed_visits を 提案枠N件 + 曜日/時刻/コース で表示する', () => {
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.getByText('提案枠 2件')).toBeInTheDocument();
    // 月 10:00・60分 / 金 14:00・45分。
    expect(screen.getByText(/10:00 ・ 60分/)).toBeInTheDocument();
    expect(screen.getByText(/14:00 ・ 45分/)).toBeInTheDocument();
    // 曜日チップ (月 / 金)。
    expect(screen.getByText('月')).toBeInTheDocument();
    expect(screen.getByText('金')).toBeInTheDocument();
  });

  it('承認ボタンで useApproveRequest が呼ばれる', () => {
    render(<ApprovePanel onToast={vi.fn()} />);
    fireEvent.click(screen.getByText('承認'));
    expect(approveMutate).toHaveBeenCalledTimes(1);
    expect(approveMutate.mock.calls[0]![0]).toBe('12121212-1212-1212-1212-121212121212');
  });

  it('patient_create 以外ではカルテプレビューを出さない', () => {
    setPending([makeReq({ request_type: 'staff_off', payload: { staff_name: '佐藤' } })]);
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.queryByText('カルテ概要')).not.toBeInTheDocument();
    expect(screen.queryByText(/提案枠/)).not.toBeInTheDocument();
  });
});
