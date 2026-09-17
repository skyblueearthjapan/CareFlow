/**
 * 録音ラッパ (`lib/voice/recorder.ts`) のテスト。
 *
 * 守りたい約束:
 *   1. 端末で使える MIME を webm/opus → mp4 の順に選ぶ (iOS 対応の生命線)
 *   2. `dataavailable` のチャンクを溜め、`stop()` で 1 本の Blob に結合する
 *   3. 60 分で自動停止し、55 分で警告する (長時間録音の暴走防止)
 *   4. 画面が隠れたら自分から止めてそこまでを保全する (iOS の停止に先回り)
 *
 * jsdom には MediaRecorder / getUserMedia / IndexedDB が無いので、MediaRecorder は
 * 偽物を差し込み、IndexedDB 不在のまま **メモリ上の控えで結合される**ことも一緒に
 * 確認する (プライベートブラウズ相当の経路)。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { installFakeIndexedDB } from './fakeIdb';
import {
  AUDIO_SIZE_STOP_BYTES,
  AUDIO_SIZE_WARN_BYTES,
  VISIT_AUDIO_MAX_BYTES,
} from '@/lib/voice/constants';
import { CHUNK_STORE, idbPut, resetVoiceDbForTest } from '@/lib/voice/idb';
import {
  MAX_RECORDING_MS,
  MIME_CANDIDATES,
  VoiceRecorder,
  buildOrphanRecording,
  discardChunkSession,
  formatElapsed,
  isRecordingSupported,
  listOrphanChunkSessions,
  pickMimeType,
  purgeStaleChunks,
  recordingUnsupportedReason,
} from '@/lib/voice/recorder';

type DataHandler = (ev: { data: Blob }) => void;

const instances: FakeMediaRecorder[] = [];
let supportedMimes: string[] = ['audio/webm;codecs=opus'];

class FakeMediaRecorder {
  static isTypeSupported(mime: string): boolean {
    return supportedMimes.includes(mime);
  }
  state: 'inactive' | 'recording' | 'paused' = 'inactive';
  mimeType: string;
  timeslice: number | undefined;
  ondataavailable: DataHandler | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(_stream: unknown, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? '';
    instances.push(this);
  }
  start(timeslice?: number) {
    this.timeslice = timeslice;
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
  /** テストからチャンクを流す。 */
  emit(text: string) {
    this.ondataavailable?.({ data: new Blob([text]) });
  }
}

function installBrowserStubs() {
  (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeMediaRecorder;
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) },
  });
}

beforeEach(() => {
  instances.length = 0;
  supportedMimes = ['audio/webm;codecs=opus'];
  installBrowserStubs();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('pickMimeType', () => {
  it('webm/opus を最優先で選ぶ', () => {
    supportedMimes = ['audio/webm;codecs=opus', 'audio/mp4'];
    expect(pickMimeType()).toBe('audio/webm;codecs=opus');
  });

  it('webm 非対応の端末 (iOS) では mp4 に落ちる', () => {
    supportedMimes = ['audio/mp4'];
    expect(pickMimeType()).toBe('audio/mp4');
  });

  it('どれも非対応ならブラウザ既定に任せる (空文字)', () => {
    supportedMimes = [];
    expect(pickMimeType()).toBe('');
  });

  it('MediaRecorder が無い端末は録音不可と判定する', () => {
    (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = undefined;
    expect(isRecordingSupported()).toBe(false);
    expect(pickMimeType()).toBe('');
  });

  it('mp4 + opus の組み合わせは候補に持たない (L-2: 再生できない音声を作らない)', () => {
    expect(MIME_CANDIDATES).not.toContain('audio/mp4;codecs=opus');
    supportedMimes = ['audio/mp4;codecs=opus', 'audio/mp4'];
    expect(pickMimeType()).toBe('audio/mp4');
  });
});

describe('recordingUnsupportedReason (L-2)', () => {
  afterEach(() => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: undefined });
  });

  it('録音できる環境では null', () => {
    expect(recordingUnsupportedReason()).toBeNull();
  });

  it('HTTP (非セキュアコンテキスト) は HTTPS が要ると伝える', () => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false });
    expect(recordingUnsupportedReason()).toContain('HTTPS');
    expect(isRecordingSupported()).toBe(false);
  });

  it('MediaRecorder が無いときは端末のブラウザの問題と伝える', () => {
    (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = undefined;
    expect(recordingUnsupportedReason()).toBe('この端末のブラウザでは録音できません');
  });
});

describe('VoiceRecorder', () => {
  it('チャンクを溜めて stop() で 1 本の Blob に結合する', async () => {
    const rec = new VoiceRecorder();
    await rec.start();
    const mr = instances[0]!;
    expect(mr.timeslice).toBe(10_000); // 10 秒ごとに退避
    mr.emit('abc');
    mr.emit('de');

    const result = await rec.stop();
    expect(result.blob.size).toBe(5);
    expect(result.mimeType).toBe('audio/webm;codecs=opus');
  });

  it('一時停止中は経過秒が進まない', async () => {
    vi.useFakeTimers();
    const rec = new VoiceRecorder();
    await rec.start();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(rec.getState().elapsedSec).toBe(3);
    rec.pause();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(rec.getState().elapsedSec).toBe(3);
    rec.resume();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(rec.getState().elapsedSec).toBe(5);
  });

  it('55 分で警告し 60 分で自動停止する', async () => {
    vi.useFakeTimers();
    const onWarn = vi.fn();
    const onAutoStop = vi.fn();
    const rec = new VoiceRecorder({ onWarn, onAutoStop });
    await rec.start();
    instances[0]!.emit('x');

    await vi.advanceTimersByTimeAsync(55 * 60 * 1000);
    expect(onWarn).toHaveBeenCalledTimes(1);
    expect(onAutoStop).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(MAX_RECORDING_MS - 55 * 60 * 1000);
    expect(onAutoStop).toHaveBeenCalledTimes(1);
    const [result, reason] = onAutoStop.mock.calls[0]!;
    expect(reason).toBe('limit');
    expect((result as { blob: Blob }).blob.size).toBe(1);
  });

  it('画面が隠れたら自分から止めて、そこまでを保全する', async () => {
    const onAutoStop = vi.fn();
    const rec = new VoiceRecorder({ onAutoStop });
    await rec.start();
    instances[0]!.emit('hidden-safe');

    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.waitFor(() => expect(onAutoStop).toHaveBeenCalledTimes(1));
    expect(onAutoStop.mock.calls[0]![1]).toBe('hidden');
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  });
});

describe('サイズ上限での自動停止 (BE 受領上限 20 MiB)', () => {
  it('累積バイト数が上限に届いたら 60 分停止と同じ経路で止める', async () => {
    const onAutoStop = vi.fn();
    const onSizeWarn = vi.fn();
    // 実データを 20 MiB 積まずに済むよう、しきい値をテスト用に縮める。
    const rec = new VoiceRecorder({ onAutoStop, onSizeWarn, maxBytes: 10, warnBytes: 9 });
    await rec.start();
    const mr = instances[0]!;

    mr.emit('12345'); // 5 バイト — まだ止まらない
    expect(onAutoStop).not.toHaveBeenCalled();
    expect(rec.getState().bytes).toBe(5);

    mr.emit('67890'); // 累積 10 バイト = 上限

    await vi.waitFor(() => expect(onAutoStop).toHaveBeenCalledTimes(1));
    const [result, reason] = onAutoStop.mock.calls[0]!;
    expect(reason).toBe('size_limit');
    // そこまでの音声は保全される (捨てない)。
    expect((result as { blob: Blob }).blob.size).toBe(10);
  });

  it('90% 相当に達したら 1 度だけ警告する', async () => {
    const onSizeWarn = vi.fn();
    const rec = new VoiceRecorder({ onSizeWarn, maxBytes: 100, warnBytes: 9 });
    await rec.start();
    const mr = instances[0]!;

    mr.emit('1234'); // 4 バイト — 警告なし
    expect(onSizeWarn).not.toHaveBeenCalled();

    mr.emit('123456'); // 累積 10 バイト — 警告
    mr.emit('1'); // さらに積んでも二度は鳴らない

    expect(onSizeWarn).toHaveBeenCalledTimes(1);
  });

  it('既定のしきい値は上限に 1 チャンク分の余裕を残す', () => {
    expect(AUDIO_SIZE_STOP_BYTES).toBe(VISIT_AUDIO_MAX_BYTES - 512 * 1024);
    expect(AUDIO_SIZE_WARN_BYTES).toBeLessThan(AUDIO_SIZE_STOP_BYTES);
    expect(VISIT_AUDIO_MAX_BYTES).toBe(20 * 1024 * 1024);
  });
});

describe('formatElapsed', () => {
  it('mm:ss / h:mm:ss で出す', () => {
    expect(formatElapsed(0)).toBe('00:00');
    expect(formatElapsed(75)).toBe('01:15');
    expect(formatElapsed(3675)).toBe('1:01:15');
  });
});

describe('前回の録音の残骸 (H-2)', () => {
  beforeEach(() => {
    installFakeIndexedDB();
    resetVoiceDbForTest();
  });

  /** jsdom の Blob は `text()` を持たないので FileReader で読む。 */
  function readBlobText(blob: Blob): Promise<string> {
    return new Promise<string>((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.onerror = () => reject(fr.error ?? new Error('read failed'));
      fr.readAsText(blob);
    });
  }

  /** チャンクを 1 件書き込む。 */
  async function seed(
    sessionId: string,
    index: number,
    at: number,
    text: string,
    link: { visitId?: string | null; patientId?: string | null; patientName?: string | null } = {},
  ) {
    await idbPut(CHUNK_STORE, {
      key: `${sessionId}:${String(index).padStart(6, '0')}`,
      sessionId,
      index,
      blob: new Blob([text]),
      mimeType: 'audio/webm;codecs=opus',
      at,
      ...link,
    });
  }

  it('セッションごとに束ね、長さの目安を出す', async () => {
    const now = Date.now();
    await seed('sess-A', 0, now, 'aa');
    await seed('sess-A', 1, now, 'bb');
    await seed('sess-A', 2, now, 'cc');

    const rows = await listOrphanChunkSessions(null);

    expect(rows).toHaveLength(1);
    expect(rows[0]!.sessionId).toBe('sess-A');
    expect(rows[0]!.chunkCount).toBe(3);
    // 10 秒 × 3 チャンク。
    expect(rows[0]!.approxDurationSec).toBe(30);
  });

  it('いま録音中のセッションは残骸に数えない', async () => {
    await seed('sess-live', 0, Date.now(), 'x');

    expect(await listOrphanChunkSessions('sess-live')).toHaveLength(0);
  });

  it('24 時間より古い孤児チャンクは掃除する', async () => {
    const now = Date.now();
    await seed('sess-old', 0, now - 25 * 60 * 60 * 1000, 'old');
    await seed('sess-new', 0, now, 'new');

    const rows = await listOrphanChunkSessions(null);

    expect(rows.map((r) => r.sessionId)).toEqual(['sess-new']);
    // 実体も消えている (掃除しないと端末の容量を食い続ける)。
    expect(await buildOrphanRecording('sess-old')).toBeNull();
  });

  it('結合して 1 本の Blob にできる / 破棄で消える', async () => {
    const now = Date.now();
    await seed('sess-A', 1, now, 'de');
    await seed('sess-A', 0, now, 'abc');

    const recovered = await buildOrphanRecording('sess-A');
    expect(recovered).not.toBeNull();
    // index 順に結合される (0 → 1)。
    expect(await readBlobText(recovered!.blob)).toBe('abcde');
    expect(recovered!.mimeType).toBe('audio/webm;codecs=opus');

    await discardChunkSession('sess-A');
    expect(await buildOrphanRecording('sess-A')).toBeNull();
  });

  it('どの訪問の録音かをチャンクに焼き込む (H-B)', async () => {
    const rec = new VoiceRecorder({
      visitId: 'visit-9',
      patientId: 'pat-9',
      patientName: '鈴木 一郎',
    });
    await rec.start();
    instances[0]!.emit('x');
    // `dataavailable` → idbPut は best-effort な非同期なので書けるまで待つ。
    await vi.waitFor(async () => expect(await listOrphanChunkSessions(null)).not.toHaveLength(0));

    const rows = await listOrphanChunkSessions(null);
    expect(rows[0]!.visitId).toBe('visit-9');
    expect(rows[0]!.patientId).toBe('pat-9');
    expect(rows[0]!.patientName).toBe('鈴木 一郎');
  });

  it('紐付けを持たない旧レコードは null のまま読む (訪問不明として扱える)', async () => {
    await seed('sess-legacy', 0, Date.now(), 'x');

    const rows = await listOrphanChunkSessions(null);

    expect(rows[0]!.visitId).toBeNull();
    expect(rows[0]!.patientName).toBeNull();
  });

  it('purgeStaleChunks は古い束だけを消す', async () => {
    const now = Date.now();
    await seed('sess-old', 0, now - 48 * 60 * 60 * 1000, 'o');
    await seed('sess-new', 0, now, 'n');

    const removed = await purgeStaleChunks();

    expect(removed).toBe(1);
    expect(await buildOrphanRecording('sess-new')).not.toBeNull();
  });
});
