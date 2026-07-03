/**
 * 同住所・同時刻ペア補正 — monitorVisitSchema の pair_waiting 寛容パース.
 *
 * カバーするシナリオ:
 *   - pair_waiting 欠落 → false に畳む (後方互換)。
 *   - pair_waiting=true → 保持。
 */
import { describe, it, expect } from 'vitest';

import { monitorVisitSchema } from '../monitor';

const base = {
  visit_id: '00000000-0000-0000-0000-000000000001',
  patient_id: '00000000-0000-0000-0000-000000000002',
  start_time: '09:00',
  end_time: '10:00',
  phase: 'awaiting',
  alert_level: 'none',
};

describe('monitorVisitSchema — pair_waiting 寛容パース', () => {
  it('pair_waiting 欠落 → false に畳む (後方互換)', () => {
    const parsed = monitorVisitSchema.parse(base);
    expect(parsed.pair_waiting).toBe(false);
  });

  it('pair_waiting=true は保持される', () => {
    const parsed = monitorVisitSchema.parse({ ...base, pair_waiting: true });
    expect(parsed.pair_waiting).toBe(true);
  });
});
