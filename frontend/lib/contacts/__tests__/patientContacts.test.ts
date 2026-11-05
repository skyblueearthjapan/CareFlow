/**
 * 連絡先の編集の決まり (0 件にしない移し方・NG 回答・同意の日)。設計 §2・§8。
 */
import { describe, expect, it } from 'vitest';

import {
  addContact,
  allTargets,
  contactErrors,
  contactsChanged,
  draftToWrite,
  draftsFromRead,
  effectiveChannel,
  onlySelf,
  removeContact,
  setConsent,
  setNotifyTarget,
  todayJst,
  type ContactDraft,
} from '../patientContacts';

function d(p: Partial<ContactDraft>): ContactDraft {
  return {
    key: Math.random().toString(36),
    relation: 'family',
    name: '',
    relation_note: '',
    phone: '',
    email: '',
    is_notify_target: false,
    notify_channel: 'sms',
    consent_status: 'unconfirmed',
    consent_on: null,
    ...p,
  };
}

const SELF = d({ relation: 'self', phone: '080-2222-3333' });
const FAMILY = d({ relation: 'family', name: '見本 由美', phone: '090-1234-5678' });
const FACILITY = d({ relation: 'facility', name: '〇〇ホーム', phone: '0431234567' });

describe('setNotifyTarget', () => {
  it('最後の 1 件を外すとご本人へ移る', () => {
    const list = [{ ...FAMILY, is_notify_target: true }, SELF, FACILITY];
    const r = setNotifyTarget(list, 0, false);
    expect(r.contacts.map((c) => c.is_notify_target)).toEqual([false, true, false]);
    expect(r.notice).toContain('2 件目（ご本人）へ移しました');
  });

  it('ご本人が NG 回答なら次 (手段がある方) へ移る', () => {
    const list = [
      { ...SELF, consent_status: 'ng' as const },
      { ...FACILITY, phone: '' },
      { ...FAMILY, is_notify_target: true },
    ];
    const r = setNotifyTarget(list, 2, false);
    // FACILITY は手段なし → FAMILY 自身は除く → FACILITY しか残らない
    expect(r.contacts.map((c) => c.is_notify_target)).toEqual([false, true, false]);
  });

  it('手段のある方を手段のない方より先にする', () => {
    const list = [
      { ...FACILITY, phone: '' },
      { ...FAMILY, email: 'a@example.jp', phone: '' },
      { ...SELF, relation: 'other' as const, is_notify_target: true },
    ];
    const r = setNotifyTarget(list, 2, false);
    expect(r.contacts.map((c) => c.is_notify_target)).toEqual([false, true, false]);
  });

  it('移す先が無い (1 件だけ) ときは外させない', () => {
    const list = [{ ...SELF, is_notify_target: true }];
    const r = setNotifyTarget(list, 0, false);
    expect(r.contacts[0]!.is_notify_target).toBe(true);
    expect(r.notice).toContain('少なくとも 1 件に送ります');
  });

  it('ほかに送る先があれば外すだけ', () => {
    const list = [
      { ...SELF, is_notify_target: true },
      { ...FAMILY, is_notify_target: true },
    ];
    const r = setNotifyTarget(list, 1, false);
    expect(r.contacts.map((c) => c.is_notify_target)).toEqual([true, false]);
    expect(r.notice).toBeNull();
  });
});

describe('setConsent', () => {
  it('確認済みにすると今日の日付が入り、未確認に戻すと消える', () => {
    const list = [{ ...SELF, is_notify_target: true }];
    const r = setConsent(list, 0, 'confirmed', '2026-11-05');
    expect(r.contacts[0]!.consent_on).toBe('2026-11-05');
    const back = setConsent(r.contacts, 0, 'unconfirmed', '2026-11-05');
    expect(back.contacts[0]!.consent_on).toBeNull();
  });

  it('読んだときの状態へ戻したら元の日付', () => {
    const [read] = draftsFromRead([
      {
        id: 'id-1',
        sort_order: 0,
        relation: 'self',
        is_notify_target: true,
        notify_channel: 'sms',
        effective_channel: 'sms',
        consent_status: 'confirmed',
        consent_on: '2026-11-02',
        phone: '08022223333',
        phone_display: '080-2222-3333',
      },
    ]);
    const a = setConsent([read!], 0, 'unconfirmed', '2026-11-05');
    const b = setConsent(a.contacts, 0, 'confirmed', '2026-11-05');
    expect(b.contacts[0]!.consent_on).toBe('2026-11-02');
  });

  it('NG 回答は送る先から外れ、ほかへ移る', () => {
    const list = [
      { ...SELF, is_notify_target: true },
      { ...FAMILY, is_notify_target: false },
    ];
    const r = setConsent(list, 0, 'ng', '2026-11-05');
    expect(r.contacts[0]!.is_notify_target).toBe(false);
    expect(r.contacts[0]!.consent_on).toBe('2026-11-05');
    expect(r.contacts[1]!.is_notify_target).toBe(true);
    expect(r.notice).toContain('2 件目（見本 由美）');
  });

  it('ほかに移せないときは NG 回答にしない', () => {
    const list = [{ ...SELF, is_notify_target: true }];
    const r = setConsent(list, 0, 'ng', '2026-11-05');
    expect(r.contacts[0]!.consent_status).toBe('unconfirmed');
    expect(r.contacts[0]!.is_notify_target).toBe(true);
    expect(r.notice).toContain('NG 回答にできません');
  });
});

describe('add / remove / 一括', () => {
  it('はじめの 1 件はご本人で送る先・3 件まで', () => {
    let list: ContactDraft[] = [];
    list = addContact(list).contacts;
    expect(list[0]).toMatchObject({ relation: 'self', is_notify_target: true });
    list = addContact(list).contacts;
    expect(list[1]).toMatchObject({ relation: 'family', is_notify_target: false });
    list = addContact(addContact(list).contacts).contacts;
    expect(list).toHaveLength(3);
  });

  it('送る先を外したら残りへ移る', () => {
    const list = [SELF, { ...FAMILY, is_notify_target: true }];
    const r = removeContact(list, 1);
    expect(r.contacts).toHaveLength(1);
    expect(r.contacts[0]!.is_notify_target).toBe(true);
    expect(r.notice).toContain('1 件目（ご本人）');
  });

  it('ご本人だけ・全員 (NG 回答は除く)', () => {
    const list = [
      { ...FAMILY, is_notify_target: true },
      SELF,
      { ...FACILITY, consent_status: 'ng' as const },
    ];
    expect(onlySelf(list).contacts.map((c) => c.is_notify_target)).toEqual([false, true, false]);
    expect(allTargets(list).contacts.map((c) => c.is_notify_target)).toEqual([true, true, false]);
    expect(onlySelf([FAMILY]).notice).toBe('ご本人の連絡先がありません。');
  });
});

describe('effectiveChannel', () => {
  it('選んだ手段が使えなければ使える方へ', () => {
    expect(effectiveChannel({ phone: '090-1234-5678', email: '', notify_channel: 'email' })).toBe(
      'sms',
    );
    expect(
      effectiveChannel({ phone: '043-123-4567', email: 'a@b.jp', notify_channel: 'sms' }),
    ).toBe('email');
    expect(effectiveChannel({ phone: '043-123-4567', email: '', notify_channel: 'sms' })).toBe(
      'none',
    );
    expect(
      effectiveChannel({ phone: '090-1234-5678', email: 'a@b.jp', notify_channel: 'email' }),
    ).toBe('email');
  });
});

describe('送る形', () => {
  it('ご本人はお名前・続柄を送らない・既存は id を付ける', () => {
    const w = draftToWrite({ ...SELF, id: 'x', name: '残り', relation_note: 'n' });
    expect(w).toMatchObject({ id: 'x', relation: 'self', name: null, relation_note: null });
    expect(draftToWrite(FAMILY)).not.toHaveProperty('id');
    expect(draftToWrite({ ...FAMILY, email: '  ' }).email).toBeNull();
  });

  it('同意の日だけ違っても変更とみなさない', () => {
    expect(contactsChanged([SELF], [{ ...SELF, consent_on: '2026-11-05' }])).toBe(false);
    expect(contactsChanged([SELF], [{ ...SELF, phone: '090' }])).toBe(true);
  });

  it('形の違う電話・メールを止める', () => {
    expect(contactErrors([{ ...SELF, phone: '090-123-456', email: 'abc' }])).toEqual([
      '1 件目の電話番号の形が違います。',
      '1 件目のメールアドレスの形が違います。',
    ]);
    expect(contactErrors([SELF, FAMILY])).toEqual([]);
  });

  it('今日 (JST)', () => {
    expect(todayJst(new Date('2026-11-04T15:30:00Z'))).toBe('2026-11-05');
  });
});
