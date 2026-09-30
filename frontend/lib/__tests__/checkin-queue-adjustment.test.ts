/**
 * 未送信の打刻に「その場で合わせた時刻」を同梱する (設計 2026-09-30 §6-2 / §7-2)。
 *
 * 圏外で退避した到着はまだサーバに無いので、到着した直後のカードは調整 API を
 * 呼ばずに、送信前の控えへ `adjusted_time` / `adjust_reason_code` を書き込む。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  enqueuePending,
  findPending,
  flushPending,
  listPending,
  setPendingAdjustment,
  type PendingEntry,
} from '@/lib/checkin-queue';

const STAFF = 'staff-1';
const READ_AT = '2026-09-30T04:06:20.000Z';

function enqueue(visitId: string, kind: 'arrival' | 'departure' = 'arrival') {
  return enqueuePending(STAFF, {
    visit_id: visitId,
    kind,
    payload: { at: READ_AT, qr_token: 'TOK', lat: 35.1, lng: 140.1 },
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('findPending', () => {
  it('その visit・その種別の未送信の打刻を返す (無ければ null)', () => {
    enqueue('visit-1');
    enqueue('visit-2', 'departure');
    expect(findPending(STAFF, 'visit-1', 'arrival')?.visit_id).toBe('visit-1');
    expect(findPending(STAFF, 'visit-1', 'departure')).toBeNull();
    expect(findPending(STAFF, 'visit-9', 'arrival')).toBeNull();
    expect(findPending('other-staff', 'visit-1', 'arrival')).toBeNull();
  });

  it('同じ visit に複数あれば最新の 1 件', () => {
    enqueue('visit-1');
    const latest = enqueue('visit-1');
    expect(findPending(STAFF, 'visit-1', 'arrival')?.id).toBe(latest!.id);
  });

  it('visit の無い打刻 (予定外の到着) は対象にしない', () => {
    enqueuePending(STAFF, {
      visit_id: '',
      kind: 'adhoc_arrival',
      payload: { at: READ_AT, qr_token: 'TOK' },
    });
    expect(findPending(STAFF, '', 'adhoc_arrival')).toBeNull();
  });
});

describe('setPendingAdjustment', () => {
  it('控えの payload に adjusted_time / adjust_reason_code を書き込む (at や座標はそのまま)', () => {
    enqueue('visit-1');
    const ok = setPendingAdjustment(STAFF, 'visit-1', 'arrival', {
      adjusted_time: '12:56',
      adjust_reason_code: 'intercom_wait',
    });
    expect(ok).toBe('written');
    expect(listPending(STAFF)[0]?.payload).toEqual({
      at: READ_AT,
      qr_token: 'TOK',
      lat: 35.1,
      lng: 140.1,
      adjusted_time: '12:56',
      adjust_reason_code: 'intercom_wait',
    });
  });

  it('押し直したら新しい時刻で上書きする', () => {
    enqueue('visit-1');
    setPendingAdjustment(STAFF, 'visit-1', 'arrival', {
      adjusted_time: '13:01',
      adjust_reason_code: 'intercom_wait',
    });
    setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:51' });
    const payload = listPending(STAFF)[0]?.payload;
    expect(payload?.adjusted_time).toBe('12:51');
    // 理由を渡さなかったら、前の理由を引きずらない。
    expect(payload).not.toHaveProperty('adjust_reason_code');
  });

  it('null は「元に戻す」— 同梱をやめ、打刻の控えそのものは残す', () => {
    enqueue('visit-1');
    setPendingAdjustment(STAFF, 'visit-1', 'arrival', {
      adjusted_time: '12:56',
      adjust_reason_code: 'intercom_wait',
    });
    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', null)).toBe('written');
    expect(listPending(STAFF)).toHaveLength(1);
    expect(listPending(STAFF)[0]?.payload).toEqual({
      at: READ_AT,
      qr_token: 'TOK',
      lat: 35.1,
      lng: 140.1,
    });
  });

  it('他の visit の控えには触れない', () => {
    enqueue('visit-1');
    enqueue('visit-2');
    setPendingAdjustment(STAFF, 'visit-2', 'arrival', { adjusted_time: '12:56' });
    const [first, second] = listPending(STAFF);
    expect(first?.payload).not.toHaveProperty('adjusted_time');
    expect(second?.payload.adjusted_time).toBe('12:56');
  });

  it('控えがもう無い (= 送信済み) ときは missing を返し、何も書かない', () => {
    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:56' })).toBe(
      'missing',
    );
    expect(listPending(STAFF)).toHaveLength(0);
  });
});

describe('flushPending — 合わせた時刻を一緒に送る', () => {
  it('再送の body に adjusted_time が載る', async () => {
    enqueue('visit-1');
    setPendingAdjustment(STAFF, 'visit-1', 'arrival', {
      adjusted_time: '12:56',
      adjust_reason_code: 'intercom_wait',
    });
    const sent: PendingEntry[] = [];
    await flushPending(STAFF, async (entry) => {
      sent.push(entry);
    });
    expect(sent).toHaveLength(1);
    expect(sent[0]?.payload).toMatchObject({
      at: READ_AT,
      adjusted_time: '12:56',
      adjust_reason_code: 'intercom_wait',
    });
    expect(listPending(STAFF)).toHaveLength(0);
  });

  it('再送の途中で書き込まれた時刻も落とさない (送る直前に読み直す)', async () => {
    enqueue('visit-1');
    enqueue('visit-2');
    const sent: PendingEntry[] = [];
    await flushPending(STAFF, async (entry) => {
      sent.push(entry);
      // 1 件目を送っている間に、2 件目の訪問でカードの「10分前」を押した。
      if (entry.visit_id === 'visit-1') {
        setPendingAdjustment(STAFF, 'visit-2', 'arrival', { adjusted_time: '12:56' });
      }
    });
    expect(sent.map((e) => e.visit_id)).toEqual(['visit-1', 'visit-2']);
    expect(sent[1]?.payload.adjusted_time).toBe('12:56');
  });

  it('送れなかった控えは、合わせた時刻ごと残る', async () => {
    enqueue('visit-1');
    setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:56' });
    const post = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    const { remaining } = await flushPending(STAFF, post);
    expect(remaining).toBe(1);
    expect(listPending(STAFF)[0]?.payload.adjusted_time).toBe('12:56');
  });
});

/**
 * レビュー M-1: 再送は「控えを読む → POST → 控えを消す」の順。POST の最中に控えへ
 * 書いても送信済みの body には載らず、成功すれば控えごと消える。書けたことにすると、
 * 画面は「合わせました」なのにサーバに届かない。
 */
describe('再送の POST 中は、控えへの書き込みを成功扱いにしない (M-1)', () => {
  /** POST を途中で止められる post。 */
  function heldPost() {
    let release!: () => void;
    let fail!: (err: unknown) => void;
    const sent: PendingEntry[] = [];
    const gate = new Promise<void>((resolve, reject) => {
      release = resolve;
      fail = reject;
    });
    const post = async (entry: PendingEntry) => {
      sent.push(entry);
      await gate;
    };
    return { post, sent, release, fail };
  }

  it('送信中は sending を返し、控えを書き換えない。送信後は missing (= 調整 API を呼ぶ)', async () => {
    enqueue('visit-1');
    const { post, sent, release } = heldPost();
    const flushing = flushPending(STAFF, post);

    // POST が飛んでいる間に、到着直後のカードの「10分前」を押した。
    expect(sent).toHaveLength(1);
    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:56' })).toBe(
      'sending',
    );
    expect(listPending(STAFF)[0]?.payload).not.toHaveProperty('adjusted_time');
    expect(sent[0]?.payload).not.toHaveProperty('adjusted_time');

    release();
    await flushing;
    expect(listPending(STAFF)).toHaveLength(0);
    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:56' })).toBe(
      'missing',
    );
  });

  it('「元に戻す」も同じ — 送信中は同梱を外せたことにしない', async () => {
    enqueue('visit-1');
    setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:56' });
    const { post, sent, release } = heldPost();
    const flushing = flushPending(STAFF, post);

    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', null)).toBe('sending');
    // 送った body には合わせた時刻が載っている。呼び出し元は送信後に DELETE で追いかける。
    expect(sent[0]?.payload.adjusted_time).toBe('12:56');

    release();
    await flushing;
    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', null)).toBe('missing');
  });

  it('送信に失敗して控えが残ったら、そのあとは控えに書ける', async () => {
    enqueue('visit-1');
    const { post, fail } = heldPost();
    const flushing = flushPending(STAFF, post);
    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:56' })).toBe(
      'sending',
    );

    fail(new TypeError('Failed to fetch'));
    const { remaining } = await flushing;
    expect(remaining).toBe(1);
    expect(setPendingAdjustment(STAFF, 'visit-1', 'arrival', { adjusted_time: '12:56' })).toBe(
      'written',
    );
    expect(listPending(STAFF)[0]?.payload.adjusted_time).toBe('12:56');
  });

  it('送信中なのはその 1 件だけ — 順番待ちの控えには書ける', async () => {
    enqueue('visit-1');
    enqueue('visit-2');
    const { post, sent, release } = heldPost();
    const flushing = flushPending(STAFF, post);

    expect(setPendingAdjustment(STAFF, 'visit-2', 'arrival', { adjusted_time: '12:56' })).toBe(
      'written',
    );
    release();
    await flushing;
    expect(sent.map((e) => e.visit_id)).toEqual(['visit-1', 'visit-2']);
    expect(sent[1]?.payload.adjusted_time).toBe('12:56');
  });
});
