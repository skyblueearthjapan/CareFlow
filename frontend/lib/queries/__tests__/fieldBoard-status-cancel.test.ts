/**
 * 現場ボード — 患者ステータス連動の取消 (source='status_cancel') は出さない
 * (design 2026-09-09 §3-4)。BE でも除外するが、旧デプロイ向けの二重の安全網。
 *
 * 非稼働患者の **残っている予定** は落とさない (バッジで見せる = 原則⑥)。
 */
import { describe, it, expect } from 'vitest';

import { hideStatusCancelled } from '../fieldBoard';
import type { BoardResponse, BoardVisit } from '@/lib/schemas/v2/board';

function visit(over: Partial<BoardVisit> & { visit_id: string }): BoardVisit {
  return {
    patient_id: '00000000-0000-0000-0000-0000000000ff',
    patient_name: `患者${over.visit_id}`,
    service_minutes: 35,
    start_time: '09:30',
    end_time: '10:05',
    mode: 'normal',
    slot_index: 0,
    status: 'planned',
    ...over,
  } as BoardVisit;
}

function board(visits: BoardVisit[]): BoardResponse {
  return {
    iso_year: 2026,
    iso_week: 38,
    course_max: 6,
    offices: [],
    weekdays: [],
    board: [
      {
        office_id: '00000000-0000-0000-0000-00000000000a',
        weekday: 0,
        weekday_code: 'Mon',
        closed: false,
        staff_count: 1,
        manager_count: 0,
        patient_count: visits.length,
        courses: [
          {
            course_id: null,
            course_code: 'A',
            course_label: 'A',
            staff_name: null,
            visits,
            capacity: { filled: visits.length, max: 6, total_minutes: 0, remaining: 6 },
          },
        ],
      },
    ],
  } as BoardResponse;
}

describe('hideStatusCancelled', () => {
  it('連動取消だけを落とす (通常訪問・今週だけ取消・非稼働の残骸は残す)', () => {
    const out = hideStatusCancelled(
      board([
        visit({ visit_id: 'plain', source: 'auto' }),
        visit({ visit_id: 'manual', source: 'manual_cancel', status: 'cancelled' }),
        visit({ visit_id: 'residue', source: 'auto', patient_status: 'admitted' }),
        visit({
          visit_id: 'status',
          source: 'status_cancel',
          status: 'cancelled',
          patient_status: 'admitted',
        }),
      ]),
    );
    const ids = out.board[0]!.courses[0]!.visits.map((v) => v.visit_id);
    expect(ids).toEqual(['plain', 'manual', 'residue']);
  });

  it('source が無い旧 BE 応答は何も落とさない', () => {
    const out = hideStatusCancelled(board([visit({ visit_id: 'plain' })]));
    expect(out.board[0]!.courses[0]!.visits).toHaveLength(1);
  });
});
