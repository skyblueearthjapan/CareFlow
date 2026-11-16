/**
 * お知らせの記録の API の形 (zod): 理由・お名前が null でも一覧の全体を落とさない。
 * お名前はすべて見本。
 */
import { describe, expect, it } from 'vitest';

import {
  SMS_NO_NAME_LABEL,
  SMS_UNKNOWN_REASON_LABEL,
  smsAttentionSchema,
  smsPatientSummarySchema,
  smsSummarySchema,
} from '@/lib/schemas/sms';

const SUMMARY = {
  month: '2026-12',
  totals: {
    messages: 2,
    delivered: 0,
    failed: 2,
    unknown: 0,
    pending: 0,
    skipped: 0,
    delivered_rate: 0,
    segments: 2,
    est_yen: 0,
  },
  by_kind: [],
  by_carrier: [],
  by_fail_reason: [
    { reason: null, label: null, count: 1 },
    { reason: 'EXPIRED', label: '時間内に届きませんでした', count: 1 },
  ],
  by_segments: [],
  changes: { count: 0, urgent_count: 0, median_minutes_to_tell: null, by_channel: [] },
  by_week: [],
};

describe('お知らせの記録の形', () => {
  it('届かなかった理由が null なら「理由は分かりません」', () => {
    const s = smsSummarySchema.parse(SUMMARY);
    expect(s.by_fail_reason[0]).toEqual({
      reason: 'unknown',
      label: SMS_UNKNOWN_REASON_LABEL,
      count: 1,
    });
    expect(SMS_UNKNOWN_REASON_LABEL).toBe('理由は分かりません');
    expect(s.by_fail_reason[1].label).toBe('時間内に届きませんでした');
  });

  it('患者様ごと・気になる番号のお名前が null なら「（お名前なし）」', () => {
    const p = smsPatientSummarySchema.parse({
      month: '2026-12',
      items: [
        {
          patient_id: 'p1',
          patient_name: null,
          messages: 1,
          segments: 1,
          delivered: 0,
          failed: 1,
          last_delivered_at: null,
          told_by_phone: 0,
        },
      ],
    });
    expect(p.items[0].patient_name).toBe(SMS_NO_NAME_LABEL);
    expect(SMS_NO_NAME_LABEL).toBe('（お名前なし）');

    const a = smsAttentionSchema.parse({
      items: [
        {
          patient_id: 'p1',
          patient_name: null,
          relation_label: 'ご本人',
          phone_last4: '0001',
          kind: 'repeated_failure',
          kind_label: '続けて届いていません',
          count: 2,
          last_at: null,
        },
        {
          patient_id: 'p2',
          patient_name: '見本 太郎',
          relation_label: null,
          phone_last4: null,
          kind: 'blocked',
          kind_label: '受信の設定で受け取れません',
          count: 1,
          last_at: null,
        },
      ],
    });
    expect(a.items.map((i) => i.patient_name)).toEqual([SMS_NO_NAME_LABEL, '見本 太郎']);
  });
});
