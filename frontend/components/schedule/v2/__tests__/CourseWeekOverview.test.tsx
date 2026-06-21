/**
 * CourseWeekOverview — Wave 18 Phase B-6 テスト.
 *
 * - 全 (template × weekday) のセルが描画される
 * - 容量バッジ "x/N" が表示され、満杯では warning スタイル
 * - A-E コースは staffCountFor (スタッフ数連動) で「休」/定員を判定
 * - 曜日ヘッダクリックで onJumpToDay(wd) が呼ばれる
 *
 * スタッフ数連動 (auto-schedule 統一): A-E コードの template は
 * courseCodeIndex(A=0..E=4) < min(staffCount, courseCodesMax) なら開講 (定員6),
 * それ以外は「休」(定員0). 既存テストは staffCountFor で全 A-E を開講させる
 * ために十分な人数 (=5) を返すスタブを渡す.
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('@/components/ui/card', () => ({
  Card: ({
    children,
    className,
    ...rest
  }: {
    children: React.ReactNode;
    className?: string;
    [k: string]: unknown;
  }) => (
    <div className={className} {...rest}>
      {children}
    </div>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import { CourseWeekOverview, type WeekOverviewVisit } from '../CourseWeekOverview';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';

const baseTpl = {
  capacity_mon: 4,
  capacity_tue: 4,
  capacity_wed: 4,
  capacity_thu: 4,
  capacity_fri: 4,
  capacity_sat: 4,
  capacity_sun: 0,
  notes: null,
  created_at: '',
  updated_at: '',
  deleted_at: null,
};

function makeTemplate(id: string, label: string, officeId: string): CourseTemplateRead {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return { id, label, office_id: officeId, ...baseTpl } as any;
}

/** A-E を全開講させる十分なスタッフ数 (=5) を返すスタブ. */
const fullStaff = () => 5;

describe('CourseWeekOverview (B-6)', () => {
  it('templates が空のとき空表示', () => {
    render(
      <CourseWeekOverview
        templates={[]}
        officeNameById={new Map()}
        visits={[]}
        onJumpToDay={vi.fn()}
      />,
    );
    expect(screen.getByTestId('course-week-overview-empty')).toBeInTheDocument();
  });

  it('全 (template × weekday) セルが描画される', () => {
    const templates = [makeTemplate('tpl-A', 'A', 'o1'), makeTemplate('tpl-B', 'B', 'o1')];
    render(
      <CourseWeekOverview
        templates={templates}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffCountFor={fullStaff}
      />,
    );
    // 2 templates × 6 weekdays = 12 セル
    for (const tplId of ['tpl-A', 'tpl-B']) {
      for (const wd of [0, 1, 2, 3, 4, 5]) {
        expect(screen.getByTestId(`course-week-overview-cell-${tplId}-${wd}`)).toBeInTheDocument();
      }
    }
    // ヘッダー (officeName-label)
    expect(screen.getByTestId('course-week-overview-row-header-tpl-A')).toHaveTextContent('本店-A');
  });

  it('容量バッジが "x/N" で表示される', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-1',
        patient_id: 'p-1',
        patient_name: '田中',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
      },
      {
        id: 'v-2',
        patient_id: 'p-2',
        patient_name: '佐藤',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
      },
    ];
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        staffCountFor={fullStaff}
      />,
    );
    const badge = screen.getByTestId('course-week-overview-capacity-tpl-A-0');
    // W32 fix (37eb867): 上限ラベルは UI 上 "上限 6" 固定表示.
    expect(badge).toHaveTextContent('2 名 / 上限 6');
    // 患者氏名が描画される
    expect(screen.getByTestId('course-week-overview-name-v-1')).toHaveTextContent('田中');
    expect(screen.getByTestId('course-week-overview-name-v-2')).toHaveTextContent('佐藤');
  });

  it('スタッフ数連動: A-E は staff_count 不足の曜日で「休」表示・容量バッジは出ない', () => {
    // D コース (index=3) は staffCount<4 で休. staffCount=3 を返すスタブで月曜を休に.
    const tplD: CourseTemplateRead = makeTemplate('tpl-D', 'D', 'o1');
    render(
      <CourseWeekOverview
        templates={[tplD]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffCountFor={() => 3}
      />,
    );
    const cell = screen.getByTestId('course-week-overview-cell-tpl-D-0');
    expect(cell).toHaveTextContent('休');
    expect(screen.queryByTestId('course-week-overview-capacity-tpl-D-0')).not.toBeInTheDocument();
  });

  it('スタッフ数連動: 4 名出勤で D コース開講, 3 名で D 休 (回帰)', () => {
    const tplD: CourseTemplateRead = makeTemplate('tpl-D', 'D', 'o1');
    // 4 名 → D (index 3 < min(4,5)=4) 開講.
    const { unmount } = render(
      <CourseWeekOverview
        templates={[tplD]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffCountFor={() => 4}
      />,
    );
    const openCell = screen.getByTestId('course-week-overview-cell-tpl-D-0');
    expect(openCell).not.toHaveTextContent('休');
    // 開講時は容量バッジ (上限 6) が出る.
    expect(screen.getByTestId('course-week-overview-capacity-tpl-D-0')).toHaveTextContent('上限 6');
    unmount();

    // 3 名 → D (index 3 < min(3,5)=3 = false) 休.
    render(
      <CourseWeekOverview
        templates={[tplD]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffCountFor={() => 3}
      />,
    );
    const restCell = screen.getByTestId('course-week-overview-cell-tpl-D-0');
    expect(restCell).toHaveTextContent('休');
  });

  it('M系コースは staff_count に依らず静的 capacity を使う', () => {
    // M template: capacity_sat=0 → staffCount に依らず土曜は「休」.
    const tplM: CourseTemplateRead = makeTemplate('tpl-M', 'M', 'o1');
    const tplM0 = { ...tplM, capacity_sat: 0 };
    render(
      <CourseWeekOverview
        templates={[tplM0]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={vi.fn()}
        staffCountFor={() => 5}
      />,
    );
    // 土曜 (wd=5) は静的 capacity_sat=0 → 休.
    expect(screen.getByTestId('course-week-overview-cell-tpl-M-5')).toHaveTextContent('休');
    // 月曜 (wd=0) は静的 capacity_mon=4 → 開講.
    expect(screen.getByTestId('course-week-overview-cell-tpl-M-0')).not.toHaveTextContent('休');
  });

  it('曜日ヘッダクリックで onJumpToDay(wd) が呼ばれる', () => {
    const onJumpToDay = vi.fn();
    render(
      <CourseWeekOverview
        templates={[makeTemplate('tpl-A', 'A', 'o1')]}
        officeNameById={new Map([['o1', '本店']])}
        visits={[]}
        onJumpToDay={onJumpToDay}
      />,
    );
    fireEvent.click(screen.getByTestId('course-week-overview-header-3')); // 木
    expect(onJumpToDay).toHaveBeenCalledWith(3);
  });

  it('start_time 付き visit でも 2026-W20 以降は患者名のみ表示 (時刻は省略)', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-t1',
        patient_id: 'p-t1',
        patient_name: '山田',
        weekday: 1,
        course_template_id: 'tpl-A',
        start_time: '09:30:00',
      },
    ];
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        staffCountFor={fullStaff}
      />,
    );
    const el = screen.getByTestId('course-week-overview-name-v-t1');
    expect(el).toHaveTextContent('山田');
    // 2026-W20 後期: 週ビューは「氏名 + 開始時刻」を表示する仕様に更新.
    //   (旧仕様: 時刻は併記しなかった. ペアリング囲み + 時刻併記で情報密度を再評価.)
    expect(el).toHaveTextContent('09:30');
  });

  it('Phase G-53: 曜日ヘッダーに患者総数 (全コース合計) を表示する', () => {
    // 月曜 (wd=0): A に 2 名 + B に 1 名 = 全コース合計 3 名.
    const templates = [makeTemplate('tpl-A', 'A', 'o1'), makeTemplate('tpl-B', 'B', 'o1')];
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-a1',
        patient_id: 'p-a1',
        patient_name: '田中',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
      },
      {
        id: 'v-a2',
        patient_id: 'p-a2',
        patient_name: '佐藤',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
      },
      {
        id: 'v-b1',
        patient_id: 'p-b1',
        patient_name: '鈴木',
        weekday: 0,
        course_template_id: 'tpl-B',
        start_time: null,
      },
      // 火曜 (wd=1): A に 1 名.
      {
        id: 'v-a3',
        patient_id: 'p-a3',
        patient_name: '山田',
        weekday: 1,
        course_template_id: 'tpl-A',
        start_time: null,
      },
    ];
    render(
      <CourseWeekOverview
        templates={templates}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        staffCountFor={fullStaff}
        staffSummaryOffices={[{ id: 'o1', label: '本店' }]}
      />,
    );
    // G-54: 「患者 N / 受入可能数 名」形式。受入可能数 = 出勤スタッフ全員(S+M)×6.
    // fullStaff=5, manager 未指定=0 → 5×6=30.
    expect(screen.getByTestId('weekday-header-patients-0')).toHaveTextContent('患者 3 / 30 名');
    expect(screen.getByTestId('weekday-header-patients-1')).toHaveTextContent('患者 1 / 30 名');
    // 訪問が無い曜日は 0 名.
    expect(screen.getByTestId('weekday-header-patients-2')).toHaveTextContent('患者 0 / 30 名');
  });

  it('Phase G-54: 受入可能数 = 全拠点スタッフ(S+M)×6 で患者比率を表示', () => {
    // 月曜: 稲毛 S4 M1 + 都賀 S1 = 計6名 × 6 = 36. 患者 A:2 = 2 名 → 「患者 2 / 36 名」.
    const inageId = 'office-inage';
    const tsugaId = 'office-tsuga';
    const templates = [makeTemplate('tpl-A', 'A', inageId)];
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v1',
        patient_id: 'p1',
        patient_name: '田中',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
      },
      {
        id: 'v2',
        patient_id: 'p2',
        patient_name: '佐藤',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: null,
      },
    ];
    render(
      <CourseWeekOverview
        templates={templates}
        officeNameById={
          new Map([
            [inageId, '稲毛'],
            [tsugaId, '都賀'],
          ])
        }
        visits={visits}
        onJumpToDay={vi.fn()}
        staffCountFor={(officeId, wd) =>
          wd === 0 ? (officeId === inageId ? 4 : officeId === tsugaId ? 1 : 0) : 0
        }
        managerCountFor={(officeId, wd) => (wd === 0 && officeId === inageId ? 1 : 0)}
        staffSummaryOffices={[
          { id: inageId, label: '稲' },
          { id: tsugaId, label: '津' },
        ]}
      />,
    );
    expect(screen.getByTestId('weekday-header-patients-0')).toHaveTextContent('患者 2 / 36 名');
    // スタッフ0の曜日は 0 名 / 0 名.
    expect(screen.getByTestId('weekday-header-patients-1')).toHaveTextContent('患者 0 / 0 名');
  });

  it('Phase G-53: 曜日ヘッダーに拠点別 S/M を表示する (稲 S:4 M:1 / 津 S:1)', () => {
    const inageId = 'office-inage';
    const tsugaId = 'office-tsuga';
    const templates = [makeTemplate('tpl-A', 'A', inageId)];
    render(
      <CourseWeekOverview
        templates={templates}
        officeNameById={
          new Map([
            [inageId, '稲毛'],
            [tsugaId, '都賀'],
          ])
        }
        visits={[]}
        onJumpToDay={vi.fn()}
        staffCountFor={(officeId, wd) => {
          if (wd !== 0) return 0;
          if (officeId === inageId) return 4;
          if (officeId === tsugaId) return 1;
          return 0;
        }}
        managerCountFor={(officeId, wd) => {
          if (wd !== 0) return 0;
          if (officeId === inageId) return 1;
          return 0;
        }}
        staffSummaryOffices={[
          { id: inageId, label: '稲' },
          { id: tsugaId, label: '津' },
        ]}
      />,
    );
    // 月曜: 稲 S:4 M:1 / 津 S:1 (M=0 拠点は M 省略).
    expect(screen.getByTestId('weekday-header-staff-0')).toHaveTextContent('稲 S:4 M:1 / 津 S:1');
    // 出勤が無い火曜は拠点別 S/M ブロックを出さない.
    expect(screen.queryByTestId('weekday-header-staff-1')).not.toBeInTheDocument();
  });

  it('start_time が null の visit は氏名のみ表示', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v-t2',
        patient_id: 'p-t2',
        patient_name: '鈴木',
        weekday: 2,
        course_template_id: 'tpl-A',
        start_time: null,
      },
    ];
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        staffCountFor={fullStaff}
      />,
    );
    const el = screen.getByTestId('course-week-overview-name-v-t2');
    expect(el).toHaveTextContent('鈴木');
    expect(el).not.toHaveTextContent(':');
  });

  it('距離は「前の患者から」。拠点座標なしなら 1 人目は空欄・2 人目から表示', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v1',
        patient_id: 'p1',
        patient_name: '青柳',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: '09:30',
        lat: 35.6,
        lng: 140.1,
      },
      {
        id: 'v2',
        patient_id: 'p2',
        patient_name: '本田',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: '10:30',
        lat: 35.7,
        lng: 140.2,
      },
    ];
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        staffCountFor={fullStaff}
      />,
    );
    // コース合計距離 (セル右端) が出る。
    expect(screen.getByTestId('course-week-overview-distance-tpl-A-0')).toHaveTextContent(/km/);
    // 拠点座標なし → 1 人目は距離なし。
    expect(screen.queryByTestId('course-week-overview-visit-distance-v1')).toBeNull();
    // 2 人目は「前の患者(v1)から」の距離が出る。
    expect(screen.getByTestId('course-week-overview-visit-distance-v2')).toHaveTextContent(/km/);
  });

  it('拠点座標があれば 1 人目に「拠点からの距離」を表示する', () => {
    const tpl = makeTemplate('tpl-A', 'A', 'o1');
    const visits: WeekOverviewVisit[] = [
      {
        id: 'v1',
        patient_id: 'p1',
        patient_name: '青柳',
        weekday: 0,
        course_template_id: 'tpl-A',
        start_time: '09:30',
        lat: 35.6,
        lng: 140.1,
      },
    ];
    render(
      <CourseWeekOverview
        templates={[tpl]}
        officeNameById={new Map([['o1', '本店']])}
        visits={visits}
        onJumpToDay={vi.fn()}
        staffCountFor={fullStaff}
        officeLatLngById={new Map([['o1', { lat: 35.5, lng: 140.0 }]])}
      />,
    );
    // 1 人目に拠点→患者の距離が出る。
    expect(screen.getByTestId('course-week-overview-visit-distance-v1')).toHaveTextContent(/km/);
  });
});
