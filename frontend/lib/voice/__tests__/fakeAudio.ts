/**
 * jsdom に無い `AudioContext` の偽物（音量メーターの試験用）。
 *
 * `amplitude` を変えると、次に測ったときの波形がその一定値になる（RMS = amplitude）。
 */
export function installFakeAudioContext() {
  const w = window as unknown as { AudioContext?: unknown; webkitAudioContext?: unknown };
  const prev = w.AudioContext;
  const state = {
    amplitude: 0,
    /** 測った回数。 */
    reads: 0,
    /** close を呼ばれた回数。 */
    closed: 0,
    /** 作った数。 */
    created: 0,
    failCreate: false,
    /** 作った AudioContext の state（iOS の suspended を真似る）。 */
    initialState: 'running' as string,
    /** resume を呼ばれた回数。 */
    resumes: 0,
    /** close がその場で投げる（古い webkitAudioContext を真似る）。 */
    throwOnClose: false,
    /** 作った最後の AudioContext（state を外から変える用）。 */
    last: null as { state: string } | null,
    uninstall() {
      w.AudioContext = prev;
    },
  };

  class FakeAudioContext {
    state = state.initialState;
    destination = {};
    constructor() {
      if (state.failCreate) throw new Error('AudioContext を作れません');
      state.created += 1;
      state.last = this;
    }
    createGain() {
      return { gain: { value: 1 }, connect: () => undefined };
    }
    createMediaStreamSource() {
      return { connect: () => undefined, disconnect: () => undefined };
    }
    createAnalyser() {
      return {
        fftSize: 0,
        connect: () => undefined,
        getFloatTimeDomainData(buf: Float32Array) {
          state.reads += 1;
          buf.fill(state.amplitude);
        },
      };
    }
    resume() {
      state.resumes += 1;
      return Promise.resolve();
    }
    close() {
      if (state.throwOnClose) throw new Error('close できません');
      state.closed += 1;
      return Promise.resolve();
    }
  }

  w.AudioContext = FakeAudioContext;
  return state;
}
