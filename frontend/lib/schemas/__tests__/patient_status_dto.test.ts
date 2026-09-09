/**
 * 患者ステータス連動 Phase 3「表示の保険」— DTO の寛容パース
 * (design 2026-09-09 §3-4 / §3-5)。
 *
 * BE は `source` / `patient_status` を非破壊で追加する。旧デプロイの応答
 * (フィールドが無い) でも、値が想定外の型でも **落ちない** ことを担保する。
 * 落ちると盤面が丸ごと空になるため、ここは「寛容」が仕様。
 */
import { describe, it, expect } from 'vitest';

import { boardVisitSchema } from '../v2/board';
import { monitorVisitSchema } from '../monitor';
import { visitV2ReadSchema } from '../v2/visit';
import { unsentSummaryReadSchema } from '../v2/cockpit';

const UUID_A = '00000000-0000-0000-0000-000000000001';
const UUID_B = '00000000-0000-0000-0000-000000000002';

describe('boardVisitSchema — source / patient_status', () => {
  const base = {
    visit_id: UUID_A,
    patient_id: UUID_B,
    patient_name: '小湊 太郎',
    service_minutes: 35,
    start_time: '09:30',
    end_time: '10:05',
    slot_index: 0,
  };

  it('欠落しても落ちず null になる (旧 BE 応答)', () => {
    const parsed = boardVisitSchema.parse(base);
    expect(parsed.source ?? null).toBeNull();
    expect(parsed.patient_status ?? null).toBeNull();
  });

  it('値があれば保持する', () => {
    const parsed = boardVisitSchema.parse({
      ...base,
      source: 'status_cancel',
      patient_status: 'admitted',
    });
    expect(parsed.source).toBe('status_cancel');
    expect(parsed.patient_status).toBe('admitted');
  });

  it('想定外の型でも落ちない (catch → null)', () => {
    const parsed = boardVisitSchema.parse({ ...base, patient_status: 123 });
    expect(parsed.patient_status ?? null).toBeNull();
  });
});

describe('monitorVisitSchema — source / patient_status', () => {
  const base = {
    visit_id: UUID_A,
    patient_id: UUID_B,
    start_time: '09:00',
    end_time: '10:00',
    phase: 'awaiting',
    alert_level: 'none',
  };

  it('欠落しても落ちず null になる', () => {
    const parsed = monitorVisitSchema.parse(base);
    expect(parsed.source ?? null).toBeNull();
    expect(parsed.patient_status ?? null).toBeNull();
  });

  it('値があれば保持する', () => {
    const parsed = monitorVisitSchema.parse({ ...base, source: 'auto', patient_status: 'pending' });
    expect(parsed.source).toBe('auto');
    expect(parsed.patient_status).toBe('pending');
  });
});

describe('visitV2ReadSchema — patient_status', () => {
  const base = {
    id: UUID_A,
    patient_id: UUID_B,
    visit_date: '2026-09-14',
    start_time: '09:30',
    end_time: '10:05',
    created_at: '2026-09-10T00:00:00Z',
    updated_at: '2026-09-10T00:00:00Z',
  };

  it('欠落しても落ちない / 値があれば保持する', () => {
    expect(visitV2ReadSchema.parse(base).patient_status ?? null).toBeNull();
    expect(visitV2ReadSchema.parse({ ...base, patient_status: 'suspended' }).patient_status).toBe(
      'suspended',
    );
  });
});

describe('unsentSummaryReadSchema — inactive_groups / inactive_residue', () => {
  const base = {
    week_start: '2026-09-14',
    snapshot: null,
    sheet_id: null,
    sendable_count: 0,
    past_count: 0,
  };

  it('欠落しても既定値 ([] / 0) に倒す (旧 BE 応答)', () => {
    const parsed = unsentSummaryReadSchema.parse(base);
    expect(parsed.inactive_groups).toEqual([]);
    expect(parsed.inactive_residue).toBe(0);
  });

  it('値があれば保持する (count = 総数 / sendable_count = 送れる分)', () => {
    const parsed = unsentSummaryReadSchema.parse({
      ...base,
      inactive_groups: [
        {
          patient_id: UUID_B,
          patient_name: '小湊 太郎',
          status: 'admitted',
          status_label: '入院中',
          count: 4,
          sendable_count: 1,
        },
      ],
      inactive_residue: 2,
    });
    expect(parsed.inactive_groups).toHaveLength(1);
    expect(parsed.inactive_groups[0]).toMatchObject({
      patient_name: '小湊 太郎',
      count: 4,
      sendable_count: 1,
    });
    expect(parsed.inactive_residue).toBe(2);
  });

  it('要素ごとに寛容 — 1 件の型崩れで配列全体を捨てない', () => {
    const parsed = unsentSummaryReadSchema.parse({
      ...base,
      inactive_groups: [
        // patient_name / count / sendable_count が壊れていても要素は残す。
        { patient_id: UUID_B, patient_name: 42, status: null, count: 'x' },
        {
          patient_id: 'p2',
          patient_name: '藤原 花子',
          status_label: '一時休止',
          count: 2,
          sendable_count: 2,
        },
      ],
    });
    expect(parsed.inactive_groups).toHaveLength(2);
    expect(parsed.inactive_groups[0]).toMatchObject({ patient_name: '', count: 0 });
    // sendable_count 欠落は 0 (旧 BE 応答)。
    expect(parsed.inactive_groups[0]!.sendable_count).toBe(0);
    expect(parsed.inactive_groups[1]).toMatchObject({ patient_name: '藤原 花子', count: 2 });
  });

  it('既定の空配列はパースごとに別インスタンス (共有して書き換わらない)', () => {
    const a = unsentSummaryReadSchema.parse({ ...base, inactive_groups: 'oops' });
    const b = unsentSummaryReadSchema.parse({ ...base, inactive_groups: 'oops' });
    expect(a.inactive_groups).not.toBe(b.inactive_groups);
  });

  it('想定外の型でも落ちない (catch)', () => {
    const parsed = unsentSummaryReadSchema.parse({
      ...base,
      inactive_groups: 'oops',
      inactive_residue: 'oops',
    });
    expect(parsed.inactive_groups).toEqual([]);
    expect(parsed.inactive_residue).toBe(0);
  });
});
