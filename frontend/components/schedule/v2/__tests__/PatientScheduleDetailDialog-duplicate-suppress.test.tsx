/**
 * PatientScheduleDetailDialog — 2026-W20 後期 Task D 重複抑制ロジックのテスト.
 *
 * 純関数 `buildDiff` + `isDuplicateEntry` で重複判定を検証する.
 * (React rendering を介さず, 重複ロジックの contract を直接テストする方が安定.
 *  full dialog rendering 経路は手動 QA / e2e で確認する.)
 *
 * シナリオ:
 *   D-1. 固定枠 vs 今週 visit が同一 → isDuplicateEntry=true
 *   D-2. 時刻が異なる → differs=true / isDuplicateEntry=false
 *   D-3. duration が異なる → differs=true
 *   D-4. course_template_id が異なる (両方非 null) → differs=true
 *   D-5. fixed のみ / week のみ → differs=true (XOR), isDuplicateEntry=false
 *   D-6. 同じ曜日に複数 visit/PFV がある場合は最早 start_time を採用
 */
import { describe, it, expect } from 'vitest';

import {
  buildDiff,
  groupByWeekday,
  isDuplicateEntry,
  pfvToCell,
  visitToCell,
  type WeekdayCell,
} from '../PatientScheduleDetailDialog';

// ヘルパー: WeekdayCell を直接組み立てる (PFV / Visit 経由を省略してテストを簡潔に).
function cell(
  weekday: number,
  start: string,
  durationMin: number,
  courseTemplateId: string | null = null,
): WeekdayCell {
  return {
    weekday,
    start_time: start,
    end_time: null,
    duration_min: durationMin,
    course_template_id: courseTemplateId,
  };
}

function mapOf(...cells: WeekdayCell[]): Map<number, WeekdayCell> {
  const m = new Map<number, WeekdayCell>();
  for (const c of cells) m.set(c.weekday, c);
  return m;
}

describe('PatientScheduleDetailDialog 重複抑制 (Task D)', () => {
  it('D-1: 固定枠 = 今週 (同 weekday + start_time + duration_min) → isDuplicateEntry=true', () => {
    const fixed = mapOf(cell(0, '10:00', 60));
    const week = mapOf(cell(0, '10:00', 60));
    const diff = buildDiff(fixed, week);
    expect(diff).toHaveLength(1);
    expect(diff[0]!.differs).toBe(false);
    expect(isDuplicateEntry(diff[0]!)).toBe(true);
  });

  it('D-2: start_time が違う → differs=true / isDuplicateEntry=false', () => {
    const fixed = mapOf(cell(0, '10:00', 60));
    const week = mapOf(cell(0, '11:00', 60));
    const diff = buildDiff(fixed, week);
    expect(diff[0]!.differs).toBe(true);
    expect(isDuplicateEntry(diff[0]!)).toBe(false);
  });

  it('D-3: duration_min が違う → differs=true', () => {
    const fixed = mapOf(cell(0, '10:00', 60));
    const week = mapOf(cell(0, '10:00', 45));
    const diff = buildDiff(fixed, week);
    expect(diff[0]!.differs).toBe(true);
    expect(isDuplicateEntry(diff[0]!)).toBe(false);
  });

  it('D-4: course_template_id が両方非 null かつ違う → differs=true', () => {
    const fixed = mapOf(cell(0, '10:00', 60, 'tpl-A'));
    const week = mapOf(cell(0, '10:00', 60, 'tpl-B'));
    const diff = buildDiff(fixed, week);
    expect(diff[0]!.differs).toBe(true);
  });

  it('D-4b: course_template_id 片方 null → differs=false (visit 側 null は許容)', () => {
    const fixed = mapOf(cell(0, '10:00', 60, 'tpl-A'));
    const week = mapOf(cell(0, '10:00', 60, null));
    const diff = buildDiff(fixed, week);
    expect(diff[0]!.differs).toBe(false);
    expect(isDuplicateEntry(diff[0]!)).toBe(true);
  });

  it('D-5: fixed のみある (week なし) → differs=true / isDuplicateEntry=false', () => {
    const fixed = mapOf(cell(0, '10:00', 60));
    const week = new Map<number, WeekdayCell>();
    const diff = buildDiff(fixed, week);
    expect(diff[0]!.differs).toBe(true);
    expect(isDuplicateEntry(diff[0]!)).toBe(false);
  });

  it('D-5b: week のみある (fixed なし) → differs=true / isDuplicateEntry=false', () => {
    const fixed = new Map<number, WeekdayCell>();
    const week = mapOf(cell(0, '10:00', 60));
    const diff = buildDiff(fixed, week);
    expect(diff[0]!.differs).toBe(true);
    expect(isDuplicateEntry(diff[0]!)).toBe(false);
  });

  it('D-6: 同曜日に複数 visit がある場合 groupByWeekday は最早 start_time を採用', () => {
    const grouped = groupByWeekday([
      cell(0, '11:00', 30),
      cell(0, '09:00', 30),
      cell(0, '10:00', 30),
    ]);
    expect(grouped.get(0)?.start_time).toBe('09:00');
  });

  it('複数曜日にまたがる diff: 1 つ differ + 2 つ duplicate', () => {
    const fixed = mapOf(
      cell(0, '09:00', 60), // 月: dup
      cell(1, '10:00', 60), // 火: dup
      cell(2, '11:00', 60), // 水: 時刻違い
    );
    const week = mapOf(
      cell(0, '09:00', 60),
      cell(1, '10:00', 60),
      cell(2, '12:00', 60), // 水だけ違う
    );
    const diff = buildDiff(fixed, week);
    expect(diff).toHaveLength(3);
    expect(isDuplicateEntry(diff[0]!)).toBe(true); // 月
    expect(isDuplicateEntry(diff[1]!)).toBe(true); // 火
    expect(isDuplicateEntry(diff[2]!)).toBe(false); // 水
    expect(diff[2]!.differs).toBe(true);
  });
});

describe('pfvToCell / visitToCell (T-helpers)', () => {
  it('pfvToCell: PatientFixedVisitV2Read → WeekdayCell (duration_min が無い場合 30 fallback)', () => {
    const pfv = {
      weekday: 0,
      start_time: '09:00:00',
      duration_min: 60,
      course_template_id: 'tpl-1',
    } as unknown as Parameters<typeof pfvToCell>[0];
    const c = pfvToCell(pfv);
    expect(c.weekday).toBe(0);
    expect(c.start_time).toBe('09:00');
    expect(c.end_time).toBeNull();
    expect(c.duration_min).toBe(60);
    expect(c.course_template_id).toBe('tpl-1');
  });

  it('visitToCell: VisitRead → WeekdayCell (visit_date から weekday を導出)', () => {
    // 2024-12-30 (Mon) → weekday=0
    const v = {
      visit_date: '2024-12-30',
      start_time: '10:00:00',
      end_time: '11:00:00',
    } as unknown as Parameters<typeof visitToCell>[0];
    const c = visitToCell(v);
    expect(c.weekday).toBe(0);
    expect(c.start_time).toBe('10:00');
    expect(c.end_time).toBe('11:00');
    expect(c.duration_min).toBe(60);
  });
});
