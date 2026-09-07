/**
 * courseTemplateMatch — `CourseDayTablePanel.findCourseForTemplate` の逆向き解決。
 * 本家と同じ 4 規則（exact / M 溢れ / 臨時溢れ / legacy 1 文字）と同じ優先順。
 */
import { describe, expect, it } from 'vitest';

import { matchCourseTemplate, type CourseTemplateLite } from '../courseTemplateMatch';

const OFFICE_A = 'office-a';
const OFFICE_B = 'office-b';

const TEMPLATES: CourseTemplateLite[] = [
  { id: 't-a', label: 'A', office_id: OFFICE_A },
  { id: 't-m', label: 'M', office_id: OFFICE_A },
  { id: 't-m2', label: 'M2', office_id: OFFICE_A },
  { id: 't-rin', label: '臨時', office_id: OFFICE_A },
  { id: 't-legacy', label: 'Cコース', office_id: OFFICE_A },
  { id: 't-b-a', label: 'A', office_id: OFFICE_B },
];

describe('matchCourseTemplate', () => {
  it('1) ラベル完全一致 (大文字小文字は無視)', () => {
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, 'A')?.id).toBe('t-a');
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, 'a')?.id).toBe('t-a');
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, ' M ')?.id).toBe('t-m');
  });

  it('拠点で絞る (同じラベルでも別拠点は返さない)', () => {
    expect(matchCourseTemplate(TEMPLATES, OFFICE_B, 'A')?.id).toBe('t-b-a');
    expect(matchCourseTemplate(TEMPLATES, 'office-x', 'A')).toBeNull();
    expect(matchCourseTemplate(TEMPLATES, null, 'A')).toBeNull();
  });

  it('2) M 溢れ: 専用 template があれば exact が勝ち、無ければ M へ流す', () => {
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, 'M2')?.id).toBe('t-m2');
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, 'M3')?.id).toBe('t-m');
  });

  it('2b) 臨時溢れ: 臨2 は先頭が 臨 の template へ', () => {
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, '臨2')?.id).toBe('t-rin');
  });

  it('3) legacy 1 文字: code=C は label=Cコース に当たる', () => {
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, 'C')?.id).toBe('t-legacy');
  });

  it('当たらなければ null (コード空・未知コード)', () => {
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, '')).toBeNull();
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, null)).toBeNull();
    expect(matchCourseTemplate(TEMPLATES, OFFICE_A, 'ZZ')).toBeNull();
  });
});
