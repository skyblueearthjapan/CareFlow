/**
 * AcceptanceMatrixBoard — 受け入れ枠マトリックス (PC版) の描画テスト。
 *
 * カバー:
 *   1. 拠点名・区名が表示される
 *   2. effective_status が ○ / △ / × の記号で描画される
 *   3. 定休日 (office_closed) のセルは「休」表示
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AcceptanceMatrixBoard } from '../AcceptanceMatrixBoard';
import type { AcceptanceMatrixResponse, MatrixCell } from '@/lib/schemas/v2/acceptance_matrix';
import type { AcceptanceStatus } from '@/lib/schemas/v2/acceptance';

function cell(time: string, effective: AcceptanceStatus, auto?: AcceptanceStatus): MatrixCell {
  return {
    time_slot: time,
    auto_status: auto ?? effective,
    manual_status: auto && auto !== effective ? effective : null,
    week_status: null,
    effective_status: effective,
    source: auto && auto !== effective ? 'manual_standing' : 'auto',
    metrics: {
      remaining_patients_total: 5,
      remaining_minutes_total: 420,
      available_course_count: effective === 'available' ? 3 : 0,
      consult_course_count: effective === 'consult' ? 2 : 0,
      max_free_gap_minutes: 90,
      reasons: ['60分枠あり'],
    },
  };
}

function buildData(): AcceptanceMatrixResponse {
  const slots = ['10:00:00', '11:00:00'];
  return {
    iso_year: 2026,
    iso_week: 20,
    service_minutes: 60,
    slots,
    week_generated_any: true,
    offices: [
      {
        office_id: '00000000-0000-0000-0000-000000000001',
        office_name: '稲毛拠点',
        city_names: ['稲毛区', '美浜区'],
        operating_weekdays: [0, 1, 2, 3, 4, 5],
        week_generated: true,
        days: [
          {
            weekday: 0,
            date: '2026-05-11',
            office_closed: false,
            cells: [cell('10:00:00', 'available'), cell('11:00:00', 'consult')],
          },
          {
            weekday: 6,
            date: '2026-05-17',
            office_closed: true,
            cells: [],
          },
        ],
      },
    ],
  };
}

describe('AcceptanceMatrixBoard', () => {
  it('拠点名と区名を表示する', () => {
    render(<AcceptanceMatrixBoard data={buildData()} />);
    expect(screen.getByText('稲毛拠点')).toBeInTheDocument();
    expect(screen.getByText('稲毛区・美浜区')).toBeInTheDocument();
  });

  it('effective_status を ○ / △ / × で描画する', () => {
    render(<AcceptanceMatrixBoard data={buildData()} />);
    expect(screen.getAllByText('○').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('△').length).toBeGreaterThanOrEqual(1);
    // 月曜以外・未指定セルは × (unavailable)。
    expect(screen.getAllByText('×').length).toBeGreaterThanOrEqual(1);
    // ○セルには空きコース数 (=3) が併記される。
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1);
  });

  it('定休日 (office_closed) のセルは「休」表示', () => {
    render(<AcceptanceMatrixBoard data={buildData()} />);
    // 日曜 (closed) × 2 スロット = 2 セル。
    expect(screen.getAllByText('休')).toHaveLength(2);
  });
});
