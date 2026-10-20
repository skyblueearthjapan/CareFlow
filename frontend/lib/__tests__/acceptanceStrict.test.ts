/**
 * 受け入れ枠 (数週続けて空いているか) の画面側の集計と文言 (設計書 §2-6・§5・§7-4)。
 */
import { describe, expect, it } from 'vitest';

import {
  cellDetail,
  cellKey,
  effectiveMark,
  failedMessages,
  grade,
  isEveningJst,
  overrideMap,
  periodLabel,
  presetSelection,
  progressLabel,
  rulesText,
  sourceLabel,
  staleSources,
  strictPayloadSchema,
  summarize,
  triangleMin,
} from '@/lib/acceptanceStrict';

import { makePayload } from './fixtures/acceptanceStrictPayload';

const STAFF = new Set(['a', 'b']);

describe('○△× の境目', () => {
  it.each([
    [4, 4, 4, '○'],
    [3, 4, 4, '△'],
    [2, 4, 4, '×'],
    [6, 6, 6, '○'],
    [5, 6, 6, '△'],
    [4, 6, 6, '△'],
    [3, 6, 6, '×'],
    [8, 8, 8, '○'],
    [5, 8, 8, '△'],
    [4, 8, 8, '×'],
    [7, 7, 8, '○'],
    [5, 7, 8, '△'],
    [4, 7, 8, '×'],
    [3, 3, 8, '－'],
  ])('%i/%i 週 (見る週 %i) → %s', (ok, counted, total, mark) => {
    expect(grade(ok, counted, total)).toBe(mark);
  });

  it('△ の下限の表', () => {
    expect([3, 4, 5, 6, 7, 8].map(triangleMin)).toEqual([2, 3, 4, 4, 5, 5]);
  });
});

describe('summarize', () => {
  const payload = makePayload();

  it('応答の形を読める', () => {
    expect(() => strictPayloadSchema.parse(payload)).not.toThrow();
  });

  it('祝日は分母から外す・休みの扱いと担い手で結果が変わる', () => {
    const s = summarize(payload, STAFF, 4, 'real');
    expect(s.countedByWeekday.get(0)).toBe(3); // 10/12 は祝日
    expect(s.countedByWeekday.get(1)).toBe(4);
    expect(s.cells.get(cellKey(0, 9))?.mark).toBe('○');
    expect(s.cells.get(cellKey(0, 14))?.mark).toBe('×');
    expect(summarize(payload, STAFF, 4, 'ignore').cells.get(cellKey(0, 14))?.mark).toBe('○');
    const withManager = summarize(payload, new Set(['a', 'b', 'm']), 4, 'real');
    expect(withManager.cells.get(cellKey(1, 15))?.mark).toBe('○');
    expect(s.cells.get(cellKey(1, 15))?.mark).toBe('×');
  });

  it('入職前の人だけの日は数えない・見られた週が半分未満は －', () => {
    const onlyTrainee = summarize(payload, new Set(['t']), 4, 'real');
    expect(onlyTrainee.countedByWeekday.get(0)).toBe(0);
    expect(onlyTrainee.cells.get(cellKey(0, 9))?.mark).toBe('－');
    const p = makePayload();
    for (const d of p.days)
      if (d.date >= '2026-09-29' && d.date <= '2026-10-13') d.state = 'no_data';
    const s = summarize(p, STAFF, 4, 'real');
    expect(s.countedByWeekday.get(1)).toBe(1);
    expect(s.cells.get(cellKey(1, 9))?.mark).toBe('－');
  });

  it('期間の表示は見る週で変わる', () => {
    expect(periodLabel(payload, 4)).toBe('9/28（月） 〜 10/20（火）');
    expect(periodLabel(payload, 8)).toBe('8/31（月） 〜 10/20（火）');
  });
});

describe('マスの内訳', () => {
  it('週ごとに新しい順・祝日・実績の入力待ち・取り込み済みの印', () => {
    const weeks = cellDetail(makePayload(), STAFF, 4, 'real', 0, 9);
    expect(weeks.map((w) => w.date)).toEqual([
      '2026-10-19',
      '2026-10-12',
      '2026-10-05',
      '2026-09-28',
    ]);
    expect(weeks[0]?.notes).toContain('らく助に取り込み済みの個別業務で見ています');
    expect(weeks[1]?.skipReason).toBe('祝日（スポーツの日）のため数えていません');
    expect(weeks[2]?.notes).toContain('実績の入力待ちのため予定で見ています');
    expect(weeks[0]?.free.map((f) => f.name)).toEqual(['職員A']);
    expect(weeks[0]?.off).toEqual([{ name: '職員B', text: '有給（らく助）' }]);
  });
});

describe('まとめボタン', () => {
  const staff = makePayload().staff;
  it.each([
    ['staff', ['a', 'b']],
    ['managers', ['a', 'b', 'm']],
    ['trainees', ['a', 'b', 't']],
    ['all', ['a', 'b', 'm', 't']],
  ] as const)('%s', (preset, ids) => {
    expect([...presetSelection(staff, preset)].sort()).toEqual([...ids].sort());
  });
});

describe('決まりの文章', () => {
  const rules = makePayload().rules;
  it('週数で「4 週」「3 週」が変わる', () => {
    expect(rulesText(rules, 4).mid).toContain('4 週のうち 3 週で');
    expect(rulesText(rules, 6).mid).toContain('6 週のうち 4〜5 週で');
    expect(rulesText(rules, 8).ok).toContain('見た 8 週のすべてで');
    expect(rulesText(rules, 4).how).toContain('20 分（ゆとり 8 分＋移動の見込み 12 分）');
    expect(rulesText(rules, 4).how).toContain('11:30〜13:30 の中に 60 分の昼休み');
  });

  it('「直す」「修正」を使わない', () => {
    const all = [4, 6, 8].flatMap((n) => Object.values(rulesText(rules, n)));
    for (const t of [...all, ...failedMessages(makePayload().sources.last_job)]) {
      expect(t).not.toMatch(/(?<!取り)直す|修正/);
    }
  });
});

describe('上書き・取り直し', () => {
  it('毎週の上書きを優先し、食い違いに印', () => {
    const p = makePayload();
    const s = summarize(p, STAFF, 4, 'real');
    const map = overrideMap(p.overrides);
    const same = effectiveMark(s.cells.get(cellKey(0, 9))!, map.get(cellKey(0, 9)));
    expect(same).toEqual({ mark: '○', manual: true, conflict: false });
    const diff = effectiveMark(s.cells.get(cellKey(0, 14))!, map.get(cellKey(0, 14)));
    expect(diff).toEqual({ mark: '○', manual: true, conflict: true });
  });

  it('進み具合・取れなかったもの・古い材料', () => {
    expect(sourceLabel('actual_csv', '2026-10-01')).toBe('10 月の実績');
    expect(sourceLabel('tasks', '2026-10-05')).toBe('第 41 週の個別業務');
    expect(
      progressLabel({
        id: 'j',
        status: 'running',
        done: 3,
        total: 6,
        current: { kind: 'actual_csv', period_start: '2026-10-01' },
        failed: [],
      }),
    ).toBe('3 / 6 本・10 月の実績を取っています');
    expect(failedMessages(makePayload().sources.last_job)).toEqual([
      '第 41 週の個別業務が取れませんでした。らく助に取り込み済みの個別業務で見ています',
    ]);
    expect(staleSources(makePayload())).toEqual(['10 月の予定（10/20 9:12 に取ったもの）']);
  });

  it('夕方 (JST 17〜18 時台) の判定', () => {
    expect(isEveningJst(new Date('2026-10-20T08:00:00Z'))).toBe(true); // 17:00 JST
    expect(isEveningJst(new Date('2026-10-20T09:59:00Z'))).toBe(true); // 18:59 JST
    expect(isEveningJst(new Date('2026-10-20T10:00:00Z'))).toBe(false);
  });
});
