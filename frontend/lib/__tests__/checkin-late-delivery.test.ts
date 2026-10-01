/**
 * 圏外で退避して遅れて届いた打刻 (設計 checkin-late-delivery-design-2026-10-01).
 *
 *   1. 表示: 「遅れて届いた（10/2 8:30 受信）」。遅れたかどうかはサーバが決める。
 *   2. キュー: 日付をまたいだ控えも捨てずに再送し、届いた entry と応答を `sent` で返す。
 *   3. サーバが断った控え (期限切れ・訪問日でない) は、理由と code 付きで破棄する。
 *   4. 再送の結果の案内 (どの読み取りが届いたか) はサーバの応答で決める。
 *   5. 「別の利用者の QR」かは code で見分ける。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

import { ApiError } from '@/lib/api-client';
import {
  enqueuePending,
  flushPending,
  listPending,
  type PendingEntry,
  type PendingKind,
} from '@/lib/checkin-queue';
import { jstDayTime, lateDeliveryLabel } from '@/lib/format/actualTime';

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

import { fetcher } from '@/lib/api/fetcher';
import { codeOf, flushCheckinQueue, isWrongPatient, lateSentNotice } from '@/lib/checkin-flush';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;
const STAFF = 'staff-late';

/** 9/29 18:45 (JST) に読み取り、翌朝 9/30 8:30 (JST) に受信。 */
const READ_AT = '2026-09-29T09:45:00Z';
const RECEIVED_ISO = '2026-09-29T23:30:00Z';

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  asMock(fetcher).mockResolvedValue({});
});

describe('遅れて届いた — 表示', () => {
  it('受信時刻を「遅れて届いた（M/D H:MM 受信）」で出す', () => {
    expect(lateDeliveryLabel('2026-10-01T23:30:00Z')).toBe('遅れて届いた（10/2 8:30 受信）');
    expect(lateDeliveryLabel(RECEIVED_ISO)).toBe('遅れて届いた（9/30 8:30 受信）');
    expect(jstDayTime(new Date('2026-10-02T03:05:00Z'))).toBe('10/2 12:05');
  });

  it('遅れていない (null / 空 / 不正) は出さない', () => {
    expect(lateDeliveryLabel(null)).toBeNull();
    expect(lateDeliveryLabel(undefined)).toBeNull();
    expect(lateDeliveryLabel('')).toBeNull();
    expect(lateDeliveryLabel('not-a-date')).toBeNull();
  });
});

describe('遅れて届いた — キュー', () => {
  it('日付をまたいだ控えも捨てずに送り、届いた entry と応答を sent で返す', async () => {
    const entry = enqueuePending(STAFF, {
      visit_id: 'visit-yesterday',
      kind: 'arrival',
      payload: { at: READ_AT, qr_token: 'TOK', lat: 35.6, lng: 140.1 },
    });
    const response = { id: 'visit-yesterday', actual_arrival_late_received_at: RECEIVED_ISO };
    const { remaining, dropped, sent } = await flushPending(STAFF, async () => response);
    expect(remaining).toBe(0);
    expect(dropped).toHaveLength(0);
    expect(sent).toEqual([{ entry, response }]);
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

  it('72 時間を過ぎてサーバが断った控えは、サーバの理由と code で破棄する', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-old',
      kind: 'arrival',
      payload: { at: READ_AT, qr_token: 'TOK' },
    });
    const reason = '読み取りから 3 日を過ぎたため送信できません。管理者に連絡してください';
    asMock(fetcher).mockRejectedValueOnce(
      new ApiError('conflict', 409, { detail: reason, code: 'late_expired' }),
    );
    const { remaining, dropped, sent } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(remaining).toBe(0);
    expect(sent).toHaveLength(0);
    expect(dropped[0]?.reason).toBe(reason);
    expect(dropped[0]?.code).toBe('late_expired');
    expect(listPending(STAFF)).toHaveLength(0);
  });

  it('code の無い 4xx は code = null で破棄する', async () => {
    enqueuePending(STAFF, { visit_id: 'v', kind: 'arrival', payload: { at: READ_AT } });
    asMock(fetcher).mockRejectedValueOnce(new ApiError('gone', 410, null));
    const { dropped } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(dropped[0]?.code).toBeNull();
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

describe('遅れて届いた — 再送の結果の案内 (サーバの応答で決める)', () => {
  function sent(at: string, kind: PendingKind, response: Record<string, unknown> | null) {
    const entry: PendingEntry = { id: at, visit_id: 'v', kind, payload: { at }, queued_at: 0 };
    return { entry, response };
  }

  it('応答の *_late_received_at がある記録だけを数え、読み取った日時で伝える', () => {
    const notice = lateSentNotice([
      sent(READ_AT, 'arrival', { actual_arrival_late_received_at: RECEIVED_ISO }),
      // 端末の時計では遅れて見えても、サーバが遅れていないと言えば数えない。
      sent('2026-09-29T10:30:00Z', 'arrival', { actual_arrival_late_received_at: null }),
      sent('2026-09-29T10:40:00Z', 'departure', {
        actual_arrival_late_received_at: RECEIVED_ISO,
        actual_departure_late_received_at: RECEIVED_ISO,
      }),
      sent('2026-09-29T23:20:00Z', 'adhoc_arrival', { actual_arrival_late_received_at: null }),
    ]);
    expect(notice).toEqual({
      title: '遅れて届いた記録を2件送信しました',
      description: '9/29 18:45 に読み取った記録 ほか 1 件は、読み取った日の訪問に保存しました',
    });
  });

  it('退出は退出の項目で判定する (到着が遅れていても退出が遅れていなければ数えない)', () => {
    expect(
      lateSentNotice([
        sent(READ_AT, 'departure', {
          actual_arrival_late_received_at: RECEIVED_ISO,
          actual_departure_late_received_at: null,
        }),
      ]),
    ).toBeNull();
  });

  it('遅れていない・応答が無い・未訪問は案内しない', () => {
    expect(lateSentNotice([sent(READ_AT, 'arrival', null)])).toBeNull();
    expect(
      lateSentNotice([sent(READ_AT, 'no_show', { actual_arrival_late_received_at: RECEIVED_ISO })]),
    ).toBeNull();
    expect(lateSentNotice([])).toBeNull();
  });

  it('案内の文言に「直す」「修正」「補正」を使わない', () => {
    const notice = lateSentNotice([
      sent(READ_AT, 'arrival', { actual_arrival_late_received_at: RECEIVED_ISO }),
    ]);
    for (const word of ['直す', '修正', '補正']) {
      expect(`${notice?.title}${notice?.description}`).not.toContain(word);
    }
  });
});

describe('断られた理由の code', () => {
  it('code を取り出し、「別の利用者の QR」だけを見分ける', () => {
    const wrong = new ApiError('conflict', 409, { detail: 'x', code: 'wrong_patient' });
    const notDay = new ApiError('conflict', 409, { detail: 'x', code: 'not_visit_day' });
    expect(codeOf(wrong)).toBe('wrong_patient');
    expect(codeOf(new ApiError('conflict', 409, { detail: 'x' }))).toBeNull();
    expect(codeOf(new TypeError('x'))).toBeNull();
    expect(isWrongPatient(wrong)).toBe(true);
    expect(isWrongPatient(notDay)).toBe(false);
    for (const code of ['late_expired', 'cancelled', 'deleted']) {
      expect(isWrongPatient(new ApiError('conflict', 409, { detail: 'x', code }))).toBe(false);
    }
  });

  it('code の無い旧 BE は「別の利用者の QR」の文言で見分ける', () => {
    expect(
      isWrongPatient(
        new ApiError('conflict', 409, { detail: "QR does not match this visit's patient" }),
      ),
    ).toBe(true);
    expect(isWrongPatient(new ApiError('conflict', 409, { detail: 'Visit is cancelled' }))).toBe(
      false,
    );
    expect(isWrongPatient(new ApiError('not found', 404, { code: 'wrong_patient' }))).toBe(false);
  });
});
