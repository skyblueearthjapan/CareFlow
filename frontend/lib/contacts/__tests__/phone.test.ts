/**
 * 電話番号・メールの見分け (設計 contacts-phone-email-design-2026-11-05 §3・§8)。
 * backend の試験と同じ例を使う。番号はすべて見本。
 */
import { describe, expect, it } from 'vitest';

import { classifyEmail, classifyPhone, phoneCanSms } from '../phone';

describe('classifyPhone', () => {
  it.each([
    ['090-1234-5678', '09012345678', 'mobile', true, '090-1234-5678'],
    ['08012345678', '08012345678', 'mobile', true, '080-1234-5678'],
    ['070 1234 5678', '07012345678', 'mobile', true, '070-1234-5678'],
    ['０９０ー１２３４ー５６７８', '09012345678', 'mobile', true, '090-1234-5678'],
    ['０９０－１２３４－５６７８', '09012345678', 'mobile', true, '090-1234-5678'],
    ['+81 90 1234 5678', '09012345678', 'mobile', true, '090-1234-5678'],
    ['+81-80-1234-5678', '08012345678', 'mobile', true, '080-1234-5678'],
    ['＋８１９０１２３４５６７８', '09012345678', 'mobile', true, '090-1234-5678'],
    ['06012345678', '06012345678', 'mobile_new', null, '060-1234-5678'],
    ['050-1234-5678', '05012345678', 'ip', false, '050-1234-5678'],
    ['02012345678', '02012345678', 'data', false, '020-1234-5678'],
    ['0120-123-456', '0120123456', 'freedial', false, '0120-123-456'],
    ['0120123456', '0120123456', 'freedial', false, '0120-123-456'],
    ['0800-123-4567', '08001234567', 'freedial', false, '0800-123-4567'],
    ['043-123-4567', '0431234567', 'landline', false, '043-123-4567'],
    ['03-1234-5678', '0312345678', 'landline', false, '03-1234-5678'],
    ['0312345678', '0312345678', 'landline', false, '0312345678'],
    ['+81 3-1234-5678', '0312345678', 'landline', false, '03-1234-5678'],
  ])('%s → %s %s', (raw, digits, kind, sms, display) => {
    const r = classifyPhone(raw);
    expect(r).toEqual({ ok: true, digits, kind, smsCapable: sms, display });
  });

  it('固定電話の前後のハイフンは除く', () => {
    const r = classifyPhone('043-123-4567-');
    expect(r).toMatchObject({ ok: true, kind: 'landline', display: '043-123-4567' });
  });

  it.each([
    ['090-123-456', 'too_short'],
    ['12345', 'too_short'],
    ['090123456789', 'too_long'],
    ['12345678901', 'bad_format'],
    ['abc', 'bad_format'],
  ])('%s は誤り (%s)', (raw, error) => {
    expect(classifyPhone(raw)).toMatchObject({ ok: false, error });
  });

  it('空・null は番号なし', () => {
    expect(classifyPhone('')).toBeNull();
    expect(classifyPhone('   ')).toBeNull();
    expect(classifyPhone(null)).toBeNull();
    expect(classifyPhone(undefined)).toBeNull();
  });

  it('SMS が使えるのは携帯と 060 (確認中) だけ', () => {
    expect(phoneCanSms(classifyPhone('090-1234-5678'))).toBe(true);
    expect(phoneCanSms(classifyPhone('06012345678'))).toBe(true);
    expect(phoneCanSms(classifyPhone('050-1234-5678'))).toBe(false);
    expect(phoneCanSms(classifyPhone('043-123-4567'))).toBe(false);
    expect(phoneCanSms(classifyPhone('090-123-456'))).toBe(false);
    expect(phoneCanSms(null)).toBe(false);
  });
});

describe('classifyEmail', () => {
  it('小文字・前後の空白を除く', () => {
    expect(classifyEmail('  Hanako@Example.JP ')).toEqual({
      ok: true,
      email: 'hanako@example.jp',
      carrier: false,
    });
  });
  it.each([
    'a@docomo.ne.jp',
    'a@ezweb.ne.jp',
    'a@au.com',
    'a@softbank.ne.jp',
    'a@i.softbank.jp',
    'a@ymobile.ne.jp',
  ])('%s は携帯会社のアドレス', (m) => {
    expect(classifyEmail(m)).toMatchObject({ ok: true, carrier: true });
  });
  it.each(['abc', 'a@b', 'a b@example.jp', '@example.jp'])('%s は形が違う', (m) => {
    expect(classifyEmail(m)).toEqual({ ok: false });
  });
  it('空は null', () => {
    expect(classifyEmail('')).toBeNull();
    expect(classifyEmail(null)).toBeNull();
  });
});
