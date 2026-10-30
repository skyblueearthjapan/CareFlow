/**
 * 2 名で訪問を 1 行で持つ訪問 (本番の形: required_staff_count=2・visit_group_id なし・
 * 2 人目は secondary_staff_id) の盤面表示。
 *   - 2 人目のコース = slot 1 のひな形のコース、引けなければ 2 人目の担当のその日のコース
 *     (backend の pair_visit.load_pair_partner_courses と同じ)
 *   - 2 人目のコースの列には薄い写し (動かせない・消せない) を出し、相手の名前を添える
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CourseGridVisit } from '@/components/schedule/v2/courseGrid';
import { resolvePairPartnerCourse } from '@/components/schedule/v2/CourseDayTablePanel';
import {
  TimelineDayBoard,
  partnerInfo,
  type TimelineCourseColumn,
} from '@/components/schedule/timeline/TimelineDayBoard';
import type { CourseV2Read } from '@/lib/queries/courses';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';

const tplA = { id: 'tA', office_id: 'o1', label: 'A' } as CourseTemplateRead;
const tplB = { id: 'tB', office_id: 'o1', label: 'B' } as CourseTemplateRead;
const templates = [tplA, tplB];

function course(over: Partial<CourseV2Read> & { id: string }): CourseV2Read {
  return {
    iso_year: 2026,
    iso_week: 45,
    weekday: 0,
    code: 'A',
    office_id: 'o1',
    assigned_staff_id: null,
    deleted_at: null,
    ...over,
  } as CourseV2Read;
}

const cA = course({ id: 'cA', code: 'A', assigned_staff_id: 'sA' });
const cB = course({ id: 'cB', code: 'B', assigned_staff_id: 'sB' });
const pairVisit = {
  course_id: 'cA',
  required_staff_count: 2,
  visit_group_id: null,
  secondary_staff_id: 'sB',
};

describe('resolvePairPartnerCourse', () => {
  it('slot 1 のひな形のその日のコースを 2 人目のコースにする', () => {
    const got = resolvePairPartnerCourse({
      visit: pairVisit,
      pfvs: [
        { mode: 'normal', weekday: 0, slot_index: 0, course_template_id: 'tA' },
        { mode: 'normal', weekday: 0, slot_index: 1, course_template_id: 'tB' },
      ],
      specialWeek: false,
      templates,
      courses: [cA, cB],
    });
    expect(got?.id).toBe('cB');
  });

  it('slot 1 が無い (カイポケ取込など) ときは 2 人目の担当のその日のコース・同じ拠点を先に', () => {
    const otherOffice = course({ id: 'cX', code: 'A', office_id: 'o2', assigned_staff_id: 'sB' });
    const got = resolvePairPartnerCourse({
      visit: pairVisit,
      pfvs: [],
      specialWeek: false,
      templates,
      courses: [cA, otherOffice, cB],
    });
    expect(got?.id).toBe('cB');
  });

  it('slot 0 と訪問する週 (A/B) が違う slot 1 は使わず、2 人目の担当で引く', () => {
    const cC = course({ id: 'cC', code: 'C', assigned_staff_id: 'sB' });
    const got = resolvePairPartnerCourse({
      visit: pairVisit,
      pfvs: [
        { mode: 'normal', weekday: 0, slot_index: 0, course_template_id: 'tA', visit_week: 'A' },
        { mode: 'normal', weekday: 0, slot_index: 1, course_template_id: 'tB', visit_week: 'B' },
      ],
      specialWeek: false,
      templates,
      courses: [cA, course({ id: 'cB', code: 'B', assigned_staff_id: 'sZ' }), cC],
    });
    expect(got?.id).toBe('cC');
  });

  it('特別週で特別週の枠がある方は特別週の slot 1 を使う', () => {
    const got = resolvePairPartnerCourse({
      visit: { ...pairVisit, secondary_staff_id: null },
      pfvs: [
        { mode: 'normal', weekday: 0, slot_index: 1, course_template_id: 'tA' },
        { mode: 'special', weekday: 0, slot_index: 0, course_template_id: 'tA' },
        { mode: 'special', weekday: 0, slot_index: 1, course_template_id: 'tB' },
      ],
      specialWeek: true,
      templates,
      courses: [cA, cB],
    });
    expect(got?.id).toBe('cB');
  });

  it('1 人目のコースが無い (担当なしの行) ときは、訪問日 (day) を渡せば同じ引き方をする', () => {
    const unassigned = { ...pairVisit, course_id: null };
    const day = { weekday: 0, isoYear: 2026, isoWeek: 45 };
    // slot 1 のひな形のコース
    expect(
      resolvePairPartnerCourse({
        visit: unassigned,
        pfvs: [{ mode: 'normal', weekday: 0, slot_index: 1, course_template_id: 'tB' }],
        specialWeek: false,
        templates,
        courses: [cA, cB],
        day,
      })?.id,
    ).toBe('cB');
    // slot 1 が無ければ 2 人目の担当のその日のコース (拠点の優先なし・記号の順)
    const otherOffice = course({ id: 'cX', code: 'A', office_id: 'o2', assigned_staff_id: 'sB' });
    expect(
      resolvePairPartnerCourse({
        visit: unassigned,
        pfvs: [],
        specialWeek: false,
        templates,
        courses: [cA, otherOffice, cB],
        day,
      })?.id,
    ).toBe('cX');
    // day を渡さなければ今までどおり null (日の盤面は 1 人目のコースの訪問だけ)
    expect(
      resolvePairPartnerCourse({
        visit: unassigned,
        pfvs: [],
        specialWeek: false,
        templates,
        courses: [cA, cB],
      }),
    ).toBe(null);
  });

  it('2 行の組 (visit_group_id あり)・1 名・2 人目なしで slot 1 も無いときは null', () => {
    const base = { visit: pairVisit, pfvs: [], specialWeek: false, templates, courses: [cA, cB] };
    expect(
      resolvePairPartnerCourse({ ...base, visit: { ...pairVisit, visit_group_id: 'g' } }),
    ).toBe(null);
    expect(
      resolvePairPartnerCourse({ ...base, visit: { ...pairVisit, required_staff_count: 1 } }),
    ).toBe(null);
    expect(
      resolvePairPartnerCourse({ ...base, visit: { ...pairVisit, secondary_staff_id: null } }),
    ).toBe(null);
  });
});

function visit(over: Partial<CourseGridVisit> & { id: string }): CourseGridVisit {
  return {
    patient_id: `p-${over.id}`,
    patient_name: `患者${over.id}`,
    patient_address: null,
    patient_requires_multiple_staff: true,
    patient_sex_restriction_label: null,
    required_staff_count: 2,
    start_slot: '11:00',
    start_time: '11:00:00',
    end_time: '12:30:00',
    ...over,
  } as CourseGridVisit;
}

function column(over: Partial<TimelineCourseColumn> & { key: string }): TimelineCourseColumn {
  return {
    template: { id: 't1', office_id: 'o1', label: 'A' } as TimelineCourseColumn['template'],
    course: { id: 'c1', assigned_staff_id: 's1' } as TimelineCourseColumn['course'],
    officeName: '稲毛',
    visits: [],
    assignedStaff: null,
    freeGaps: [],
    capacity: { filled: 0, max: 6 },
    staffEvents: [],
    staffOptions: [],
    ...over,
  };
}

describe('2 名で 1 行の訪問のカード', () => {
  it('partnerInfo は相方の注記 (pair_note) をそのまま出す', () => {
    expect(partnerInfo({ pair_note: '2 名（田中さんと）' }).text).toBe('2 名（田中さんと）');
  });

  it('1 人目のカードに 2 人目の名前、2 人目の列に薄い写し (消せない) を出す', () => {
    const onDeleteVisit = vi.fn();
    render(
      <TimelineDayBoard
        columns={[
          column({
            key: 'cA',
            visits: [visit({ id: 'v1', pair_note: '2 名（佐藤さんと）' })],
          }),
          column({
            key: 'cB',
            template: { id: 't2', office_id: 'o1', label: 'B' } as TimelineCourseColumn['template'],
            visits: [visit({ id: 'v1', pair_note: '2 名（田中さんと）', pair_linked: true })],
          }),
        ]}
        weekdayLabel="月"
        onDeleteVisit={onDeleteVisit}
      />,
    );
    expect(screen.getByText('2 名（佐藤さんと）')).toBeInTheDocument();
    expect(screen.getByText('2 名（田中さんと）')).toBeInTheDocument();
    const linked = screen.getByTestId('tl-visit-linked-v1');
    expect(linked).toHaveAttribute('data-pair-linked', 'true');
    expect(linked.style.opacity).toBe('0.6');
    // 消す × は 1 人目のカードだけ
    expect(screen.getAllByTestId('tl-delete-visit-v1')).toHaveLength(1);
    expect(screen.getByTestId('tl-visit-v1')).not.toHaveAttribute('data-pair-linked');
  });
});
