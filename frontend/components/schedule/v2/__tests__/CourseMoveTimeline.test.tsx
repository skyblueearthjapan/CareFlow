/**
 * CourseMoveTimeline — T-4 意匠統一テスト.
 *
 * 提案系タイムラインの行が日リスト (TimelineDayList.VisitRow) と同じ
 * カード視覚言語 (性別ウォッシュ地 + 性別左帯 + 性別ドット) で描画されること、
 * および既存契約 (testId / 「様」 / 「← ここへ移動」 / data-kind) が
 * 維持されることを固定する。jsdom はレイアウト計算をしないため、
 * inline style (var(--sched-*)) と className の契約で担保する。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { CourseSnapshot, ImprovementSuggestion } from '@/lib/schemas/v2/improvementSuggestion';

import {
  BeforeAfterCourseTimeline,
  CourseMoveTimeline,
  TimelinePanel,
  type TimelineRow,
  type TimelineRowMeta,
} from '../CourseMoveTimeline';

// ─── fixtures ────────────────────────────────────────────────────────────

const srcCourse = {
  course_label: '稲A',
  staff_name: '熊澤 妙子',
  visits: [
    {
      patient_id: 'p-target',
      patient_name: '田中 花子',
      start_time: '10:00:00',
      end_time: '10:35:00',
    },
    {
      patient_id: 'p-other',
      patient_name: '佐藤 太郎',
      start_time: '11:00:00',
      end_time: '11:35:00',
    },
  ],
} as unknown as CourseSnapshot;

const suggestion = {
  candidate: { start_time: '14:00:00', end_time: '14:35:00' },
  swap_counterpart: null,
} as unknown as ImprovementSuggestion;

const META: ReadonlyMap<string, TimelineRowMeta> = new Map([
  ['p-target', { sex: 'female', address: '千葉市稲毛区1-2-3', condLabel: '👩女性のみ' }],
  ['p-other', { sex: 'male', address: '千葉市花見川区9-8-7' }],
]);

// ─── tests ───────────────────────────────────────────────────────────────

describe('CourseMoveTimeline (T-4 意匠統一)', () => {
  it('同一コース内: 既存 testId / 「様」 / 「← ここへ移動」 契約を維持する', () => {
    render(
      <CourseMoveTimeline
        suggestion={suggestion}
        targetPatientId="p-target"
        patientName="田中 花子"
        sourceCourse={srcCourse}
        destinationCourse={null}
      />,
    );
    expect(screen.getByTestId('course-move-timeline-single')).toBeInTheDocument();
    expect(screen.getByTestId('course-move-timeline-src')).toBeInTheDocument();
    expect(screen.getByText('← ここへ移動')).toBeInTheDocument();
    expect(screen.getByText('移動元')).toBeInTheDocument();
    expect(screen.getAllByText(/様$/).length).toBeGreaterThan(0);
    expect(screen.getByText(/コース内の動き（稲A・熊澤 妙子）/)).toBeInTheDocument();
  });

  it('patientMetaById 指定時: 行が性別ウォッシュ (var(--sched-*)) のカード行になる', () => {
    const { container } = render(
      <CourseMoveTimeline
        suggestion={suggestion}
        targetPatientId="p-target"
        patientName="田中 花子"
        sourceCourse={srcCourse}
        destinationCourse={null}
        patientMetaById={META}
      />,
    );
    const rows = Array.from(container.querySelectorAll('[data-kind]')) as HTMLElement[];
    expect(rows.length).toBe(3); // normal(佐藤) + out(田中旧) + in(田中新)

    const normal = rows.find((r) => r.dataset.kind === 'normal')!;
    // 男性ウォッシュ地 + 左帯 + 角丸カード。
    expect(normal.getAttribute('style') ?? '').toContain('var(--sched-male-bg)');
    expect(normal.getAttribute('style') ?? '').toContain('var(--sched-male-bar)');
    expect(normal.className).toContain('rounded-md');
    expect(normal.className).toContain('border-l-[3px]');

    // out (移動元の旧位置) はウォッシュ無し + 打ち消し。
    const out = rows.find((r) => r.dataset.kind === 'out')!;
    expect(out.getAttribute('style') ?? '').not.toContain('-bg)');
    expect(out.className).toContain('line-through');

    // in (新位置) は女性ウォッシュ + brand リング強調。
    const inRow = rows.find((r) => r.dataset.kind === 'in')!;
    expect(inRow.getAttribute('style') ?? '').toContain('var(--sched-female-bg)');
    expect(inRow.className).toContain('ring-brand-primary');

    // 条件ピルと📍住所 (メタ由来)。対象患者の旧位置(out)と新位置(in)の両方に出る。
    expect(screen.getAllByText('👩女性のみ').length).toBe(2);
    expect(screen.getAllByText(/📍千葉市稲毛区1-2-3/).length).toBe(2);
  });

  it('patientMetaById 未指定 (旧呼び出し互換): 中立色で成立する', () => {
    const { container } = render(
      <CourseMoveTimeline
        suggestion={suggestion}
        targetPatientId="p-target"
        patientName="田中 花子"
        sourceCourse={srcCourse}
        destinationCourse={null}
      />,
    );
    const normal = container.querySelector('[data-kind="normal"]')!;
    expect(normal.getAttribute('style') ?? '').toContain('var(--sched-neutral-bg)');
  });

  it('各行の行頭に性別ドット (aria-hidden) が付く', () => {
    const { container } = render(
      <TimelinePanel
        title="t"
        testId="tp-test"
        rows={[
          { key: 'a', name: 'A', start: '10:00', kind: 'normal', sex: 'female' } as TimelineRow,
        ]}
      />,
    );
    const dot = container.querySelector('[data-kind="normal"] i[aria-hidden="true"]')!;
    expect(dot).toBeTruthy();
    expect(dot.getAttribute('style') ?? '').toContain('var(--sched-female-bar)');
  });

  it('BeforeAfterCourseTimeline: before/after 2 枚 + ピン/条件が描画される', () => {
    const rows: TimelineRow[] = [
      { key: 'b1', name: '山田', start: '09:00', kind: 'normal', condLabel: '2名', pinned: true },
    ];
    render(
      <BeforeAfterCourseTimeline
        title="稲B・火曜"
        beforeRows={rows}
        afterRows={[...rows, { key: 'in1', name: '新規', start: '10:00', kind: 'in' }]}
        testIdPrefix="ba-test"
      />,
    );
    expect(screen.getByTestId('ba-test-before')).toBeInTheDocument();
    expect(screen.getByTestId('ba-test-after')).toBeInTheDocument();
    expect(screen.getByText(/変更前（稲B・火曜）/)).toBeInTheDocument();
    expect(screen.getAllByText('2名').length).toBe(2);
    expect(screen.getAllByLabelText('ピン留め').length).toBe(2);
  });
});
