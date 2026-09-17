/**
 * 訪問の音声記録 — MediaRecorder ラッパ（設計 §2-2 / §10-5）。
 *
 * 現場の端末は iOS Safari (PWA) が主で、ブラウザ録音には固有の制約がある:
 *   - 画面ロック / バックグラウンドで MediaRecorder が止まる
 *     → Wake Lock を取り、`visibilitychange` で hidden になったら **自分から止めて
 *       そこまでを保全する**（黙って尻切れの音声を作らない）。
 *   - 対応コーデックが端末で違う
 *     → `isTypeSupported` を webm/opus → mp4 の順で当てる（{@link pickMimeType}）。
 *   - 長時間録音のメモリ / 消失リスク
 *     → `start(10000)` で 10 秒ごとに `dataavailable` を受け、その都度 IndexedDB へ
 *       退避する。アプリが落ちてもチャンクは残る。
 *   - 上限 60 分で自動停止（55 分で警告）。
 *
 * SSR 安全: モジュールの読み込み時にブラウザ API へは触れない。
 */
import {
  AUDIO_SIZE_STOP_BYTES,
  AUDIO_SIZE_WARN_BYTES,
  ORPHAN_CHUNK_MAX_AGE_MS,
} from '@/lib/voice/constants';
import { CHUNK_STORE, idbDeleteMany, idbGetAll, idbPut } from '@/lib/voice/idb';

/** 10 秒ごとにチャンクを受け取る（設計 §2-2）。 */
export const CHUNK_TIMESLICE_MS = 10_000;
/** 上限 60 分で自動停止。 */
export const MAX_RECORDING_MS = 60 * 60 * 1000;
/** 55 分で警告。 */
export const WARN_RECORDING_MS = 55 * 60 * 1000;
/** 音声は 32kbps（Safari は指定が効かず AAC 既定になる）。 */
export const AUDIO_BITS_PER_SECOND = 32_000;

/**
 * 端末で試す MIME の優先順（設計 §2-2）。
 *
 * `audio/mp4;codecs=opus` は入れない（レビュー L-2）。MP4 コンテナに Opus を
 * 入れる組み合わせは実在の端末で使われておらず、`isTypeSupported` が true を
 * 返す実装（過去の Chromium）に当たると**再生も文字起こしもできない**ファイルを
 * 作る。iOS は `audio/mp4`（AAC）に落とすのが正。
 */
export const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/mp4',
  'audio/ogg;codecs=opus',
] as const;

export type RecorderStatus = 'idle' | 'recording' | 'paused' | 'stopping' | 'stopped';

export interface VoiceRecorderState {
  status: RecorderStatus;
  /** 一時停止中を除いた実録音秒数。 */
  elapsedSec: number;
  /** 55 分警告を出したか。 */
  warned: boolean;
  /** ここまでに受け取った音声の累積バイト数。 */
  bytes: number;
}

export interface VoiceRecordingResult {
  sessionId: string;
  blob: Blob;
  mimeType: string;
  durationSec: number;
}

/**
 * 自分から止めた理由。
 *   `limit`      … 60 分上限
 *   `hidden`     … 画面が隠れた
 *   `size_limit` … サーバの受領上限（20 MiB）に届きそう
 */
export type AutoStopReason = 'limit' | 'hidden' | 'size_limit';

export interface VoiceRecorderOptions {
  /**
   * 録音していた訪問（レビュー H-B）。チャンクに焼き込んでおくと、アプリが落ちて
   * 残骸だけが残った場合でも**どの訪問の誰の録音か**が分かる。無い（＝訪問を離れた
   * 場所での録音）ときは null。
   */
  visitId?: string | null;
  patientId?: string | null;
  /** 残骸カードに出す患者名（表示のためだけ。突合には使わない）。 */
  patientName?: string | null;
  /** 55 分警告。 */
  onWarn?: () => void;
  /** 上限バイト数の 90% に達したとき（あと少しで止まる）。 */
  onSizeWarn?: (bytes: number) => void;
  /** 自動停止したとき（上限 / 画面が隠れた / サイズ）。録音済みデータを渡す。 */
  onAutoStop?: (result: VoiceRecordingResult, reason: AutoStopReason) => void;
  /** 録音開始後のエラー（トラック切断など）。 */
  onError?: (err: Error) => void;
  timesliceMs?: number;
  maxMs?: number;
  warnMs?: number;
  maxBytes?: number;
  warnBytes?: number;
}

/** IndexedDB に退避する 1 チャンク。 */
interface ChunkRecord {
  key: string;
  sessionId: string;
  index: number;
  blob: Blob;
  mimeType: string;
  /** 退避した時刻（epoch ms）。孤児チャンクの掃除・表示に使う。 */
  at: number;
  /** 録音していた訪問 / 患者（レビュー H-B）。旧レコードには無い。 */
  visitId?: string | null;
  patientId?: string | null;
  patientName?: string | null;
}

/** この端末で使える録音 MIME。どれも非対応なら空文字（ブラウザ既定に任せる）。 */
export function pickMimeType(): string {
  if (typeof MediaRecorder === 'undefined') return '';
  for (const mime of MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(mime)) return mime;
    } catch {
      /* isTypeSupported 自体が無い古い実装 — 次の候補へ */
    }
  }
  return '';
}

/**
 * 録音できない理由（録音できるなら null・レビュー L-2）。
 *
 * `getUserMedia` は**セキュアコンテキスト以外では存在しない**。社内 LAN の
 * `http://` で開いた端末は「この端末では録音できません」と出るが、本当の原因は
 * 端末ではなく URL なので、直せる案内（HTTPS で開き直す）に変える。
 */
export function recordingUnsupportedReason(): string | null {
  if (typeof window === 'undefined') return 'この端末では録音できません';
  // jsdom / 古い実装は `isSecureContext` を持たない。false のときだけ弾く。
  if (window.isSecureContext === false) {
    return 'このURLでは録音できません（HTTPS が必要です）';
  }
  if (typeof MediaRecorder === 'undefined') return 'この端末のブラウザでは録音できません';
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    return 'この端末のブラウザでは録音できません';
  }
  return null;
}

/** 録音できる環境か（MediaRecorder + getUserMedia + セキュアコンテキスト）。 */
export function isRecordingSupported(): boolean {
  return recordingUnsupportedReason() === null;
}

function genSessionId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch {
    /* fall through */
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

interface WakeLockLike {
  release(): Promise<void>;
}

async function requestWakeLock(): Promise<WakeLockLike | null> {
  try {
    const nav = navigator as unknown as {
      wakeLock?: { request(type: 'screen'): Promise<WakeLockLike> };
    };
    const lock = await nav.wakeLock?.request('screen');
    return lock ?? null;
  } catch {
    // 非対応 / 権限なし — 警告帯だけで運用する（録音は続ける）。
    return null;
  }
}

/**
 * 1 回の録音セッション。`start()` → (`pause()`/`resume()`) → `stop()` で使い切り、
 * 次の録音は新しいインスタンスを作る（`sessionId` = チャンクの名前空間）。
 */
export class VoiceRecorder {
  readonly sessionId: string;
  private readonly opts: VoiceRecorderOptions;
  private readonly timesliceMs: number;
  private readonly maxMs: number;
  private readonly warnMs: number;
  private readonly maxBytes: number;
  private readonly warnBytes: number;

  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private wakeLock: WakeLockLike | null = null;
  private tickTimer: ReturnType<typeof setInterval> | null = null;
  private visibilityHandler: (() => void) | null = null;

  /** メモリ上の控え（IndexedDB が使えない端末はこちらが正）。 */
  private chunks: Blob[] = [];
  private chunkIndex = 0;
  private mimeType = '';

  private startedAt = 0;
  private accumulatedMs = 0;
  /** 受け取った音声の累積バイト数（サーバの受領上限を跨がせないため）。 */
  private bytes = 0;
  private sizeWarned = false;
  private state: VoiceRecorderState = { status: 'idle', elapsedSec: 0, warned: false, bytes: 0 };
  private listeners = new Set<(s: VoiceRecorderState) => void>();
  private stopPromise: Promise<VoiceRecordingResult> | null = null;

  constructor(options: VoiceRecorderOptions = {}) {
    this.sessionId = genSessionId();
    this.opts = options;
    this.timesliceMs = options.timesliceMs ?? CHUNK_TIMESLICE_MS;
    this.maxMs = options.maxMs ?? MAX_RECORDING_MS;
    this.warnMs = options.warnMs ?? WARN_RECORDING_MS;
    this.maxBytes = options.maxBytes ?? AUDIO_SIZE_STOP_BYTES;
    this.warnBytes = options.warnBytes ?? AUDIO_SIZE_WARN_BYTES;
  }

  /** 現在の状態（購読しない呼び出し側向け）。 */
  getState(): VoiceRecorderState {
    return this.state;
  }

  /** 状態（経過秒を含む）の購読。戻り値を呼ぶと解除。 */
  subscribe(listener: (s: VoiceRecorderState) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** マイク許可を取り、録音を開始する。許可拒否 / 非対応は throw。 */
  async start(): Promise<void> {
    if (this.state.status !== 'idle') return;
    if (!isRecordingSupported()) {
      throw new Error('この端末では録音できません');
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.stream = stream;
    this.mimeType = pickMimeType();
    const options: MediaRecorderOptions = { audioBitsPerSecond: AUDIO_BITS_PER_SECOND };
    if (this.mimeType) options.mimeType = this.mimeType;
    const recorder = new MediaRecorder(stream, options);
    this.recorder = recorder;
    // 実際に採用された MIME（ブラウザ既定に落ちた場合はここで判明する）。
    if (recorder.mimeType) this.mimeType = recorder.mimeType;

    recorder.ondataavailable = (ev: BlobEvent) => {
      if (!ev.data || ev.data.size === 0) return;
      const index = this.chunkIndex++;
      this.chunks.push(ev.data);
      const record: ChunkRecord = {
        key: chunkKey(this.sessionId, index),
        sessionId: this.sessionId,
        index,
        blob: ev.data,
        mimeType: this.mimeType,
        at: Date.now(),
        visitId: this.opts.visitId ?? null,
        patientId: this.opts.patientId ?? null,
        patientName: this.opts.patientName ?? null,
      };
      // 退避は best-effort（メモリ上の控えが正）。`idbPut` は失敗で reject するので、
      // ここで必ず受けて unhandled rejection にしない。
      void idbPut(CHUNK_STORE, record).catch(() => undefined);
      this.accountBytes(ev.data.size);
    };
    recorder.onerror = () => {
      this.opts.onError?.(new Error('録音が中断されました'));
    };

    recorder.start(this.timesliceMs);
    this.startedAt = Date.now();
    this.accumulatedMs = 0;
    this.bytes = 0;
    this.sizeWarned = false;
    this.emit({ status: 'recording', elapsedSec: 0, warned: false, bytes: 0 });

    this.wakeLock = await requestWakeLock();
    this.installVisibilityGuard();
    this.tickTimer = setInterval(() => this.tick(), 1000);
  }

  /** 一時停止（経過秒も止まる）。 */
  pause(): void {
    if (this.state.status !== 'recording' || !this.recorder) return;
    try {
      this.recorder.pause();
    } catch {
      return;
    }
    this.accumulatedMs += Date.now() - this.startedAt;
    this.emit({ ...this.state, status: 'paused' });
  }

  /** 一時停止からの再開。 */
  resume(): void {
    if (this.state.status !== 'paused' || !this.recorder) return;
    try {
      this.recorder.resume();
    } catch {
      return;
    }
    this.startedAt = Date.now();
    this.emit({ ...this.state, status: 'recording' });
  }

  /**
   * 停止して 1 本の Blob にまとめる。IndexedDB に退避したチャンクが揃っていれば
   * そちらを、無ければメモリ上の控えを使う。
   */
  stop(): Promise<VoiceRecordingResult> {
    if (this.stopPromise) return this.stopPromise;
    const recorder = this.recorder;
    if (!recorder || this.state.status === 'idle' || this.state.status === 'stopped') {
      return Promise.reject(new Error('録音していません'));
    }
    if (this.state.status === 'recording') {
      this.accumulatedMs += Date.now() - this.startedAt;
    }
    this.emit({ ...this.state, status: 'stopping' });
    this.clearTimers();

    this.stopPromise = new Promise<VoiceRecordingResult>((resolve, reject) => {
      recorder.onstop = () => {
        void this.finalize().then(resolve, reject);
      };
      try {
        recorder.stop();
      } catch (err) {
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
    return this.stopPromise;
  }

  /** ストリーム / Wake Lock / タイマーを手放す（画面を離れるときに必ず呼ぶ）。 */
  dispose(): void {
    this.clearTimers();
    this.releaseWakeLock();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.recorder = null;
    this.listeners.clear();
  }

  // -- internals ------------------------------------------------------------

  /**
   * 停止して 1 本にまとめる。**IndexedDB のチャンクはここでは消さない**
   * （レビュー N-2）。
   *
   * 結合した Blob はまだメモリの上にしか無い。ここで消すと、保存（未送信キューへの
   * 投入）が失敗した瞬間に端末から音声が丸ごと消える — アプリが落ちたときに拾える
   * はずの残骸も一緒に。消すのは**キューへ積めたことを確認した後**で、
   * {@link discardChunkSession} を呼ぶ側の責任にする。
   */
  private async finalize(): Promise<VoiceRecordingResult> {
    this.releaseWakeLock();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;

    const persisted = await this.readPersistedChunks();
    const parts = persisted.length >= this.chunks.length ? persisted : this.chunks;
    const mimeType = this.mimeType || parts[0]?.type || 'audio/webm';
    const blob = new Blob(parts, { type: mimeType });
    const durationSec = Math.max(0, Math.round(this.accumulatedMs / 1000));
    this.emit({ ...this.state, status: 'stopped', elapsedSec: durationSec });
    return { sessionId: this.sessionId, blob, mimeType, durationSec };
  }

  private async readPersistedChunks(): Promise<Blob[]> {
    const rows = await idbGetAll<ChunkRecord>(CHUNK_STORE);
    return rows
      .filter((r) => r.sessionId === this.sessionId)
      .sort((a, b) => a.index - b.index)
      .map((r) => r.blob);
  }

  /**
   * 累積バイト数を数え、サーバの受領上限（20 MiB）に届く前に自分から止める。
   *
   * 時間の上限（60 分）だけでは足りない。ビットレートは端末任せで、iPhone の
   * AAC は 1 分あたり約 1 MB — 20 分ほどで上限に届く。**録り切ってから
   * 「大きすぎます」と言われるのが最悪**（録り直しができない）なので、
   * 1 チャンク分の余裕を残して 60 分上限と同じ経路で停止・保存する。
   */
  private accountBytes(size: number): void {
    this.bytes += size;
    if (!this.sizeWarned && this.bytes >= this.warnBytes) {
      this.sizeWarned = true;
      this.opts.onSizeWarn?.(this.bytes);
    }
    this.emit({ ...this.state, bytes: this.bytes });
    if (this.bytes >= this.maxBytes) {
      if (this.state.status === 'recording' || this.state.status === 'paused') {
        void this.autoStop('size_limit');
      }
    }
  }

  private tick(): void {
    if (this.state.status !== 'recording') return;
    const ms = this.accumulatedMs + (Date.now() - this.startedAt);
    const elapsedSec = Math.floor(ms / 1000);
    let warned = this.state.warned;
    if (!warned && ms >= this.warnMs) {
      warned = true;
      this.opts.onWarn?.();
    }
    this.emit({ ...this.state, elapsedSec, warned });
    if (ms >= this.maxMs) {
      void this.autoStop('limit');
    }
  }

  private installVisibilityGuard(): void {
    if (typeof document === 'undefined') return;
    // iOS は画面ロック / バックグラウンドで MediaRecorder が止まる。尻切れの
    // 音声を黙って作らないよう、こちらから止めてそこまでを保全する。
    this.visibilityHandler = () => {
      if (document.hidden && this.state.status === 'recording') {
        void this.autoStop('hidden');
      }
    };
    document.addEventListener('visibilitychange', this.visibilityHandler);
  }

  private async autoStop(reason: AutoStopReason): Promise<void> {
    try {
      const result = await this.stop();
      this.opts.onAutoStop?.(result, reason);
    } catch (err) {
      this.opts.onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  }

  private clearTimers(): void {
    if (this.tickTimer) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
    if (this.visibilityHandler && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.visibilityHandler);
      this.visibilityHandler = null;
    }
  }

  private releaseWakeLock(): void {
    const lock = this.wakeLock;
    this.wakeLock = null;
    if (lock) void lock.release().catch(() => undefined);
  }

  private emit(next: VoiceRecorderState): void {
    this.state = next;
    for (const listener of this.listeners) listener(next);
  }
}

function chunkKey(sessionId: string, index: number): string {
  return `${sessionId}:${String(index).padStart(6, '0')}`;
}

// -- 前回の録音の残骸（レビュー H-2） ----------------------------------------

/**
 * `voice-chunks` に取り残されたセッション 1 件。
 *
 * アプリが落ちた / タブを閉じた / iOS がプロセスごと切った場合、`stop()` まで
 * 到達しないので 10 秒ごとのチャンクだけが残る。**次に開いたときに拾えなければ
 * 録音は消えたのと同じ**なので、現セッション以外の束を見つけて出口を出す。
 */
export interface OrphanChunkSession {
  sessionId: string;
  chunkCount: number;
  mimeType: string;
  /** 最後のチャンクの時刻（epoch ms）。 */
  at: number;
  /** おおよその長さ（秒）。チャンク数 × タイムスライス（10 秒）。 */
  approxDurationSec: number;
  /**
   * 録音していた訪問 / 患者（レビュー H-B）。旧レコードや訪問外の録音では null。
   *
   * **いま開いている訪問と一致したときだけ**「この訪問に保存」して良い。別の訪問
   * （前の患者宅で落ちた録音）を目の前の訪問に足すのは、記録の取り違えそのもの。
   */
  visitId: string | null;
  patientId: string | null;
  patientName: string | null;
}

function groupChunks(rows: ChunkRecord[]): Map<string, ChunkRecord[]> {
  const bySession = new Map<string, ChunkRecord[]>();
  for (const row of rows) {
    if (!row?.sessionId) continue;
    const list = bySession.get(row.sessionId) ?? [];
    list.push(row);
    bySession.set(row.sessionId, list);
  }
  return bySession;
}

/**
 * 24 時間より古い孤児チャンクを掃除する（レビュー H-2）。
 *
 * 拾う出口を出したまま放置すると端末の容量を食い続けるので、1 日を過ぎた束は
 * 黙って消す（その日のうちに気付けなかった録音は業務上もう使えない）。
 * `at` を持たない旧レコードは掃除の対象にしない（消す根拠が無い）。
 */
export async function purgeStaleChunks(maxAgeMs = ORPHAN_CHUNK_MAX_AGE_MS): Promise<number> {
  const rows = await idbGetAll<ChunkRecord>(CHUNK_STORE);
  const limit = Date.now() - maxAgeMs;
  const stale: string[] = [];
  for (const [, list] of groupChunks(rows)) {
    const newest = Math.max(...list.map((r) => r.at ?? 0));
    if (newest === 0 || newest >= limit) continue;
    for (const row of list) stale.push(row.key);
  }
  await idbDeleteMany(CHUNK_STORE, stale);
  return stale.length;
}

/** 現セッション以外の残骸（新しい順）。掃除も一緒に済ませる。 */
export async function listOrphanChunkSessions(
  currentSessionId?: string | null,
): Promise<OrphanChunkSession[]> {
  await purgeStaleChunks();
  const rows = await idbGetAll<ChunkRecord>(CHUNK_STORE);
  const out: OrphanChunkSession[] = [];
  for (const [sessionId, list] of groupChunks(rows)) {
    if (currentSessionId && sessionId === currentSessionId) continue;
    // 紐付けはセッション内で同じなので先頭の 1 件から読む（無い旧レコードは null）。
    const head = list.find((r) => r.visitId) ?? list[0];
    out.push({
      sessionId,
      chunkCount: list.length,
      mimeType: list[0]?.mimeType || 'audio/webm',
      at: Math.max(...list.map((r) => r.at ?? 0)),
      approxDurationSec: list.length * Math.round(CHUNK_TIMESLICE_MS / 1000),
      visitId: head?.visitId ?? null,
      patientId: head?.patientId ?? null,
      patientName: head?.patientName ?? null,
    });
  }
  return out.sort((a, b) => b.at - a.at);
}

/** 残骸を 1 本の Blob に結合する（見つからなければ null）。 */
export async function buildOrphanRecording(
  sessionId: string,
): Promise<VoiceRecordingResult | null> {
  const rows = await idbGetAll<ChunkRecord>(CHUNK_STORE);
  const list = rows.filter((r) => r.sessionId === sessionId).sort((a, b) => a.index - b.index);
  if (list.length === 0) return null;
  const mimeType = list[0]?.mimeType || list[0]?.blob?.type || 'audio/webm';
  const blob = new Blob(
    list.map((r) => r.blob),
    { type: mimeType },
  );
  return {
    sessionId,
    blob,
    mimeType,
    durationSec: list.length * Math.round(CHUNK_TIMESLICE_MS / 1000),
  };
}

/**
 * その録音のチャンクを捨てる（「破棄」／**キューへ積めたことを確認した後**の後始末）。
 *
 * 削除は 1 トランザクションにまとめる（レビュー N-2）。途中で端末が落ちても
 * 「消えた」か「残った」のどちらかになり、半端に欠けた残骸を作らない。
 */
export async function discardChunkSession(sessionId: string): Promise<void> {
  const rows = await idbGetAll<ChunkRecord>(CHUNK_STORE);
  await idbDeleteMany(
    CHUNK_STORE,
    rows.filter((r) => r.sessionId === sessionId).map((r) => r.key),
  );
}

/** `mm:ss` / `h:mm:ss` 表示（タイマー用）。 */
export function formatElapsed(totalSec: number): string {
  const sec = Math.max(0, Math.floor(totalSec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
