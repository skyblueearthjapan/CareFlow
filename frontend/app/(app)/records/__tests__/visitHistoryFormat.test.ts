/**
 * 打刻履歴タブの期間計算・備考バッジ（純関数）の vitest。
 *
 * 期間は請求の締めに直結するので、月末・年跨ぎ・週の月曜始まりを直接縛る。
 */
import { describe, it, expect } from 'vitest';

import {
  formatAdjustedAt,
  formatRangeLabel,
  matchPreset,
  plannedMinutes,
  plannedRange,
  presetRange,
  rangeDays,
  remarkBadges,
  shiftRange,
} from '../_components/visitHistoryFormat';

/** 2026-09-30 (水)。 */
const NOW = new Date(2026, 8, 30, 15, 10, 0);

describe('presetRange', () => {
  it('今週 / 先週は月曜〜日曜', () => {
    expect(presetRange('week', NOW)).toEqual({
      unit: 'week',
      from: '2026-09-28',
      to: '2026-10-04',
    });
    expect(presetRange('lastweek', NOW)).toEqual({
      unit: 'week',
      from: '2026-09-21',
      to: '2026-09-27',
    });
  });

  it('日曜は「その週の月曜」まで 6 日戻る', () => {
    expect(presetRange('week', new Date(2026, 9, 4))).toMatchObject({
      from: '2026-09-28',
      to: '2026-10-04',
    });
  });

  it('今月 / 先月は月初〜月末（1 月の先月は前年 12 月）', () => {
    expect(presetRange('month', NOW)).toEqual({
      unit: 'month',
      from: '2026-09-01',
      to: '2026-09-30',
    });
    expect(presetRange('lastmonth', new Date(2027, 0, 15))).toEqual({
      unit: 'month',
      from: '2026-12-01',
      to: '2026-12-31',
    });
  });
});

describe('shiftRange', () => {
  it('月は 1 か月ぶん送り、月末の日数に合わせる', () => {
    const jan = { unit: 'month', from: '2027-01-01', to: '2027-01-31' } as const;
    expect(shiftRange(jan, 1)).toEqual({ unit: 'month', from: '2027-02-01', to: '2027-02-28' });
    expect(shiftRange(jan, -1)).toEqual({ unit: 'month', from: '2026-12-01', to: '2026-12-31' });
  });

  it('週は 7 日ぶん送る', () => {
    expect(shiftRange(presetRange('week', NOW), -1)).toEqual(presetRange('lastweek', NOW));
  });

  it('期間指定は動かさない', () => {
    const custom = { unit: 'custom', from: '2026-09-18', to: '2026-09-24' } as const;
    expect(shiftRange(custom, 1)).toEqual(custom);
  });
});

describe('matchPreset', () => {
  it('一致するプリセットを返し、期間指定・一致なしは null', () => {
    expect(matchPreset(presetRange('lastmonth', NOW), NOW)).toBe('lastmonth');
    expect(matchPreset({ unit: 'month', from: '2026-07-01', to: '2026-07-31' }, NOW)).toBeNull();
    expect(matchPreset({ unit: 'custom', from: '2026-09-01', to: '2026-09-30' }, NOW)).toBeNull();
  });
});

describe('rangeDays / formatRangeLabel', () => {
  it('日数は両端を含む', () => {
    expect(rangeDays('2026-09-01', '2026-09-30')).toBe(30);
    expect(rangeDays('2026-09-30', '2026-09-30')).toBe(1);
    expect(rangeDays('2026-07-01', '2026-09-30')).toBe(92);
  });

  it('ラベルは終わり側の年を省く', () => {
    expect(formatRangeLabel('2026-09-01', '2026-09-30')).toBe('2026/09/01 (火) 〜 09/30 (水)');
  });
});

describe('plannedRange / plannedMinutes', () => {
  it('秒つきの時刻も HH:MM に揃える', () => {
    const row = { start_time: '13:00:00', end_time: '13:40:00' };
    expect(plannedRange(row)).toBe('13:00–13:40');
    expect(plannedMinutes(row)).toBe(40);
  });

  it('予定外（null）は null', () => {
    const row = { start_time: null, end_time: null };
    expect(plannedRange(row)).toBeNull();
    expect(plannedMinutes(row)).toBeNull();
  });
});

describe('remarkBadges', () => {
  it('打刻の有無は state から、それ以外は BE の備考から作る', () => {
    expect(remarkBadges({ state: 'future', remarks: [] })).toEqual([
      { label: 'これから', tone: 'muted' },
    ]);
    expect(remarkBadges({ state: 'in_progress', remarks: null })).toEqual([
      { label: '訪問中', tone: 'success' },
    ]);
    expect(remarkBadges({ state: 'done', remarks: [] })).toEqual([]);
  });

  it('代行・予定外は短くし、元の文言を full に残す', () => {
    expect(
      remarkBadges({
        state: 'no_departure',
        remarks: ['退出なし', '予定外の訪問', '代行（予定: 川名 幸子）', 'QRなし'],
      }),
    ).toEqual([
      { label: '退出なし', tone: 'warning' },
      { label: '予定外', tone: 'unplanned', full: '予定外の訪問' },
      { label: '代行', tone: 'info', full: '代行（予定: 川名 幸子）' },
      { label: 'QRなし', tone: 'muted' },
    ]);
  });

  it('それ以外の備考（語彙が増えた場合を含む）は文言そのままで注意色', () => {
    expect(
      remarkBadges({ state: 'done', remarks: ['到着と退出が近い', '取消済みの予定に記録'] }),
    ).toEqual([
      { label: '到着と退出が近い', tone: 'warning' },
      { label: '取消済みの予定に記録', tone: 'warning' },
    ]);
  });

  it('「時刻調整」は「調整」に縮め、注意色にしない（BE の並びのまま）', () => {
    expect(remarkBadges({ state: 'done', remarks: ['時刻調整', '取消済みの予定に記録'] })).toEqual([
      { label: '調整', tone: 'adjust', full: '時刻調整' },
      { label: '取消済みの予定に記録', tone: 'warning' },
    ]);
  });

  it('備考に無くても、行のフラグが立っていれば「調整」を 1 つだけ出す', () => {
    expect(remarkBadges({ state: 'done', remarks: [], departure_manual: true })).toEqual([
      { label: '調整', tone: 'adjust' },
    ]);
    expect(
      remarkBadges({ state: 'done', remarks: ['時刻調整'], arrival_adjusted: true }),
    ).toHaveLength(1);
  });
});

describe('調整の日時', () => {
  it('合わせた日時は JST の M/D HH:MM。読めない値は空文字', () => {
    // 15:10Z = JST 翌日 00:10。
    expect(formatAdjustedAt('2026-09-29T15:10:00Z')).toBe('9/30 00:10');
    expect(formatAdjustedAt('x')).toBe('');
    expect(formatAdjustedAt(null)).toBe('');
  });
});
