/**
 * P0-2 Commit 3: PUT /patients/{id}/fixed-visits エンベロープレスポンスの
 * 寛容パース (`patientFixedVisitsBulkPutResponseSchema` /
 * `parsePatientFixedVisitsBulkPutResponse`) 単体テスト.
 *
 * 方針:
 *   - warnings 要素は {code, message, weekday(nullable), severity} を受理し、
 *     未知 code / severity / 余剰キーも壊さない (寛容パース).
 *   - items は既存 Read スキーマで検証するが、要素 drift 時も warnings 表示を
 *     巻き込まない (items のみ [] に落ちる).
 *   - object でない等の致命ケースでも throw せず {items:[], warnings:[]} を返し、
 *     採用/保存フローを止めない.
 */
import { describe, it, expect } from 'vitest';

import {
  patientFixedVisitsBulkPutResponseSchema,
  parsePatientFixedVisitsBulkPutResponse,
} from '../patient_fixed_visit';

const READ_ITEM = {
  id: '00000000-0000-4000-8000-000000000001',
  patient_id: '00000000-0000-4000-8000-000000000002',
  mode: 'normal' as const,
  weekday: 1,
  start_time: '14:00',
  duration_min: 35,
  slot_index: 0,
  course_template_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

describe('patientFixedVisitsBulkPutResponseSchema (P0-2 Commit 3)', () => {
  it('items + warnings のエンベロープを parse する', () => {
    const result = patientFixedVisitsBulkPutResponseSchema.safeParse({
      items: [READ_ITEM],
      warnings: [
        { code: 'time_conflict', message: '時間が重複', weekday: 1, severity: 'warning' },
        { code: 'lunch_break', message: '昼休みと重複', weekday: null, severity: 'warning' },
      ],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.items).toHaveLength(1);
      expect(result.data.warnings).toHaveLength(2);
      expect(result.data.warnings[1]!.weekday).toBeNull();
    }
  });

  it('未知 code / severity / 余剰キーを壊さず受理する (寛容パース)', () => {
    const result = patientFixedVisitsBulkPutResponseSchema.safeParse({
      items: [],
      warnings: [
        { code: 'future_new_code', message: '将来警告', severity: 'critical', extra: 'ok' },
      ],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.warnings[0]!.code).toBe('future_new_code');
      expect(result.data.warnings[0]!.severity).toBe('critical');
    }
  });

  it('items drift 時は items のみ [] に落ち、warnings は保持される', () => {
    const result = patientFixedVisitsBulkPutResponseSchema.safeParse({
      items: [{ bogus: true }],
      warnings: [{ code: 'x', message: 'm', weekday: 0, severity: 'warning' }],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.items).toEqual([]);
      expect(result.data.warnings).toHaveLength(1);
    }
  });

  it('parsePatientFixedVisitsBulkPutResponse は object でない値でも throw せず空を返す', () => {
    // week_sync は後から追加されたフィールド (フォールバックは null)。
    const empty = { items: [], warnings: [], week_sync: null };
    expect(parsePatientFixedVisitsBulkPutResponse(null)).toEqual(empty);
    expect(parsePatientFixedVisitsBulkPutResponse('boom')).toEqual(empty);
    expect(parsePatientFixedVisitsBulkPutResponse(undefined)).toEqual(empty);
  });
});
