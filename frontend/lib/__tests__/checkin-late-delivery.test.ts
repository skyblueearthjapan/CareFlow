/**
 * 圏外で退避して遅れて届いた打刻 (設計 checkin-late-delivery-design-2026-10-01).
 *
 *   1. 表示: 「遅れて届いた（10/2 8:30 受信）」と、遅れたかどうかの目安 (30 分・日付)。
 *   2. キュー: 日付をまたいだ控えも捨てずに再送し、届いた entry を `sent` で返す。
 *   3. 期限 (72 時間) を過ぎてサーバが断った控えは、理由付きで破棄する。
 *   4. 再送の結果の案内 (どの読み取りが届いたか)。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

import { ApiError } from '@/lib/api-client';
import { enqueuePending, flushPending, listPending } from '@/lib/checkin-queue';
import { isLateDelivery, jstDayTime, lateDeliveryLabel } from '@/lib/format/actualTime';

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

import { fetcher } from '@/lib/api/fetcher';
import { flushCheckinQueue, lateSentNotice } from '@/lib/checkin-flush';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;
const STAFF = 'staff-late';

/** 9/29 18:45 (JST) に読み取り、翌朝 9/30 8:30 (JST) に受信。 */
const READ_AT = '2026-09-29T09:45:00Z';
const RECEIVED = new Date('2026-09-29T23:30:00Z');

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  asMock(fetcher).mockResolvedValue({});
});

describe('遅れて届いた — 表示', () => {
  it('受信時刻を「遅れて届いた（M/D H:MM 受信）」で出す', () => {
    expect(lateDeliveryLabel('2026-10-01T23:30:00Z')).toBe('遅れて届いた（10/2 8:30 受信）');
    expect(lateDeliveryLabel(RECEIVED.toISOString())).toBe('遅れて届いた（9/30 8:30 受信）');
    expect(jstDayTime(new Date('2026-10-02T03:05:00Z'))).toBe('10/2 12:05');
  });

  it('遅れていない (null / 空 / 不正) は出さない', () => {
    expect(lateDeliveryLabel(null)).toBeNull();
    expect(lateDeliveryLabel(undefined)).toBeNull();
    expect(lateDeliveryLabel('')).toBeNull();
    expect(lateDeliveryLabel('not-a-date')).toBeNull();
  });

  it('目安: 30 分を超えた、または日付をまたいだら遅れて届いた', () => {
    const read = new Date('2026-09-29T04:00:00Z'); // 13:00 JST
    const plus = (min: number) => new Date(read.getTime() + min * 60_000);
    expect(isLateDelivery(read.toISOString(), plus(30))).toBe(false);
    expect(isLateDelivery(read.toISOString(), plus(31))).toBe(true);
    // 23:50 に読み、0:05 に届いた (15 分でも日付をまたぐ)。
    expect(isLateDelivery('2026-09-29T14:50:00Z', new Date('2026-09-29T15:05:00Z'))).toBe(true);
    // 端末の時計が進んでいる (読み取りが受信より後) は遅れていない。
    expect(isLateDelivery(plus(5).toISOString(), read)).toBe(false);
    expect(isLateDelivery(null, read)).toBe(false);
  });
});

describe('遅れて届いた — キュー', () => {
  it('日付をまたいだ控えも捨てずに送り、届いた entry を sent で返す', async () => {
    const entry = enqueuePending(STAFF, {
      visit_id: 'visit-yesterday',
      kind: 'arrival',
      payload: { at: READ_AT, qr_token: 'TOK', lat: 35.6, lng: 140.1 },
    });
    const { remaining, dropped, sent } = await flushPending(STAFF, async () => undefined);
    expect(remaining).toBe(0);
    expect(dropped).toHaveLength(0);
    expect(sent.map((e) => e.id)).toEqual([entry!.id]);
  });

  it('読み取った瞬間 (at) と位置は、送るときもそのまま (再送で書き換えない)', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-yesterday',
      kind: 'arrival',
      payload: { at: READ_AT, qr_token: 'TOK', lat: 35.6, lng: 140.1, accuracy: 12 },
    });
    await flushCheckinQueue(STAFF, 'a', 'r');
    const body = JSON.parse((asMock(fetcher).mock.calls[0][1] as { body: string }).body) as Record<
      string,
      unknown
    >;
    expect(body).toEqual({ at: READ_AT, qr_token: 'TOK', lat: 35.6, lng: 140.1, accuracy: 12 });
  });

  it('72 時間を過ぎてサーバが断った控えは、サーバの理由で破棄する', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-old',
      kind: 'arrival',
      payload: { at: READ_AT, qr_token: 'TOK' },
    });
    const reason = '読み取りから 3 日を過ぎたため送信できません。管理者に連絡してください';
    asMock(fetcher).mockRejectedValueOnce(new ApiError('conflict', 409, { detail: reason }));
    const { remaining, dropped, sent } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(remaining).toBe(0);
    expect(sent).toHaveLength(0);
    expect(dropped[0]?.reason).toBe(reason);
    expect(listPending(STAFF)).toHaveLength(0);
  });

  it('圏外のまま (fetch 失敗) は日付をまたいでも残す', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-yesterday',
      kind: 'departure',
      payload: { at: READ_AT, qr_token: 'TOK' },
    });
    asMock(fetcher).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const { remaining, sent } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(remaining).toBe(1);
    expect(sent).toHaveLength(0);
  });
});

describe('遅れて届いた — 再送の結果の案内', () => {
  function entry(at: string) {
    return {
      id: at,
      visit_id: 'v',
      kind: 'arrival' as const,
      payload: { at },
      queued_at: 0,
    };
  }

  it('遅れて届いた記録だけを数え、読み取った日時で伝える', () => {
    const notice = lateSentNotice(
      [entry(READ_AT), entry('2026-09-29T10:30:00Z'), entry('2026-09-29T23:20:00Z')],
      RECEIVED,
    );
    expect(notice).toEqual({
      title: '遅れて届いた記録を2件送信しました',
      description: '9/29 18:45 に読み取った記録 ほか 1 件は、読み取った日の訪問に保存しました',
    });
  });

  it('すぐに届いた記録だけなら案内しない', () => {
    expect(lateSentNotice([entry('2026-09-29T23:20:00Z')], RECEIVED)).toBeNull();
    expect(lateSentNotice([], RECEIVED)).toBeNull();
  });

  it('案内の文言に「直す」「修正」「補正」を使わない', () => {
    const notice = lateSentNotice([entry(READ_AT)], RECEIVED);
    for (const word of ['直す', '修正', '補正']) {
      expect(`${notice?.title}${notice?.description}`).not.toContain(word);
    }
  });
});
