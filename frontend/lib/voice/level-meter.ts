/**
 * 録音中の音量メーター（v2 設計 §3 P5）。
 *
 * 録音中に「声が小さい」「音が入っていない」と気づけないと、帰ってから文字起こしが
 * 空になって初めて分かる。`AnalyserNode` で RMS を測り、**直近 1.6 秒の最大値**で
 * 3 段階に分ける（話の切れ目の無音で「音が入っていません」と点滅させないため）。
 *
 * 録音そのもの（MediaRecorder）には触らない。`AudioContext` が作れない端末では
 * {@link createLevelMeter} が null を返し、メーターを出さずに録音だけ続ける。
 *
 * SSR 安全: モジュールの読み込み時にブラウザ API へは触れない。
 */

/** 約 −32 dBFS。これ以上なら「聞こえています」。 */
export const LEVEL_OK_RMS = 0.025;
/** 約 −48 dBFS。これ以上なら「声が小さい」、未満は「音が入っていません」。 */
export const LEVEL_QUIET_RMS = 0.004;
/** 段階を決める窓（直近の最大値を見る長さ）。 */
export const LEVEL_WINDOW_MS = 1600;
/** 測る間隔。 */
export const LEVEL_SAMPLE_MS = 100;

export type MicLevel = 'ok' | 'quiet' | 'silent';

/** 画面に出す文言（「直す」「修正」は使わない）。 */
export const MIC_LEVEL_MESSAGES: Record<MicLevel, string> = {
  ok: '聞こえています',
  quiet: '声が小さいようです。スマホを話す人に近づけてください',
  silent: '音が入っていません',
};

/** RMS（0〜1）を 3 段階に分ける。 */
export function classifyLevel(rms: number): MicLevel {
  if (rms >= LEVEL_OK_RMS) return 'ok';
  if (rms >= LEVEL_QUIET_RMS) return 'quiet';
  return 'silent';
}

/** 波形（−1〜1）の RMS。 */
export function computeRms(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = samples[i] ?? 0;
    sum += v * v;
  }
  return Math.sqrt(sum / samples.length);
}

/**
 * 直近 `windowMs` の最大値を持つ窓。
 *
 * 測った値を時刻つきで積み、古いものを捨てる。最大値で見るのは、話の切れ目の
 * 一瞬の無音で段階が落ちないようにするため。
 */
export class PeakWindow {
  private readonly windowMs: number;
  private samples: { at: number; rms: number }[] = [];

  constructor(windowMs = LEVEL_WINDOW_MS) {
    this.windowMs = windowMs;
  }

  push(rms: number, at: number): number {
    this.samples.push({ at, rms });
    const limit = at - this.windowMs;
    while (this.samples.length > 0 && (this.samples[0]?.at ?? 0) < limit) this.samples.shift();
    return this.peak();
  }

  peak(): number {
    let max = 0;
    for (const s of this.samples) if (s.rms > max) max = s.rms;
    return max;
  }

  clear(): void {
    this.samples = [];
  }
}

export interface LevelMeter {
  /** 一時停止中は測らない（窓も空にする）。 */
  pause(): void;
  resume(): void;
  /** 測るのをやめて `AudioContext` を閉じる（何度呼んでもよい）。 */
  close(): void;
}

type AudioContextCtor = new () => AudioContext;

function audioContextCtor(): AudioContextCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    AudioContext?: AudioContextCtor;
    webkitAudioContext?: AudioContextCtor;
  };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

/**
 * ストリームの音量を測り始める。段階が変わったときだけ `onLevel` を呼ぶ。
 *
 * 作れない端末（`AudioContext` が無い・作成で throw）は null。録音は止めない。
 */
export function createLevelMeter(
  stream: MediaStream,
  onLevel: (level: MicLevel) => void,
): LevelMeter | null {
  const Ctor = audioContextCtor();
  if (!Ctor) return null;
  let ctx: AudioContext;
  let source: MediaStreamAudioSourceNode;
  let analyser: AnalyserNode;
  try {
    ctx = new Ctor();
    source = ctx.createMediaStreamSource(stream);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
  } catch {
    return null;
  }
  // iOS は作った直後に suspended のことがある。起こせなくても録音は続ける。
  if (ctx.state === 'suspended') void ctx.resume?.().catch(() => undefined);

  const buf = new Float32Array(analyser.fftSize);
  const peaks = new PeakWindow();
  let last: MicLevel | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  let closed = false;

  const sample = () => {
    try {
      analyser.getFloatTimeDomainData(buf);
    } catch {
      return;
    }
    const level = classifyLevel(peaks.push(computeRms(buf), Date.now()));
    if (level !== last) {
      last = level;
      onLevel(level);
    }
  };
  const run = () => {
    if (closed || timer) return;
    timer = setInterval(sample, LEVEL_SAMPLE_MS);
  };
  const halt = () => {
    if (timer) clearInterval(timer);
    timer = null;
  };

  run();
  return {
    pause() {
      halt();
      peaks.clear();
      last = null;
    },
    resume() {
      run();
    },
    close() {
      if (closed) return;
      closed = true;
      halt();
      try {
        source.disconnect();
      } catch {
        /* 既に切れている */
      }
      void ctx.close?.().catch(() => undefined);
    },
  };
}
