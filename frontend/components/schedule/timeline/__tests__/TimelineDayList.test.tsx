import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CourseListItem, VisitListItem } from '@/components/schedule/WeekdayScheduleCard';
import { TimelineDayList } from '@/components/schedule/timeline/TimelineDayList';
import { genderPalette } from '@/lib/scheduling/timeline';
import type { AccompanimentBinding } from '@/components/schedule/timeline/accompaniment/types';

/** 新人同行 binding を手組み (§7.2)。over で個別/コース名を差し込む。 */
function makeBinding(over: Partial<AccompanimentBinding> = {}): AccompanimentBinding {
  return {
    active: false,
    isCourseSelected: () => false,
    isVisitSelected: () => false,
    isVisitInSelectedCourse: () => false,
    isVisitOverlapping: () => false,
    toggleCourse: vi.fn(),
    toggleVisit: vi.fn(),
    visitBadgeName: () => null,
    courseBadgeName: () => null,
    resolveCourseId: (t, wd) => `${t}:${wd}`,
    ...over,
  };
}

function v(over: Partial<VisitListItem> & { key: string }): VisitListItem {
  return {
    patient_id: `p-${over.key}`,
    start_time: '09:30:00',
    patient_name: `患者${over.key}`,
    ...over,
  } as VisitListItem;
}

function course(over: Partial<CourseListItem> & { key: string }): CourseListItem {
  return {
    title: '稲毛 A コース',
    office_name: '稲毛',
    course_code: 'A',
    staff_name: '田中 一郎',
    visits: [],
    capacity: { filled: 0, max: 6 },
    ...over,
  } as CourseListItem;
}

describe('TimelineDayList', () => {
  it('グループ見出しに拠点+コード・担当者名・n/N件を出す', () => {
    render(
      <TimelineDayList
        courses={[
          course({ key: 'c1', capacity: { filled: 2, max: 6 }, visits: [v({ key: 'a' })] }),
        ]}
      />,
    );
    expect(screen.getByText('稲毛A')).toBeInTheDocument();
    expect(screen.getByText('田中 一郎')).toBeInTheDocument();
    expect(screen.getByText('2/6件')).toBeInTheDocument();
  });

  it('列見出し(時刻/利用者/時間/住所 など)を出す', () => {
    render(<TimelineDayList courses={[course({ key: 'c1', visits: [v({ key: 'a' })] })]} />);
    expect(screen.getByText('時刻')).toBeInTheDocument();
    expect(screen.getByText('利用者')).toBeInTheDocument();
    expect(screen.getByText('住所 / 条件')).toBeInTheDocument();
  });

  it('訪問行に氏名・実動時間・住所を出す', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [
              v({
                key: 'a',
                patient_name: '青柳 あい',
                duration_min: 35,
                address: '稲毛区小仲台2-5',
              }),
            ],
          }),
        ]}
      />,
    );
    expect(screen.getByText('青柳 あい')).toBeInTheDocument();
    expect(screen.getByText('35 分')).toBeInTheDocument();
    expect(screen.getByText(/稲毛区小仲台/)).toBeInTheDocument();
  });

  it('患者名クリックで onPatientClick を呼ぶ', () => {
    const onClick = vi.fn();
    render(
      <TimelineDayList
        courses={[course({ key: 'c1', visits: [v({ key: 'a' })] })]}
        onPatientClick={onClick}
      />,
    );
    screen.getByText('患者a').click();
    expect(onClick).toHaveBeenCalledWith('p-a');
  });

  it('空きがあり容量に余裕があれば空き枠行を時刻順に差し込む', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            capacity: { filled: 1, max: 6 },
            visits: [v({ key: 'a', start_time: '09:30:00', duration_min: 35 })],
            freeGaps: [{ startMin: 13 * 60, endMin: 14 * 60, label: '13:00〜14:00' }],
          }),
        ]}
      />,
    );
    expect(screen.getByTestId('tdl-gap-780')).toBeInTheDocument();
    expect(screen.getByText(/空き時間/)).toBeInTheDocument();
  });

  it('満員(remaining<=0)のときは空き枠を出さない', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            capacity: { filled: 6, max: 6 },
            visits: [v({ key: 'a', start_time: '09:30:00', duration_min: 35 })],
            freeGaps: [{ startMin: 13 * 60, endMin: 14 * 60, label: '13:00〜14:00' }],
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId('tdl-gap-780')).toBeNull();
  });

  it('連続する同住所の2名は琥珀の囲みにまとまる (M-1)', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [
              v({ key: 'sa1', patient_name: '安永 一', same_address_group_id: 'g1' }),
              v({ key: 'sa2', patient_name: '菅原 二', same_address_group_id: 'g1' }),
              v({ key: 'solo', patient_name: '田中 三' }),
            ],
          }),
        ]}
      />,
    );
    const box = screen.getByTestId('tdl-sameaddr-0');
    expect(box).toBeInTheDocument();
    // 見出し行は廃止 (高さ節約)。内訳は囲みの title と各行の📍チップで伝える。
    expect(box.getAttribute('title')).toContain('同住所（2名');
    expect(screen.getAllByText('📍同住所')).toHaveLength(2);
  });

  it('移動警告のある行は薄い赤背景＋行ツールチップになる (M-2)', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [
              v({
                key: 'w',
                warnings: [{ type: 'travel_time_shortage', message: '移動が厳しい' }],
              }),
            ],
          }),
        ]}
      />,
    );
    const row = screen.getByTestId('tdl-row-w');
    expect(row.className).toContain('bg-error-bg/40');
    expect(row.getAttribute('title')).toBe('移動が厳しい');
  });

  it('訪問行は列見出しと同じ6列グリッドで1行に並ぶ (縦積み回帰防止)', () => {
    render(<TimelineDayList courses={[course({ key: 'c1', visits: [v({ key: 'a' })] })]} />);
    // grid-template-columns が無いと display:grid は1列になり、セルが縦に積まれてしまう。
    expect(screen.getByTestId('tdl-row-a').className).toContain('grid-cols-[');
  });

  it('性別ドット/左帯の色が患者性別で変わる', () => {
    render(
      <TimelineDayList
        courses={[
          course({
            key: 'c1',
            visits: [v({ key: 'm', patient_sex: 'male' }), v({ key: 'f', patient_sex: 'female' })],
          }),
        ]}
      />,
    );
    const rm = screen.getByTestId('tdl-row-m');
    const rf = screen.getByTestId('tdl-row-f');
    expect(rm.style.borderLeftColor).toBe(genderPalette('male').bar);
    expect(rf.style.borderLeftColor).toBe(genderPalette('female').bar);
  });

  // ─── G2: 訪問削除 (行末の ×) ────────────────────────────────────────────
  describe('G2. 訪問削除 (×)', () => {
    it('× クリックで onDeleteVisit(visitId, patientName) が呼ばれ、患者名クリックは発火しない', () => {
      const onDelete = vi.fn();
      const onPatientClick = vi.fn();
      render(
        <TimelineDayList
          courses={[
            course({
              key: 'c1',
              visits: [v({ key: 'a', visit_id: 'vis-a', patient_name: '青柳 あい' })],
            }),
          ]}
          onPatientClick={onPatientClick}
          onDeleteVisit={onDelete}
        />,
      );
      screen.getByTestId('tdl-delete-visit-a').click();
      expect(onDelete).toHaveBeenCalledWith('vis-a', '青柳 あい');
      expect(onPatientClick).not.toHaveBeenCalled();
    });

    it('onDeleteVisit 未指定 (read-only) / visit_id 欠落では × を出さない', () => {
      const { rerender } = render(
        <TimelineDayList
          courses={[course({ key: 'c1', visits: [v({ key: 'a', visit_id: 'vis-a' })] })]}
        />,
      );
      expect(screen.queryByTestId('tdl-delete-visit-a')).toBeNull();
      rerender(
        <TimelineDayList
          courses={[course({ key: 'c1', visits: [v({ key: 'a' })] })]}
          onDeleteVisit={vi.fn()}
        />,
      );
      expect(screen.queryByTestId('tdl-delete-visit-a')).toBeNull();
    });
  });

  // ─── G3: 「今週のみ」チップ → 固定昇格 ──────────────────────────────────
  describe('G3. 「今週のみ」チップ', () => {
    it('source=manual_week のときチップが出て、クリック (confirm OK) で onPromoteWeekOnly が呼ばれる', () => {
      const onPromote = vi.fn();
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      render(
        <TimelineDayList
          courses={[
            course({
              key: 'c1',
              visits: [v({ key: 'a', source: 'manual_week', patient_name: '青柳 あい' })],
            }),
          ]}
          onPromoteWeekOnly={onPromote}
        />,
      );
      screen.getByTestId('tdl-week-only-chip-a').click();
      expect(onPromote).toHaveBeenCalledWith('p-a', '青柳 あい');
      confirmSpy.mockRestore();
    });

    it('confirm キャンセルでは昇格しない / source が違えばチップを出さない', () => {
      const onPromote = vi.fn();
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
      const { rerender } = render(
        <TimelineDayList
          courses={[course({ key: 'c1', visits: [v({ key: 'a', source: 'manual_week' })] })]}
          onPromoteWeekOnly={onPromote}
        />,
      );
      screen.getByTestId('tdl-week-only-chip-a').click();
      expect(onPromote).not.toHaveBeenCalled();
      confirmSpy.mockRestore();

      rerender(
        <TimelineDayList
          courses={[course({ key: 'c1', visits: [v({ key: 'a', source: 'allocate' })] })]}
          onPromoteWeekOnly={onPromote}
        />,
      );
      expect(screen.queryByTestId('tdl-week-only-chip-a')).toBeNull();
    });

    it('onPromoteWeekOnly 未指定 (read-only) では非クリックの表示専用チップ', () => {
      render(
        <TimelineDayList
          courses={[course({ key: 'c1', visits: [v({ key: 'a', source: 'manual_week' })] })]}
        />,
      );
      expect(screen.getByTestId('tdl-week-only-chip-a').tagName).toBe('SPAN');
    });
  });

  /** T-6撤去: 旧テーブルの ①/② と「相方: ...」注記が日リストへ移設されたことの担保。 */
  describe('2名体制の相方情報 (T-6撤去に伴う移設)', () => {
    it('相方が別セルなら通常色、プール残存なら警告色で出す', () => {
      const { rerender } = render(
        <TimelineDayList
          courses={[
            course({
              key: 'c1',
              visits: [
                v({
                  key: 'a',
                  group_slot_label: 1,
                  partner_location: { kind: 'cell', cellLabel: '本店-A', time: '15:00' },
                }),
              ],
            }),
          ]}
        />,
      );
      expect(screen.getByTestId('tdl-slot-mark-a')).toHaveTextContent('①');
      expect(screen.getByTestId('tdl-partner-note-a')).toHaveTextContent('相方: 本店-A 15:00');
      expect(screen.getByTestId('tdl-partner-note-a').className).not.toContain('text-error');

      rerender(
        <TimelineDayList
          courses={[
            course({
              key: 'c1',
              visits: [v({ key: 'a', group_slot_label: 1, partner_location: { kind: 'pool' } })],
            }),
          ]}
        />,
      );
      const note = screen.getByTestId('tdl-partner-note-a');
      expect(note).toHaveTextContent('複数 ① のみ (相方未配置)');
      expect(note.className).toContain('text-error');
    });

    it('通常患者には ①/② も注記も出ない', () => {
      render(<TimelineDayList courses={[course({ key: 'c1', visits: [v({ key: 'a' })] })]} />);
      expect(screen.queryByTestId('tdl-slot-mark-a')).toBeNull();
      expect(screen.queryByTestId('tdl-partner-note-a')).toBeNull();
    });
  });

  // ─── 新人同行 (§7.2): チップ表示 ──────────────────────────────────────
  describe('新人同行チップ (案: 「👥新人名」info チップ)', () => {
    it('患者行: visitBadgeName があれば「👥新人名」チップ (title に全文)', () => {
      render(
        <TimelineDayList
          courses={[
            course({
              key: 'tpl-A:0',
              visits: [v({ key: 'a', visit_id: 'vis-a', patient_name: '青柳 あい' })],
            }),
          ]}
          accompaniment={makeBinding({
            visitBadgeName: (id) => (id === 'vis-a' ? '髙梨' : null),
          })}
        />,
      );
      const chip = screen.getByTestId('tdl-accompaniment-a');
      expect(chip).toHaveTextContent('髙梨');
      expect(chip.getAttribute('title')).toBe('同行: 髙梨（新人）');
    });

    it('コース見出し: courseBadgeName があれば見出しに「👥新人名」チップ', () => {
      render(
        <TimelineDayList
          courses={[
            course({
              key: 'tpl-A:0',
              course_template_id: 'tpl-A',
              weekday: 0,
              visits: [v({ key: 'a', visit_id: 'vis-a' })],
            }),
          ]}
          accompaniment={makeBinding({
            // resolveCourseId('tpl-A', 0) = 'tpl-A:0'.
            courseBadgeName: (cid) => (cid === 'tpl-A:0' ? '川名' : null),
          })}
        />,
      );
      const chip = screen.getByTestId('tdl-course-accompaniment-tpl-A:0');
      expect(chip).toHaveTextContent('川名');
      expect(chip.getAttribute('title')).toBe('同行: 川名（新人）');
    });

    it('accompaniment 未指定ならチップを出さない', () => {
      render(
        <TimelineDayList
          courses={[
            course({
              key: 'tpl-A:0',
              course_template_id: 'tpl-A',
              weekday: 0,
              visits: [v({ key: 'a', visit_id: 'vis-a' })],
            }),
          ]}
        />,
      );
      expect(screen.queryByTestId('tdl-accompaniment-a')).toBeNull();
      expect(screen.queryByTestId('tdl-course-accompaniment-tpl-A:0')).toBeNull();
    });

    it('同行モード中 (active) はリストにチップを出さない (選択UIはタイムライン専用)', () => {
      render(
        <TimelineDayList
          courses={[
            course({
              key: 'tpl-A:0',
              course_template_id: 'tpl-A',
              weekday: 0,
              visits: [v({ key: 'a', visit_id: 'vis-a' })],
            }),
          ]}
          accompaniment={makeBinding({
            active: true,
            visitBadgeName: () => '髙梨',
            courseBadgeName: () => '川名',
          })}
        />,
      );
      expect(screen.queryByTestId('tdl-accompaniment-a')).toBeNull();
      expect(screen.queryByTestId('tdl-course-accompaniment-tpl-A:0')).toBeNull();
    });
  });
});
