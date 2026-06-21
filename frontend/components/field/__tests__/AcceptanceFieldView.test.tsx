/**
 * AcceptanceFieldView — モバイル受け入れ枠マトリックス (現場ボード /m) の描画テスト。
 *
 * カバー:
 *   1. 拠点名とマトリックスの ○ / △ 記号が表示される
 *   2. 定休日 (office_closed) の曜日列は「休」表示
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { AcceptanceMatrixResponse } from '@/lib/schemas/v2/acceptance_matrix';

const MOCK: AcceptanceMatrixResponse = {
  iso_year: 2026,
  iso_week: 20,
  service_minutes: 60,
  slots: ['10:00:00', '11:00:00'],
  week_generated_any: true,
  offices: [
    {
      office_id: '00000000-0000-0000-0000-000000000001',
      office_name: '稲毛拠点',
      city_names: ['稲毛区'],
      operating_weekdays: [0, 1, 2, 3, 4, 5],
      week_generated: true,
      days: [
        {
          weekday: 0,
          date: '2026-05-11',
          office_closed: false,
          cells: [
            {
              time_slot: '10:00:00',
              auto_status: 'available',
              manual_status: null,
              week_status: null,
              effective_status: 'available',
              source: 'auto',
              metrics: {
                remaining_patients_total: 5,
                remaining_minutes_total: 420,
                available_course_count: 1,
                consult_course_count: 0,
                max_free_gap_minutes: 90,
                reasons: ['60分枠あり'],
              },
            },
            {
              time_slot: '11:00:00',
              auto_status: 'consult',
              manual_status: null,
              week_status: null,
              effective_status: 'consult',
              source: 'auto',
              metrics: {
                remaining_patients_total: 3,
                remaining_minutes_total: 60,
                available_course_count: 0,
                consult_course_count: 1,
                max_free_gap_minutes: 30,
                reasons: ['部分空きのみ(相談)'],
              },
            },
          ],
        },
        { weekday: 6, date: '2026-05-17', office_closed: true, cells: [] },
      ],
    },
  ],
};

vi.mock('@/lib/queries/acceptance_matrix', () => ({
  useAcceptanceMatrix: () => ({ data: MOCK, isLoading: false, isError: false }),
}));

import { AcceptanceFieldView } from '../AcceptanceFieldView';

describe('AcceptanceFieldView', () => {
  it('拠点名とマトリックスの ○ / △ 記号を表示する', () => {
    render(<AcceptanceFieldView />);
    expect(screen.getByText('稲毛拠点')).toBeInTheDocument();
    // 月曜の 10:00=○, 11:00=△ がマトリックスに出る (× は他の曜日列に多数)。
    expect(screen.getAllByText('○').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('△').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('×').length).toBeGreaterThanOrEqual(1);
  });

  it('定休日 (日曜) の列は「休」表示', () => {
    render(<AcceptanceFieldView />);
    // 日曜 (closed) の 2 時間帯セル = 「休」2 つ。
    expect(screen.getAllByText('休')).toHaveLength(2);
  });
});
