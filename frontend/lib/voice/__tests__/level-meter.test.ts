/**
 * 音量メーター (`lib/voice/level-meter.ts`) のテスト（v2 設計 §3 P5）。
 *
 * 守りたい約束:
 *   1. RMS 0.025 以上「聞こえています」・0.004 以上「声が小さい」・未満「音が入っていません」
 *   2. 段階は直近 1.6 秒の**最大値**で決める（話の切れ目の無音で落ちない）
 *   3. 一時停止中は測らない・閉じたら AudioContext を必ず閉じる
 *   4. AudioContext が作れない端末では null（録音は続ける側の責任）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LEVEL_OK_RMS,
  LEVEL_QUIET_RMS,
  LEVEL_SAMPLE_MS,
  LEVEL_WINDOW_MS,
  MIC_LEVEL_MESSAGES,
  PeakWindow,
  classifyLevel,
  computeRms,
  createLevelMeter,
} from '@/lib/voice/level-meter';
import { installFakeAudioContext } from './fakeAudio';

describe('classifyLevel（3 段階のしきい値）', () => {
  it('0.025 以上は「聞こえています」', () => {
    expect(LEVEL_OK_RMS).toBe(0.025);
    expect(classifyLevel(0.025)).toBe('ok');
    expect(classifyLevel(0.3)).toBe('ok');
  });

  it('0.004 以上 0.025 未満は「声が小さい」', () => {
    expect(LEVEL_QUIET_RMS).toBe(0.004);
    expect(classifyLevel(0.004)).toBe('quiet');
    expect(classifyLevel(0.0249)).toBe('quiet');
  });

  it('0.004 未満は「音が入っていません」', () => {
    expect(classifyLevel(0.0039)).toBe('silent');
    expect(classifyLevel(0)).toBe('silent');
  });

  it('文言は設計どおり', () => {
    expect(MIC_LEVEL_MESSAGES).toEqual({
      ok: '聞こえています',
      quiet: '声が小さいようです。スマホを話す人に近づけてください',
      silent: '音が入っていません',
    });
  });
});

describe('computeRms', () => {
  it('一定の振幅ならその値になる', () => {
    expect(computeRms(new Float32Array(8).fill(0.5))).toBeCloseTo(0.5);
    expect(computeRms(new Float32Array(0))).toBe(0);
  });
});

describe('PeakWindow（直近 1.6 秒の最大値）', () => {
  it('窓の中の最大値を返し、1.6 秒より古い値は捨てる', () => {
    expect(LEVEL_WINDOW_MS).toBe(1600);
    const w = new PeakWindow();
    expect(w.push(0.1, 0)).toBe(0.1);
    // 1.6 秒以内は大きい声が残る（切れ目の無音で落ちない）。
    expect(w.push(0.001, 1000)).toBe(0.1);
    expect(w.push(0.001, 1600)).toBe(0.1);
    // 1.6 秒を過ぎたら消える。
    expect(w.push(0.001, 1601)).toBe(0.001);
  });
});

describe('createLevelMeter', () => {
  let audio: ReturnType<typeof installFakeAudioContext>;

  beforeEach(() => {
    vi.useFakeTimers();
    audio = installFakeAudioContext();
  });

  afterEach(() => {
    audio.uninstall();
    vi.useRealTimers();
  });

  it('AudioContext が無い端末では null（メーターを出さない）', () => {
    audio.uninstall();
    expect(createLevelMeter({} as MediaStream, vi.fn())).toBeNull();
  });

  it('AudioContext の作成で throw しても null', () => {
    audio.failCreate = true;
    expect(createLevelMeter({} as MediaStream, vi.fn())).toBeNull();
  });

  it('段階が変わったときだけ知らせる', () => {
    const onLevel = vi.fn();
    createLevelMeter({} as MediaStream, onLevel);
    audio.amplitude = 0.1;
    vi.advanceTimersByTime(LEVEL_SAMPLE_MS * 3);
    expect(onLevel).toHaveBeenCalledTimes(1);
    expect(onLevel).toHaveBeenLastCalledWith('ok');

    // 声が止んでも 1.6 秒は「聞こえています」のまま。
    audio.amplitude = 0;
    vi.advanceTimersByTime(1000);
    expect(onLevel).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1000);
    expect(onLevel).toHaveBeenLastCalledWith('silent');

    audio.amplitude = 0.01;
    vi.advanceTimersByTime(LEVEL_SAMPLE_MS);
    expect(onLevel).toHaveBeenLastCalledWith('quiet');
  });

  it('一時停止中は測らず、再開したら測り直す', () => {
    const onLevel = vi.fn();
    const meter = createLevelMeter({} as MediaStream, onLevel)!;
    audio.amplitude = 0.1;
    vi.advanceTimersByTime(LEVEL_SAMPLE_MS);
    meter.pause();
    const reads = audio.reads;
    vi.advanceTimersByTime(5000);
    expect(audio.reads).toBe(reads);

    meter.resume();
    vi.advanceTimersByTime(LEVEL_SAMPLE_MS);
    // 窓を空にしているので、再開直後にもう一度今の段階を知らせる。
    expect(onLevel).toHaveBeenCalledTimes(2);
    expect(onLevel).toHaveBeenLastCalledWith('ok');
  });

  it('close で AudioContext を閉じ、以後は測らない（2 回呼んでも 1 回だけ閉じる）', () => {
    const meter = createLevelMeter({} as MediaStream, vi.fn())!;
    meter.close();
    meter.close();
    expect(audio.closed).toBe(1);
    const reads = audio.reads;
    meter.resume();
    vi.advanceTimersByTime(5000);
    expect(audio.reads).toBe(reads);
  });

  it('AudioContext が動いていない間（iOS の suspended）は段階を出さず、起こそうとする', () => {
    audio.initialState = 'suspended';
    const onLevel = vi.fn();
    createLevelMeter({} as MediaStream, onLevel);
    audio.amplitude = 0;
    vi.advanceTimersByTime(3000);
    // 0 しか読めない間に「音が入っていません」と出さない。
    expect(onLevel).not.toHaveBeenCalled();
    expect(audio.reads).toBe(0);
    expect(audio.resumes).toBeGreaterThan(1);

    // 動き出したら測り始める。
    audio.last!.state = 'running';
    audio.amplitude = 0.1;
    vi.advanceTimersByTime(LEVEL_SAMPLE_MS);
    expect(onLevel).toHaveBeenLastCalledWith('ok');
  });

  it('close がその場で投げても外へ漏らさない', () => {
    audio.throwOnClose = true;
    const meter = createLevelMeter({} as MediaStream, vi.fn())!;
    expect(() => meter.close()).not.toThrow();
  });
});
