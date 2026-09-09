/**
 * 患者ステータス連動の zod / 判定ヘルパのユニットテスト
 * (`docs/plans/patient-status-schedule-design-2026-09-09.md` §7-6 FE-3)。
 *
 * BE `app/schemas/patient_status.py` と 1:1 のため、**キー名と既定値**を固定する。
 * ここがズレると「表示 0 件・実行 9 件」のような事故になる。
 */
import { describe, it, expect } from 'vitest';

import { formatJstMonthDay, formatStatusSince, jstDateString } from '@/lib/format/patientStatus';
import {
  STATUS_LABEL,
  isSchedulableStatus,
  statusChangeDirection,
  STATUS_OPTIONS,
} from '@/lib/schemas/patient';
import {
  patientNotActiveDetailSchema,
  statusChangeRequestSchema,
  statusChangeResultSchema,
  statusImpactSchema,
} from '@/lib/schemas/patientStatus';

const PID = '00000000-0000-0000-0000-000000000001';
const SPID = '00000000-0000-0000-0000-0000000000a1';

describe('statusChangeDirection', () => {
  it('稼働中 → 非稼働 は deactivate、逆は reactivate', () => {
    for (const s of ['suspended', 'admitted', 'pending', 'cancelled'] as const) {
      expect(statusChangeDirection('active', s)).toBe('deactivate');
      expect(statusChangeDirection(s, 'active')).toBe('reactivate');
    }
  });

  it('同じ側どうし (開始前 → 入院中 など) は none', () => {
    expect(statusChangeDirection('active', 'active')).toBe('none');
    expect(statusChangeDirection('admitted', 'suspended')).toBe('none');
    expect(statusChangeDirection('pending', 'cancelled')).toBe('none');
    // 開始前 (pending) も非稼働扱い = PO 決定。稼働中との往復だけが向きを持つ。
    expect(statusChangeDirection('pending', 'active')).toBe('reactivate');
  });

  it('null / undefined / 旧値は非稼働側に倒れる', () => {
    expect(statusChangeDirection(null, 'active')).toBe('reactivate');
    expect(statusChangeDirection(undefined, undefined)).toBe('none');
    expect(statusChangeDirection('inactive', 'active')).toBe('reactivate');
    expect(isSchedulableStatus('inactive')).toBe(false);
    expect(isSchedulableStatus('active')).toBe(true);
  });

  it('STATUS_LABEL は 5 値すべてを持つ', () => {
    for (const s of STATUS_OPTIONS) expect(STATUS_LABEL[s]).toBeTruthy();
    expect(STATUS_LABEL.admitted).toBe('入院中');
    expect(STATUS_LABEL.active).toBe('稼働中');
  });
});

describe('statusImpactSchema', () => {
  it('BE の応答例 (§7-3 (a)) をそのまま通す', () => {
    const parsed = statusImpactSchema.parse({
      patient_id: PID,
      current_status: 'active',
      to_status: 'admitted',
      from_date: '2026-09-09',
      direction: 'deactivate',
      visits: {
        total: 9,
        by_week: [{ iso_year: 2026, iso_week: 38, count: 3, label: '9/14週' }],
        by_source: { auto: 7, manual_week: 2, inbound: 0 },
        pair_groups: 0,
        excluded: { checked_in: 0, in_progress: 0, week_pinned: 0 },
      },
      special_period: {
        id: SPID,
        start_date: '2026-09-03',
        end_date: '2026-12-02',
        pool_marks: 25,
        placed_marks: 5,
        placed_future_visits: 2,
      },
      fixed_visit_rows: 3,
      pending_requests: 1,
      kaipoke_weeks: 3,
      regenerate: null,
    });
    expect(parsed.visits.total).toBe(9);
    expect(parsed.visits.by_week[0]?.label).toBe('9/14週');
    expect(parsed.special_period?.pool_marks).toBe(25);
    expect(parsed.regenerate ?? null).toBeNull();
  });

  it('省略可能な集計キーは既定値で埋まる (旧 BE 耐性)', () => {
    const parsed = statusImpactSchema.parse({
      patient_id: PID,
      current_status: 'admitted',
      to_status: 'active',
      from_date: '2026-09-09',
      direction: 'reactivate',
      visits: { total: 0 },
      regenerate: { weeks: [{ iso_year: 2026, iso_week: 39, count: 2, label: '9/21週' }] },
    });
    expect(parsed.visits.by_week).toEqual([]);
    expect(parsed.visits.excluded).toEqual({});
    expect(parsed.visits.pair_groups).toBe(0);
    expect(parsed.fixed_visit_rows).toBe(0);
    expect(parsed.regenerate?.total).toBe(0);
  });
});

describe('statusChangeRequestSchema', () => {
  it('special_period_action の既定は keep・regenerate の既定は true', () => {
    const parsed = statusChangeRequestSchema.parse({ status: 'admitted' });
    expect(parsed.special_period_action).toBe('keep');
    expect(parsed.regenerate).toBe(true);
    expect(parsed.from_date).toBeUndefined();
  });

  it('Literal 外のステータスは弾く', () => {
    expect(() => statusChangeRequestSchema.parse({ status: 'inactive' })).toThrow();
  });
});

describe('statusChangeResultSchema', () => {
  it('BE の応答例 (§7-3 (b)) をそのまま通す', () => {
    const parsed = statusChangeResultSchema.parse({
      patient: {
        id: PID,
        code: 'P001',
        name: '小湊',
        status: 'admitted',
        created_at: '2026-09-01T00:00:00',
        updated_at: '2026-09-09T00:00:00',
        status_changed_at: '2026-09-09T12:00:00',
      },
      direction: 'deactivate',
      cancelled_visit_ids: [SPID],
      cancelled_count: 9,
      special_period: { id: SPID, action: 'end', cancelled_pool_marks: 25 },
      rejected_requests: 1,
      op_groups: [{ iso_year: 2026, iso_week: 38, op_group_id: SPID }],
      regenerated: null,
      notification_count: 2,
    });
    expect(parsed.cancelled_count).toBe(9);
    expect(parsed.special_period?.action).toBe('end');
    expect(parsed.op_groups[0]?.iso_week).toBe(38);
    expect(parsed.patient.status_changed_at).toBe('2026-09-09T12:00:00');
  });

  it('欠けたキーは既定値で埋まる', () => {
    const parsed = statusChangeResultSchema.parse({
      patient: {
        id: PID,
        code: 'P001',
        name: '小湊',
        status: 'active',
        created_at: '2026-09-01T00:00:00',
        updated_at: '2026-09-09T00:00:00',
      },
      direction: 'reactivate',
    });
    expect(parsed.cancelled_count).toBe(0);
    expect(parsed.cancelled_visit_ids).toEqual([]);
    expect(parsed.rejected_requests).toBe(0);
  });
});

describe('patientNotActiveDetailSchema (Phase 2 の 422)', () => {
  it('can_override の既定は false', () => {
    const parsed = patientNotActiveDetailSchema.parse({
      code: 'patient_not_active',
      patient_id: PID,
      status: 'admitted',
      status_label: '入院中',
      message: '入院中のため予定に入れられません',
    });
    expect(parsed.can_override).toBe(false);
  });
});

describe('日付整形 (JST 固定)', () => {
  it('jstDateString は Asia/Tokyo の YYYY-MM-DD', () => {
    // 2026-09-09T15:30Z = JST 2026-09-10 00:30 → 「今日」は 9/10
    const base = new Date('2026-09-09T15:30:00Z');
    expect(jstDateString(0, base)).toBe('2026-09-10');
    expect(jstDateString(1, base)).toBe('2026-09-11');
  });

  it('formatJstMonthDay / formatStatusSince', () => {
    expect(formatJstMonthDay('2026-09-08T10:00:00+09:00')).toBe('9/8');
    expect(formatJstMonthDay(null)).toBeNull();
    expect(formatJstMonthDay('ではない')).toBeNull();
    expect(formatStatusSince('2026-09-08T10:00:00+09:00')).toBe('（9/8〜）');
    expect(formatStatusSince(undefined)).toBe('');
  });
});
