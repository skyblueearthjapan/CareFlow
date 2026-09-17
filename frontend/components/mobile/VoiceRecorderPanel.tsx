'use client';

/**
 * 訪問の音声記録パネル（設計 §2-2・モック `visit-voice-record-mock.html` ①②）。
 *
 * 流れ: 同意チェック → 「録音を始める」→ 録音中（注意帯・タイマー・波形・一時停止）
 * → 停止 → 「保存して文字起こしへ」→ 未送信キューへ積んで即送信。
 *
 * 送信は原則キュー（`lib/voice/queue.ts`）を通す。訪問先は電波が弱く、その場で
 * 送れなくても**端末に残っていれば後から必ず送れる**ため（打刻の退避と同じ約束）。
 *
 * ただし**端末に保存できなかったときだけは別**（レビュー C-1）。IndexedDB が
 * 書けない（容量超過 / プライベートブラウズ）状況で「電波が戻ると自動で送信します」
 * と言うのは嘘になる。その場合は手元の Blob を握ったまま review に留まり、
 * 「もう一度保存」「端末に保存（ダウンロード）」「直接送信」の 3 つの出口を出す。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { AlertTriangle, Download, Mic, Pause, Play, Send, Square, Upload } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { CheckInButton } from '@/components/mobile/CheckInButton';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import {
  buildOrphanRecording,
  discardChunkSession,
  formatElapsed,
  listOrphanChunkSessions,
  recordingUnsupportedReason,
  type AutoStopReason,
  type OrphanChunkSession,
  type VoiceRecorder,
  type VoiceRecordingResult,
} from '@/lib/voice/recorder';
import {
  acquireVoiceSession,
  attachVoiceSession,
  clearVoiceSession,
  detachVoiceSession,
  dropQueuedVoice,
  isRecordingSession,
  startVoiceSessionRecorder,
  UNSAVED_RECORDING_MESSAGE,
  type RecordingLink,
  type UnsavedRecording,
  type VoiceSession,
  type VoiceSessionHandlers,
} from '@/lib/voice/session';
import { audioFileName, enqueueVoice, flushVoiceQueue, newVoiceClientId } from '@/lib/voice/queue';
import { approxMinutes, formatBytes, VISIT_AUDIO_MAX_BYTES } from '@/lib/voice/constants';
import { useUploadRecording } from '@/lib/queries/visit-recordings';

/** 同意の説明ダイアログを一度でも読んだか（端末ごと）。 */
const CONSENT_SEEN_KEY = 'rakusuke:voice-consent-seen';

/**
 * サイズ上限で自動停止したときの説明。
 *
 * 「なぜ勝手に止まったのか」が分からないと現場は不具合だと思う。原因（上限）と
 * 目安（iPhone は 1 分あたり約 1 MB）を同じ文に置く。
 */
const SIZE_LIMIT_NOTE =
  '録音サイズの上限に達したため保存しました（iPhone は AAC で 1 分あたり約 1 MB）';

type PanelMode = 'idle' | 'recording' | 'review' | 'saving';
/** 説明ダイアログの後に続ける操作。 */
type GatedAction = 'record' | 'import';

interface VoiceRecorderPanelProps {
  visitId?: string | null;
  patientId?: string | null;
  patientName: string;
  /**
   * 待機カードの見出し（既定は「音声記録」）。
   *
   * このパネルは到着打刻を跨いで**同じインスタンスのまま**置かれる（レビュー
   * H-C）。到着前と訪問中で出し分けたいのは見出しだけなので、分岐して別々に
   * 置く代わりにここを変える（分岐して置くと打刻の瞬間に unmount して録音が飛ぶ）。
   */
  heading?: string;
  /** 保存（キュー投入）が終わったとき。一覧の再取得などに使う。 */
  onSaved?: () => void;
}

/** キュー投入 1 回分の入力（引数が増えたので名前付きにする）。 */
interface SaveInput {
  blob: Blob;
  mimeType: string;
  durationSec: number;
  recordedAt: string;
  consent: boolean;
  /** 省略時はこの画面の訪問。孤児の救出だけが null（訪問不明）を渡す。 */
  link?: RecordingLink;
  /** review で確定済みの `client_id`（レビュー M-B）。無ければここで作る。 */
  clientId?: string | null;
  /**
   * この音声の録音セッション id（レビュー N-2）。
   *
   * **積めたことを確認してから**この id のチャンクを消す。停止した時点で消すと、
   * 保存に失敗した瞬間に端末から音声が丸ごと消える。
   */
  sessionId?: string | null;
}

/** 説明ダイアログを読んだか（プライベートブラウズでは常に false）。 */
function consentDialogSeen(): boolean {
  try {
    return window.localStorage.getItem(CONSENT_SEEN_KEY) === '1';
  } catch {
    return false;
  }
}

/** 端末へダウンロードする（送れないときの最後の出口）。 */
function downloadBlob(blob: Blob, filename: string): boolean {
  try {
    if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return false;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // 端末が読み終えるまで少し待ってから開放する。
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
    return true;
  } catch {
    return false;
  }
}

/**
 * 空き容量が音声の 2 倍に満たなければ警告する（レビュー M-3）。
 *
 * IndexedDB は容量が尽きるとコミット時に abort するので、**積む前に**知らせる。
 * 2 倍見ているのは、キューが自分の控えとブラウザ側の一時領域を同時に持つため。
 */
async function warnIfLowStorage(bytes: number): Promise<void> {
  try {
    const estimate = await navigator.storage?.estimate?.();
    if (!estimate) return;
    const free = (estimate.quota ?? 0) - (estimate.usage ?? 0);
    if (free > 0 && free < bytes * 2) {
      toast.warning('端末の空き容量が少なくなっています', {
        description: `残り ${formatBytes(free)}・不要な写真やアプリを整理してください`,
      });
    }
  } catch {
    /* 非対応 — 警告を出せないだけ */
  }
}

/** 残骸カードの日時（`9/17 21:40`）。どの録音かを人が見分けるためだけの表示。 */
function formatWhen(at: number): string {
  const d = new Date(at);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

/** 自動停止の説明（サイズ上限だけ review に残す）。 */
function autoStopNoteOf(reason: AutoStopReason | null): string | null {
  return reason === 'size_limit' ? SIZE_LIMIT_NOTE : null;
}

export function VoiceRecorderPanel({
  visitId,
  patientId,
  patientName,
  heading = '音声記録',
  onSaved,
}: VoiceRecorderPanelProps) {
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();
  const upload = useUploadRecording();

  /** この画面のセッションを取り直す（保存 / 破棄で畳んだ後の次の録音用）。 */
  const newSession = useCallback(
    () => acquireVoiceSession({ visitId: visitId ?? null, patientId: patientId ?? null }),
    [visitId, patientId],
  );

  /**
   * この訪問の録音セッション（レビュー H-C / N-5）。
   *
   * 録音の本体はここではなくセッションが持つ。このパネルは mount で繋ぎ、unmount で
   * 外すだけ — スキャナを出しても録音は切れない。以下の初期値はすべて
   * 「戻ってきたときに続きから見える」ためのもの。
   *
   * **レンダー中には取りに行かない**（N-5）。`getVoiceSession` は無ければ作る副作用
   * を持つので、React が捨てるレンダー（StrictMode / 中断）でも訪問外セッションが
   * 増えてしまう。掴むのは初期化と「畳んだ後」の 2 箇所だけにする。
   */
  const [voice, setVoice] = useState<VoiceSession>(newSession);

  const [mode, setMode] = useState<PanelMode>(() =>
    isRecordingSession(voice) ? 'recording' : voice.pending ? 'review' : 'idle',
  );
  // 録音中 / review を復元したなら同意は取得済み（そうでないと始められていない）。
  const [consent, setConsent] = useState(() => isRecordingSession(voice) || !!voice.pending);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [gatedAction, setGatedAction] = useState<GatedAction>('record');
  const [elapsedSec, setElapsedSec] = useState(() => voice.recorder?.getState().elapsedSec ?? 0);
  const [paused, setPaused] = useState(() => voice.recorder?.getState().status === 'paused');
  const [result, setResult] = useState<VoiceRecordingResult | null>(
    () => voice.pending?.result ?? null,
  );
  const [startedAtIso, setStartedAtIso] = useState<string | null>(
    () => voice.pending?.recordedAt ?? voice.startedAtIso,
  );
  const [blocked, setBlocked] = useState<string | null>(null);
  /** 端末に保存できなかった理由（レビュー C-1）。null なら通常の review。 */
  const [saveError, setSaveError] = useState<string | null>(() => voice.saveError);
  /** review に載っている音声について**実際に確認した同意**（レビュー H-3）。 */
  const [resultConsent, setResultConsent] = useState(() => voice.pending?.consent ?? false);
  /** 直接送信の進捗（0〜1・レビュー M-4）。 */
  const [uploadRatio, setUploadRatio] = useState<number | null>(null);
  /** 自動停止の理由の説明（サイズ上限のときだけ review に残す）。 */
  const [autoStopNote, setAutoStopNote] = useState<string | null>(() =>
    autoStopNoteOf(voice.autoStopReason),
  );
  /**
   * review に載せた音声の `client_id`（レビュー M-B）。
   *
   * キュー投入が失敗して「直接送信」へ回っても**同じ値**を使う。ここで値を作り
   * 直すと、キューに残った控えと直接送信が BE から別の録音に見えて二重登録になる。
   */
  const [clientId, setClientId] = useState<string | null>(() => voice.pending?.clientId ?? null);
  /** review に載せた音声の紐付け先（孤児を訪問不明で積んだときは両方 null）。 */
  const [resultLink, setResultLink] = useState<RecordingLink | null>(
    () => voice.pending?.link ?? null,
  );
  /** 前回の録音の残骸（レビュー H-2）。 */
  const [orphan, setOrphan] = useState<OrphanChunkSession | null>(null);
  const [orphanBusy, setOrphanBusy] = useState(false);

  const recorderRef = useRef<VoiceRecorder | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  /**
   * 停止した音声を review に載せる（レビュー M-B / H-C）。
   *
   * `client_id` は**録音開始時にセッションが決めた 1 つ**を使う（レビュー N-1）。
   * ここで作り直すと、救出で積まれた控えと保存ボタンの送信が BE から別の録音に
   * 見えて二重登録になる。結果はセッションへ預けるので、保存を押す前にスキャナを
   * 開いても、戻ってきたパネルがこの控えから review を復元する。
   */
  const enterReview = useCallback(
    (r: VoiceRecordingResult) => {
      // 自動停止はセッション側が先に控えている（同じ音声なら作り直さない）。
      const item: UnsavedRecording =
        voice.pending?.result === r
          ? voice.pending
          : {
              result: r,
              recordedAt: voice.startedAtIso ?? new Date().toISOString(),
              // 録音は同意チェックを通らないと始められない（開始時点の同意が正）。
              consent: true,
              clientId: voice.clientId,
              link: voice.link,
            };
      voice.pending = item;
      setResult(r);
      setClientId(item.clientId);
      setResultLink(item.link);
      setResultConsent(item.consent);
      setStartedAtIso(item.recordedAt);
      setMode('review');
    },
    [voice],
  );

  /** 画面の状態だけを待機へ戻す（セッションには触らない）。 */
  const resetPanelState = useCallback(() => {
    setResult(null);
    setStartedAtIso(null);
    setElapsedSec(0);
    setSaveError(null);
    setUploadRatio(null);
    // 同意は録音ごとに取り直す（レビュー M-5）。
    setConsent(false);
    setResultConsent(false);
    setAutoStopNote(null);
    setClientId(null);
    setResultLink(null);
    setMode('idle');
  }, []);

  /**
   * UI をセッションへ繋ぐ / 外す（レビュー H-C）。
   *
   * **unmount では止めない**。スキャナはページ内のオーバーレイで、出した瞬間に
   * このパネルは unmount される — ここで `stop()` すると「録音中に QR を読んだら
   * 録音が終わる」ことになる。止めて積むのはページ離脱
   * （`[visitId]/page.tsx` の `rescueVoiceSessions`）の仕事。
   */
  useEffect(() => {
    const handlers: VoiceSessionHandlers = {
      onWarn: () => toast.warning('あと 5 分で自動停止します'),
      // サイズ上限の 90%。止まる前に「まとめに入る」判断ができるように。
      onSizeWarn: () =>
        toast.warning('まもなく録音サイズの上限です', {
          description: 'あと少しで自動的に保存されます',
        }),
      onAutoStop: (r, stopReason) => {
        enterReview(r);
        setAutoStopNote(autoStopNoteOf(stopReason));
        toast.warning(
          stopReason === 'size_limit'
            ? SIZE_LIMIT_NOTE
            : stopReason === 'limit'
              ? '上限の 60 分になったので録音を止めました'
              : '画面が消えたので録音を止めました（ここまでは保存できます）',
        );
      },
      onError: (err) => toast.error('録音が中断されました', { description: err.message }),
      // 画面ロック / ページ離脱の救出で外から畳まれた。保存済みの録音を
      // 「録音中」のまま見せ続けないよう、次の録音を受けられる形に戻す。
      onCleared: () => {
        unsubRef.current?.();
        unsubRef.current = null;
        recorderRef.current = null;
        setVoice(newSession());
        resetPanelState();
      },
    };
    attachVoiceSession(voice, handlers, staffId);
    const recorder = voice.recorder;
    recorderRef.current = recorder;
    if (recorder) {
      unsubRef.current = recorder.subscribe((s) => {
        setElapsedSec(s.elapsedSec);
        setPaused(s.status === 'paused');
      });
      // 停止処理の途中で繋ぎ直したなら、止まり切ったところで review へ。
      if (recorder.getState().status === 'stopping') {
        void recorder.stop().then(enterReview, () => undefined);
      }
    }
    return () => {
      unsubRef.current?.();
      unsubRef.current = null;
      recorderRef.current = null;
      detachVoiceSession(voice, handlers);
    };
  }, [voice, enterReview, staffId, newSession, resetPanelState]);

  // 録音中にタブを閉じる / 再読込されると MediaRecorder ごと消える。警告を出す
  // （レビュー H-1）。ブラウザ既定の文言になるが、止める機会は作れる。
  useEffect(() => {
    if (mode !== 'recording') return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
      return '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [mode]);

  // 前回の録音の残骸を拾う（レビュー H-2）。24 時間より古い束はここで掃除される。
  useEffect(() => {
    let cancelled = false;
    void listOrphanChunkSessions(null).then(
      (rows) => {
        if (!cancelled) setOrphan(rows[0] ?? null);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * セッションごと畳む（保存できた / 破棄した / 始められなかった）。
   *
   * 畳んだセッションは登録簿から消えるので、次の録音のために**新しいセッションを
   * 掴み直す**（レビュー N-4 / N-5）。掴み直さないと、次の録音がページ離脱の救出の
   * 対象から外れる。
   */
  const endSession = useCallback(() => {
    unsubRef.current?.();
    unsubRef.current = null;
    recorderRef.current = null;
    clearVoiceSession(voice);
    setVoice(newSession());
  }, [voice, newSession]);

  /** 保存できた後の後始末（同意は毎回取り直す = レビュー M-5）。 */
  const resetAfterSave = useCallback(() => {
    endSession();
    resetPanelState();
  }, [endSession, resetPanelState]);

  /**
   * 未送信キューへ積み、その場で送れるなら送る。
   *
   * `consentValue` は**実際に確認した同意**（レビュー H-3）。ここで固定の `true` を
   * 送ると、同意を取らずに取り込んだ音声まで「同意済み」として保存されてしまう。
   */
  const save = useCallback(
    async (input: SaveInput) => {
      if (!staffId) {
        toast.error('スタッフ情報が取得できないため保存できません');
        return;
      }
      const { blob, mimeType, durationSec, recordedAt, consent: consentValue } = input;
      const link = input.link ?? voice.link;
      // review 経由なら既に確定している。取り込みなど review を通らない経路はここで。
      const cid = input.clientId ?? newVoiceClientId();
      setMode('saving');
      setSaveError(null);
      await warnIfLowStorage(blob.size);
      const entry = await enqueueVoice({
        staffId,
        visitId: link.visitId,
        patientId: link.patientId,
        recordedAt,
        durationSec,
        mimeType,
        blob,
        consent: consentValue,
        clientId: cid,
      });
      if (!entry) {
        // 端末に残せていない = 「後で自動送信」は嘘になる（レビュー C-1）。
        const recovered: VoiceRecordingResult = {
          sessionId: 'unsaved',
          blob,
          mimeType,
          durationSec,
        };
        setResult(recovered);
        setStartedAtIso(recordedAt);
        setResultConsent(consentValue);
        setClientId(cid);
        setResultLink(link);
        // 画面を離れてもここから積み直せるようにセッションへ預ける（レビュー H-C）。
        voice.pending = {
          result: recovered,
          recordedAt,
          consent: consentValue,
          clientId: cid,
          link,
        };
        voice.saveError = '端末に保存できませんでした（空き容量をご確認ください）';
        setSaveError('端末に保存できませんでした（空き容量をご確認ください）');
        setMode('review');
        toast.error('音声を端末に保存できませんでした', {
          description: 'この画面を閉じる前に、下のいずれかで残してください',
        });
        return;
      }
      // 積めた = セッションの控えは役目を終えた（救出の対象から外す）。
      voice.pending = null;
      voice.saveError = null;
      // ここで初めて端末のチャンクを消す（レビュー N-2）。積む前に消すと、保存に
      // 失敗した瞬間に端末から音声が丸ごと消える。
      if (input.sessionId) await discardChunkSession(input.sessionId);
      // 利用者が待っている経路なのでタブ跨ぎロックは待たない（レビュー L-A）。
      const flushed = await flushVoiceQueue(
        staffId,
        { accessToken, refreshToken },
        { interactive: true },
      );
      if (flushed.dropped.length > 0) {
        toast.error('音声を送信できませんでした', {
          description: `${flushed.dropped[0]?.reason ?? '送信できないため'}・「送れなかった録音」から再送できます`,
        });
      } else if (flushed.sent > 0) {
        toast.success('らく助が文字起こし中です（数分）');
      } else {
        toast.warning('電波が戻ると自動で送信します');
      }
      void qc.invalidateQueries({ queryKey: ['visit-recordings'] });
      resetAfterSave();
      onSaved?.();
    },
    [staffId, voice, accessToken, refreshToken, qc, resetAfterSave, onSaved],
  );

  /** 端末に保存できないときの直接送信（レビュー C-1 / M-4）。 */
  const sendDirectly = useCallback(async () => {
    if (!result) return;
    const link = resultLink ?? voice.link;
    setUploadRatio(0);
    try {
      await upload.mutateAsync({
        audio: result.blob,
        visitId: link.visitId,
        patientId: link.patientId,
        recordedAt: startedAtIso ?? new Date().toISOString(),
        durationSec: result.durationSec,
        consent: resultConsent,
        deviceMime: result.mimeType,
        // キュー投入と同じキー（レビュー M-B）。両方届いても BE が 1 件に畳む。
        clientId,
        onProgress: setUploadRatio,
      });
      // 届いたことを確認してから端末のチャンクを消す（レビュー N-2）。
      await discardChunkSession(result.sessionId);
      toast.success('らく助が文字起こし中です（数分）');
      resetAfterSave();
      onSaved?.();
    } catch (err) {
      setUploadRatio(null);
      toast.error('直接送信できませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }, [
    result,
    upload,
    voice,
    resultLink,
    startedAtIso,
    resultConsent,
    clientId,
    resetAfterSave,
    onSaved,
  ]);

  const beginRecording = useCallback(async () => {
    const reason = recordingUnsupportedReason();
    if (reason) {
      setBlocked(reason);
      return;
    }
    // 録音機はセッションが持つ（レビュー H-C）。コールバックはセッション経由で
    // 「いま繋がっているパネル」へ配られるので、スキャナ往復しても迷子にならない。
    // チャンクに訪問を焼き込むのは残骸の取り違え防止（レビュー H-B）。
    let recorder: VoiceRecorder;
    try {
      recorder = startVoiceSessionRecorder(voice, {
        visitId: visitId ?? null,
        patientId: patientId ?? null,
        patientName,
      });
    } catch {
      // 未保存の録音が残っている（レビュー N-4）。上書きせずに出口を案内する。
      toast.warning(UNSAVED_RECORDING_MESSAGE, {
        description: '先に保存か破棄をしてください',
      });
      return;
    }
    recorderRef.current = recorder;
    unsubRef.current?.();
    unsubRef.current = recorder.subscribe((s) => {
      setElapsedSec(s.elapsedSec);
      setPaused(s.status === 'paused');
    });
    try {
      await recorder.start();
      const iso = new Date().toISOString();
      voice.startedAtIso = iso;
      setStartedAtIso(iso);
      setBlocked(null);
      setAutoStopNote(null);
      setSaveError(null);
      setMode('recording');
    } catch (err) {
      endSession();
      const name = err instanceof Error ? err.name : '';
      setBlocked(
        name === 'NotAllowedError' || name === 'SecurityError'
          ? 'マイクの使用が許可されていません'
          : 'この端末では録音を始められませんでした',
      );
      setMode('idle');
    }
  }, [endSession, voice, visitId, patientId, patientName]);

  /** 取り込みファイルの検査 → キュー投入（レビュー H-3 / M-3）。 */
  const importFile = useCallback(
    async (file: File) => {
      if (file.size > VISIT_AUDIO_MAX_BYTES) {
        toast.error('音声が大きすぎます', {
          description: `${formatBytes(file.size)}（上限 ${formatBytes(VISIT_AUDIO_MAX_BYTES)}・約 ${approxMinutes(VISIT_AUDIO_MAX_BYTES)} 分まで）・分けて取り込んでください`,
        });
        return;
      }
      // 端末アプリで録った音声は長さが分からない（サーバ側で確定する）。
      // 録音日時は**ファイルの更新時刻**が実際に近い（取り込みは後日のこともある）。
      const recordedAt = new Date(file.lastModified || Date.now()).toISOString();
      // 取り込みも同意が要る（チェック無しではボタンが押せない）。
      await save({
        blob: file,
        mimeType: file.type || 'audio/mp4',
        durationSec: 0,
        recordedAt,
        consent,
      });
    },
    [save, consent],
  );

  /** 説明ダイアログを通してから実行する（初回のみ・レビュー H-3）。 */
  function gate(action: GatedAction) {
    if (!consentDialogSeen()) {
      setGatedAction(action);
      setDialogOpen(true);
      return;
    }
    if (action === 'record') void beginRecording();
    else fileInputRef.current?.click();
  }

  function acceptConsentDialog() {
    try {
      window.localStorage.setItem(CONSENT_SEEN_KEY, '1');
    } catch {
      /* private mode — 次回も説明を出すだけ */
    }
    setDialogOpen(false);
    if (gatedAction === 'record') void beginRecording();
    else fileInputRef.current?.click();
  }

  async function handleStop() {
    const recorder = recorderRef.current;
    if (!recorder) return;
    try {
      const r = await recorder.stop();
      enterReview(r);
    } catch (err) {
      toast.error('録音を止められませんでした', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  function handleDiscard() {
    // 利用者が明示的に捨てた = セッションごと畳む（救出の対象から外す）。
    // 画面ロックの自動保存で積んであった行もここで取り消す（レビュー N-2）。
    void dropQueuedVoice(voice);
    endSession();
    resetPanelState();
  }

  function handleDownload() {
    if (!result) return;
    const ok = downloadBlob(result.blob, audioFileName(result.mimeType));
    if (ok) toast.success('端末に保存しました');
    else toast.error('端末に保存できませんでした');
  }

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    await importFile(file);
  }

  /** 残骸がいま開いている訪問のものか（レビュー H-B）。 */
  const orphanMatchesVisit = !!orphan?.visitId && !!visitId && orphan.visitId === visitId;

  /**
   * 残骸を結合して積む（レビュー H-2 / H-B）。
   *
   * **同じ訪問のときだけ**この訪問に紐付ける。別の訪問（前の患者宅で落ちた録音）や
   * 紐付けを持たない旧レコードは `visitId`/`patientId` を null で積み、あとから
   * 管理画面で紐付ける。目の前の訪問に足すと記録の取り違えそのものになる。
   */
  async function handleOrphanSave() {
    if (!orphan) return;
    setOrphanBusy(true);
    try {
      const recovered = await buildOrphanRecording(orphan.sessionId);
      if (!recovered) {
        setOrphan(null);
        return;
      }
      await save({
        blob: recovered.blob,
        mimeType: recovered.mimeType,
        durationSec: recovered.durationSec,
        recordedAt: new Date(orphan.at).toISOString(),
        // 録音は同意チェックを通らないと始められない = この音声は同意済み。
        consent: true,
        link: orphanMatchesVisit
          ? { visitId: visitId ?? null, patientId: patientId ?? null }
          : { visitId: null, patientId: null },
        // チャンクを消すのは**積めたときだけ**（レビュー N-2）。積めないまま消すと
        // 拾い直せる唯一の控えが無くなる。
        sessionId: orphan.sessionId,
      });
      setOrphan(null);
    } finally {
      setOrphanBusy(false);
    }
  }

  async function handleOrphanDiscard() {
    if (!orphan) return;
    setOrphanBusy(true);
    try {
      await discardChunkSession(orphan.sessionId);
      setOrphan(null);
    } finally {
      setOrphanBusy(false);
    }
  }

  /** 前回の録音の残骸カード（どのモードでも先頭に出す）。 */
  const orphanCard = orphan ? (
    <Card className="space-y-2 border-warning/40 bg-warning-bg/40 p-4" data-testid="voice-orphan">
      <p className="text-sm font-semibold text-text-primary">
        前回の録音が残っています（約 {Math.max(1, Math.round(orphan.approxDurationSec / 60))} 分）
      </p>
      {/* どの録音かを人が見分けられるように、元の患者名と日時を出す（H-B）。 */}
      <p className="text-xs text-text-secondary" data-testid="voice-orphan-origin">
        {orphan.patientName || '訪問不明'}・{formatWhen(orphan.at)}
      </p>
      <p className="text-xs text-text-muted">
        {orphanMatchesVisit
          ? 'アプリが閉じられたため保存されていません。この訪問に保存できます。'
          : 'アプリが閉じられたため保存されていません。訪問不明の録音として保存し、後で紐付けます。'}
      </p>
      <div className="flex gap-2">
        <Button
          type="button"
          className="flex-1"
          disabled={orphanBusy || mode === 'saving'}
          onClick={() => void handleOrphanSave()}
        >
          {orphanMatchesVisit ? 'この訪問に保存' : '保存する'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          className="flex-1 text-text-muted"
          disabled={orphanBusy || mode === 'saving'}
          onClick={() => void handleOrphanDiscard()}
        >
          破棄する
        </Button>
      </div>
    </Card>
  ) : null;

  // ---- 録音中 --------------------------------------------------------------
  if (mode === 'recording') {
    return (
      <Card className="overflow-hidden p-0" data-testid="voice-recording-panel">
        <div className="flex items-center gap-2 bg-warning-bg px-3 py-2 text-sm font-medium text-warning">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          録音中は画面を点けたままにしてください
        </div>
        <div className="space-y-3 p-4 text-center">
          <div>
            <p className="text-sm text-text-muted">録音中の訪問先</p>
            <p className="font-serif text-lg font-bold text-text-primary">{patientName}</p>
          </div>
          <div className="flex items-center justify-center gap-2">
            <span className="relative flex h-3 w-3">
              {!paused && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-error opacity-75" />
              )}
              <span className="relative inline-flex h-3 w-3 rounded-full bg-error" />
            </span>
            <span
              className="font-serif text-3xl font-bold tnum text-error"
              data-testid="voice-timer"
            >
              {formatElapsed(elapsedSec)}
            </span>
          </div>
          <div className="flex h-8 items-end justify-center gap-1" aria-hidden>
            {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((i) => (
              <span
                key={i}
                className={paused ? 'voice-wave-bar' : 'voice-wave-bar voice-wave-bar--on'}
                style={{ animationDelay: `${(i % 6) * 0.12}s` }}
              />
            ))}
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              className="flex-1"
              onClick={() =>
                paused ? recorderRef.current?.resume() : recorderRef.current?.pause()
              }
            >
              {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
              {paused ? '再開' : '一時停止'}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="flex-1 text-error"
              onClick={() => void handleStop()}
            >
              <Square className="h-4 w-4" />
              停止・保存
            </Button>
          </div>
          <p className="text-xs text-text-muted">
            上限 60 分・約 {approxMinutes(VISIT_AUDIO_MAX_BYTES)} 分で自動保存します
          </p>
        </div>
      </Card>
    );
  }

  // ---- 停止後の確認 --------------------------------------------------------
  if ((mode === 'review' || mode === 'saving') && result) {
    return (
      <div className="space-y-2">
        {orphanCard}
        <Card className="space-y-3 p-4" data-testid="voice-review-panel">
          {autoStopNote && (
            <div
              className="flex items-start gap-2 rounded-md bg-warning-bg px-3 py-2 text-sm text-warning"
              data-testid="voice-autostop-note"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{autoStopNote}</span>
            </div>
          )}
          <div>
            <p className="text-sm font-semibold text-text-primary">録音を保存しますか？</p>
            <p className="text-sm text-text-secondary">
              {patientName}・{formatElapsed(result.durationSec)}・{formatBytes(result.blob.size)}
            </p>
          </div>

          {saveError ? (
            // 端末に残せていない（レビュー C-1）。「後で自動送信」とは決して言わない。
            <div className="space-y-2" data-testid="voice-save-failed">
              <div className="flex items-start gap-2 rounded-md bg-error-bg px-3 py-2 text-sm text-error">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{saveError}この画面を閉じると録音は失われます。</span>
              </div>
              <CheckInButton
                loading={mode === 'saving'}
                onClick={() =>
                  void save({
                    blob: result.blob,
                    mimeType: result.mimeType,
                    durationSec: result.durationSec,
                    recordedAt: startedAtIso ?? new Date().toISOString(),
                    consent: resultConsent,
                    link: resultLink ?? undefined,
                    clientId,
                    sessionId: result.sessionId,
                  })
                }
              >
                <Upload className="h-5 w-5" />
                もう一度保存
              </CheckInButton>
              <Button type="button" variant="outline" className="w-full" onClick={handleDownload}>
                <Download className="h-4 w-4" />
                端末に保存（ダウンロード）
              </Button>
              <Button
                type="button"
                variant="outline"
                className="w-full"
                disabled={upload.isPending}
                onClick={() => void sendDirectly()}
              >
                <Send className="h-4 w-4" />
                直接送信
              </Button>
              {uploadRatio !== null && (
                <div
                  className="h-1.5 w-full overflow-hidden rounded-full bg-bg-muted"
                  role="progressbar"
                  aria-label="送信の進捗"
                  aria-valuenow={Math.round(uploadRatio * 100)}
                  data-testid="voice-upload-progress"
                >
                  <div
                    className="h-full bg-brand-primary transition-[width]"
                    style={{ width: `${Math.round(uploadRatio * 100)}%` }}
                  />
                </div>
              )}
            </div>
          ) : (
            <CheckInButton
              loading={mode === 'saving'}
              onClick={() =>
                void save({
                  blob: result.blob,
                  mimeType: result.mimeType,
                  durationSec: result.durationSec,
                  recordedAt: startedAtIso ?? new Date().toISOString(),
                  // 録音は同意チェックを通らないと始められない（開始時点の同意が正）。
                  consent,
                  link: resultLink ?? undefined,
                  clientId,
                  sessionId: result.sessionId,
                })
              }
            >
              <Upload className="h-5 w-5" />
              保存して文字起こしへ
            </CheckInButton>
          )}

          <Button
            type="button"
            variant="ghost"
            className="w-full text-text-muted"
            disabled={mode === 'saving'}
            onClick={handleDiscard}
          >
            破棄する
          </Button>
        </Card>
      </div>
    );
  }

  // ---- 待機（同意 → 開始） -------------------------------------------------
  return (
    <div className="space-y-2">
      {orphanCard}
      <Card className="space-y-3 p-4" data-testid="voice-recorder-panel">
        <div className="flex items-center gap-2 text-sm font-semibold text-text-secondary">
          <Mic className="h-4 w-4" />
          {heading}
        </div>

        {blocked ? (
          <div className="space-y-2">
            <RakusukeNote
              pose="puzzled"
              size="sm"
              title={blocked}
              comment="端末のボイスメモで録って、下から取り込んでくださいね"
            />
          </div>
        ) : null}

        {/* 同意は録音にも取り込みにも要る（レビュー H-3）。録音できない端末でも出す。 */}
        <label className="flex items-center gap-2 text-sm text-text-primary">
          <Checkbox
            checked={consent}
            onCheckedChange={(v) => setConsent(v === true)}
            aria-label="患者様に録音の了承を得ています"
          />
          患者様に録音の了承を得ています
        </label>

        {!blocked && (
          <CheckInButton disabled={!consent || mode === 'saving'} onClick={() => gate('record')}>
            <Mic className="h-5 w-5" />
            録音を始める
          </CheckInButton>
        )}

        <div className="text-center">
          <button
            type="button"
            className="text-sm text-brand-primary underline disabled:opacity-50 disabled:no-underline"
            onClick={() => gate('import')}
            disabled={!consent || mode === 'saving'}
          >
            ボイスメモから取り込む
          </button>
          {!consent && <p className="text-xs text-text-muted">了承のチェックが要ります</p>}
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*"
            className="hidden"
            data-testid="voice-file-input"
            onChange={(e) => void handleFileSelected(e)}
          />
        </div>

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>音声記録について</DialogTitle>
              <DialogDescription>
                訪問の会話を録音し、らく助が文字起こしと要約を作ります。録音は事業所内でのみ
                閲覧でき、一定期間が過ぎた音声は自動で削除されます（文字起こしと要約は残ります）。
                録音の前に、患者様・ご家族に必ず了承を得てください。
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setDialogOpen(false)}>
                やめる
              </Button>
              <Button type="button" onClick={acceptConsentDialog}>
                {gatedAction === 'record' ? '了解して録音を始める' : '了解して取り込む'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </Card>
    </div>
  );
}
