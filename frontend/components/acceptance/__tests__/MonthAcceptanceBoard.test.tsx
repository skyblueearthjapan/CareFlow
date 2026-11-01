/** 受け入れ枠「月で見る」— マス・混みの印・内訳・切り替え。 */
import * as React from 'react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

vi.mock('@/lib/queries/acceptance_month', () => ({
  useMonthAcceptance: vi.fn(),
  useMonthPrint: vi.fn(() => ({ isPending: false, mutateAsync: vi.fn() })),
}));

import { useMonthAcceptance, type MonthParams } from '@/lib/queries/acceptance_month';
import { monthPayload } from '@/lib/__tests__/fixtures/acceptanceMonthPayload';

import { MonthAcceptanceBoard } from '../MonthAcceptanceBoard';

function lastParams(): MonthParams {
  const calls = (useMonthAcceptance as Mock).mock.calls;
  return calls[calls.length - 1][0] as MonthParams;
}

beforeEach(() => {
  vi.clearAllMocks();
  (useMonthAcceptance as Mock).mockReturnValue({
    data: monthPayload(),
    isLoading: false,
    isError: false,
    isFetching: false,
  });
});

describe('MonthAcceptanceBoard', () => {
  it('最初は既定の担い手・休みは実際のとおり・昼休みは考える', () => {
    render(<MonthAcceptanceBoard officeId={null} initialMonth="2026-11" />);
    expect(lastParams()).toEqual({
      month: '2026-11',
      officeId: null,
      staffIds: null,
      mode: 'real',
      lunch: true,
    });
    expect(screen.getByRole('button', { name: '職員A' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /管理者M/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('マスに ○△×・稼働率・混みの印・▲▼ が出る', () => {
    render(<MonthAcceptanceBoard officeId={null} initialMonth="2026-11" />);
    const grid = screen.getByTestId('month-grid');
    const crowded = within(grid).getByRole('button', { name: /月曜 10:00 .*混み/ });
    expect(crowded).toHaveAttribute('data-crowded', 'true');
    expect(crowded).toHaveTextContent('82%');
    expect(crowded).toHaveTextContent('▲');
    expect(within(grid).getByRole('button', { name: /火曜 10:00 .*下がった/ })).toHaveTextContent(
      '▼',
    );
    const ok = within(grid).getByRole('button', { name: /月曜 09:30 ○/ });
    expect(ok).not.toHaveAttribute('data-crowded');
    const summary = screen.getByLabelText('曜日ごとの合計');
    expect(within(summary).getByRole('row', { name: /担い手 件数/ })).toHaveTextContent('29.8');
    expect(within(summary).getByRole('row', { name: /事業所全体 件数/ })).toHaveTextContent('41.3');
    expect(within(summary).getByRole('row', { name: /事業所全体 稼働率/ })).toHaveTextContent(
      '47%',
    );
    expect(screen.getByTestId('month-summary-note')).toHaveTextContent('担い手の主担当の件数');
    expect(screen.getByLabelText('月の要約')).toHaveTextContent('混み 1 マス');
  });

  it('マスを押すと内訳 (件数・稼働率・6 件以上・間の余裕・時間固定)', () => {
    render(<MonthAcceptanceBoard officeId={null} initialMonth="2026-11" />);
    fireEvent.click(screen.getByRole('button', { name: /月曜 10:00/ }));
    const panel = screen.getByLabelText('マスの内訳');
    expect(panel).toHaveTextContent('月曜 10:00〜10:30');
    expect(panel).toHaveTextContent('新しい訪問は入れない方がよい');
    expect(panel).toHaveTextContent('1 日 1.0 件（月 4 件）');
    expect(panel).toHaveTextContent('6 件以上の職員');
    expect(panel).toHaveTextContent('間の余裕 20 分未満');
    expect(panel).toHaveTextContent('50%（続く訪問 2 組）');
    expect(panel).toHaveTextContent('時間固定の利用者');
    expect(panel).toHaveTextContent('+12 ポイント');
  });

  it('切り替えで問い合わせ直す (昼休み・休み・担い手・月)', () => {
    render(<MonthAcceptanceBoard officeId="o1" initialMonth="2026-11" />);
    fireEvent.click(screen.getByRole('button', { name: '考えない' }));
    expect(lastParams().lunch).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '無いとする' }));
    expect(lastParams().mode).toBe('ignore');
    fireEvent.click(screen.getByRole('button', { name: /管理者M/ }));
    expect(lastParams().staffIds).toEqual(['m1', 's1']);
    fireEvent.click(screen.getByRole('button', { name: '職員だけ' }));
    expect(lastParams().staffIds).toEqual(['s1']);
    fireEvent.click(screen.getByRole('button', { name: '次の月' }));
    expect(lastParams().month).toBe('2026-12');
    fireEvent.click(screen.getByRole('button', { name: '前の月' }));
    fireEvent.click(screen.getByRole('button', { name: '前の月' }));
    expect(lastParams().month).toBe('2026-10');
    expect(lastParams().officeId).toBe('o1');
  });
});
