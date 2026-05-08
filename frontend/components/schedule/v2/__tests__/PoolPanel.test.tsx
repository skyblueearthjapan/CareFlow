/**
 * PoolPanel — Wave 18 Phase B-3 / B-4 テスト.
 *
 * - poolGroupKey / poolGroupLabel pure-function unit tests (B-3)
 * - PoolGroupedByWeekday: 希望曜日別グループ化 + 希望なしは末尾 (B-3)
 * - formatPreferredTimeLabel: WeeklyPattern → ラベル変換 (B-4)
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@dnd-kit/core', () => ({
  useDroppable: () => ({ isOver: false, setNodeRef: vi.fn() }),
}));

vi.mock('lucide-react', () => ({
  Inbox: () => <span aria-hidden />,
}));

vi.mock('@/components/ui/card', () => ({
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { PoolGroupedByWeekday, poolGroupKey, poolGroupLabel } from '../PoolPanel';
import { formatPreferredTimeLabel, emptyWeeklyPattern } from '@/lib/schemas/patient';
import type { PatientRead } from '@/lib/schemas/patient';

describe('poolGroupKey / poolGroupLabel (B-3)', () => {
  it('preferred_weekdays が空 → __none__ / "希望なし"', () => {
    expect(poolGroupKey([])).toBe('__none__');
    expect(poolGroupKey(null)).toBe('__none__');
    expect(poolGroupKey(undefined)).toBe('__none__');
    expect(poolGroupLabel('__none__')).toBe('希望なし');
  });

  it('単曜日 [Mon] → "Mon" / "月希望"', () => {
    expect(poolGroupKey(['Mon'])).toBe('Mon');
    expect(poolGroupLabel('Mon')).toBe('月希望');
  });

  it('複数曜日 [Fri, Mon, Wed] → canonical 順で結合 / "月・水・金 希望"', () => {
    expect(poolGroupKey(['Fri', 'Mon', 'Wed'])).toBe('Mon,Wed,Fri');
    expect(poolGroupLabel('Mon,Wed,Fri')).toBe('月・水・金 希望');
  });
});

describe('formatPreferredTimeLabel (B-4)', () => {
  it('time_type=固定 + preferred_start → "09:30 (固定)"', () => {
    expect(
      formatPreferredTimeLabel({
        ...emptyWeeklyPattern,
        time_type: '固定',
        preferred_start: '09:30',
      }),
    ).toBe('09:30 (固定)');
  });

  it('time_type=時間帯 + start/end → "09:30〜10:00 (時間帯)"', () => {
    expect(
      formatPreferredTimeLabel({
        ...emptyWeeklyPattern,
        time_type: '時間帯',
        preferred_start: '09:30',
        preferred_end: '10:00',
      }),
    ).toBe('09:30〜10:00 (時間帯)');
  });

  it('time_type=午前 → "午前"', () => {
    expect(formatPreferredTimeLabel({ ...emptyWeeklyPattern, time_type: '午前' })).toBe('午前');
  });

  it('time_type=終日 で start/end なし → "終日"', () => {
    expect(formatPreferredTimeLabel({ ...emptyWeeklyPattern, time_type: '終日' })).toBe('終日');
  });

  it('null/undefined → null', () => {
    expect(formatPreferredTimeLabel(null)).toBeNull();
    expect(formatPreferredTimeLabel(undefined)).toBeNull();
  });
});

describe('PoolGroupedByWeekday (B-3)', () => {
  function makePatient(
    id: string,
    name: string,
    pattern: Partial<{ preferred_weekdays: string[] }> = {},
  ): PatientRead {
    return {
      id,
      code: id,
      name,
      kana: null,
      sex: null,
      status: 'active',
      insurance: null,
      address: null,
      lat: null,
      lng: null,
      primary_office_id: null,
      sex_restriction: null,
      note: null,
      weekly_pattern: { ...pattern },
      special_weekly_pattern: null,
      special_week_active: [],
      created_at: '',
      updated_at: '',
      deleted_at: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
  }

  it('希望曜日別にセクション化され、希望なしは末尾に表示', () => {
    const patients: PatientRead[] = [
      makePatient('p1', '田中', { preferred_weekdays: ['Mon'] }),
      makePatient('p2', '佐藤', { preferred_weekdays: ['Mon', 'Wed', 'Fri'] }),
      makePatient('p3', '鈴木', { preferred_weekdays: [] }),
      makePatient('p4', '山田', { preferred_weekdays: ['Tue'] }),
    ];
    render(
      <PoolGroupedByWeekday
        patients={patients}
        renderCard={(p) => <div data-testid={`card-${p.id}`}>{p.name}</div>}
      />,
    );

    // 4 グループ生成 (Mon, Tue, Mon-Wed-Fri, __none__)
    expect(screen.getByTestId('pool-group-Mon')).toBeInTheDocument();
    expect(screen.getByTestId('pool-group-Tue')).toBeInTheDocument();
    expect(screen.getByTestId('pool-group-Mon,Wed,Fri')).toBeInTheDocument();
    expect(screen.getByTestId('pool-group-__none__')).toBeInTheDocument();

    // 各グループ内に該当 patient が描画される
    expect(screen.getByTestId('card-p1')).toHaveTextContent('田中');
    expect(screen.getByTestId('card-p2')).toHaveTextContent('佐藤');
    expect(screen.getByTestId('card-p3')).toHaveTextContent('鈴木');
    expect(screen.getByTestId('card-p4')).toHaveTextContent('山田');

    // 「希望なし」は末尾 (DOM 順): __none__ > Mon,Wed,Fri > Tue > Mon
    const groups = screen.getAllByTestId(/^pool-group-/);
    const lastKey = (groups[groups.length - 1]!.getAttribute('data-testid') ?? '').replace(
      'pool-group-',
      '',
    );
    expect(lastKey).toBe('__none__');
  });

  it('プールが空ならプレースホルダー文を表示', () => {
    render(<PoolGroupedByWeekday patients={[]} renderCard={() => <span />} />);
    expect(screen.getByText(/プールは空です/)).toBeInTheDocument();
  });
});
