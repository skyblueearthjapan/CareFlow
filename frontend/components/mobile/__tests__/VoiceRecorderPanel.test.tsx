/**
 * 録音パネル (`VoiceRecorderPanel`) のテスト。
 *
 * 守りたい導線 (設計 §2-2 + レビュー是正):
 *   同意チェック → (初回のみ説明) → 開始 → 停止 → 「保存して文字起こしへ」で
 *   **未送信キューへ積んでから**送る。同意していないうちは録音も取り込みもできない。
 *   録音できない端末では責めずにボイスメモ取り込みへ案内する。
 *
 * 是正で足した約束:
 *   C-1 端末に保存できなかったら「後で自動送信」と**言わず**、手元の音声を
 *       握ったまま「もう一度保存 / 端末に保存 / 直接送信」を出す
 *   H-1 録音中に画面を離れても、そこまでをキューへ積んでから手放す
 *   H-3 取り込みにも同意が要る・`consent` は実際の値を送る
 *   M-3 上限 (20 MiB) を超える取り込みは投入前に弾く / 取り込みの録音日時は
 *       ファイルの更新時刻
 *   M-5 保存・破棄のあとは同意チェックを外す (次の患者に持ち越さない)
 *
 * 再レビューで足した約束:
 *   H-B 残骸は**同じ訪問のときだけ**この訪問へ紐付ける。別の訪問 / 紐付け無しは
 *       訪問不明 (visitId=null) として積む — 記録の取り違えを作らない
 *   H-C 停止中 (stopping) / 停止後 review のまま画面を離れても積む
 *   M-B review で決めた `client_id` をキュー投入と直接送信で共有する
 *   L-A 保存ボタン経由の flush は `interactive` (タブ跨ぎロックを待たない)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1' }, accessToken: 'token', refreshToken: 'refresh' },
    status: 'authenticated',
  }),
}));

const qcStub = { invalidateQueries: vi.fn() };
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => qcStub }));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/lib/voice/queue', async () => {
  const actual = await vi.importActual<typeof import('@/lib/voice/queue')>('@/lib/voice/queue');
  return {
    ...actual,
    enqueueVoice: vi.fn(async (entry: unknown) => entry),
    flushVoiceQueue: vi.fn(async () => ({
      sent: 1,
      remaining: 0,
      dropped: [],
      failed: 0,
      sentEntries: [],
    })),
  };
});

const uploadStub = { mutateAsync: vi.fn(async () => ({})), isPending: false };
vi.mock('@/lib/queries/visit-recordings', () => ({
  useUploadRecording: () => uploadStub,
}));

// 残骸 (H-2 / H-B) は IndexedDB 由来なので、読み書きだけ差し替えて
// `VoiceRecorder` 本体は実物を使う。
vi.mock('@/lib/voice/recorder', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/voice/recorder')>('@/lib/voice/recorder');
  return {
    ...actual,
    listOrphanChunkSessions: vi.fn(async () => []),
    buildOrphanRecording: vi.fn(async () => null),
    discardChunkSession: vi.fn(async () => undefined),
  };
});

import { enqueueVoice, flushVoiceQueue } from '@/lib/voice/queue';
import { toast } from '@/components/ui/sonner';
import { AUDIO_SIZE_WARN_BYTES, VISIT_AUDIO_MAX_BYTES } from '@/lib/voice/constants';
import {
  buildOrphanRecording,
  discardChunkSession,
  listOrphanChunkSessions,
} from '@/lib/voice/recorder';
import { rescueVoiceSessions, resetVoiceSessionsForTest } from '@/lib/voice/session';
import { VoiceRecorderPanel } from '@/components/mobile/VoiceRecorderPanel';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

/** RFC4122 v4（`client_id` の形・M-C）。 */
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** キューの行 id（`enqueueVoice` が付ける値のモック）。 */
const ENTRY_ID = 'entry-1';

const instances: FakeMediaRecorder[] = [];

class FakeMediaRecorder {
  static isTypeSupported(mime: string) {
    return mime === 'audio/webm;codecs=opus';
  }
  state = 'inactive';
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
    // 実機と同じく開始直後にもチャンクが来る。
    queueMicrotask(() => this.ondataavailable?.({ data: new Blob(['audio']) }));
  }
  pause() {
    this.state = 'paused';
  }
  resume() {
    this.state = 'recording';
  }
  /** true にすると `stop()` が `onstop` を呼ばない = `stopping` のまま止まる。 */
  deferStop = false;
  stop() {
    this.state = 'inactive';
    if (this.deferStop) return;
    this.onstop?.();
  }
  /** 遅らせていた停止をここで完了させる (H-C の stopping テスト用)。 */
  finishStop() {
    this.deferStop = false;
    this.onstop?.();
  }
  /** 大きさを詐称したチャンクを流す (20 MiB を実体で作らないため)。 */
  emitBytes(bytes: number) {
    const blob = new Blob(['x']);
    Object.defineProperty(blob, 'size', { value: bytes, configurable: true });
    this.ondataavailable?.({ data: blob });
  }
}

function installRecorderStubs(supported = true) {
  (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = supported
    ? FakeMediaRecorder
    : undefined;
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: supported ? { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) } : undefined,
  });
}

/** 大きさを詐称したファイル (50 MiB 超を実体で作らないため)。 */
function fileOfSize(bytes: number, lastModified = Date.now()): File {
  const file = new File(['x'], 'memo.m4a', { type: 'audio/mp4', lastModified });
  Object.defineProperty(file, 'size', { value: bytes, configurable: true });
  return file;
}

beforeEach(() => {
  vi.clearAllMocks();
  // 録音セッションはモジュール単位のシングルトン。テスト間で持ち越さない。
  resetVoiceSessionsForTest();
  instances.length = 0;
  window.localStorage.clear();
  installRecorderStubs(true);
  // 実物は必ず行 id を付ける（`client_id` か端末キー）。保存後の `onSaved` は
  // この id で「自分の行が送れたか」を見るので、モックでも付ける。
  asMock(enqueueVoice).mockImplementation(async (entry: Record<string, unknown>) => ({
    ...entry,
    id: ENTRY_ID,
  }));
  asMock(flushVoiceQueue).mockImplementation(async () => ({
    sent: 1,
    remaining: 0,
    dropped: [],
    failed: 0,
    sentEntries: [{ entryId: ENTRY_ID, recordingId: 'rec-1' }],
  }));
  uploadStub.mutateAsync = vi.fn(async () => ({ id: 'rec-direct' }));
  uploadStub.isPending = false;
  asMock(listOrphanChunkSessions).mockImplementation(async () => []);
  asMock(buildOrphanRecording).mockImplementation(async () => null);
});

function renderPanel() {
  return render(<VoiceRecorderPanel visitId="visit-1" patientId="pat-1" patientName="山田 花子" />);
}

/** 同意 → 開始 → 停止 で review まで進める。 */
async function recordAndStop() {
  window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
  const view = renderPanel();
  fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
  fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));
  fireEvent.click(await screen.findByRole('button', { name: /停止・保存/ }));
  return view;
}

describe('VoiceRecorderPanel', () => {
  it('同意にチェックするまで録音を始められない', () => {
    renderPanel();
    const start = screen.getByRole('button', { name: /録音を始める/ });
    expect(start).toBeDisabled();

    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    expect(screen.getByRole('button', { name: /録音を始める/ })).toBeEnabled();
  });

  it('初回は説明ダイアログを挟み、了解すると録音が始まる', async () => {
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));

    expect(await screen.findByText('音声記録について')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '了解して録音を始める' }));

    expect(await screen.findByTestId('voice-recording-panel')).toBeInTheDocument();
    expect(screen.getByText('録音中は画面を点けたままにしてください')).toBeInTheDocument();
    expect(screen.getByTestId('voice-timer')).toHaveTextContent('00:00');
  });

  it('停止 → 「保存して文字起こしへ」でキューに積んでから送る', async () => {
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));

    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.staffId).toBe('staff-1');
    expect(entry.visitId).toBe('visit-1');
    expect(entry.patientId).toBe('pat-1');
    expect(entry.consent).toBe(true);
    expect(entry.mimeType).toBe('audio/webm;codecs=opus');
    expect(entry.blob).toBeInstanceOf(Blob);
    // L-A: 利用者が待っている経路なのでタブ跨ぎロックは待たない。
    expect(flushVoiceQueue).toHaveBeenCalledWith(
      'staff-1',
      { accessToken: 'token', refreshToken: 'refresh' },
      { interactive: true },
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('らく助が文字起こし中です（数分）'),
    );
  });

  it('保存できたら onSaved に録音 id を渡す (2026-09-18 是正)', async () => {
    const onSaved = vi.fn();
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    render(
      <VoiceRecorderPanel
        visitId="visit-1"
        patientId="pat-1"
        patientName="山田 花子"
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));
    fireEvent.click(await screen.findByRole('button', { name: /停止・保存/ }));
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));

    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith(
        expect.objectContaining({ recordingId: 'rec-1', queued: false }),
      ),
    );
  });

  it('送れずキューに残ったら onSaved は queued を立てる', async () => {
    asMock(flushVoiceQueue).mockImplementation(async () => ({
      sent: 0,
      remaining: 1,
      dropped: [],
      failed: 0,
      sentEntries: [],
    }));
    const onSaved = vi.fn();
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    render(
      <VoiceRecorderPanel
        visitId="visit-1"
        patientId="pat-1"
        patientName="山田 花子"
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));
    fireEvent.click(await screen.findByRole('button', { name: /停止・保存/ }));
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));

    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith(
        expect.objectContaining({ recordingId: null, queued: true }),
      ),
    );
  });

  it('保存のあとは同意チェックが外れる (M-5)', async () => {
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));

    await waitFor(() => expect(screen.getByTestId('voice-recorder-panel')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /録音を始める/ })).toBeDisabled();
  });

  it('送れなかったときは「電波が戻ると自動で送信します」と伝える', async () => {
    asMock(flushVoiceQueue).mockImplementation(async () => ({
      sent: 0,
      remaining: 1,
      dropped: [],
      failed: 0,
      sentEntries: [],
    }));
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith('電波が戻ると自動で送信します'));
  });

  it('録音できない端末はボイスメモ取り込みへ案内する', async () => {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    installRecorderStubs(false);
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));

    expect(await screen.findByText('この端末のブラウザでは録音できません')).toBeInTheDocument();
    expect(screen.getByText('ボイスメモから取り込む')).toBeInTheDocument();
  });
});

describe('端末に保存できなかったとき (C-1)', () => {
  it('「後で自動送信」とは言わず、3 つの出口を出す', async () => {
    asMock(enqueueVoice).mockImplementation(async () => null);
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));

    expect(await screen.findByTestId('voice-save-failed')).toBeInTheDocument();
    // review に留まる = 手元の音声を握ったまま。
    expect(screen.getByTestId('voice-review-panel')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /もう一度保存/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /端末に保存（ダウンロード）/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /直接送信/ })).toBeInTheDocument();
    // 嘘をつかない。
    expect(toast.warning).not.toHaveBeenCalledWith('電波が戻ると自動で送信します');
    expect(flushVoiceQueue).not.toHaveBeenCalled();
  });

  it('「直接送信」は useUploadRecording を通す (M-4)', async () => {
    asMock(enqueueVoice).mockImplementation(async () => null);
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));
    fireEvent.click(await screen.findByRole('button', { name: /直接送信/ }));

    await waitFor(() => expect(uploadStub.mutateAsync).toHaveBeenCalledTimes(1));
    const vars = uploadStub.mutateAsync.mock.calls[0]![0] as Record<string, unknown>;
    expect(vars.visitId).toBe('visit-1');
    expect(vars.patientId).toBe('pat-1');
    expect(vars.consent).toBe(true);
    // 進捗を受け取る口がある (進捗バー用)。
    expect(typeof vars.onProgress).toBe('function');
  });
});

describe('ボイスメモの取り込み (H-3 / M-3)', () => {
  it('同意していないうちは取り込めない', () => {
    renderPanel();

    expect(screen.getByRole('button', { name: 'ボイスメモから取り込む' })).toBeDisabled();
    expect(screen.getByText('了承のチェックが要ります')).toBeInTheDocument();
  });

  it('同意のうえ取り込むと、ファイルの更新時刻を録音日時にする', async () => {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    const lastModified = Date.parse('2026-09-16T05:30:00.000Z');
    const file = new File(['memo'], 'memo.m4a', { type: 'audio/mp4', lastModified });
    fireEvent.change(screen.getByTestId('voice-file-input'), { target: { files: [file] } });

    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.mimeType).toBe('audio/mp4');
    expect(entry.durationSec).toBe(0);
    expect(entry.consent).toBe(true);
    expect(entry.recordedAt).toBe(new Date(lastModified).toISOString());
  });

  it('上限を超える音声は投入前に弾く (M-3)', async () => {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.change(screen.getByTestId('voice-file-input'), {
      target: { files: [fileOfSize(VISIT_AUDIO_MAX_BYTES + 1)] },
    });

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('音声が大きすぎます', expect.anything()),
    );
    const description = (asMock(toast.error).mock.calls[0]![1] as { description: string })
      .description;
    // 上限 20 MiB = 約 20 分。現場の言葉 (分) で伝える。
    expect(description).toContain('20.0 MB');
    expect(description).toContain('約 20 分まで');
    expect(enqueueVoice).not.toHaveBeenCalled();
  });
});

/**
 * スキャナはページ内のオーバーレイで、出した瞬間にこのパネルは unmount される。
 * ここで止めると「録音中に QR を読んだら録音が切れる」ことになるので、パネルの
 * unmount は **UI を外すだけ** — 録音はセッションが持ち続ける（レビュー H-C）。
 */
describe('スキャナ往復 — パネルの unmount では止めない (H-C)', () => {
  /** 同意 → 録音開始まで。 */
  async function startRecording() {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    const view = renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));
    await screen.findByTestId('voice-recording-panel');
    return view;
  }

  it('録音中に unmount しても止めない・積まない（QR スキャンで録音が切れない）', async () => {
    const { unmount } = await startRecording();

    unmount();

    // 止めない = 積まない（救出はページ離脱の仕事）。
    expect(enqueueVoice).not.toHaveBeenCalled();
    // 録音機も作り直さない = 同じ録音が続いている。
    expect(instances).toHaveLength(1);
    expect(instances[0]!.state).toBe('recording');
  });

  it('戻ってきたら録音中のまま復元され、経過時間が進んでいる', async () => {
    const { unmount } = await startRecording();
    unmount();

    // スキャナを閉じて戻ってきた = パネルを作り直す。
    renderPanel();

    expect(await screen.findByTestId('voice-recording-panel')).toBeInTheDocument();
    // 録音機は 1 つのまま（新しい MediaRecorder は作られていない）。
    expect(instances).toHaveLength(1);
    // タイマーが動き続けている（1 秒ごとの tick はセッション側で生きている）。
    await waitFor(() => expect(screen.getByTestId('voice-timer')).not.toHaveTextContent('00:00'), {
      timeout: 3_000,
    });
  });

  it('review（保存前）で往復しても録音が残る', async () => {
    const { unmount } = await recordAndStop();
    await screen.findByTestId('voice-review-panel');

    unmount();
    expect(enqueueVoice).not.toHaveBeenCalled();
    renderPanel();

    // 戻ると「保存しますか？」の続きから。
    expect(await screen.findByTestId('voice-review-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /保存して文字起こしへ/ }));
    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect((entry.blob as Blob).size).toBeGreaterThan(0);
  });

  it('停止処理の途中 (stopping) に離れても、戻れば review になる', async () => {
    const { unmount } = await startRecording();
    // MediaRecorder の onstop が返ってこない = `stopping` のまま。
    instances[0]!.deferStop = true;
    fireEvent.click(screen.getByRole('button', { name: /停止・保存/ }));
    unmount();

    // 端末側の停止がようやく完了 → 結果はセッションが受け取る。
    instances[0]!.finishStop();
    renderPanel();

    expect(await screen.findByTestId('voice-review-panel')).toBeInTheDocument();
  });
});

describe('ページ離脱の救出 (H-1 / H-C)', () => {
  it('録音中ならそこまでを止めてキューへ積む', async () => {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));
    await screen.findByTestId('voice-recording-panel');

    const saved = await rescueVoiceSessions('staff-1');

    expect(saved).toBe(1);
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.staffId).toBe('staff-1');
    expect(entry.visitId).toBe('visit-1');
    expect(entry.consent).toBe(true);
    expect((entry.blob as Blob).size).toBeGreaterThan(0);
  });

  it('停止済み (review) で保存を押していなければ、それも積む', async () => {
    await recordAndStop();
    await screen.findByTestId('voice-review-panel');

    const saved = await rescueVoiceSessions('staff-1');

    expect(saved).toBe(1);
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.visitId).toBe('visit-1');
    expect((entry.blob as Blob).size).toBeGreaterThan(0);
  });

  it('「破棄する」を押した後は積まない (捨てたものは捨てたまま)', async () => {
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: '破棄する' }));

    const saved = await rescueVoiceSessions('staff-1');

    expect(saved).toBe(0);
    expect(enqueueVoice).not.toHaveBeenCalled();
  });
});

describe('前回の録音の残骸 (H-B)', () => {
  /** 残骸 1 件を読ませる。 */
  function seedOrphan(link: {
    visitId: string | null;
    patientId: string | null;
    patientName: string | null;
  }) {
    asMock(listOrphanChunkSessions).mockImplementation(async () => [
      {
        sessionId: 'sess-1',
        chunkCount: 6,
        mimeType: 'audio/webm;codecs=opus',
        at: Date.parse('2026-09-17T12:40:00.000Z'),
        approxDurationSec: 60,
        ...link,
      },
    ]);
    asMock(buildOrphanRecording).mockImplementation(async () => ({
      sessionId: 'sess-1',
      blob: new Blob(['orphan']),
      mimeType: 'audio/webm;codecs=opus',
      durationSec: 60,
    }));
  }

  it('同じ訪問の残骸は「この訪問に保存」で紐付けて積む', async () => {
    seedOrphan({ visitId: 'visit-1', patientId: 'pat-1', patientName: '山田 花子' });
    renderPanel();

    const card = await screen.findByTestId('voice-orphan');
    expect(card).toHaveTextContent('山田 花子');
    fireEvent.click(screen.getByRole('button', { name: 'この訪問に保存' }));

    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.visitId).toBe('visit-1');
    expect(entry.patientId).toBe('pat-1');
  });

  it('別の訪問の残骸は訪問不明 (visitId=null) で積み、いまの訪問に紐付けない', async () => {
    seedOrphan({ visitId: 'visit-OTHER', patientId: 'pat-9', patientName: '佐藤 太郎' });
    renderPanel();

    const card = await screen.findByTestId('voice-orphan');
    // 元の患者名を出す = どの録音かを人が判断できる。
    expect(card).toHaveTextContent('佐藤 太郎');
    expect(card).toHaveTextContent('訪問不明の録音として保存し、後で紐付けます');
    expect(screen.queryByRole('button', { name: 'この訪問に保存' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '保存する' }));

    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.visitId).toBeNull();
    expect(entry.patientId).toBeNull();
  });

  it('紐付けを持たない旧レコードは「訪問不明」と出す', async () => {
    seedOrphan({ visitId: null, patientId: null, patientName: null });
    renderPanel();

    expect(await screen.findByTestId('voice-orphan-origin')).toHaveTextContent('訪問不明');
    fireEvent.click(screen.getByRole('button', { name: '保存する' }));

    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    const entry = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(entry.visitId).toBeNull();
  });
});

describe('client_id の共有 (M-B)', () => {
  it('キュー投入と直接送信で同じ値を送る', async () => {
    asMock(enqueueVoice).mockImplementation(async () => null);
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));
    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    const queued = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    expect(queued.clientId).toMatch(UUID_V4);

    fireEvent.click(await screen.findByRole('button', { name: /直接送信/ }));

    await waitFor(() => expect(uploadStub.mutateAsync).toHaveBeenCalledTimes(1));
    const vars = uploadStub.mutateAsync.mock.calls[0]![0] as Record<string, unknown>;
    expect(vars.clientId).toBe(queued.clientId);
  });

  it('「もう一度保存」でもキーを作り直さない (二重登録にしない)', async () => {
    asMock(enqueueVoice).mockImplementation(async () => null);
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));
    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole('button', { name: /もう一度保存/ }));

    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(2));
    const first = asMock(enqueueVoice).mock.calls[0]![0] as Record<string, unknown>;
    const again = asMock(enqueueVoice).mock.calls[1]![0] as Record<string, unknown>;
    expect(again.clientId).toBe(first.clientId);
  });
});

/**
 * 停止しただけでは端末のチャンクを消さない（レビュー N-2）。結合した Blob はまだ
 * メモリの上にしか無いので、積む前に消すと保存に失敗した瞬間に音声が丸ごと消える。
 */
describe('端末のチャンクを消す順序 (N-2)', () => {
  it('キューへ積めたことを確認してから消す', async () => {
    await recordAndStop();
    // 停止しただけでは消さない。
    await screen.findByTestId('voice-review-panel');
    expect(discardChunkSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /保存して文字起こしへ/ }));

    await waitFor(() => expect(enqueueVoice).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(discardChunkSession).toHaveBeenCalledTimes(1));
    expect(asMock(discardChunkSession).mock.calls[0]![0]).toEqual(expect.any(String));
  });

  it('積めなかったら消さない（端末に音声を残す）', async () => {
    asMock(enqueueVoice).mockImplementation(async () => null);
    await recordAndStop();
    fireEvent.click(await screen.findByRole('button', { name: /保存して文字起こしへ/ }));

    await screen.findByTestId('voice-save-failed');
    expect(discardChunkSession).not.toHaveBeenCalled();
  });
});

describe('録音サイズの上限 (BE 受領上限 20 MiB)', () => {
  it('上限に達したら自動保存し、理由と目安を出す', async () => {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));
    await screen.findByTestId('voice-recording-panel');

    instances[0]!.emitBytes(VISIT_AUDIO_MAX_BYTES);

    // 停止・保存を押さなくても review へ移る (録り続けさせない)。
    expect(await screen.findByTestId('voice-review-panel')).toBeInTheDocument();
    const note = await screen.findByTestId('voice-autostop-note');
    expect(note).toHaveTextContent('録音サイズの上限に達したため保存しました');
    expect(note).toHaveTextContent('1 分あたり約 1 MB');
  });

  it('90% で警告する (止まる前に知らせる)', async () => {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));
    await screen.findByTestId('voice-recording-panel');

    instances[0]!.emitBytes(AUDIO_SIZE_WARN_BYTES);

    await waitFor(() =>
      expect(toast.warning).toHaveBeenCalledWith('まもなく録音サイズの上限です', expect.anything()),
    );
    // まだ止まっていない。
    expect(screen.getByTestId('voice-recording-panel')).toBeInTheDocument();
  });

  it('録音中の案内にも自動保存の目安を出す', async () => {
    window.localStorage.setItem('rakusuke:voice-consent-seen', '1');
    renderPanel();
    fireEvent.click(screen.getByLabelText('患者様に録音の了承を得ています'));
    fireEvent.click(screen.getByRole('button', { name: /録音を始める/ }));

    expect(await screen.findByText(/約 20 分で自動保存します/)).toBeInTheDocument();
  });
});
