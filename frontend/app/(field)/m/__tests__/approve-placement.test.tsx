/**
 * ApprovePanel placement カード (Phase G-84) — 振る舞いテスト.
 *
 * カバー:
 *   1. placement を持つ patient_visit_add 申請で「訪問追加」ラベル + 追加枠 + 枠座標カードを出す
 *   2. warnings (赤/黄) と override_reason を転記する
 *   3. placement の無い申請ではカードを出さない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

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

function makeReq(over: Partial<PendingRequestV2Read> = {}): PendingRequestV2Read {
  return {
    id: '12121212-1212-1212-1212-121212121212',
    request_type: 'patient_visit_add',
    payload: {
      patient_id: 'pid-1',
      patient_name: '田中 太郎',
      proposed_visits: [{ weekday: 0, start_time: '10:30', duration_min: 35, course_code: '稲A' }],
      placement: {
        office_id: 'office-1',
        office_name: '稲毛',
        course_template_id: null,
        course_code: '稲A',
        course_label: '稲A',
        weekday: 0,
        start_time: '10:30',
        duration_min: 35,
        prev_visit: { patient_name: '前 太郎', end_time: '10:00' },
        next_visit: { patient_name: '後 花子', start_time: '11:30' },
      },
      warnings: [
        { level: 'red', code: 'capacity_full', message: 'このコースは満員です（空き枠なし）' },
        { level: 'yellow', code: 'travel_shortage', message: '移動時間が不足しています' },
      ],
      override_reason: '管理者判断で増枠',
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
  setPending([makeReq()]);
  (useApproveRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
  });
  (useRejectRequest as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
  });
});

describe('ApprovePanel placement カード', () => {
  it('patient_visit_add で「訪問追加」ラベル + 追加枠 + 枠座標カードを出す', () => {
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.getByText('訪問追加')).toBeInTheDocument();
    expect(screen.getByText('追加する枠 1件')).toBeInTheDocument();
    expect(screen.getByText('配置する枠')).toBeInTheDocument();
    // 拠点 / コース / 曜日。
    expect(screen.getByText(/稲毛.*稲A.*月曜/)).toBeInTheDocument();
    // prev/next visit。
    expect(screen.getByText(/直前: 前 太郎/)).toBeInTheDocument();
    expect(screen.getByText(/直後: 後 花子/)).toBeInTheDocument();
  });

  it('warnings (赤/黄) と override_reason を転記する', () => {
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.getByText('このコースは満員です（空き枠なし）')).toBeInTheDocument();
    expect(screen.getByText('移動時間が不足しています')).toBeInTheDocument();
    expect(screen.getByText(/管理者判断で増枠/)).toBeInTheDocument();
  });

  it('placement の無い申請では枠座標カードを出さない', () => {
    setPending([makeReq({ request_type: 'staff_off', payload: { staff_name: '佐藤' } })]);
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.queryByText('配置する枠')).not.toBeInTheDocument();
  });
});

describe('ApprovePanel scope ラベル (Phase G-85)', () => {
  it('scope 未指定 (恒常) は「毎週固定」を出す', () => {
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.getAllByText('毎週固定').length).toBeGreaterThan(0);
  });

  it('scope=one_time + iso は「この週だけ（YYYY-Www）」を出す', () => {
    const base = makeReq();
    setPending([
      makeReq({
        payload: {
          ...(base.payload as Record<string, unknown>),
          scope: 'one_time',
          course_id: 'weekly-course-1',
          iso_year: 2026,
          iso_week: 23,
        },
      }),
    ]);
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.getAllByText('この週だけ（2026-W23）').length).toBeGreaterThan(0);
  });

  it('scope=one_time だが iso 欠落でも「この週だけ（単発）」にフォールバック', () => {
    const base = makeReq();
    setPending([
      makeReq({
        payload: {
          ...(base.payload as Record<string, unknown>),
          scope: 'one_time',
        },
      }),
    ]);
    render(<ApprovePanel onToast={vi.fn()} />);
    expect(screen.getAllByText('この週だけ（単発）').length).toBeGreaterThan(0);
  });
});
