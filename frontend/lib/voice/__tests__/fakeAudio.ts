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
    uninstall() {
      w.AudioContext = prev;
    },
  };

  class FakeAudioContext {
    state = 'running';
    constructor() {
      if (state.failCreate) throw new Error('AudioContext を作れません');
      state.created += 1;
    }
    createMediaStreamSource() {
      return { connect: () => undefined, disconnect: () => undefined };
    }
    createAnalyser() {
      return {
        fftSize: 0,
        getFloatTimeDomainData(buf: Float32Array) {
          state.reads += 1;
          buf.fill(state.amplitude);
        },
      };
    }
    resume() {
      return Promise.resolve();
    }
    close() {
      state.closed += 1;
      return Promise.resolve();
    }
  }

  w.AudioContext = FakeAudioContext;
  return state;
}
