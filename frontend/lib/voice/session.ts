/**
 * 録音セッションのページ単位シングルトン（レビュー H-C の追加是正）。
 *
 * `VoiceRecorderPanel` の寿命に録音を縛ると、**到着前に録音 → QR スキャン**で
 * 録音が終わってしまう。スキャナはページ内のオーバーレイで、出した瞬間に基本操作の
 * ブロックごとパネルが unmount されるからである。現場から見れば「録音中にQRを読んだら
 * 勝手に録音が切れた」であり、これは不具合そのものになる。
 *
 * そこで **録音の本体（`VoiceRecorder` と未保存の結果）はここが持つ**:
 *   - パネルの mount … このセッションに UI を繋ぎ直す（録音中ならそのまま録音中 UI）
 *   - パネルの unmount … **UI を外すだけ**。止めない・捨てない
 *   - ページ離脱 … {@link rescueVoiceSessions} で止めて未送信キューへ積む
 *
 * 画面が繋がっていない間に自動停止（60 分 / 画面ロック / サイズ上限）が起きても、
 * 結果は {@link VoiceSession.pending} に残るので、戻ってきたパネルが review を
 * 復元できる。
 *
 * SSR 安全: モジュール読み込み時にブラウザ API へは触れない。
 */
'use client';

import { toast } from '@/components/ui/sonner';
import { enqueueVoice, newVoiceClientId, removeVoice } from '@/lib/voice/queue';
import {
  discardChunkSession,
  VoiceRecorder,
  type AutoStopReason,
  type VoiceRecorderOptions,
  type VoiceRecordingResult,
} from '@/lib/voice/recorder';

/** 紐付け先（訪問不明で積むときは両方 null）。 */
export interface RecordingLink {
  visitId: string | null;
  patientId: string | null;
}

/** 停止済みで未保存の録音（review に載っているもの）。 */
export interface UnsavedRecording {
  result: VoiceRecordingResult;
  recordedAt: string;
  consent: boolean;
  clientId: string | null;
  link: RecordingLink;
}

/** いま UI を繋いでいるパネルの通知先。 */
export interface VoiceSessionHandlers {
  onWarn?: () => void;
  onSizeWarn?: (bytes: number) => void;
  onAutoStop?: (result: VoiceRecordingResult, reason: AutoStopReason) => void;
  onError?: (err: Error) => void;
  /**
   * セッションが外から畳まれた（＝救出で保存された）。
   *
   * 画面ロックやページ離脱の救出はパネルの外で走るので、これが無いと保存済みの
   * 録音を「録音中」のまま見せ続けることになる。
   */
  onCleared?: () => void;
}

export interface VoiceSession {
  /** `visit:{id}` または `unlinked:{一意なid}`。 */
  key: string;
  /** このセッションの録音が属する訪問。 */
  link: RecordingLink;
  recorder: VoiceRecorder | null;
  /** 録音を始めた時刻（ISO 8601）。 */
  startedAtIso: string | null;
  /**
   * この録音の `client_id`（レビュー N-1）。
   *
   * **録音開始時に 1 つだけ**決める。review / 救出 / 直接送信のどれを通っても
   * 同じ値を使う — 経路ごとに作り直すと、同じ録音が BE から別物に見えて二重登録に
   * なる。UUID を作れない端末では null（`client_id` を送らない・M-C）。
   */
  clientId: string | null;
  /** いま画面を開いているスタッフ（画面ロックの自動保存の宛先・レビュー N-2）。 */
  staffId: string | null;
  /** 停止済み・未保存（review の中身）。 */
  pending: UnsavedRecording | null;
  /** 画面ロックの自動保存で積んだキュー行の id（「破棄する」で取り消す）。 */
  queuedId: string | null;
  /** 端末に保存できなかった理由（レビュー C-1）。 */
  saveError: string | null;
  /** 自動停止の理由（表示文言はパネルが決める）。 */
  autoStopReason: AutoStopReason | null;
  /** UI を繋いでいないときは null。 */
  handlers: VoiceSessionHandlers | null;
}

const sessions = new Map<string, VoiceSession>();

/** 訪問に紐付かない録音のセッション id（録音ごとに別物）。 */
function newUnlinkedId(): string {
  return newVoiceClientId() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * セッションのキー。訪問ごとに 1 つ、訪問が無い録音は**録音ごと**に 1 つ
 * （レビュー N-4）。
 *
 * 訪問外の録音をすべて `unlinked` 1 本に集めると、別の場面で始めた次の録音が
 * 同じセッションを掴み、まだ保存していない前の録音を上書きして消してしまう。
 * 引数を省いた呼び出しは**毎回違うキー**を返す。
 */
export function voiceSessionKey(visitId?: string | null, unlinkedId?: string | null): string {
  if (visitId) return `visit:${visitId}`;
  return `unlinked:${unlinkedId ?? newUnlinkedId()}`;
}

/** セッションを取り出す（無ければ作る）。 */
export function getVoiceSession(key: string, link: RecordingLink): VoiceSession {
  const found = sessions.get(key);
  if (found) return found;
  const created: VoiceSession = {
    key,
    link,
    recorder: null,
    startedAtIso: null,
    clientId: null,
    staffId: null,
    pending: null,
    queuedId: null,
    saveError: null,
    autoStopReason: null,
    handlers: null,
  };
  sessions.set(key, created);
  return created;
}

/**
 * この画面のセッションを得る（レビュー N-4 / N-5）。
 *
 * 訪問があれば訪問ごとの 1 つ。無ければ**生きている訪問外セッションを引き継ぎ**、
 * 無ければ新しいキーで作る（前の録音を保存 / 破棄すると畳まれるので、次の録音は
 * 必ず別のキーになる）。
 */
export function acquireVoiceSession(link: RecordingLink): VoiceSession {
  if (link.visitId) return getVoiceSession(voiceSessionKey(link.visitId), link);
  for (const session of sessions.values()) {
    if (!session.link.visitId) return session;
  }
  return getVoiceSession(voiceSessionKey(null), link);
}

/** UI を繋ぐ（`staffId` は画面ロックの自動保存の宛先・レビュー N-2）。 */
export function attachVoiceSession(
  session: VoiceSession,
  handlers: VoiceSessionHandlers,
  staffId?: string | null,
): void {
  session.handlers = handlers;
  if (staffId) session.staffId = staffId;
}

/** UI を外す（**止めない**）。後から来た別のパネルの繋ぎ先は壊さない。 */
export function detachVoiceSession(session: VoiceSession, handlers: VoiceSessionHandlers): void {
  if (session.handlers === handlers) session.handlers = null;
}

/** 録音が生きているか（繋ぎ直したら録音中 UI に戻す判定）。 */
export function isRecordingSession(session: VoiceSession): boolean {
  const status = session.recorder?.getState().status;
  return status === 'recording' || status === 'paused' || status === 'stopping';
}

/** 未保存の録音が残っているセッションで録音を始めようとしたとき。 */
export const UNSAVED_RECORDING_MESSAGE = '前の録音がまだ保存されていません';

/**
 * このセッションの録音機を作る。
 *
 * コールバックは**セッション経由で今のパネルへ配る**。録音機を作ったパネルに直接
 * 束ねると、スキャナ往復で作り直された後のパネルへ自動停止が届かない。
 *
 * **未保存の録音が残っていたら始めない**（レビュー N-4）。ここで `pending` を
 * 捨てて上書きすると、保存も破棄もしていない録音が黙って消える。
 */
export function startVoiceSessionRecorder(
  session: VoiceSession,
  options: Pick<VoiceRecorderOptions, 'visitId' | 'patientId' | 'patientName'>,
): VoiceRecorder {
  if (session.pending) throw new Error(UNSAVED_RECORDING_MESSAGE);
  // `client_id` はこの 1 回の録音の名前。以後どの経路もこれを使う（N-1）。
  session.clientId = newVoiceClientId();
  const recorder = new VoiceRecorder({
    ...options,
    onWarn: () => session.handlers?.onWarn?.(),
    onSizeWarn: (bytes) => session.handlers?.onSizeWarn?.(bytes),
    onAutoStop: (result, reason) => {
      // UI が繋がっていなくても結果は落とさない（戻ってきたら review を復元する）。
      const item: UnsavedRecording = session.pending ?? {
        result,
        recordedAt: session.startedAtIso ?? new Date().toISOString(),
        // 録音は同意チェックを通らないと始められない（開始時点の同意が正）。
        consent: true,
        clientId: session.clientId,
        link: session.link,
      };
      session.pending = item;
      session.autoStopReason = reason;
      session.handlers?.onAutoStop?.(result, reason);
      // 画面ロック（iOS）はそのままアプリごと切られることがある。セッション（＝
      // メモリ）に置くだけでは足りないので、その場で未送信キューへ落とす（N-2）。
      if (reason === 'hidden') void persistPending(session, item, session.staffId ?? '');
    },
    onError: (err) => session.handlers?.onError?.(err),
  });
  session.recorder = recorder;
  session.saveError = null;
  session.autoStopReason = null;
  session.queuedId = null;
  return recorder;
}

/**
 * 未保存の録音を未送信キューへ落とす（積めたら true・レビュー N-2）。
 *
 * **積めたことを確認してから**チャンクを消す。staffId が取れない / 端末に書けない
 * ときはチャンクを残す — 端末に残っていれば次に開いたときに残骸として拾える。
 */
async function persistPending(
  session: VoiceSession,
  item: UnsavedRecording,
  staffId: string,
): Promise<boolean> {
  if (!staffId || item.result.blob.size === 0) return false;
  // 画面ロック等で既にキューへ積んであれば二度送らない (client_id は同じなので
  // BE は畳むが、20 MiB を無駄に 2 回送る)。
  if (session.queuedId) return true;
  let entry: Awaited<ReturnType<typeof enqueueVoice>> = null;
  try {
    entry = await enqueueVoice({
      staffId,
      visitId: item.link.visitId,
      patientId: item.link.patientId,
      recordedAt: item.recordedAt,
      durationSec: item.result.durationSec,
      mimeType: item.result.mimeType,
      blob: item.result.blob,
      consent: item.consent,
      clientId: item.clientId,
    });
  } catch {
    return false;
  }
  if (!entry) return false;
  session.queuedId = entry.id;
  await discardChunkSession(item.result.sessionId);
  return true;
}

/**
 * 画面ロックの自動保存で積んだ行を取り消す（「破棄する」を押したとき）。
 *
 * 利用者が捨てたものを、裏で積んでおいたからといって送ってはならない。
 */
export async function dropQueuedVoice(session: VoiceSession): Promise<void> {
  const id = session.queuedId;
  session.queuedId = null;
  if (id) await removeVoice(id);
}

/** マイクとタイマーだけ手放す（セッションは畳まない）。 */
function releaseRecorder(session: VoiceSession): void {
  session.recorder?.dispose();
  session.recorder = null;
}

/**
 * セッションを畳む（保存できた / 破棄した / 始められなかった）。
 *
 * `notify` はパネルの外（救出）から畳んだとき。繋がっている画面へ「もう無い」と
 * 伝えないと、保存済みの録音を「録音中」のまま見せ続けることになる。
 */
export function clearVoiceSession(session: VoiceSession, options: { notify?: boolean } = {}): void {
  const handlers = session.handlers;
  session.recorder?.dispose();
  session.recorder = null;
  session.startedAtIso = null;
  session.clientId = null;
  session.pending = null;
  session.queuedId = null;
  session.saveError = null;
  session.autoStopReason = null;
  session.handlers = null;
  sessions.delete(session.key);
  if (options.notify) handlers?.onCleared?.();
}

/** テスト用: 全セッションを（救出せずに）捨てる。 */
export function resetVoiceSessionsForTest(): void {
  for (const session of Array.from(sessions.values())) clearVoiceSession(session);
  sessions.clear();
  rescueInFlight.clear();
}

/** 救出 1 件の結末。 */
type RescueOutcome = 'saved' | 'kept' | 'empty';

/**
 * 実行中の救出（セッションごと・レビュー N-1b）。
 *
 * ページ離脱では `pagehide` と `visibilitychange` がほぼ同時に飛ぶ。ガードが無いと
 * 同じ録音に対して `stop()` と `enqueueVoice` が 2 本走り、`client_id` の取り違えや
 * 二重投入の余地を作る。同じセッションの救出は**同じ Promise を返す**。
 */
const rescueInFlight = new Map<string, Promise<RescueOutcome>>();

function rescueOnce(session: VoiceSession, staffId: string): Promise<RescueOutcome> {
  const running = rescueInFlight.get(session.key);
  if (running) return running;
  const started = rescueOne(session, staffId);
  rescueInFlight.set(session.key, started);
  void started.then(
    () => releaseRescue(session.key, started),
    () => releaseRescue(session.key, started),
  );
  return started;
}

function releaseRescue(key: string, settled: Promise<RescueOutcome>): void {
  if (rescueInFlight.get(key) === settled) rescueInFlight.delete(key);
}

/**
 * ページ離脱の救出（レビュー H-1 / H-C）。止めて未送信キューへ積み、畳む。
 *
 * パネルの unmount ではなく**ページを離れるとき**に呼ぶ。戻り値は積めた件数。
 * 積めなかったセッションは**畳まない**（レビュー N-3）— 畳めばその録音は消える。
 */
export async function rescueVoiceSessions(staffId: string): Promise<number> {
  let saved = 0;
  let kept = 0;
  for (const session of Array.from(sessions.values())) {
    const outcome = await rescueOnce(session, staffId);
    if (outcome === 'saved') saved += 1;
    else if (outcome === 'kept') kept += 1;
  }
  if (kept > 0) {
    toast.error(`録音 ${kept} 件を保存できませんでした`, {
      description: staffId
        ? '録音は画面に残しています。端末の空き容量をご確認のうえ保存してください'
        : 'スタッフ情報が取得できません。ログインし直してから保存してください',
    });
  }
  return saved;
}

async function rescueOne(session: VoiceSession, staffId: string): Promise<RescueOutcome> {
  const recorder = session.recorder;
  let item = session.pending;
  try {
    if (recorder && isRecordingSession(session)) {
      // `stopping` なら既に走っている `stop()` の結果を待つ（二重には止めない）。
      const result = await recorder.stop();
      item = session.pending ?? {
        result,
        recordedAt: session.startedAtIso ?? new Date().toISOString(),
        consent: true,
        // 録音開始時に決めた 1 つを使う（作り直さない・レビュー N-1）。
        clientId: session.clientId,
        link: session.link,
      };
      session.pending = item;
    }
  } catch {
    /* 止められなければ諦める（下で畳んでマイクを手放す） */
  }
  if (!item || item.result.blob.size === 0) {
    clearVoiceSession(session);
    return 'empty';
  }
  if (await persistPending(session, item, staffId)) {
    clearVoiceSession(session, { notify: true });
    return 'saved';
  }
  // 積めていない = 畳めば音声が消える（N-3）。マイクだけ手放して録音は残す。
  releaseRecorder(session);
  return 'kept';
}
