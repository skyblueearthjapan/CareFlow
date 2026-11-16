/**
 * 予定のお知らせ (SMS) の画面の言葉 (決まり §11)。
 * - 送っていない理由: test_mode と前の名前 not_in_test_allowlist は同じ言葉。
 * - キャリア: KDDI の値をそのまま持つので大文字・小文字を区別しない。知らない値はそのまま出す。
 * - 待ちの分: 小数は丸める。
 */
import { describe, expect, it } from 'vitest';

import { smsPendingListSchema, weekNoticePreviewSchema } from '@/lib/schemas/sms';

import {
  formatHm,
  formatPhone,
  formatWaiting,
  smsCarrierLabel,
  smsNotSendableLabel,
  smsStatusWithReason,
} from '../smsLabels';

describe('送っていない理由', () => {
  it('試験の状態 (新しい名前と前の名前)', () => {
    expect(smsStatusWithReason('skipped', 'test_mode')).toBe('送っていません（試験の状態のため）');
    expect(smsStatusWithReason('skipped', 'not_in_test_allowlist')).toBe(
      '送っていません（試験の状態のため）',
    );
  });

  it('送る直前の確かめで送らなかった理由 (決まり §13-5)・知らない理由は「ほかの理由のため」', () => {
    expect(smsStatusWithReason('skipped', 'expired')).toBe(
      '送っていません（送る前に時間が過ぎたため）',
    );
    expect(smsStatusWithReason('skipped', 'superseded')).toBe(
      '送っていません（新しいお知らせを作ったため）',
    );
    expect(smsStatusWithReason('skipped', 'contact_changed')).toBe(
      '送っていません（連絡先が変わったため）',
    );
    expect(smsStatusWithReason('skipped', 'patient_inactive')).toBe(
      '送っていません（稼働中でなくなったため）',
    );
    expect(smsStatusWithReason('skipped', 'zzz')).toBe('送っていません（ほかの理由のため）');
  });
});

describe('送れない理由 (決まり §13-3)', () => {
  it('サーバーの言葉を先に使い、無いときは決まりの言葉', () => {
    expect(smsNotSendableLabel('unsupported_char', 'サーバーの言葉')).toBe('サーバーの言葉');
    expect(smsNotSendableLabel('unsupported_char', '')).toBe(
      'お名前などに、SMS で送れない文字があります',
    );
    expect(smsNotSendableLabel('patient_inactive', null)).toBe(
      'いまは稼働中でない方です（お電話でお伝えください）',
    );
    expect(smsNotSendableLabel('zzz', null)).toBe('ほかの理由');
  });
});

describe('キャリア', () => {
  it('大文字・小文字を区別しない', () => {
    expect(smsCarrierLabel('au')).toBe('au');
    expect(smsCarrierLabel('AU')).toBe('au');
    expect(smsCarrierLabel('docomo')).toBe('docomo');
    expect(smsCarrierLabel('softbank')).toBe('SoftBank');
    expect(smsCarrierLabel('SoftBank')).toBe('SoftBank');
    expect(smsCarrierLabel('rakuten')).toBe('楽天');
    expect(smsCarrierLabel('SMS')).toBe('使われていない番号');
  });

  it('知らない値はそのまま・空は「—」', () => {
    expect(smsCarrierLabel('mvno-x')).toBe('mvno-x');
    expect(smsCarrierLabel(null)).toBe('—');
    expect(smsCarrierLabel('')).toBe('—');
  });
});

describe('待ちの分', () => {
  it('小数は丸める', () => {
    expect(formatWaiting(42.4)).toBe('42 分');
    expect(formatWaiting(59.6)).toBe('1 時間');
    expect(formatWaiting(135.2)).toBe('2 時間 15 分');
    expect(formatWaiting(null)).toBe('—');
  });
});

describe('API の形 (ゆるく読む)', () => {
  it('待ちの分の小数・null の欄・欠けた印を受け付ける', () => {
    const pending = smsPendingListSchema.parse({
      within_send_hours: true,
      send_hours: '8:00〜20:00',
      count: 1,
      urgent_count: 0,
      items: [
        {
          patient_id: 'p1',
          patient_name: '見本 太郎',
          week_start: '2026-11-30',
          urgent: false,
          detected_at: null,
          waiting_minutes: 42.5,
          changes: [],
          sendable: false,
          not_sendable_reason: 'no_contact',
          destinations: [],
          call_numbers: null,
          body: null,
        },
      ],
    });
    const preview = weekNoticePreviewSchema.parse({
      week_start: '2026-11-30',
      week_label: '11/30 からの週',
      within_send_hours: true,
      send_hours: '8:00〜20:00',
      rows: [
        {
          patient_id: 'p1',
          patient_name: '見本 太郎',
          surname: '見本',
          state: 'new',
          destinations: [],
          visits: [],
          body: null,
          message_count: 1,
          est_yen: 11,
        },
      ],
      not_sendable: [],
    });
    expect(pending.items[0]?.waiting_minutes).toBe(42.5);
    expect(pending.items[0]?.token).toBeNull();
    expect(pending.items[0]?.call_numbers).toEqual([]);
    expect(preview.rows[0]?.body).toBe('');
    expect(preview.rows[0]?.char_count).toBe(0);
  });
});

describe('番号と時刻の出し方', () => {
  it('11 けたの携帯はハイフンで区切り、ほかの長さはそのまま', () => {
    expect(formatPhone('09000000002')).toBe('090-0000-0002');
    expect(formatPhone('0312345678')).toBe('0312345678');
    expect(formatPhone(null)).toBe('—');
  });

  it('時の 0 を付けない (画面の文面と同じ)', () => {
    expect(formatHm('09:00')).toBe('9:00');
    expect(formatHm('14:30')).toBe('14:30');
    expect(formatHm('9:30')).toBe('9:30');
  });
});
