/**
 * サインで記録 — 送信の形と、圏外で退避した記録の再送 (signature-checkin-design §5-1)。
 *
 * - multipart: `image` + 位置・時刻・`client_id`。
 * - 再送 (`departure_signature`): 画像を IndexedDB から読んで送る・送れたら画像を消す・
 *   ネットワーク障害は画像ごと残す・4xx と画像が無いときは理由付きで破棄。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DROP_REASON_SIGNATURE_MISSING, flushCheckinQueue, postPending } from '@/lib/checkin-flush';
import { enqueuePending, listPending, type PendingEntry } from '@/lib/checkin-queue';
import {
  SIGNATURE_STORE,
  buildSignatureForm,
  loadSignatureImage,
  resetSignatureDbForTest,
  saveSignatureImage,
} from '@/lib/signature-checkout';
import { installFakeIndexedDB, type FakeIdb } from '@/lib/voice/__tests__/fakeIdb';

const STAFF = 'staff-1';
const CLIENT_ID = '11111111-2222-4333-8444-555555555555';

function signatureEntry(clientId = CLIENT_ID): PendingEntry {
  return {
    id: 'e-1',
    visit_id: 'visit-1',
    kind: 'departure_signature',
    payload: { at: '2026-10-06T04:38:00.000Z', lat: 35.1, lng: 140.1, client_id: clientId },
    queued_at: 0,
  };
}

let fake: FakeIdb;
const fetchMock = vi.fn();

beforeEach(() => {
  window.localStorage.clear();
  fake = installFakeIndexedDB();
  resetSignatureDbForTest();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetSignatureDbForTest();
});

describe('buildSignatureForm', () => {
  it('画像と位置・時刻・client_id を multipart にする', () => {
    const form = buildSignatureForm(
      { at: '2026-10-06T04:38:00Z', lat: 35.1, lng: 140.1, accuracy: 12, client_id: CLIENT_ID },
      new Blob(['png'], { type: 'image/png' }),
    );
    expect(form.get('image')).toBeInstanceOf(Blob);
    expect(form.get('lat')).toBe('35.1');
    expect(form.get('lng')).toBe('140.1');
    expect(form.get('accuracy')).toBe('12');
    expect(form.get('at')).toBe('2026-10-06T04:38:00Z');
    expect(form.get('client_id')).toBe(CLIENT_ID);
    expect(form.get('is_override')).toBeNull();
  });
});

describe('IndexedDB のサインの画像', () => {
  it('保存して読み出せる', async () => {
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    expect(fake.stores.get(SIGNATURE_STORE)?.has(CLIENT_ID)).toBe(true);
    const loaded = await loadSignatureImage(CLIENT_ID);
    expect(loaded.ok && loaded.blob).toBeInstanceOf(Blob);
    // DB は開けて、その画像は本当に無い。
    expect(await loadSignatureImage('other')).toEqual({ ok: true, blob: null });
  });

  it('DB を開けないときは「読めない」(無いとは言わない)・失敗は覚えず次は開き直す', async () => {
    const realOpen = window.indexedDB.open.bind(window.indexedDB);
    let fail = true;
    Object.defineProperty(window, 'indexedDB', {
      configurable: true,
      value: {
        open: (...args: Parameters<IDBFactory['open']>) => {
          if (!fail) return realOpen(...args);
          const req: { onblocked?: () => void } = {};
          queueMicrotask(() => req.onblocked?.());
          return req;
        },
      },
    });
    expect(await loadSignatureImage(CLIENT_ID)).toEqual({ ok: false });
    fail = false;
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    const loaded = await loadSignatureImage(CLIENT_ID);
    expect(loaded.ok && loaded.blob).toBeInstanceOf(Blob);
  });

  it('保存できなければ throw する (保存したと嘘をつかない)', async () => {
    fake.state.failWrites = true;
    await expect(
      saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' })),
    ).rejects.toThrow();
  });
});

describe('postPending (departure_signature)', () => {
  it('画像を読んで checkout-signature へ送り、送れたら画像を消す', async () => {
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'visit-1', departure_signature_id: 'sig-1' }), {
        status: 200,
      }),
    );
    const res = await postPending(signatureEntry(), 'tok', 'ref');
    expect(res).toMatchObject({ departure_signature_id: 'sig-1' });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/visits/visit-1/checkout-signature');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    const body = init.body as FormData;
    expect(body.get('client_id')).toBe(CLIENT_ID);
    expect(body.get('image')).toBeInstanceOf(Blob);
    expect(fake.stores.get(SIGNATURE_STORE)?.has(CLIENT_ID)).toBe(false);
  });

  it('ネットワーク障害は画像ごと残す (再送する)', async () => {
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(postPending(signatureEntry(), 'tok', null)).rejects.toBeInstanceOf(TypeError);
    expect(fake.stores.get(SIGNATURE_STORE)?.has(CLIENT_ID)).toBe(true);
  });

  it('DB を読めないときは捨てずに残す (送らない)', async () => {
    Object.defineProperty(window, 'indexedDB', {
      configurable: true,
      value: {
        open: () => {
          const req: { onerror?: () => void } = {};
          queueMicrotask(() => req.onerror?.());
          return req;
        },
      },
    });
    resetSignatureDbForTest();
    const err = await postPending(signatureEntry(), 'tok', null).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).name).not.toBe('DropPendingError');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('IndexedDB に置けなかった画像 (控えの data URL) はサインとして送る', async () => {
    const entry = signatureEntry();
    entry.payload.image_data_url = `data:image/png;base64,${btoa('png-bytes')}`;
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 200 }));
    await postPending(entry, 'tok', null);
    const body = (fetchMock.mock.calls[0] as [string, RequestInit])[1].body as FormData;
    const image = body.get('image') as Blob;
    expect(image).toBeInstanceOf(Blob);
    expect(image.type).toBe('image/png');
    expect(image.size).toBe('png-bytes'.length);
    expect(body.get('client_id')).toBe(CLIENT_ID);
    // data URL を本文に載せない。
    expect(body.get('image_data_url')).toBeNull();
  });

  it.each([
    ['401 (ログインの切れ)', 401, { detail: 'Not authenticated' }],
    ['405 (ルートが無い)', 405, null],
    ['404 Not Found (ルートが無い)', 404, { detail: 'Not Found' }],
    ['404 本文なし', 404, null],
  ])('%s は画像ごと残す', async (_label, status, body) => {
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    fetchMock.mockResolvedValueOnce(
      new Response(body ? JSON.stringify(body) : '', { status: status as number }),
    );
    const err = await postPending(signatureEntry(), 'tok', null).catch((e: unknown) => e);
    expect((err as Error).name).not.toBe('DropPendingError');
    expect(fake.stores.get(SIGNATURE_STORE)?.has(CLIENT_ID)).toBe(true);
  });

  it('4xx は理由付きで破棄し、画像も消す', async () => {
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'この訪問は今日の予定ではないため記録できません' }), {
        status: 409,
      }),
    );
    await expect(postPending(signatureEntry(), 'tok', null)).rejects.toMatchObject({
      name: 'DropPendingError',
      reason: 'この訪問は今日の予定ではないため記録できません',
    });
    expect(fake.stores.get(SIGNATURE_STORE)?.has(CLIENT_ID)).toBe(false);
  });

  it('画像が端末に無ければ送らずに理由付きで破棄する', async () => {
    await expect(postPending(signatureEntry('missing'), 'tok', null)).rejects.toMatchObject({
      reason: DROP_REASON_SIGNATURE_MISSING,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('flushCheckinQueue (departure_signature)', () => {
  it('退避した記録を送ってキューから外す', async () => {
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    enqueuePending(STAFF, {
      visit_id: 'visit-1',
      kind: 'departure_signature',
      payload: { at: '2026-10-06T04:38:00.000Z', client_id: CLIENT_ID },
    });
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 200 }));
    const result = await flushCheckinQueue(STAFF, 'tok', null);
    expect(result.remaining).toBe(0);
    expect(result.sent).toHaveLength(1);
    expect(listPending(STAFF)).toHaveLength(0);
  });

  it('client_id の無い departure_signature はキューに読み込まない', () => {
    window.localStorage.setItem(
      `checkin-pending:${STAFF}`,
      JSON.stringify([
        {
          id: 'x',
          visit_id: 'visit-1',
          kind: 'departure_signature',
          payload: { at: '2026-10-06T04:38:00.000Z' },
          queued_at: 0,
        },
      ]),
    );
    expect(listPending(STAFF)).toHaveLength(0);
  });

  it('5xx は ApiError として残す', async () => {
    await saveSignatureImage(CLIENT_ID, new Blob(['png'], { type: 'image/png' }));
    enqueuePending(STAFF, {
      visit_id: 'visit-1',
      kind: 'departure_signature',
      payload: { at: '2026-10-06T04:38:00.000Z', client_id: CLIENT_ID },
    });
    fetchMock.mockResolvedValueOnce(new Response('oops', { status: 502 }));
    const result = await flushCheckinQueue(STAFF, 'tok', null);
    expect(result.remaining).toBe(1);
    expect(result.dropped).toHaveLength(0);
  });
});
