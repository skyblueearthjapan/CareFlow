/**
 * CourseDayTable / WeekdayScheduleCard 経由のペア囲み描画テスト
 * (2026-W20 後期 /schedule UX 改修 — Task E).
 *
 * 旧挙動: 同住所 patient 1 人ずつに `border-l-2 bg-yellow-50/60` が付き、
 *   2 ペア (4 人) だと 4 個の独立した黄色囲みに見える問題.
 * 新挙動: 同 same_address_group_id を持つ連続 visit を 1 つの大きな黄色囲み
 *   (`border-2 border-yellow-400`) で wrap する.
 *
 * 検証:
 *   - 2 ペア (A B, C D) 入力 → pair-cluster 要素が 2 個描画される
 *   - 各 cluster の data-pair-size=2, ヘッダー「📍 同住所 (2 名)」を表示
 *   - 単独 patient は cluster の外 (= 別 li)
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

import {
  WeekdayScheduleCard,
  type CourseListItem,
} from '@/components/schedule/WeekdayScheduleCard';

function makeCourse(visits: { key: string; gid: string | null; addr?: string }[]): CourseListItem {
  return {
    key: 'course-1',
    title: '本店 A コース',
    summary: `${visits.length}件`,
    visits: visits.map((v, i) => ({
      key: v.key,
      patient_id: v.key,
      patient_name: `患者${v.key}`,
      start_time: `09:${String(i * 15).padStart(2, '0')}`,
      address: v.addr ?? '住所X',
      same_address_group_id: v.gid,
    })),
  };
}

describe('WeekdayScheduleCard ペア囲み統合 (Task E)', () => {
  it('2 ペア 4 人 → pair-cluster 要素は 2 個描画される', () => {
    const course = makeCourse([
      { key: 'a', gid: 'g1' },
      { key: 'b', gid: 'g1' },
      { key: 'c', gid: 'g2' },
      { key: 'd', gid: 'g2' },
    ]);
    render(<WeekdayScheduleCard title="月曜 コース一覧" courses={[course]} testIdPrefix="t" />);

    // pair cluster wrapper × 2.
    const clusters = screen.getAllByTestId(/^t-pair-cluster-/);
    expect(clusters).toHaveLength(2);
    // それぞれ data-pair-size=2.
    for (const c of clusters) {
      expect(c.getAttribute('data-pair-size')).toBe('2');
    }
    // ヘッダー「📍 同住所 (2 名)」が 2 つ表示される.
    expect(screen.getAllByText(/📍 同住所 \(2 名\)/)).toHaveLength(2);
  });

  it('単独 patient は pair-cluster の外で描画される', () => {
    const course = makeCourse([
      { key: 'a', gid: 'g1' },
      { key: 'b', gid: 'g1' },
      { key: 'lonely', gid: null },
    ]);
    render(<WeekdayScheduleCard title="月曜 コース一覧" courses={[course]} testIdPrefix="t" />);

    const clusters = screen.getAllByTestId(/^t-pair-cluster-/);
    expect(clusters).toHaveLength(1);
    expect(clusters[0]!.getAttribute('data-pair-size')).toBe('2');
    // 単独患者 (lonely) の row は cluster の外に描画される.
    const lonelyRow = screen.getByTestId('t-visit-lonely');
    expect(lonelyRow).toBeTruthy();
    // 単独 row は data-pair-member 属性を持たない (= cluster 内 row には付く).
    expect(lonelyRow.getAttribute('data-pair-member')).toBeNull();
  });

  it('3 名同 group_id → 先頭 2 名がペア + 残り 1 名は単独描画', () => {
    const course = makeCourse([
      { key: 'a', gid: 'g1' },
      { key: 'b', gid: 'g1' },
      { key: 'c', gid: 'g1' },
    ]);
    render(<WeekdayScheduleCard title="月曜 コース一覧" courses={[course]} testIdPrefix="t" />);

    const clusters = screen.getAllByTestId(/^t-pair-cluster-/);
    expect(clusters).toHaveLength(1);
    expect(clusters[0]!.getAttribute('data-pair-size')).toBe('2');
    // 残った C は単独として描画 (= pair-member 属性を持たない).
    const lonelyRow = screen.getByTestId('t-visit-c');
    expect(lonelyRow.getAttribute('data-pair-member')).toBeNull();
  });

  it('weekView=true でも pair cluster は維持される (Task C)', () => {
    const course = makeCourse([
      { key: 'a', gid: 'g1' },
      { key: 'b', gid: 'g1' },
    ]);
    render(
      <WeekdayScheduleCard title="月曜 コース一覧" courses={[course]} testIdPrefix="t" weekView />,
    );

    const clusters = screen.getAllByTestId(/^t-pair-cluster-/);
    expect(clusters).toHaveLength(1);
  });
});
