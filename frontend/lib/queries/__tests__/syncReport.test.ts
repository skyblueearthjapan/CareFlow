import { describe, expect, it } from 'vitest';

import { SyncReportSchema } from '../syncReport';

describe('SyncReportSchema', () => {
  it('html があれば通り、BE が後から足した未知のキーも落とさない', () => {
    const parsed = SyncReportSchema.parse({
      job: { id: 'job-1' },
      summary: { total: 24 },
      detailLevel: 'full',
      generatedAt: '2026-09-03T12:00:00Z',
      html: '<html></html>',
      // 未知のキー (BE の章追加を FE で止めない)
      verification: { matched: 24, mismatched: 0 },
      rows: [],
    });
    expect(parsed.html).toBe('<html></html>');
    expect((parsed as Record<string, unknown>).verification).toEqual({
      matched: 24,
      mismatched: 0,
    });
  });

  it('html が無ければ弾く', () => {
    expect(() => SyncReportSchema.parse({ job: {}, summary: {} })).toThrow();
    expect(() => SyncReportSchema.parse({ html: 123 })).toThrow();
  });

  it('job / summary / detailLevel は null でも欠けていても通る', () => {
    expect(
      SyncReportSchema.parse({ job: null, summary: null, detailLevel: null, html: '<p/>' }).html,
    ).toBe('<p/>');
    expect(SyncReportSchema.parse({ html: '<p/>' }).html).toBe('<p/>');
    // enum で閉じていないので将来値でも通る
    expect(SyncReportSchema.parse({ detailLevel: 'items_only', html: '<p/>' }).detailLevel).toBe(
      'items_only',
    );
  });
});
