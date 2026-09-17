/**
 * 未送信音声キュー (`lib/voice/queue.ts`) のテスト。
 *
 * 守りたい約束 (打刻キューと同じ):
 *   - 送信できたときだけ取り除く
 *   - ネット障害 / 5xx は残して再試行する (黙って消さない)
 *   - **サーバの確定回答ではない 4xx** (ルート不在の 404 / 405 / 408 / 423 / 429 /
 *     Cloudflare の HTML) も残す — `checkin-flush.ts` の `isRouteMissing` と同じ規則
 *   - 確定 4xx はキューから外すが**捨てず** `voice-failed` へ退避する
 *   - 端末に保存できなければ `enqueueVoice` は null を返す (嘘をつかない)
 *
 * IndexedDB の代用は `./fakeIdb` (トランザクションの確定までモデル化し、
 * `failWrites` で quota 超過と同じ `onabort` を起こせる)。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { installFakeIndexedDB } from './fakeIdb';
import { idbPut, PENDING_STORE, resetVoiceDbForTest } from '@/lib/voice/idb';
import {
  countFailedVoice,
  countVoice,
  enqueueVoice,
  flushVoiceQueue,
  listFailedVoice,
  listVoice,
  newVoiceClientId,
  removeFailedVoice,
  requeueFailedVoice,
  VOICE_MAX_ATTEMPTS,
} from '@/lib/voice/queue';

/** RFC4122 v4（version / variant のニブルまで見る）。 */
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const STAFF = 'staff-1';
let fake: ReturnType<typeof installFakeIndexedDB>;

function enqueueOne() {
  return enqueueVoice({
    staffId: STAFF,
    visitId: 'visit-1',
    patientId: 'pat-1',
    recordedAt: '2026-09-17T14:08:00.000Z',
    durationSec: 1680,
    mimeType: 'audio/webm;codecs=opus',
    blob: new Blob(['audio']),
    consent: true,
  });
}

/** 応答を差し替える。`body` はそのまま `text()` で返す生文字列。 */
function mockFetch(status: number, body = '') {
  const fn = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  }));
  (globalThis as unknown as { fetch: unknown }).fetch = fn;
  return fn;
}

const DETAIL_400 = JSON.stringify({ detail: '同意がありません' });

beforeEach(() => {
  fake = installFakeIndexedDB();
  resetVoiceDbForTest();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('enqueueVoice / listVoice', () => {
  it('積んだ録音を staffId で読み戻せる', async () => {
    const entry = await enqueueOne();
    expect(entry).not.toBeNull();
    const rows = await listVoice(STAFF);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.visitId).toBe('visit-1');
    expect(rows[0]!.attempts).toBe(0);
    // 他人の端末切替で混ざらない。
    expect(await countVoice('staff-2')).toBe(0);
  });

  it('端末に保存できなければ null を返す (C-1: 保存できたと嘘をつかない)', async () => {
    fake.state.failWrites = true;

    const entry = await enqueueOne();

    expect(entry).toBeNull();
    fake.state.failWrites = false;
    expect(await countVoice(STAFF)).toBe(0);
  });
});

describe('flushVoiceQueue', () => {
  it('送信できたらキューから取り除く', async () => {
    await enqueueOne();
    const fetchMock = mockFetch(202);

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.sent).toBe(1);
    expect(res.remaining).toBe(0);
    expect(await countVoice(STAFF)).toBe(0);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain('/api/v1/visit-recordings');
    const form = init.body as FormData;
    expect(form.get('visit_id')).toBe('visit-1');
    expect(form.get('patient_id')).toBe('pat-1');
    expect(form.get('recorded_at')).toBe('2026-09-17T14:08:00.000Z');
    expect(form.get('duration_sec')).toBe('1680');
    expect(form.get('consent')).toBe('true');
    expect(form.get('device_mime')).toBe('audio/webm;codecs=opus');
    expect(form.get('audio')).toBeInstanceOf(Blob);
    // H-4: 端末側の一意キー。再送が重なっても BE 側で 1 件に畳める。
    expect(form.get('client_id')).toBeTruthy();
  });

  it('送信できた行はサーバ上の録音 id を持ち帰る (2026-09-18 是正)', async () => {
    const entry = await enqueueOne();
    mockFetch(202, JSON.stringify({ id: 'rec-1', recorded_at: '2026-09-17T14:08:00Z' }));

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    // 「未紐付け一覧のいちばん新しい行」を推測させないための対応表。
    expect(res.sentEntries).toEqual([{ entryId: entry!.id, recordingId: 'rec-1' }]);
  });

  it('id を読めない応答でも送信は成功、recordingId は null', async () => {
    await enqueueOne();
    mockFetch(202, '');

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.sent).toBe(1);
    expect(res.sentEntries[0]!.recordingId).toBeNull();
  });

  it('409 で BE が既存行を返せばその id を使う', async () => {
    await enqueueOne();
    mockFetch(409, JSON.stringify({ id: 'rec-existing' }));

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.sentEntries[0]!.recordingId).toBe('rec-existing');
  });

  it('409 (client_id 重複) は「既に登録済み」として成功扱いにする (H-4)', async () => {
    await enqueueOne();
    mockFetch(409, JSON.stringify({ detail: 'already registered' }));

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.sent).toBe(1);
    expect(res.dropped).toHaveLength(0);
    expect(await countVoice(STAFF)).toBe(0);
    expect(await countFailedVoice(STAFF)).toBe(0);
  });

  it('5xx は残して再試行する (黙って消さない)', async () => {
    await enqueueOne();
    mockFetch(503);

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.sent).toBe(0);
    expect(res.remaining).toBe(1);
    expect(res.dropped).toHaveLength(0);
    const rows = await listVoice(STAFF);
    expect(rows[0]!.attempts).toBe(1);
    expect(rows[0]!.lastError).toBeTruthy();
  });

  it('ネットワーク障害も残す', async () => {
    await enqueueOne();
    (globalThis as unknown as { fetch: unknown }).fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.remaining).toBe(1);
    expect(res.dropped).toHaveLength(0);
  });

  // C-2: `checkin-flush.ts` の `isRouteMissing` と同じ規則。
  it.each([
    ['405 (メソッド不許可 = BE ロールバック中)', 405, ''],
    ['404 (JSON の detail が無い = ルート不在)', 404, ''],
    ['404 (FastAPI 既定の Not Found)', 404, JSON.stringify({ detail: 'Not Found' })],
    ['408 (タイムアウト)', 408, ''],
    ['423 (ロック)', 423, ''],
    ['429 (レート制限)', 429, ''],
    ['HTML ボディ (Cloudflare のエラーページ)', 403, '<!DOCTYPE html><html>error</html>'],
  ])('%s は残す', async (_label, status, body) => {
    await enqueueOne();
    mockFetch(status, body);

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.sent).toBe(0);
    expect(res.remaining).toBe(1);
    expect(res.dropped).toHaveLength(0);
    expect(await countFailedVoice(STAFF)).toBe(0);
  });

  it('detail のある 404 (対象の訪問が無い) は確定回答として退避する', async () => {
    await enqueueOne();
    mockFetch(404, JSON.stringify({ detail: 'Visit not found for this staff' }));

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.dropped).toHaveLength(1);
    expect(res.remaining).toBe(0);
    expect(await countFailedVoice(STAFF)).toBe(1);
  });
});

/**
 * 404 の切り分け（M-A）。BE が業務上の 404 を日本語 detail で返すようになるため、
 * 「detail が空 か Starlette 既定の `"Not Found"`」だけを残す規則で線を引く。
 */
describe('404 の切り分け (M-A)', () => {
  it('Starlette 既定の "Not Found" は BE 不在としてキューに残す', async () => {
    await enqueueOne();
    // 実際に返ってくる綴り（大文字 N・F）。
    mockFetch(404, JSON.stringify({ detail: 'Not Found' }));

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.remaining).toBe(1);
    expect(res.dropped).toHaveLength(0);
    expect(await countFailedVoice(STAFF)).toBe(0);
  });

  it('detail が空文字の 404 も残す（プロキシが JSON だけ返した場合）', async () => {
    await enqueueOne();
    mockFetch(404, JSON.stringify({ detail: '' }));

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.remaining).toBe(1);
    expect(res.dropped).toHaveLength(0);
  });

  it('日本語 detail の 404（担当外の訪問です）は確定回答として voice-failed へ', async () => {
    await enqueueOne();
    mockFetch(404, JSON.stringify({ detail: '担当外の訪問です' }));

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.remaining).toBe(0);
    expect(res.dropped).toHaveLength(1);
    expect(res.dropped[0]!.reason).toBe('担当外の訪問です');
    expect(await countFailedVoice(STAFF)).toBe(1);
  });
});

describe('再試行の上限 (M-A)', () => {
  it(`${VOICE_MAX_ATTEMPTS} 回試しても送れなければ voice-failed へ落とす（捨てはしない）`, async () => {
    const entry = await enqueueOne();
    // 19 回失敗済みの状態にして、この flush で上限に届かせる。
    await idbPut(PENDING_STORE, { ...entry!, attempts: VOICE_MAX_ATTEMPTS - 1 });
    mockFetch(429);

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.remaining).toBe(0);
    expect(res.dropped).toHaveLength(1);
    expect(res.dropped[0]!.reason).toContain(`${VOICE_MAX_ATTEMPTS} 回`);
    const failed = await listFailedVoice(STAFF);
    // 音声そのものは残る = 画面から再送できる。
    expect(failed[0]!.blob).toBeInstanceOf(Blob);
  });

  it('上限の手前では今までどおり残す', async () => {
    const entry = await enqueueOne();
    await idbPut(PENDING_STORE, { ...entry!, attempts: VOICE_MAX_ATTEMPTS - 2 });
    mockFetch(429);

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.remaining).toBe(1);
    expect(res.dropped).toHaveLength(0);
  });
});

describe('退避先にも書けないとき (L-B)', () => {
  it('dropped に数えず pending に残す（案内できない出口は案内しない）', async () => {
    await enqueueOne();
    mockFetch(400, DETAIL_400);
    // voice-failed への put も quota で abort する。
    fake.state.failWrites = true;

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    fake.state.failWrites = false;
    expect(res.dropped).toHaveLength(0);
    expect(res.failed).toBe(0);
    // 消えていない = 次回また送る。
    expect(res.remaining).toBe(1);
    expect(await countVoice(STAFF)).toBe(1);
  });
});

describe('client_id (M-C)', () => {
  it('crypto.randomUUID があればそれを使う', () => {
    expect(newVoiceClientId()).toMatch(UUID_V4);
  });

  it('randomUUID が無い端末 (http:// の iOS) でも getRandomValues で UUID を組む', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (arr: Uint8Array) => {
        for (let i = 0; i < arr.length; i++) arr[i] = (i * 37) % 256;
        return arr;
      },
    });

    expect(newVoiceClientId()).toMatch(UUID_V4);
  });

  it('どちらも無ければ client_id を付けない（衝突する値を送らない）', async () => {
    vi.stubGlobal('crypto', undefined);

    expect(newVoiceClientId()).toBeNull();
    const entry = await enqueueOne();
    expect(entry!.clientId).toBeNull();
    // キーは要るので端末ローカルの値を作る（BE へは送らない）。
    expect(entry!.id).toBeTruthy();

    const fetchMock = mockFetch(202);
    await flushVoiceQueue(STAFF, { accessToken: 'token' });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.body as FormData).get('client_id')).toBeNull();
  });

  it('積んだ entry の id と client_id は同じ UUID（M-B の共有キー）', async () => {
    const entry = await enqueueOne();

    expect(entry!.clientId).toMatch(UUID_V4);
    expect(entry!.id).toBe(entry!.clientId);

    const fetchMock = mockFetch(202);
    await flushVoiceQueue(STAFF, { accessToken: 'token' });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.body as FormData).get('client_id')).toBe(entry!.clientId);
  });

  it('呼び出し側が決めた clientId をそのまま使う（画面が直接送信へ回っても同じキー）', async () => {
    const fixed = '11111111-2222-4333-8444-555555555555';
    const entry = await enqueueVoice({
      staffId: STAFF,
      visitId: 'visit-1',
      patientId: 'pat-1',
      recordedAt: '2026-09-17T14:08:00.000Z',
      durationSec: 10,
      mimeType: 'audio/webm;codecs=opus',
      blob: new Blob(['audio']),
      consent: true,
      clientId: fixed,
    });

    expect(entry!.clientId).toBe(fixed);
    expect(entry!.id).toBe(fixed);
  });
});

describe('確定 4xx の退避 (C-3: 破棄を不可逆にしない)', () => {
  it('キューから外すが voice-failed に音声ごと残す', async () => {
    await enqueueOne();
    mockFetch(400, DETAIL_400);

    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });

    expect(res.sent).toBe(0);
    expect(res.remaining).toBe(0);
    expect(res.dropped).toHaveLength(1);
    expect(res.dropped[0]!.reason).toBe('同意がありません');
    expect(res.failed).toBe(1);
    expect(await countVoice(STAFF)).toBe(0);

    const failed = await listFailedVoice(STAFF);
    expect(failed).toHaveLength(1);
    expect(failed[0]!.reason).toBe('同意がありません');
    expect(failed[0]!.droppedAt).toBeGreaterThan(0);
    // 音声そのものが残っている (ここが要点 — 録り直せない)。
    expect(failed[0]!.blob).toBeInstanceOf(Blob);
    expect(failed[0]!.visitId).toBe('visit-1');
  });

  it('「再送」でキューへ戻し、直っていれば送れる', async () => {
    await enqueueOne();
    mockFetch(400, DETAIL_400);
    await flushVoiceQueue(STAFF, { accessToken: 'token' });
    const failed = await listFailedVoice(STAFF);

    const moved = await requeueFailedVoice(failed[0]!.id);
    expect(moved).toBe(true);
    expect(await countFailedVoice(STAFF)).toBe(0);
    expect(await countVoice(STAFF)).toBe(1);

    mockFetch(202);
    const res = await flushVoiceQueue(STAFF, { accessToken: 'token' });
    expect(res.sent).toBe(1);
    expect(await countVoice(STAFF)).toBe(0);
  });

  it('「削除」でだけ消える', async () => {
    await enqueueOne();
    mockFetch(400, DETAIL_400);
    await flushVoiceQueue(STAFF, { accessToken: 'token' });
    const failed = await listFailedVoice(STAFF);

    await removeFailedVoice(failed[0]!.id);

    expect(await countFailedVoice(STAFF)).toBe(0);
  });
});
