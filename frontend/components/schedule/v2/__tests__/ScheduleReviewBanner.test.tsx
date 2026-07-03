/**
 * ScheduleReviewBanner テスト (Schedule Advisor Phase 3「見直しどき通知」).
 *
 * useScheduleHealthTrend をモックし、劣化判定 (FE 側) と UI 連携を施錠する:
 *   1. 直近4週の移動時間が前4週比 +20% 以上 → バナー表示 (+N% 文言).
 *   2. +20% 未満 → 非表示.
 *   3. +20% ちょうどの境界 (prior=100/recent=120) → shouldShow=true, increasePct=20.
 *   4. +19% (prior=100/recent=119) → shouldShow=false.
 *   5. データ薄週 (visit_count=0 が 3週以上) → 抑制 (非表示).
 *   6. [今週は非表示] → localStorage に記憶し即時に消える.
 *   7. [スケジュール診断を開く] → onOpenHealth コールバックが呼ばれる.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

import type { ScheduleHealthTrendWeek } from '@/lib/schemas/v2/scheduleHealth';

const { trendMock } = vi.hoisted(() => ({ trendMock: vi.fn() }));

vi.mock('@/lib/queries/scheduleHealth', () => ({
  useScheduleHealthTrend: (params: unknown) => trendMock(params),
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    ...rest
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} {...(rest as Record<string, unknown>)}>
      {children}
    </button>
  ),
}));

vi.mock('lucide-react', () => ({
  TriangleAlert: () => <span data-testid="icon-triangle-alert" />,
}));

import {
  ScheduleReviewBanner,
  evaluateScheduleReview,
  reviewDismissKey,
} from '../ScheduleReviewBanner';

// --- フィクスチャ ---

function week(isoWeek: number, travel: number, visits: number): ScheduleHealthTrendWeek {
  return {
    iso_year: 2026,
    iso_week: isoWeek,
    totals: { visit_count: visits, travel_minutes: travel, travel_km: 0, gap_minutes: 0 },
  };
}

// 前4週 travel=100 (25*4), 直近4週 travel=130 → +30% (>= +20%). 全週 visit あり.
function increasingWeeks(): ScheduleHealthTrendWeek[] {
  return [
    week(13, 25, 5),
    week(14, 25, 5),
    week(15, 25, 5),
    week(16, 25, 5),
    week(17, 30, 5),
    week(18, 32, 5),
    week(19, 33, 5),
    week(20, 35, 5),
  ];
}

// 前4週 travel=100, 直近4週 travel=105 → +5% (< +20%).
function flatWeeks(): ScheduleHealthTrendWeek[] {
  return [
    week(13, 25, 5),
    week(14, 25, 5),
    week(15, 25, 5),
    week(16, 25, 5),
    week(17, 26, 5),
    week(18, 26, 5),
    week(19, 26, 5),
    week(20, 27, 5),
  ];
}

// 前4週 travel=100 (25*4), 直近4週 travel=120 (30*4) → ちょうど +20%.
function exactThresholdWeeks(): ScheduleHealthTrendWeek[] {
  return [
    week(13, 25, 5),
    week(14, 25, 5),
    week(15, 25, 5),
    week(16, 25, 5),
    week(17, 30, 5),
    week(18, 30, 5),
    week(19, 30, 5),
    week(20, 30, 5),
  ];
}

// 前4週 travel=100, 直近4週 travel=119 (29+30+30+30) → +19% (< +20%).
function belowThresholdWeeks(): ScheduleHealthTrendWeek[] {
  return [
    week(13, 25, 5),
    week(14, 25, 5),
    week(15, 25, 5),
    week(16, 25, 5),
    week(17, 29, 5),
    week(18, 30, 5),
    week(19, 30, 5),
    week(20, 30, 5),
  ];
}

function renderBanner(onOpenHealth = vi.fn()) {
  render(
    <ScheduleReviewBanner isoYear={2026} isoWeek={20} officeId="o1" onOpenHealth={onOpenHealth} />,
  );
  return onOpenHealth;
}

beforeEach(() => {
  trendMock.mockReset();
  window.localStorage.clear();
});

// --- 純関数の判定 ---

describe('evaluateScheduleReview', () => {
  it('+20% 以上の増加で shouldShow=true と増加率 % を返す', () => {
    const r = evaluateScheduleReview(increasingWeeks());
    expect(r.shouldShow).toBe(true);
    expect(r.increasePct).toBe(30); // (130-100)/100.
  });

  it('+20% 未満なら shouldShow=false', () => {
    expect(evaluateScheduleReview(flatWeeks()).shouldShow).toBe(false);
  });

  it('+20% ちょうどの境界: prior=100/recent=120 → shouldShow=true かつ increasePct=20', () => {
    const r = evaluateScheduleReview(exactThresholdWeeks());
    expect(r.shouldShow).toBe(true);
    expect(r.increasePct).toBe(20);
  });

  it('+19% (prior=100/recent=119) → shouldShow=false', () => {
    expect(evaluateScheduleReview(belowThresholdWeeks()).shouldShow).toBe(false);
  });

  it('8週未満なら判定しない', () => {
    expect(evaluateScheduleReview(increasingWeeks().slice(0, 6)).shouldShow).toBe(false);
  });

  it('visit_count=0 の週が3週以上あれば抑制する', () => {
    const w = increasingWeeks();
    w[0].totals.visit_count = 0;
    w[1].totals.visit_count = 0;
    w[2].totals.visit_count = 0;
    expect(evaluateScheduleReview(w).shouldShow).toBe(false);
  });

  it('直近週の visit_count=0 なら抑制する', () => {
    const w = increasingWeeks();
    w[w.length - 1].totals.visit_count = 0;
    expect(evaluateScheduleReview(w).shouldShow).toBe(false);
  });

  it('前ブロックの移動時間が0なら分母なしで抑制する', () => {
    const w = increasingWeeks().map((x, i) =>
      i < 4 ? { ...x, totals: { ...x.totals, travel_minutes: 0 } } : x,
    );
    expect(evaluateScheduleReview(w).shouldShow).toBe(false);
  });
});

// --- バナー UI ---

describe('ScheduleReviewBanner', () => {
  it('判定成立で +N% 文言のバナーを表示する', () => {
    trendMock.mockReturnValue({ data: { weeks: increasingWeeks() } });
    renderBanner();
    const banner = screen.getByTestId('schedule-review-banner');
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner.textContent).toContain('+30%');
    expect(banner.textContent).toContain('見直しどき');
  });

  it('判定非成立ではバナーを表示しない', () => {
    trendMock.mockReturnValue({ data: { weeks: flatWeeks() } });
    renderBanner();
    expect(screen.queryByTestId('schedule-review-banner')).toBeNull();
  });

  it('データ薄週 (0件週3つ) では抑制する', () => {
    const w = increasingWeeks();
    w[0].totals.visit_count = 0;
    w[1].totals.visit_count = 0;
    w[2].totals.visit_count = 0;
    trendMock.mockReturnValue({ data: { weeks: w } });
    renderBanner();
    expect(screen.queryByTestId('schedule-review-banner')).toBeNull();
  });

  it('[今週は非表示] で localStorage に記憶し即時に消える', () => {
    trendMock.mockReturnValue({ data: { weeks: increasingWeeks() } });
    renderBanner();
    expect(screen.getByTestId('schedule-review-banner')).toBeTruthy();
    fireEvent.click(screen.getByTestId('schedule-review-dismiss'));
    expect(screen.queryByTestId('schedule-review-banner')).toBeNull();
    expect(window.localStorage.getItem(reviewDismissKey('o1', 2026, 20))).toBe('1');
  });

  it('既に非表示記憶があれば最初から出さない', () => {
    window.localStorage.setItem(reviewDismissKey('o1', 2026, 20), '1');
    trendMock.mockReturnValue({ data: { weeks: increasingWeeks() } });
    renderBanner();
    expect(screen.queryByTestId('schedule-review-banner')).toBeNull();
  });

  it('[スケジュール診断を開く] で onOpenHealth を呼ぶ', () => {
    trendMock.mockReturnValue({ data: { weeks: increasingWeeks() } });
    const onOpen = renderBanner();
    fireEvent.click(screen.getByTestId('schedule-review-open-health'));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
