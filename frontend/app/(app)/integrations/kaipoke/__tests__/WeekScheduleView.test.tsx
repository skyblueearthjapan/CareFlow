/**
 * WeekScheduleView — カード意匠統一 (性別ウォッシュ) の回帰テスト。
 *
 * 本体スケジュール (TimelineDayList) と同じ視覚言語:
 *   - patientSex=female の行カードは genderPalette('female').bg を inline style で塗る
 *   - patientSex 未設定 (null) は genderPalette(null) の中立 (砂色) ウォッシュ
 *
 * WeekScheduleView は props 駆動 (クエリ非依存) なのでモック不要。
 */
import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';

import type { WeekScheduleRow } from '@/lib/schemas/integration';
import { genderPalette } from '@/lib/scheduling/timeline';

import { WeekScheduleView } from '../_components/WeekScheduleView';

function row(overrides: Partial<WeekScheduleRow>): WeekScheduleRow {
  return {
    visitDate: '2026-07-06',
    weekday: 0,
    startTime: '09:00',
    endTime: '09:40',
    patientName: '患者',
    patientSex: null,
    staff1: '',
    staff2: '',
    courseCode: 'A',
    officeName: '稲毛',
    ...overrides,
  };
}

describe('WeekScheduleView カード意匠統一', () => {
  it('patientSex=female の行は genderPalette(female).bg を inline style で塗る', () => {
    const rows: WeekScheduleRow[] = [
      row({ patientName: '女性 花子', patientSex: 'female', startTime: '09:00' }),
      row({ patientName: '未設定 太郎', patientSex: null, startTime: '10:00' }),
    ];
    const { getByTestId } = render(
      <WeekScheduleView weekStart={new Date('2026-07-06')} rows={rows} />,
    );

    // 同一セル (コースA・月曜) に開始時刻順で 2 行。
    const femaleCard = getByTestId('wsv-visit-A-0-0');
    const neutralCard = getByTestId('wsv-visit-A-0-1');

    expect(femaleCard.style.background).toBe(genderPalette('female').bg);
    expect(neutralCard.style.background).toBe(genderPalette(null).bg);
    // female と中立で塗り分けられている (= sex 結線が効いている)。
    expect(femaleCard.style.background).not.toBe(neutralCard.style.background);
  });
});
