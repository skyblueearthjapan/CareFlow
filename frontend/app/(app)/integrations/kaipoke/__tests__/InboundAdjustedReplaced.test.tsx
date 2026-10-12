/**
 * InboundControls — BP-6 (青ピン廃止 / PO 2026-10-12):
 * らく助で調整した訪問がカイポケの内容に合わせて変わるときだけ、落ち着いた 1 行を出す。
 *   - 0 件なら何も出さない
 *   - N 件なら 1 行 + 「▸ 見る」で一覧が開く (押したときだけ)
 *   - 警告色にしない
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import { InboundControls } from '../_components/InboundControls';
import type { InboundVm } from '../_components/useInbound';
import { SmartInboundPreviewSchema, type SmartInboundPreview } from '@/lib/schemas/integration';

const idleMutation = {
  mutateAsync: vi.fn(),
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
};
const eligQuery = { data: { eligible: true }, isLoading: false };

const MONDAY = new Date(2026, 6, 20);

function plan(extra: Record<string, unknown> = {}): SmartInboundPreview {
  return SmartInboundPreviewSchema.parse({
    weekStart: '2026-07-20',
    weekEnd: '2026-07-25',
    protectedDays: [],
    replaceDays: ['2026-07-22'],
    sheetId: null,
    diffSummary: {},
    replace: null,
    ...extra,
  });
}

function makeVm(overrides: Partial<InboundVm> = {}): InboundVm {
  return {
    busy: false,
    credentialsConfigured: true,
    weekOffset: 0,
    weekStart: MONDAY,
    currentElig: eligQuery,
    eligible: true,
    canGoNext: true,
    changeWeek: vi.fn(),
    goToThisWeek: vi.fn(),
    smartPreview: { ...idleMutation },
    applySmart: { ...idleMutation },
    smartPlan: null,
    sheetId: null,
    itemsQuery: { data: { items: [] } },
    items: [],
    eventsPreview: { ...idleMutation },
    applyEvents: { ...idleMutation },
    eventsPlan: null,
    eventsError: null,
    hasEventChanges: false,
    confirm: false,
    setConfirm: vi.fn(),
    runDiff: vi.fn(),
    runApply: vi.fn(),
    fetching: false,
    applying: false,
    canApply: false,
    snapshots: [],
    restoreSnapshot: vi.fn(),
    restoring: false,
    inboundHistory: [],
    ...overrides,
  } as unknown as InboundVm;
}

describe('InboundControls — らく助で調整した訪問の置き換え (BP-6)', () => {
  it('0 件なら何も出さない', () => {
    render(
      <InboundControls vm={makeVm({ smartPlan: plan({ rakusuke_adjusted_replaced: [] }) })} />,
    );
    expect(screen.getByTestId('smart-plan-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('adjusted-replaced')).not.toBeInTheDocument();
  });

  it('項目が来ない古い応答でも何も出さない', () => {
    render(<InboundControls vm={makeVm({ smartPlan: plan() })} />);
    expect(screen.queryByTestId('adjusted-replaced')).not.toBeInTheDocument();
  });

  it('N 件なら 1 行を出し、「見る」を押したときだけ一覧が開く', () => {
    render(
      <InboundControls
        vm={makeVm({
          smartPlan: plan({
            rakusuke_adjusted_replaced: [
              {
                visit_id: 'v1',
                date: '2026-07-22',
                start_time: '10:00',
                patient_label: '佐藤 花子',
                change: '10:00 → 10:30',
              },
              {
                visit_id: 'v2',
                date: '2026-07-22',
                start_time: '13:00',
                patient_label: '鈴木 一郎',
                change: '担当が変わる',
              },
            ],
          }),
        })}
      />,
    );
    const line = screen.getByTestId('adjusted-replaced');
    expect(line).toHaveTextContent(
      'らく助で調整した訪問のうち 2 件が、カイポケの内容に合わせて変わります',
    );
    // 警告色にしない
    expect(line.className).not.toMatch(/warning|error/);
    expect(screen.queryByTestId('adjusted-replaced-list')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('adjusted-replaced-toggle'));
    const list = screen.getByTestId('adjusted-replaced-list');
    expect(list).toHaveTextContent('佐藤 花子');
    expect(list).toHaveTextContent('10:00 → 10:30');
    expect(list).toHaveTextContent('鈴木 一郎');
    expect(list).toHaveTextContent('取り込み前に戻す');

    fireEvent.click(screen.getByTestId('adjusted-replaced-toggle'));
    expect(screen.queryByTestId('adjusted-replaced-list')).not.toBeInTheDocument();
  });
});
