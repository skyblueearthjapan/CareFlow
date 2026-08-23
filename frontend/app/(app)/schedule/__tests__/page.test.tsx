/**
 * /schedule ページ — 上部折りたたみ (コンパクト表示) テスト。
 *
 * PO 要望 (2026-08-23): 盤面を広く見せるため、ページ見出し (らく助タイトル) と
 * 週セレクタ Card を畳めるようにした。畳んだときは週切替 / 拠点フィルタを
 * CourseDayTablePanel のコンパクト行へ委譲する (props で渡す)。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const panelProps: Record<string, unknown>[] = [];

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

vi.mock('@/components/brand/Rakusuke', () => ({
  RakusukeTitle: ({ title }: { title: string }) => <h1>{title}</h1>,
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
});
