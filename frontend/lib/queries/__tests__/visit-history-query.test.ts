/**
 * 打刻履歴のクエリ組み立て・応答パースの vitest
 * （契約 = `docs/plans/visit-history-design-2026-09-30.md` §3）。
 *
 * 縛る挙動:
 *   1. クエリ名は契約どおり（from / to / patient_id / office_id / staff_id / state / q / sort
 *      / limit / offset、A4 は group / include_none / page_break）
 *   2. 検索語は 2 文字以上のときだけ送る
 *   3. Excel はページングを付けない。A4 は sort ではなく group を送る
 *   4. 応答は行単位で検証し、読めない行だけ捨てる。集計の欠けは 0
 */
import { describe, it, expect, vi } from 'vitest';

import {
  buildHistoryExportQuery,
  buildHistoryListQuery,
  buildHistoryReportQuery,
  parseDispositionFilename,
  parseVisitHistory,
} from '@/lib/queries/visit-history';

const RANGE = { from: '2026-09-01', to: '2026-09-30' };

function params(qs: string): URLSearchParams {
  return new URLSearchParams(qs);
}

describe('buildHistoryListQuery', () => {
  it('既定は from / to と sort=date・limit=50・offset=0 だけ', () => {
    const qs = params(buildHistoryListQuery(RANGE));
    expect(Object.fromEntries(qs)).toEqual({
      from: '2026-09-01',
      to: '2026-09-30',
      sort: 'date',
      limit: '50',
      offset: '0',
    });
  });

  it('絞り込みは契約のクエリ名で送る', () => {
    const qs = params(
      buildHistoryListQuery({
        ...RANGE,
        patientId: 'p-1',
        officeId: 'of-1',
        staffId: 'st-1',
        state: 'nodep',
        q: '  山田  ',
        sort: 'staff',
        limit: 50,
        offset: 100,
      }),
    );
    expect(Object.fromEntries(qs)).toEqual({
      from: '2026-09-01',
      to: '2026-09-30',
      patient_id: 'p-1',
      office_id: 'of-1',
      staff_id: 'st-1',
      state: 'nodep',
      q: '山田',
      sort: 'staff',
      limit: '50',
      offset: '100',
    });
  });

  it('1 文字の検索語・空の絞り込みは送らない', () => {
    const qs = params(buildHistoryListQuery({ ...RANGE, q: '山', state: '', staffId: null }));
    expect(qs.has('q')).toBe(false);
    expect(qs.has('state')).toBe(false);
    expect(qs.has('staff_id')).toBe(false);
  });

  it('「時刻の調整あり」は state=adjusted で送る', () => {
    expect(params(buildHistoryListQuery({ ...RANGE, state: 'adjusted' })).get('state')).toBe(
      'adjusted',
    );
  });

  it('enabled は FE 専用なので BE へ渡さない', () => {
    expect(params(buildHistoryListQuery({ ...RANGE, enabled: false })).has('enabled')).toBe(false);
  });
});

describe('buildHistoryExportQuery', () => {
  it('絞り込みと並びを送り、ページングは付けない', () => {
    const qs = params(buildHistoryExportQuery({ ...RANGE, state: 'in', sort: 'patient' }));
    expect(Object.fromEntries(qs)).toEqual({
      from: '2026-09-01',
      to: '2026-09-30',
      state: 'in',
      sort: 'patient',
    });
  });
});

describe('buildHistoryReportQuery', () => {
  it('group / include_none / page_break を送り、sort は送らない', () => {
    const qs = params(
      buildHistoryReportQuery(
        { ...RANGE, staffId: 'st-1', sort: 'date' },
        { group: 'staff', includeNone: false, pageBreak: true },
      ),
    );
    expect(Object.fromEntries(qs)).toEqual({
      from: '2026-09-01',
      to: '2026-09-30',
      staff_id: 'st-1',
      group: 'staff',
      include_none: 'false',
      page_break: 'true',
    });
  });
});

describe('parseVisitHistory', () => {
  const row = { visit_id: 'v-1', visit_date: '2026-09-29', state: 'done' };

  it('items / total / summary を読む', () => {
    const out = parseVisitHistory({
      items: [row],
      total: 120,
      summary: { visits: 40, with_arrival: 10, with_departure: 8, no_departure: 2, none: 30 },
    });
    expect(out.items).toHaveLength(1);
    expect(out.total).toBe(120);
    expect(out.summary).toEqual({
      visits: 40,
      with_arrival: 10,
      with_departure: 8,
      no_departure: 2,
      none: 30,
    });
  });

  it('読めない行だけ捨て、残りは出す', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = parseVisitHistory({ items: [row, { visit_date: '2026-09-29' }], total: 2 });
    expect(out.items.map((r) => r.visit_id)).toEqual(['v-1']);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  it('予定外（予定・担当が null）や未知の state でも行を捨てない', () => {
    const out = parseVisitHistory({
      items: [
        { ...row, start_time: null, end_time: null, planned_staff_name: null, state: 'later' },
      ],
    });
    expect(out.items).toHaveLength(1);
  });

  it('summary が欠けていれば 0、形が違う応答は空', () => {
    expect(parseVisitHistory({ items: [row] }).summary).toEqual({
      visits: 0,
      with_arrival: 0,
      with_departure: 0,
      no_departure: 0,
      none: 0,
    });
    expect(parseVisitHistory(null)).toMatchObject({ items: [], total: 0 });
    expect(parseVisitHistory('<html>')).toMatchObject({ items: [], total: 0 });
  });

  // ── 実績の時刻を合わせる（設計 actual-time-adjust-design-2026-09-30 §6-3）──

  it('調整の項目（読取時刻・調整の有無・手入力・調整の内容・合わせられるか）を読む', () => {
    const out = parseVisitHistory({
      items: [
        {
          ...row,
          arrival_at: '2026-09-29T03:56:00Z',
          arrival_read_at: '2026-09-29T04:06:00Z',
          arrival_adjusted: true,
          departure_adjusted: false,
          departure_manual: false,
          adjust_allowed: true,
          adjustments: [
            {
              kind: 'arrival',
              reason_label: 'インターホン待ち',
              reason_text: null,
              by_name: '川名 幸子',
              created_at: '2026-09-29T04:10:00Z',
            },
          ],
        },
      ],
    });
    expect(out.items[0]).toMatchObject({
      arrival_read_at: '2026-09-29T04:06:00Z',
      arrival_adjusted: true,
      departure_manual: false,
      adjust_allowed: true,
      adjustments: [{ kind: 'arrival', reason_label: 'インターホン待ち', by_name: '川名 幸子' }],
    });
  });

  it('調整の理由コード（reason_code）を読む。無い応答（古い BE）でも行は読める（L-11）', () => {
    const out = parseVisitHistory({
      items: [
        {
          ...row,
          adjustments: [
            { kind: 'arrival', reason_code: 'read_later', reason_label: '読み取りが後になった' },
            { kind: 'departure', reason_label: '読み取りなし' },
          ],
        },
      ],
    });
    expect(out.items[0]?.adjustments?.[0]?.reason_code).toBe('read_later');
    expect(out.items[0]?.adjustments?.[1]?.reason_code).toBeUndefined();
    expect(out.items[0]?.adjustments?.[1]?.reason_label).toBe('読み取りなし');
  });

  it('調整の形が崩れていても行は捨てない（調整だけ落とす）', () => {
    const out = parseVisitHistory({ items: [{ ...row, adjustments: [{ reason_label: 'x' }] }] });
    expect(out.items).toHaveLength(1);
    expect(out.items[0]?.adjustments).toBeNull();
  });

  it('summary.adjusted は応答にあるときだけ持つ（古い BE では出さない）', () => {
    expect(parseVisitHistory({ items: [], summary: { visits: 3, adjusted: 2 } }).summary).toEqual({
      visits: 3,
      with_arrival: 0,
      with_departure: 0,
      no_departure: 0,
      none: 0,
      adjusted: 2,
    });
    expect(
      parseVisitHistory({ items: [], summary: { visits: 3 } }).summary.adjusted,
    ).toBeUndefined();
  });

  it('groups は絞り込み結果全体の件数。応答に無ければ null、読めない要素は捨てる', () => {
    expect(parseVisitHistory({ items: [row] }).groups).toBeNull();
    expect(parseVisitHistory({ items: [row], groups: [] }).groups).toEqual([]);
    expect(
      parseVisitHistory({
        items: [row],
        groups: [
          { label: '川名 幸子', count: 120, with_arrival: 32 },
          { label: '中村 彩', count: 4 },
          { count: 1 },
        ],
      }).groups,
    ).toEqual([
      { label: '川名 幸子', count: 120, with_arrival: 32 },
      { label: '中村 彩', count: 4, with_arrival: 0 },
    ]);
  });
});

describe('parseDispositionFilename', () => {
  it('ASCII の filename を読む', () => {
    expect(
      parseDispositionFilename('attachment; filename="visit-history_2026-09-01_2026-09-30.xlsx"'),
    ).toBe('visit-history_2026-09-01_2026-09-30.xlsx');
  });

  it("filename*=UTF-8'' を優先して復号する", () => {
    expect(
      parseDispositionFilename(
        `attachment; filename="x.xlsx"; filename*=UTF-8''${encodeURIComponent('打刻履歴.xlsx')}`,
      ),
    ).toBe('打刻履歴.xlsx');
  });

  it('ヘッダが無ければ null', () => {
    expect(parseDispositionFilename(null)).toBeNull();
  });
});
