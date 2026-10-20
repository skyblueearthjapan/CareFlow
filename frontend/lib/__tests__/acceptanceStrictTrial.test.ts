/**
 * 受け入れ枠 — 画面の集計 (TypeScript) が試算と同じ結果になること (設計書 §7-1)。
 *
 * 材料はバックエンドが「予定だけで見る」・上限 6 件で作った応答 (職員A〜D・名前は置き換え済み)。
 * 書き出しと突き合わせはバックエンドの tests/test_acceptance_strict_review.py。
 * 期待値は試算 (acc_calc.py / 試算ページ) の結果。あわせてレビューの指摘の小さな試験。
 */
import { describe, expect, it } from 'vitest';

import {
  failedMessages,
  isJobActive,
  officeNotice,
  rulesText,
  strictPayloadSchema,
  summarize,
  unmatchedCount,
  type LeaveMode,
} from '@/lib/acceptanceStrict';

import { makePayload } from './fixtures/acceptanceStrictPayload';
import trial from './fixtures/acceptanceStrictTrial.json';

interface ExpectedCell {
  n_weeks: number;
  weeks_ok: number;
  per_week: number[];
}
interface Expected {
  staff: string[];
  weeks: number[];
  off: string;
  cells: Record<string, ExpectedCell>;
}

const payload = strictPayloadSchema.parse(trial.payload);
const expected = trial.expected as Record<string, Expected>;

describe('試算との一致 (TypeScript の summarize)', () => {
  it.each(Object.keys(expected))('%s', (name) => {
    const e = expected[name]!;
    const mode: LeaveMode = e.off === 'real' ? 'real' : 'ignore';
    const got = summarize(payload, new Set(e.staff), e.weeks.length, mode);
    expect(got.cells.size).toBe(54);
    for (const [key, cell] of Object.entries(e.cells)) {
      const mine = got.cells.get(key)!;
      expect([key, mine.counted, mine.ok, mine.perWeek]).toEqual([
        key,
        cell.n_weeks,
        cell.weeks_ok,
        cell.per_week,
      ]);
    }
  });

  it('職員 4 人・4 週は ○0・△0', () => {
    const got = summarize(payload, new Set(['A', 'B', 'C', 'D']), 4, 'real');
    expect(got.counts.ok).toBe(0);
    expect(got.counts.mid).toBe(0);
  });
});

describe('レビューの指摘', () => {
  const job = makePayload().sources.last_job!;

  it('残骸のジョブは動いていないとみなす', () => {
    expect(isJobActive({ ...job, status: 'running' })).toBe(true);
    expect(isJobActive({ ...job, status: 'running', stale: true })).toBe(false);
    expect(isJobActive({ ...job, status: 'completed' })).toBe(false);
  });

  it('ジョブの開始より後に取れた本は「取れませんでした」を出さない', () => {
    const later = [
      {
        kind: 'tasks',
        period_start: '2026-10-05',
        status: 'ok',
        fetched_at: '2026-10-20T02:00:00Z',
      },
    ];
    const earlier = [{ ...later[0]!, fetched_at: '2026-10-19T02:00:00Z' }];
    expect(failedMessages(job, later)).toEqual([]);
    expect(failedMessages(job, earlier)).toHaveLength(1);
  });

  it('△ の説明の括弧は 4 週のときだけ', () => {
    const rules = makePayload().rules;
    expect(rulesText(rules, 4).mid).toContain('（6 週なら 4〜5 週、8 週なら 5〜7 週）');
    expect(rulesText(rules, 6).mid).not.toContain('（');
    expect(rulesText(rules, 8).mid).not.toContain('（');
  });

  it('職員の権限では結び付かない職員名は件数だけ', () => {
    const sources = {
      ...makePayload().sources,
      unmatched_staff_names: {},
      unmatched_staff_count: 4,
    };
    expect(unmatchedCount(sources)).toBe(4);
    expect(unmatchedCount(makePayload().sources)).toBe(2);
  });

  it('拠点が 0 件・読み込みの失敗', () => {
    expect(officeNotice({ isError: true })).toMatch(/拠点を読み込めませんでした/);
    expect(officeNotice({ isSuccess: true, allOffices: [] })).toMatch(/拠点が登録されていません/);
    expect(officeNotice({ isSuccess: true, allOffices: [{}] })).toBeNull();
    expect(officeNotice({})).toBeNull();
  });
});
