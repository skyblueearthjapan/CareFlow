/**
 * lib/cfAccess — Cloudflare Access セッション切れ検知の単体テスト (handoff §6-3)。
 *
 * 1. checkAccessSession: 200=有効 / opaqueredirect・401=切れ / fetch失敗=切れ扱い
 * 2. オフライン時は「切れ」と判定しない (誤誘導防止)
 * 3. reportPossibleAccessIssue: 切れ検知で CF_ACCESS_EXPIRED_EVENT を発火し、
 *    cooldown 中の再探針はスキップされる
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  CF_ACCESS_EXPIRED_EVENT,
  checkAccessSession,
  reportPossibleAccessIssue,
  _resetAccessProbeStateForTest,
} from '@/lib/cfAccess';

function mockFetchResponse(partial: Partial<Response>): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(partial as Response));
}

describe('checkAccessSession', () => {
  beforeEach(() => {
    _resetAccessProbeStateForTest();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('healthz が 200 なら有効 (true)', async () => {
    mockFetchResponse({ type: 'basic', status: 200 });
    await expect(checkAccessSession()).resolves.toBe(true);
  });

  it('opaqueredirect (Access ログインへの 302) なら切れ (false)', async () => {
    mockFetchResponse({ type: 'opaqueredirect', status: 0 });
    await expect(checkAccessSession()).resolves.toBe(false);
  });

  it('401 なら切れ (false)', async () => {
    mockFetchResponse({ type: 'basic', status: 401 });
    await expect(checkAccessSession()).resolves.toBe(false);
  });

  it('fetch 自体の失敗 (ネットワークエラー) は切れ扱い (false)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(checkAccessSession()).resolves.toBe(false);
  });

  it('オフライン時は探針せず有効扱い (誤誘導しない)', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const onLineSpy = vi.spyOn(window.navigator, 'onLine', 'get').mockReturnValue(false);
    try {
      await expect(checkAccessSession()).resolves.toBe(true);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      onLineSpy.mockRestore();
    }
  });
});

describe('reportPossibleAccessIssue', () => {
  beforeEach(() => {
    _resetAccessProbeStateForTest();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('切れ検知で CF_ACCESS_EXPIRED_EVENT を発火する', async () => {
    mockFetchResponse({ type: 'opaqueredirect', status: 0 });
    const handler = vi.fn();
    window.addEventListener(CF_ACCESS_EXPIRED_EVENT, handler);
    try {
      await reportPossibleAccessIssue();
      expect(handler).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(CF_ACCESS_EXPIRED_EVENT, handler);
    }
  });

  it('Access 正常ならイベントを発火しない', async () => {
    mockFetchResponse({ type: 'basic', status: 200 });
    const handler = vi.fn();
    window.addEventListener(CF_ACCESS_EXPIRED_EVENT, handler);
    try {
      await reportPossibleAccessIssue();
      expect(handler).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(CF_ACCESS_EXPIRED_EVENT, handler);
    }
  });

  it('cooldown 中の連続呼び出しは探針をスキップする', async () => {
    const fetchSpy = vi.fn().mockResolvedValue({ type: 'basic', status: 200 } as Response);
    vi.stubGlobal('fetch', fetchSpy);
    await reportPossibleAccessIssue();
    await reportPossibleAccessIssue();
    await reportPossibleAccessIssue();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
