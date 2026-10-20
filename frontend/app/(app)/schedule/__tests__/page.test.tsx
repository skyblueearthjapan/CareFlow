/**
 * /schedule ページ — 上部折りたたみ (コンパクト表示) テスト。
 *
 * PO 要望 (2026-08-23): 盤面を広く見せるため、ページ見出し (らく助タイトル) と
 * 週セレクタ Card を畳めるようにした。畳んだときは週切替 / 拠点フィルタを
 * CourseDayTablePanel のコンパクト行へ委譲する (props で渡す)。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';

const panelProps: Record<string, unknown>[] = [];

// 道の ?unit&day&view を読む (App Router の外なので window.location から作る)。
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { role: 'admin' } }, status: 'authenticated' }),
}));

vi.mock('@/lib/queries/offices', () => ({
  useOffices: () => ({ allOffices: [{ id: 'office-honten', name: '本店' }], isLoading: false }),
}));

vi.mock('@/components/schedule/v2/CourseDayTablePanel', () => ({
  CourseDayTablePanel: (props: Record<string, unknown>) => {
    panelProps.push(props);
    return <div data-testid="course-day-table-panel" />;
  },
}));

// 実現性チェックボタンは react-query の mutation を使う (Provider 不要にするため stub)。
vi.mock('@/components/template-ws/TemplateWorkspaceLink', () => ({
  TemplateWorkspaceLink: () => null,
}));
vi.mock('@/components/schedule/FeasibilityCheckButton', () => ({
  FeasibilityCheckButton: () => <div data-testid="feasibility-check-stub" />,
}));
// 突合レポートボタンも同様に stub (react-query mutation を使うため)。
vi.mock('@/components/integrations/ReconcileReportButton', () => ({
  ReconcileReportButton: () => <div data-testid="reconcile-report-stub" />,
  ReconcileRefreshButton: () => <div data-testid="reconcile-refresh-stub" />,
  ReconcileToolbar: () => <div data-testid="reconcile-toolbar-stub" />,
}));
vi.mock('@/components/brand/Rakusuke', () => ({
  RakusukeTitle: ({ title, subtitle }: { title: string; subtitle?: string }) => (
    <>
      <h1>{title}</h1>
      {subtitle ? <p>{subtitle}</p> : null}
    </>
  ),
}));

vi.mock('@/components/ui/card', () => ({
  Card: ({
    children,
    className,
    ...rest
  }: {
    children: React.ReactNode;
    className?: string;
    [k: string]: unknown;
  }) => (
    <div className={className} {...rest}>
      {children}
    </div>
  ),
}));

import SchedulePage from '../page';
import { useUIStore } from '@/lib/stores/ui';

describe('/schedule — 上部折りたたみ', () => {
  beforeEach(() => {
    panelProps.length = 0;
    window.localStorage.clear();
    useUIStore.setState({ scheduleHeaderCollapsed: false });
  });

  it('既定 (展開) では見出しと週セレクタ Card が表示される', () => {
    render(<SchedulePage />);

    expect(screen.getByTestId('schedule-page-header')).toBeInTheDocument();
    expect(screen.getByTestId('schedule-week-selector-card')).toBeInTheDocument();
    expect(screen.getByTestId('course-day-table-panel')).toBeInTheDocument();
  });

  it('畳むと見出しと週セレクタ Card が消える (盤面がその分広がる)', () => {
    useUIStore.setState({ scheduleHeaderCollapsed: true });
    render(<SchedulePage />);

    expect(screen.queryByTestId('schedule-page-header')).not.toBeInTheDocument();
    expect(screen.queryByTestId('schedule-week-selector-card')).not.toBeInTheDocument();
    // 盤面は残る.
    expect(screen.getByTestId('course-day-table-panel')).toBeInTheDocument();
  });

  it('週切替 / 拠点フィルタのハンドラを Panel に渡す (コンパクト行が描画するため)', () => {
    render(<SchedulePage />);

    expect(panelProps.length).toBeGreaterThan(0);
    const props = panelProps[panelProps.length - 1];
    expect(typeof props.onWeekChange).toBe('function');
    expect(typeof props.onOfficeChange).toBe('function');
  });

  it('URL の ?unit&day&view を初期値にし、変わったら replaceState で書く (他のクエリは残す)', () => {
    window.history.replaceState(null, '', '/schedule?visit=abc&unit=course&day=wed&view=timeline');
    const spy = vi.spyOn(window.history, 'replaceState');
    render(<SchedulePage />);
    const props = panelProps[panelProps.length - 1]!;
    expect(props.initialViewState).toEqual({ unit: 'course', day: 2, view: 'timeline' });
    // 見出しの説明文は unit に合わせる。
    expect(screen.getByText('コースごとに、曜日と週の予定を見ます。')).toBeInTheDocument();

    act(() => {
      (props.onViewStateChange as (s: unknown) => void)({
        unit: 'staff',
        day: 'week',
        view: 'list',
      });
    });
    expect(spy).toHaveBeenCalled();
    const params = new URLSearchParams(window.location.search);
    expect(params.get('visit')).toBe('abc');
    expect(params.get('unit')).toBe('staff');
    expect(params.get('day')).toBe('week');
    expect(params.get('view')).toBe('list');
    expect(
      screen.getByText('職員ごとに、曜日と週の予定と、その日の訪問の件数を見ます。'),
    ).toBeInTheDocument();
    spy.mockRestore();
    window.history.replaceState(null, '', '/');
  });
});
