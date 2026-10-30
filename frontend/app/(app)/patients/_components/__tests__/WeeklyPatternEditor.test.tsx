/**
 * WeeklyPatternEditor — 訪問頻度の詳しい設定 (起点の週・第何週・注意書き・次の訪問)。
 *
 * biweekly-monthly-design-2026-10-30.md §3・§4 (段 2 のレビュー):
 *   - 起点の週はどの日を選んでも月曜にそろえ、「A の週」と出す
 *   - 設定が足りない間は「毎週の予定になります」と知らせ、足りたら消す
 *   - 第何週のチェックを "1,3" の形で持つ
 *   - 次の訪問の試算に、保存前の希望曜日も渡す (枠の無い方用)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import type { WeeklyPattern } from '@/lib/schemas/patient';

const { mockPreview } = vi.hoisted(() => ({ mockPreview: vi.fn() }));

vi.mock('@/lib/queries/patients', () => ({
  useVisitFrequencyPreview: (...args: unknown[]) => mockPreview(...args),
}));

import { WeeklyPatternEditor } from '../WeeklyPatternEditor';

const BASE: WeeklyPattern = {
  frequency_per_week: 1,
  visit_frequency: null,
  biweekly_anchor: null,
  visit_weeks: null,
  preferred_weekdays: [],
  service_minutes: 35,
  time_type: '終日',
  preferred_start: null,
  preferred_end: null,
  ng_weekdays: null,
};

let latest: WeeklyPattern = BASE;

function Harness({ initial }: { initial: WeeklyPattern }) {
  const [value, setValue] = React.useState(initial);
  latest = value;
  return (
    <WeeklyPatternEditor
      value={value}
      onChange={(next) => {
        latest = next;
        setValue(next);
      }}
      patientId="p-1"
    />
  );
}

function lastPreviewParams(): Record<string, unknown> {
  const calls = mockPreview.mock.calls;
  return calls[calls.length - 1][1] as Record<string, unknown>;
}

describe('WeeklyPatternEditor — 訪問頻度', () => {
  beforeEach(() => {
    mockPreview.mockReset();
    mockPreview.mockReturnValue({ isError: false, data: undefined });
  });

  it('毎週・未設定のときは詳しい設定を出さず、試算もしない', () => {
    render(<Harness initial={BASE} />);
    expect(screen.queryByTestId('visit-frequency-detail')).toBeNull();
    expect(mockPreview).not.toHaveBeenCalled();
  });

  it('隔週: 起点が無い間は注意書き → 水曜を選ぶと月曜にそろえて A の週と出し、注意書きを消す', async () => {
    render(<Harness initial={{ ...BASE, visit_frequency: 'biweekly' }} />);
    expect(screen.getByTestId('visit-frequency-warning').textContent).toBe(
      '⚠ 起点の週が未設定のため、毎週の予定になります。起点の週を選んでください。',
    );
    fireEvent.change(screen.getByLabelText('起点の週'), { target: { value: '2026-11-04' } });
    expect(latest.biweekly_anchor).toBe('2026-11-02');
    expect(screen.getByTestId('biweekly-anchor-label').textContent).toBe(
      '11/2（月）の週（A の週）',
    );
    expect(screen.queryByTestId('visit-frequency-warning')).toBeNull();
    await waitFor(() => expect(lastPreviewParams().biweekly_anchor).toBe('2026-11-02'));
  });

  it('月の第何週: 週が空の間は注意書き → 第 3・第 1 週を選ぶと "1,3" で持ち、注意書きを消す', async () => {
    render(<Harness initial={{ ...BASE, visit_frequency: 'monthly' }} />);
    expect(screen.getByTestId('visit-frequency-warning').textContent).toBe(
      '⚠ 第何週が未設定のため、毎週の予定になります。訪問する週を選んでください。',
    );
    fireEvent.click(screen.getByRole('checkbox', { name: '第3週' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '第1週' }));
    expect(latest.visit_weeks).toBe('1,3');
    expect(screen.queryByTestId('visit-frequency-warning')).toBeNull();
    await waitFor(() => expect(lastPreviewParams().visit_weeks).toBe('1,3'));
  });

  it('次の訪問の試算に、保存前の希望曜日を渡す', async () => {
    render(
      <Harness initial={{ ...BASE, visit_frequency: 'biweekly', biweekly_anchor: '2026-11-02' }} />,
    );
    fireEvent.click(screen.getByRole('checkbox', { name: '火' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '金' }));
    expect(latest.preferred_weekdays).toEqual(['Tue', 'Fri']);
    await waitFor(() => expect(lastPreviewParams().preferred_weekdays).toEqual(['Tue', 'Fri']));
  });
});

describe('WeeklyPatternEditor — NG 曜日', () => {
  beforeEach(() => {
    mockPreview.mockReset();
    mockPreview.mockReturnValue({ isError: false, data: undefined });
  });

  it('7 つの切り替えで NG 曜日を月→日の順に持ち、全部外すと null に戻す', () => {
    render(<Harness initial={BASE} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'NG 曜日 金' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'NG 曜日 月' }));
    expect(latest.ng_weekdays).toEqual(['Mon', 'Fri']);
    fireEvent.click(screen.getByRole('checkbox', { name: 'NG 曜日 月' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'NG 曜日 金' }));
    expect(latest.ng_weekdays).toBeNull();
  });

  it('希望曜日と重なったらやわらかく知らせる', () => {
    render(<Harness initial={{ ...BASE, preferred_weekdays: ['Tue', 'Thu'] }} />);
    expect(screen.queryByTestId('ng-weekday-conflict')).toBeNull();
    fireEvent.click(screen.getByRole('checkbox', { name: 'NG 曜日 木' }));
    expect(screen.getByTestId('ng-weekday-conflict').textContent).toBe(
      '希望曜日の 木 は NG 曜日にも入っています。NG 曜日が優先され、その曜日には予定を入れません。',
    );
  });

  it('保存済みの NG 曜日にチェックが付いている', () => {
    render(<Harness initial={{ ...BASE, ng_weekdays: ['Sun'] }} />);
    expect(screen.getByRole('checkbox', { name: 'NG 曜日 日' }).getAttribute('aria-checked')).toBe(
      'true',
    );
  });
});
