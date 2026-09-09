/**
 * 訪問モニター — 患者ステータス連動の取消 (source='status_cancel') は出さない
 * (design 2026-09-09 §3-4)。BE も返さないが、旧デプロイ向けの二重の安全網。
 *
 * ここは共通の `classifyVisitDisplay` を **使わない**: MonitorVisit は
 * `visits.status` を持たず phase (時間進捗) しか返さないため、source と status の
 * 両方を見る共通判定では永久に偽になる。source 単独で落とすのが正しい。
 */
import { describe, it, expect } from 'vitest';

import { hideStatusCancelled } from '../monitor';
import type { MonitorResponse, MonitorVisit } from '@/lib/schemas/monitor';

function visit(over: Partial<MonitorVisit> & { visit_id: string }): MonitorVisit {
  return {
    patient_id: '00000000-0000-0000-0000-0000000000ff',
    patient_name: `患者${over.visit_id}`,
    start_time: '09:00',
    end_time: '10:00',
    phase: 'future',
    alert_level: 'none',
    is_substitute: false,
    is_unplanned: false,
    pair_waiting: false,
    reviewed: false,
    ...over,
  } as MonitorVisit;
}

function response(visits: MonitorVisit[]): MonitorResponse {
  return {
    date: '2026-09-14',
    now: '2026-09-14T09:00:00+09:00',
    thresholds: {
      match_m: 100,
      review_m: 300,
      accuracy_m: 100,
      no_show_grace_min: 30,
      late_min: 15,
      max_inprogress_min: 180,
    },
    offices: [],
    staff: [
      {
        staff_ids: [],
        visits,
      },
    ],
  } as unknown as MonitorResponse;
}

describe('hideStatusCancelled (monitor)', () => {
  it('source=status_cancel だけを落とす (phase を持たない判定に依存しない)', () => {
    const out = hideStatusCancelled(
      response([
        visit({ visit_id: 'plain', source: 'auto' }),
        visit({ visit_id: 'residue', source: 'auto', patient_status: 'admitted' }),
        // BE は status を返さないので source だけで落とせなければならない。
        visit({ visit_id: 'status', source: 'status_cancel', patient_status: 'admitted' }),
      ]),
    );
    expect(out.staff[0]!.visits.map((v) => v.visit_id)).toEqual(['plain', 'residue']);
  });

  it('source が無い旧 BE 応答は何も落とさない', () => {
    const out = hideStatusCancelled(response([visit({ visit_id: 'plain' })]));
    expect(out.staff[0]!.visits).toHaveLength(1);
  });
});
