/**
 * AddVisitDialog — 盤面セルの「＋訪問」(週空間 Phase E・運転席)。
 *
 * add-visit-anywhere-design.md Phase 0 (§1 欠陥 2・4 / §3-3 ③ / PO 決定 12) の契約:
 * ① 所要時間の選択肢は 5 分刻み・15〜120 分
 * ② 患者を選ぶと所要時間がその患者の基本時間 (service_minutes) になる
 * ③ 基本時間が 5 分刻みの外でも選択肢に出る (選べなくならない)
 * ④ staff=null は「（担当なし）」と出し、payload の staff_id も null
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { AddVisitDialog, DURATION_OPTIONS } from '../AddVisitDialog';

const P_ITO = '00000000-0000-4000-8000-0000000000e1';
const P_TAKAOKA = '00000000-0000-4000-8000-0000000000e2';
const STAFF_1 = '00000000-0000-4000-8000-000000000001';

const candidates = [
  { patient_id: P_ITO, patient_name: '伊藤　花子', hint: '月/木 午前', service_minutes: 35 },
  { patient_id: P_TAKAOKA, patient_name: '高岡　一郎', hint: null, service_minutes: null },
];

function renderDialog(props: Partial<React.ComponentProps<typeof AddVisitDialog>> = {}) {
  const onSubmit = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <AddVisitDialog
      open
      onOpenChange={onOpenChange}
      staff={{ id: STAFF_1, name: '宇田川　優莉' }}
      date="2026-09-14"
      poolCandidates={candidates}
      onSubmit={onSubmit}
      {...props}
    />,
  );
  return { onSubmit, onOpenChange };
}

/** 所要時間セレクトの選択肢 (value を数値で)。 */
function minutesOptionValues(): number[] {
  const select = screen.getByTestId('add-visit-minutes') as HTMLSelectElement;
  return Array.from(select.options).map((o) => Number.parseInt(o.value, 10));
}

describe('AddVisitDialog — 所要時間 (PO 決定 12)', () => {
  it('① 選択肢は 5 分刻み・15〜120 分', () => {
    // 定数そのものの契約
    expect(DURATION_OPTIONS[0]).toBe(15);
    expect(DURATION_OPTIONS[DURATION_OPTIONS.length - 1]).toBe(120);
    expect(DURATION_OPTIONS).toHaveLength(22);
    expect(DURATION_OPTIONS.every((m) => m % 5 === 0)).toBe(true);
    expect(DURATION_OPTIONS).toContain(35);

    renderDialog();
    expect(minutesOptionValues()).toEqual([...DURATION_OPTIONS]);
  });

  it('② 患者を選ぶと所要時間がその患者の基本時間 (35 分) になる', () => {
    renderDialog({ defaultMinutes: 45 });
    const minutes = screen.getByTestId('add-visit-minutes') as HTMLSelectElement;
    expect(minutes.value).toBe('45');

    fireEvent.change(screen.getByTestId('add-visit-patient'), { target: { value: P_ITO } });
    expect((screen.getByTestId('add-visit-minutes') as HTMLSelectElement).value).toBe('35');
  });

  it('② 基本時間が無い患者は defaultMinutes のまま', () => {
    renderDialog({ defaultMinutes: 45 });
    fireEvent.change(screen.getByTestId('add-visit-patient'), { target: { value: P_TAKAOKA } });
    expect((screen.getByTestId('add-visit-minutes') as HTMLSelectElement).value).toBe('45');
  });

  it('③ 5 分刻みの外の基本時間も選択肢に出る (昇順)', () => {
    renderDialog({
      poolCandidates: [{ patient_id: P_ITO, patient_name: '伊藤　花子', service_minutes: 32 }],
    });
    fireEvent.change(screen.getByTestId('add-visit-patient'), { target: { value: P_ITO } });

    const values = minutesOptionValues();
    expect((screen.getByTestId('add-visit-minutes') as HTMLSelectElement).value).toBe('32');
    expect(values).toContain(32);
    expect(values).toEqual([...values].sort((a, b) => a - b));
  });
});

describe('AddVisitDialog — 担当なし (設計 §3-4 D)', () => {
  const COURSE_A = '00000000-0000-4000-8000-0000000000a1';
  const COURSE_M = '00000000-0000-4000-8000-0000000000a2';
  const courseOptions = [
    { id: COURSE_A, label: '稲毛 A' },
    { id: COURSE_M, label: '稲毛 M' },
  ];

  it('④ staff=null なら「（担当なし）」と出し、payload の staff_id も null', () => {
    const { onSubmit } = renderDialog({ staff: null });
    expect(screen.getByTestId('add-visit-dialog')).toHaveTextContent('（担当なし）');

    fireEvent.change(screen.getByTestId('add-visit-patient'), { target: { value: P_ITO } });
    fireEvent.click(screen.getByTestId('add-visit-submit'));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      patient_id: P_ITO,
      date: '2026-09-14',
      service_minutes: 35,
      staff_id: null,
      course_id: null,
    });
  });

  it('⑤ staff=null では M（受け皿）を既定コースにする — 臨だと盤面から消えるため', () => {
    const { onSubmit } = renderDialog({ staff: null, courseOptions });
    expect((screen.getByTestId('add-visit-course') as HTMLSelectElement).value).toBe(COURSE_M);

    fireEvent.change(screen.getByTestId('add-visit-patient'), { target: { value: P_ITO } });
    fireEvent.click(screen.getByTestId('add-visit-submit'));
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({ course_id: COURSE_M, staff_id: null });
  });

  it('⑤ 臨（コースなし）も選べる', () => {
    const { onSubmit } = renderDialog({ staff: null, courseOptions });
    fireEvent.change(screen.getByTestId('add-visit-patient'), { target: { value: P_ITO } });
    fireEvent.change(screen.getByTestId('add-visit-course'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('add-visit-submit'));
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({ course_id: null });
  });

  it('⑤ M が無い拠点は従来どおり 臨（コースなし）が既定', () => {
    renderDialog({ staff: null, courseOptions: [{ id: COURSE_A, label: '稲毛 A' }] });
    expect((screen.getByTestId('add-visit-course') as HTMLSelectElement).value).toBe('');
  });

  it('⑤ 職員行から開いたときは M を既定にしない', () => {
    renderDialog({ courseOptions });
    expect((screen.getByTestId('add-visit-course') as HTMLSelectElement).value).toBe('');
  });
});
