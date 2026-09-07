/**
 * courseDnd — droppable 名前空間と共通リゾルバ
 * (`docs/plans/dnd-all-views-design-2026-09-08.md` §2-1 / §4)。
 *
 * 検証:
 *   1. `sw-cell:` id の組み立て / 解析 (担当なし行を含む)
 *   2. `resolveDropTarget` が 3 種類の drop 先を 1 つの形に落とす
 *      - `sw-cell:` → 時刻なし・行スタッフあり (担当なし行は staffId=null)
 *      - `tl-col:`  → 既存のスナップ計算で時刻あり
 *      - それ以外 (プール等) → null
 */
import { describe, it, expect } from 'vitest';

import { timeToY } from '@/lib/scheduling/timeline';

import {
  buildStaffWeekCellDroppableId,
  parseStaffWeekCellDroppableId,
  resolveDropTarget,
  UNASSIGNED_ROW_KEY,
} from '../courseDnd';

const STAFF_ID = '00000000-0000-4000-8000-000000000001';
const TPL_ID = '00000000-0000-4000-8000-0000000000aa';

describe('sw-cell droppable id', () => {
  it('rowKey × weekday を往復できる', () => {
    const id = buildStaffWeekCellDroppableId(STAFF_ID, 3);
    expect(id).toBe(`sw-cell:${STAFF_ID}:3`);
    expect(parseStaffWeekCellDroppableId(id)).toEqual({ rowKey: STAFF_ID, weekday: 3 });
  });

  it('「（担当なし）」行も往復できる', () => {
    const id = buildStaffWeekCellDroppableId(UNASSIGNED_ROW_KEY, 0);
    expect(parseStaffWeekCellDroppableId(id)).toEqual({ rowKey: UNASSIGNED_ROW_KEY, weekday: 0 });
  });

  it('他の名前空間 / 壊れた id は null', () => {
    expect(parseStaffWeekCellDroppableId(`tl-col:${TPL_ID}:0`)).toBeNull();
    expect(parseStaffWeekCellDroppableId('pool')).toBeNull();
    expect(parseStaffWeekCellDroppableId('sw-cell:')).toBeNull();
    expect(parseStaffWeekCellDroppableId(`sw-cell:${STAFF_ID}`)).toBeNull();
    expect(parseStaffWeekCellDroppableId(`sw-cell:${STAFF_ID}:x`)).toBeNull();
    // 職員スケジュールは月〜土の 6 列だけ (日曜の列は無い)。
    expect(parseStaffWeekCellDroppableId(`sw-cell:${STAFF_ID}:6`)).toBeNull();
    expect(parseStaffWeekCellDroppableId(`sw-cell:${STAFF_ID}:9`)).toBeNull();
  });
});

describe('resolveDropTarget', () => {
  it('sw-cell: は時刻なし・行スタッフありで解決する', () => {
    const r = resolveDropTarget(buildStaffWeekCellDroppableId(STAFF_ID, 2), null, null);
    expect(r).toEqual({
      kind: 'sw-cell',
      weekday: 2,
      courseTemplateId: null,
      staffId: STAFF_ID,
      time: null,
    });
  });

  it('sw-cell: の「（担当なし）」行は staffId=null', () => {
    const r = resolveDropTarget(buildStaffWeekCellDroppableId(UNASSIGNED_ROW_KEY, 5), null, null);
    expect(r?.staffId).toBeNull();
    expect(r?.weekday).toBe(5);
  });

  it('tl-col: は既存のスナップ計算で時刻を決める', () => {
    const overTop = 10;
    const r = resolveDropTarget(
      `tl-col:${TPL_ID}:1`,
      { top: overTop + (timeToY('10:15') ?? 0) },
      { top: overTop },
    );
    expect(r).toEqual({
      kind: 'tl-col',
      weekday: 1,
      courseTemplateId: TPL_ID,
      staffId: null,
      time: '10:15',
    });
  });

  it('tl-col: は 15 分にスナップする', () => {
    // 10:20 相当の位置 → 10:15 へ丸まる (snapYOffsetToMinutes と同じ規則)。
    const r = resolveDropTarget(`tl-col:${TPL_ID}:0`, { top: timeToY('10:20') ?? 0 }, { top: 0 });
    expect(r?.time).toBe('10:15');
  });

  it('tl-col: で矩形が取れないときは null (列の上で離せていない)', () => {
    expect(resolveDropTarget(`tl-col:${TPL_ID}:0`, null, { top: 0 })).toBeNull();
    expect(resolveDropTarget(`tl-col:${TPL_ID}:0`, { top: 0 }, null)).toBeNull();
  });

  it('プールなど他の droppable は null', () => {
    expect(resolveDropTarget('pool', { top: 100 }, { top: 10 })).toBeNull();
    expect(resolveDropTarget('not-a-column', { top: 100 }, { top: 10 })).toBeNull();
  });
});
