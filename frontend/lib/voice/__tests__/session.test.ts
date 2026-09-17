/**
 * 録音セッション (`lib/voice/session.ts`) のテスト — 最終レビュー N 系の是正。
 *
 * 守りたい約束:
 *   N-1 `client_id` は**録音開始時に 1 つ**決まり、review / 救出 / 直接送信の
 *       どれを通っても同じ値になる。同時に飛んだ救出は 1 本に畳む
 *       （`pagehide` と `visibilitychange` はほぼ同時に来る）
 *   N-2 画面ロック（hidden）の自動停止はセッションに置くだけでなく**その場で
 *       未送信キューへ積む**。IndexedDB のチャンクは**積めた後**にだけ消す
 *   N-3 `staffId` が取れないときはセッションを畳まない（畳めば録音が消える）
 *   N-4 訪問外の録音は録音ごとに別セッション。未保存の録音があるうちは
 *       新しい録音を始めない（黙って上書きしない）
 *
 * jsdom には MediaRecorder / getUserMedia / IndexedDB が無いので、MediaRecorder は
 * 偽物を差し込み、IndexedDB はテスト用の代用を入れる（キューへの投入は本物の
 * `enqueueVoice` を通し、書き込みの成否まで含めて確かめる）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// 本物の `enqueueVoice`（= 実際に IndexedDB へ書く）を通したまま、回数だけ数える。
vi.mock('@/lib/voice/queue', async () => {
  const actual = await vi.importActual<typeof import('@/lib/voice/queue')>('@/lib/voice/queue');
  return { ...actual, enqueueVoice: vi.fn(actual.enqueueVoice) };
});

import { installFakeIndexedDB, type FakeIdb } from './fakeIdb';
import { toast } from '@/components/ui/sonner';
import { CHUNK_STORE, PENDING_STORE, resetVoiceDbForTest } from '@/lib/voice/idb';
import { enqueueVoice } from '@/lib/voice/queue';
import {
  acquireVoiceSession,
  attachVoiceSession,
  rescueVoiceSessions,
  resetVoiceSessionsForTest,
  startVoiceSessionRecorder,
  UNSAVED_RECORDING_MESSAGE,
  voiceSessionKey,
  type VoiceSession,
} from '@/lib/voice/session';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

/** RFC4122 v4（`client_id` の形・M-C）。 */
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const instances: FakeMediaRecorder[] = [];

class FakeMediaRecorder {
  static isTypeSupported(mime: string): boolean {
    return mime === 'audio/webm;codecs=opus';
  }
  state: 'inactive' | 'recording' | 'paused' = 'inactive';
  mimeType: string;
  ondataavailable: ((ev: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(_stream: unknown, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? '';
    instances.push(this);
  }
  start() {
    this.state = 'recording';
  }
  pause() {
    this.state = 'paused';
  }
  resume() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.onstop?.();
  }
  emit(text: string) {
    this.ondataavailable?.({ data: new Blob([text]) });
  }
}

let idb: FakeIdb;

/** 画面が隠れた（iOS の画面ロック相当）。 */
function goHidden() {
  Object.defineProperty(document, 'hidden', { configurable: true, value: true });
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
  document.dispatchEvent(new Event('visibilitychange'));
}

/** 録音中のセッションを 1 つ作る（チャンクも 1 件流す）。 */
async function startSession(staffId = 'staff-1'): Promise<VoiceSession> {
  const session = acquireVoiceSession({ visitId: 'visit-1', patientId: 'pat-1' });
  attachVoiceSession(session, {}, staffId);
  const recorder = startVoiceSessionRecorder(session, {
    visitId: 'visit-1',
    patientId: 'pat-1',
    patientName: '山田 花子',
  });
  await recorder.start();
  session.startedAtIso = new Date().toISOString();
  instances[0]!.emit('audio');
  // `dataavailable` → IndexedDB は best-effort な非同期なので書けるまで待つ。
  await vi.waitFor(() => expect(idb.stores.get(CHUNK_STORE)?.size ?? 0).toBeGreaterThan(0));
  return session;
}

function pendingRows(): unknown[] {
  return Array.from(idb.stores.get(PENDING_STORE)?.values() ?? []);
}

function chunkCount(): number {
  return idb.stores.get(CHUNK_STORE)?.size ?? 0;
}

beforeEach(() => {
  vi.clearAllMocks();
  resetVoiceSessionsForTest();
  instances.length = 0;
  (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeMediaRecorder;
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) },
  });
  idb = installFakeIndexedDB();
  resetVoiceDbForTest();
});

afterEach(() => {
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
});

describe('client_id はセッションが 1 つだけ決める (N-1)', () => {
  it('救出は録音開始時の client_id をそのまま使う', async () => {
    const session = await startSession();
    const clientId = session.clientId;
    expect(clientId).toMatch(UUID_V4);

    const saved = await rescueVoiceSessions('staff-1');

    expect(saved).toBe(1);
    expect(enqueueVoice).toHaveBeenCalledTimes(1);
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.clientId).toBe(clientId);
  });

  it('同時に救出しても積むのは 1 回だけ (pagehide + visibilitychange)', async () => {
    await startSession();

    const [a, b] = await Promise.all([
      rescueVoiceSessions('staff-1'),
      rescueVoiceSessions('staff-1'),
    ]);

    // 同じ Promise を共有するので、両方が「1 件積めた」を見る。
    expect(a).toBe(1);
    expect(b).toBe(1);
    expect(enqueueVoice).toHaveBeenCalledTimes(1);
    expect(pendingRows()).toHaveLength(1);
  });
});

describe('画面ロックの自動停止 (N-2)', () => {
  it('セッションに置くだけでなく、その場でキューへ積む', async () => {
    const session = await startSession();

    goHidden();

    await vi.waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    expect(pendingRows()).toHaveLength(1);
    expect(session.pending).not.toBeNull();
    // 積めたことを確認してから端末のチャンクを消す（順序が逆だと消えた音声は戻らない）。
    await vi.waitFor(() => expect(chunkCount()).toBe(0));
  });

  it('積めなかったときはチャンクを残す（端末に音声を残す）', async () => {
    const session = await startSession();
    // 以後の書き込みは quota 超過で abort する = キューへ積めない。
    idb.state.failWrites = true;

    goHidden();

    await vi.waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    expect(pendingRows()).toHaveLength(0);
    // 消さない = 次に開いたときに残骸として拾える。
    expect(chunkCount()).toBeGreaterThan(0);
    expect(session.pending).not.toBeNull();
  });
});

describe('積めなかった救出 (N-3)', () => {
  it('staffId が空ならセッションを畳まず、トーストで知らせる', async () => {
    const session = await startSession('');

    const saved = await rescueVoiceSessions('');

    expect(saved).toBe(0);
    expect(enqueueVoice).not.toHaveBeenCalled();
    // 畳んでいない = 同じセッションが返る（録音はまだ手元にある）。
    expect(acquireVoiceSession({ visitId: 'visit-1', patientId: 'pat-1' })).toBe(session);
    expect(session.pending).not.toBeNull();
    // マイクだけは手放す。
    expect(session.recorder).toBeNull();
    expect(toast.error).toHaveBeenCalledWith('録音 1 件を保存できませんでした', expect.anything());
  });
});

describe('訪問外のセッションと未保存の録音 (N-4)', () => {
  it('voiceSessionKey(null) は録音ごとに別のキーになる', () => {
    const first = voiceSessionKey(null);
    const second = voiceSessionKey(null);

    expect(first).toMatch(/^unlinked:/);
    expect(first).not.toBe(second);
    expect(voiceSessionKey('visit-1')).toBe('visit:visit-1');
  });

  it('未保存の録音が残っているあいだは新しい録音を始めない', async () => {
    const session = await startSession('');
    // 積めなかった = 未保存の録音がセッションに残る。
    await rescueVoiceSessions('');
    expect(session.pending).not.toBeNull();

    expect(() =>
      startVoiceSessionRecorder(session, {
        visitId: 'visit-1',
        patientId: 'pat-1',
        patientName: '山田 花子',
      }),
    ).toThrow(UNSAVED_RECORDING_MESSAGE);
    // 上書きしない = 前の録音は残っている。
    expect(session.pending).not.toBeNull();
  });
});
