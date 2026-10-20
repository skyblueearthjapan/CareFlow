/**
 * 職員の 1 日の件数 (schedule-unit-switch-design-2026-10-21.md §3) の表の試験。
 * 職員の名前は 職員A〜 (実名を書かない)。
 */
import { describe, expect, it } from 'vitest';

import { UNASSIGNED_ROW_KEY } from '@/components/schedule/v2/courseDnd';

import {
  countStaffDays,
  dayCountTone,
  formatDayCount,
  resolveVisitRowKey,
  staffDayCountKey,
  UNASSIGNED_COUNT_ROW_KEY,
  weekVisitTotal,
  type DayCountCompanion,
  type DayCountVisit,
} from '../staffDayCount';

const A = 'staff-a';
const B = 'staff-b';
const TODAY = '2026-10-21';

/** コース T1 の月曜の担当は 職員A。 */
const assigned = new Map<string, string>([
  ['T1:0', A],
  ['T2:0', B],
]);

let seq = 0;
function visit(over: Partial<DayCountVisit> = {}): DayCountVisit {
  seq += 1;
  return {
    id: `v${String(seq).padStart(3, '0')}`,
    patient_id: `p${seq}`,
    weekday: 0,
    course_template_id: 'T1',
    status: 'planned',
    visit_date: '2026-10-19',
    ...over,
  };
}

interface Row {
  name: string;
  visits: DayCountVisit[];
  companions?: DayCountCompanion[];
  groups?: [string, string][];
  /** 期待: rowKey → [件数, 人数, 同行]。 */
  expect: Record<string, [number, number, number]>;
}

const p1 = 'patient-1';
const rows: Row[] = [
  {
    name: 'コース担当の訪問 2 件は 職員A に 2 件',
    visits: [visit(), visit()],
    expect: { [A]: [2, 2, 0] },
  },
  {
    name: '取消は数えない (取消を表示に関係なく)',
    visits: [visit(), visit({ status: 'cancelled', source: 'manual_cancel' })],
    expect: { [A]: [1, 1, 0] },
  },
  {
    name: '患者ステータス連動の取消も数えない',
    visits: [visit(), visit({ status: 'cancelled', source: 'status_cancel' })],
    expect: { [A]: [1, 1, 0] },
  },
  {
    name: '非稼働 (入院中) の利用者の残った予定は数えない',
    visits: [
      visit(),
      visit({
        patient_status: 'admitted',
        patient_status_since: '2026-10-01',
        visit_date: '2026-10-22',
      }),
    ],
    expect: { [A]: [1, 1, 0] },
  },
  {
    name: '同じ方へ 1 日に 2 回は 2 件・1 名',
    visits: [visit({ patient_id: p1 }), visit({ patient_id: p1 }), visit()],
    expect: { [A]: [3, 2, 0] },
  },
  {
    name: '訪問自身の担当が コース担当より先',
    visits: [visit({ primary_staff_id: B })],
    expect: { [B]: [1, 1, 0] },
  },
  {
    name: '担当なし (コース担当も無い) は担当なし行',
    visits: [visit({ course_template_id: 'T9' })],
    expect: { [UNASSIGNED_COUNT_ROW_KEY]: [1, 1, 0] },
  },
  {
    name: '明示の担当なし (primary 空＋manual) はコース担当に読み替えない',
    visits: [visit({ primary_staff_id: null, manual_staff_override: true })],
    expect: { [UNASSIGNED_COUNT_ROW_KEY]: [1, 1, 0] },
  },
  {
    name: '休みで担当なしへ移ったものも担当なし行 (職員A の件数から外れる)',
    visits: [visit(), visit({ primary_staff_id: null, manual_staff_override: true })],
    expect: { [A]: [1, 1, 0], [UNASSIGNED_COUNT_ROW_KEY]: [1, 1, 0] },
  },
  {
    name: '同行は本人の件数に入れず ＋同行 に数える',
    visits: [visit({ id: 'acc-1' })],
    companions: [{ staffId: B, visitId: 'acc-1' }],
    expect: { [A]: [1, 1, 0], [B]: [0, 0, 1] },
  },
  {
    name: '取消の訪問への同行は数えない',
    visits: [visit({ id: 'acc-2', status: 'cancelled' })],
    companions: [{ staffId: B, visitId: 'acc-2' }],
    expect: {},
  },
  {
    name: '2 名体制は 1 人目が 1 件・2 人目は ＋同行',
    visits: [
      visit({ id: 'g-1', patient_id: p1 }),
      visit({ id: 'g-2', patient_id: p1, course_template_id: 'T2' }),
    ],
    groups: [
      ['g-1', 'grp'],
      ['g-2', 'grp'],
    ],
    expect: { [A]: [1, 1, 0], [B]: [0, 0, 1] },
  },
  {
    name: '2 名体制で片方が担当なしなら、担当のある方が 1 人目・担当なしも 1 件',
    visits: [
      visit({ id: 'h-1', patient_id: p1, course_template_id: 'T9' }),
      visit({ id: 'h-2', patient_id: p1 }),
    ],
    groups: [
      ['h-1', 'grp2'],
      ['h-2', 'grp2'],
    ],
    expect: { [A]: [1, 1, 0], [UNASSIGNED_COUNT_ROW_KEY]: [1, 1, 0] },
  },
  {
    name: '同じ建物の別の方は 2 件',
    visits: [visit({ patient_id: 'bld-1' }), visit({ patient_id: 'bld-2' })],
    expect: { [A]: [2, 2, 0] },
  },
];

describe('countStaffDays (表)', () => {
  it.each(rows)('$name', ({ visits, companions, groups, expect: want }) => {
    const counts = countStaffDays(visits, {
      assignedStaffByTemplateWeekday: assigned,
      companions,
      groupIdByVisitId: groups ? new Map(groups) : undefined,
      today: TODAY,
    });
    const got: Record<string, [number, number, number]> = {};
    for (const [key, c] of counts) {
      const rowKey = key.slice(0, key.lastIndexOf(':'));
      got[rowKey] = [c.visits, c.patients, c.companions];
    }
    expect(got).toEqual(want);
  });
});

describe('行の帰属', () => {
  it('担当なしのキーは盤面の UNASSIGNED_ROW_KEY と同じ', () => {
    expect(UNASSIGNED_COUNT_ROW_KEY).toBe(UNASSIGNED_ROW_KEY);
  });
  it.each([
    [{ primary_staff_id: B }, B],
    [{ primary_staff_id: null }, A],
    [{ primary_staff_id: null, manual_staff_override: true }, UNASSIGNED_ROW_KEY],
    [{ course_template_id: 'T9' }, UNASSIGNED_ROW_KEY],
  ] as const)('%o → %s', (over, want) => {
    expect(resolveVisitRowKey({ ...visit(), ...over }, assigned)).toBe(want);
  });
});

describe('週の合計', () => {
  it('月〜土の本人の件数を足す (同行は入れない)', () => {
    const counts = countStaffDays(
      [visit(), visit({ weekday: 2, primary_staff_id: A }), visit({ id: 'c', weekday: 0 })],
      {
        assignedStaffByTemplateWeekday: assigned,
        companions: [{ staffId: B, visitId: 'c' }],
        today: TODAY,
      },
    );
    expect(weekVisitTotal(counts, A)).toBe(3);
    expect(weekVisitTotal(counts, B)).toBe(0);
    expect(counts.get(staffDayCountKey(B, 0))?.companions).toBe(1);
  });
});

describe('書き方と色', () => {
  it.each([
    [{ visits: 6, patients: 6 }, '6件'],
    [{ visits: 6, patients: 5 }, '6件（5名）'],
    [{ visits: 0, patients: 0 }, '0件'],
  ])('%o → %s', (c, want) => {
    expect(formatDayCount(c)).toBe(want);
  });
  it.each([
    [5, 6, 'normal'],
    [6, 6, 'full'],
    [7, 6, 'over'],
    [7, null, 'plain'],
    [7, undefined, 'plain'],
    [3, 0, 'plain'],
  ] as const)('%d 件・上限 %s → %s', (n, limit, want) => {
    expect(dayCountTone(n, limit)).toBe(want);
  });
});
