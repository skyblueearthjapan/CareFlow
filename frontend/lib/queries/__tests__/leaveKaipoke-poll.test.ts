/**
 * 照合・送信のジョブを読み直す決まり (refetchInterval): 終わった・止まった (stale)・
 * 読めなかった (error) ジョブは読まない。まだ読めていなければ読む。
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('next-auth/react', () => ({ useSession: () => ({ data: null, status: 'loading' }) }));

import { compareJobPollMs, LEAVE_JOB_POLL_MS, outboundJobPollMs } from '@/lib/queries/leaveKaipoke';
import {
  isJobActive,
  type CompareJobStatus,
  type OutboundJobStatus,
} from '@/lib/schemas/leaveKaipoke';

function jobView(status: string, stale = false) {
  return {
    id: 'j',
    status,
    done: 0,
    total: 1,
    results: [],
    failed: [],
    stale,
  };
}

function compare(
  state: CompareJobStatus['state'],
  status = 'running',
  stale = false,
): CompareJobStatus {
  return { job: jobView(status, stale), state, run: null, compare_error: null };
}

function outbound(status: string, stale = false): OutboundJobStatus {
  return {
    job: jobView(status, stale),
    count: 1,
    items: [],
    sent_count: 0,
    failed_count: 0,
    skipped_count: 0,
    needs_action: [],
  };
}

describe('照合のジョブ', () => {
  it('まだ読めていない・取り直し中・比べているは読む', () => {
    expect(compareJobPollMs({ status: 'pending', data: undefined })).toBe(LEAVE_JOB_POLL_MS);
    expect(compareJobPollMs({ status: 'success', data: compare('fetching') })).toBe(
      LEAVE_JOB_POLL_MS,
    );
    expect(compareJobPollMs({ status: 'success', data: compare('comparing', 'completed') })).toBe(
      LEAVE_JOB_POLL_MS,
    );
  });

  it('終わった・止まった・読めなかったら読まない', () => {
    for (const st of ['done', 'compare_failed', 'failed', 'cancelled'] as const) {
      expect(compareJobPollMs({ status: 'success', data: compare(st, 'completed') })).toBe(false);
    }
    expect(
      compareJobPollMs({ status: 'success', data: compare('fetching', 'running', true) }),
    ).toBe(false);
    expect(compareJobPollMs({ status: 'error', data: compare('fetching') })).toBe(false);
    expect(compareJobPollMs({ status: 'error', data: undefined })).toBe(false);
  });
});

describe('送信のジョブ', () => {
  it('動いている間だけ読む', () => {
    expect(outboundJobPollMs({ status: 'pending', data: undefined })).toBe(LEAVE_JOB_POLL_MS);
    expect(outboundJobPollMs({ status: 'success', data: outbound('pending') })).toBe(
      LEAVE_JOB_POLL_MS,
    );
    expect(outboundJobPollMs({ status: 'success', data: outbound('running') })).toBe(
      LEAVE_JOB_POLL_MS,
    );
    expect(outboundJobPollMs({ status: 'success', data: outbound('completed') })).toBe(false);
    expect(outboundJobPollMs({ status: 'success', data: outbound('running', true) })).toBe(false);
    expect(outboundJobPollMs({ status: 'error', data: outbound('running') })).toBe(false);
  });

  it('止まったジョブは動いていない', () => {
    expect(isJobActive({ status: 'running', stale: true })).toBe(false);
    expect(isJobActive({ status: 'pending' })).toBe(true);
    expect(isJobActive(null)).toBe(false);
  });
});
