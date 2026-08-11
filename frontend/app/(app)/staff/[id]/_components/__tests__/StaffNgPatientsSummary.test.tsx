/**
 * StaffNgPatientsSummary vitest tests (patient-ng-staff-design.md §8-2 Phase 2).
 *
 * カバーするシナリオ:
 *   1. 一覧表示 (患者名 / 理由メモ / 患者詳細リンク)
 *   2. メモ null は「—」
 *   3. 0 件ならカードごと非表示 (null 返し)
 *   4. loading 中も非表示 (0 件との区別がつかないためちらつかせない)
 *   5. エラー時はカード + エラー表示
 */
import * as React from 'react';
import { describe, it, expect, vi, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';

// ─── Mock next/link (素の <a> に落とす) ───────────────────────────────────────
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// ─── Mock query hook ─────────────────────────────────────────────────────────
vi.mock('@/lib/queries/patient_ng_staff', () => ({
  useStaffNgPatients: vi.fn(),
}));

import { useStaffNgPatients } from '@/lib/queries/patient_ng_staff';

import { StaffNgPatientsSummary } from '../StaffNgPatientsSummary';

const STAFF_ID = '00000000-0000-0000-0000-0000000000s1';
const PATIENT_A = '00000000-0000-0000-0000-0000000000a1';
const PATIENT_B = '00000000-0000-0000-0000-0000000000b1';

function setupMock(opts: {
  rows?: unknown[];
  isLoading?: boolean;
  isError?: boolean;
  error?: Error | null;
}) {
  (useStaffNgPatients as unknown as Mock).mockReturnValue({
    data: opts.rows ?? [],
    isLoading: opts.isLoading ?? false,
    isError: opts.isError ?? false,
    error: opts.error ?? null,
  });
}

describe('StaffNgPatientsSummary', () => {
  it('1. NG 指定している患者を一覧表示し、患者詳細へリンクする', () => {
    setupMock({
      rows: [
        {
          patient_id: PATIENT_A,
          patient_name: '山田 太郎',
          note: '相性不良',
          created_at: '2026-08-11T00:00:00Z',
        },
        {
          patient_id: PATIENT_B,
          patient_name: '佐藤 花子',
          note: null,
          created_at: '2026-08-11T00:00:00Z',
        },
      ],
    });
    render(<StaffNgPatientsSummary staffId={STAFF_ID} />);

    expect(screen.getByTestId('staff-ng-patients-summary')).toBeInTheDocument();
    expect(screen.getByText('山田 太郎')).toBeInTheDocument();
    expect(screen.getByText('相性不良')).toBeInTheDocument();
    expect(screen.getByText('山田 太郎').closest('a')).toHaveAttribute(
      'href',
      `/patients/${PATIENT_A}`,
    );
  });

  it('2. 理由メモが null の行は「—」', () => {
    setupMock({
      rows: [
        {
          patient_id: PATIENT_B,
          patient_name: '佐藤 花子',
          note: null,
          created_at: '2026-08-11T00:00:00Z',
        },
      ],
    });
    render(<StaffNgPatientsSummary staffId={STAFF_ID} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('3. 0 件ならカードごと描画しない', () => {
    setupMock({ rows: [] });
    const { container } = render(<StaffNgPatientsSummary staffId={STAFF_ID} />);
    expect(screen.queryByTestId('staff-ng-patients-summary')).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it('4. loading 中も描画しない (0 件と区別できずちらつくため)', () => {
    setupMock({ rows: undefined, isLoading: true });
    const { container } = render(<StaffNgPatientsSummary staffId={STAFF_ID} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('5. エラー時はカード + エラーメッセージを出す', () => {
    setupMock({ isError: true, error: new Error('boom') });
    render(<StaffNgPatientsSummary staffId={STAFF_ID} />);
    expect(screen.getByText('取得に失敗しました')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });
});
