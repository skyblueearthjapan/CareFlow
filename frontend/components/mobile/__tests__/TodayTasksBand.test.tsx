/** スマホの「今日のやること N 件」の帯 (patient-tasks-design §3)。 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { TodayTasksBand } from '../TodayTasksBand';

const c = (open: number, total: number) => ({
  open_count: open,
  total_count: total,
  has_carried_over: false,
  has_overdue: false,
  all_done: open === 0,
});

describe('TodayTasksBand', () => {
  it('一覧に出している訪問の未完了と全部の件数を足す', () => {
    render(
      <TodayTasksBand
        counts={{ v1: c(2, 2), v2: c(0, 1), hidden: c(5, 5) }}
        visitIds={['v1', 'v2', 'v3']}
      />,
    );
    const band = screen.getByTestId('today-tasks-band');
    expect(band.textContent).toContain('今日のやること 2 件');
    expect(band.textContent).toContain('全部で 3 件');
    expect(band.textContent).toContain('訪問を開くと、事務所からのお願いが見られます。');
  });

  it('全部完了なら「すべて完了しました」', () => {
    render(<TodayTasksBand counts={{ v1: c(0, 2) }} visitIds={['v1']} />);
    expect(screen.getByTestId('today-tasks-band').textContent).toContain(
      '今日の分はすべて完了しました。',
    );
  });

  it('やることが無い日は出さない', () => {
    const { container } = render(<TodayTasksBand counts={{}} visitIds={['v1']} />);
    expect(container.innerHTML).toBe('');
  });
});
