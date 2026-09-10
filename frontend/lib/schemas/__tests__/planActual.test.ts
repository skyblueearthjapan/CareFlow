import { describe, expect, it } from 'vitest';

import {
  PLAN_ACTUAL_COUNT_KEYS,
  PlanActualByStaffSchema,
  PlanActualCompareRequestSchema,
  PlanActualCountsSchema,
  PlanActualSummarySchema,
} from '../integration';

describe('予実比較スキーマ (寛容さ)', () => {
  it('件数は欠けていても壊れていても落ちない (数値でなければ 0)', () => {
    const r = PlanActualCountsSchema.parse({ 一致: 480, 時刻ズレ: '12', 重複: null });
    expect(r['一致']).toBe(480);
    // 文字列・null は 0 に落とす (BE の型ゆらぎで画面を落とさない)
    expect(r['時刻ズレ']).toBe(0);
    expect(r['重複']).toBe(0);
    // 欠けているキーは undefined (呼び出し側で「出さない」判断ができる)
    expect(r['担当違い']).toBeUndefined();
  });

  it('未知の区分キーは捨てずに素通しする', () => {
    const r = PlanActualCountsSchema.parse({ 一致: 1, 新区分: 7 });
    expect((r as Record<string, unknown>)['新区分']).toBe(7);
  });

  it('区分の表示順は 7 種', () => {
    expect(PLAN_ACTUAL_COUNT_KEYS).toEqual([
      '一致',
      '時刻ズレ',
      '担当違い',
      '相違',
      '予定のみ',
      '実績のみ',
      '重複',
    ]);
  });

  it('result_summary は counts / by_staff 欠落でも既定値で通る', () => {
    const r = PlanActualSummarySchema.parse({ month: '2026-08' });
    expect(r.month).toBe('2026-08');
    expect(r.counts).toEqual({});
    expect(r.by_staff).toEqual([]);
  });

  it('counts / by_staff が壊れた型でも既定値に落ちる', () => {
    const r = PlanActualSummarySchema.parse({ month: '2026-08', counts: 'x', by_staff: 3 });
    expect(r.counts).toEqual({});
    expect(r.by_staff).toEqual([]);
  });

  it('by_staff は staff / counts / duplicates / total、余剰キーは保持', () => {
    const r = PlanActualSummarySchema.parse({
      month: '2026-08',
      plan_rows: 500,
      actual_rows: 498,
      counts: { 一致: 480, 相違: 3 },
      by_staff: [
        {
          staff: '熊澤',
          counts: { 一致: 40, 実績のみ: 1 },
          duplicates: 2,
          total: 43,
          office: '都賀A',
        },
      ],
      error: null,
    });
    expect(r.plan_rows).toBe(500);
    expect(r.by_staff[0]!.staff).toBe('熊澤');
    expect(r.by_staff[0]!.counts['一致']).toBe(40);
    expect(r.by_staff[0]!.duplicates).toBe(2);
    expect(r.by_staff[0]!.total).toBe(43);
    expect((r.by_staff[0] as Record<string, unknown>)['office']).toBe('都賀A');
    // BE が章立てを足しても素通し
    expect(r.error).toBeNull();
  });

  it('by_staff は staff / counts が欠けても既定値に落ちる', () => {
    const r = PlanActualByStaffSchema.parse({ duplicates: 'x' });
    expect(r.staff).toBe('');
    expect(r.counts).toEqual({});
    expect(r.duplicates).toBe(0);
  });

  it('内数のタグ (職種未設定・同日複数) も件数として読める', () => {
    const r = PlanActualCountsSchema.parse({ 一致: 3, 職種未設定: 1, 同日複数: 2 });
    expect(r['職種未設定']).toBe(1);
    expect(r['同日複数']).toBe(2);
  });

  it('by_staff の untyped / multi_same_day も読む (古い BE では欠ける)', () => {
    const r = PlanActualByStaffSchema.parse({
      staff: '唐鎌',
      counts: { 一致: 1, 実績のみ: 1 },
      duplicates: 0,
      untyped: 1,
      multi_same_day: 2,
      total: 2,
    });
    expect(r.untyped).toBe(1);
    expect(r.multi_same_day).toBe(2);
    expect(PlanActualByStaffSchema.parse({ staff: '熊澤' }).untyped).toBeUndefined();
  });

  it('events_skipped も件数として読める', () => {
    const r = PlanActualCountsSchema.parse({ 一致: 3, events_skipped: 5 });
    expect(r.events_skipped).toBe(5);
  });

  it('リクエストは YYYY-MM のみ受ける', () => {
    expect(PlanActualCompareRequestSchema.parse({ month: '2026-08' }).month).toBe('2026-08');
    expect(PlanActualCompareRequestSchema.safeParse({ month: '2026-08-01' }).success).toBe(false);
    expect(PlanActualCompareRequestSchema.safeParse({ month: '' }).success).toBe(false);
  });
});
