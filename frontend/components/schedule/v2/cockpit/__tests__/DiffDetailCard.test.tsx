/**
 * DiffDetailCard — 「何から何へ」表 (週空間 Phase E)。
 *
 * ① 列は常に「らく助(今)」「カイポケ」で、direction が before/after を読み替える
 * ② 変わった項目だけ → が付く (同じ項目は ＝)
 * ③ 新規は左が「（無い）」・取消は右が「（無い）」(direction で左右が入れ替わる)
 * ④ 見出しは方向×種別で文言が変わる
 */
import * as React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { DiffDetailCard } from '../DiffDetailCard';
import type { CockpitMarker } from '../reconcileMarkers';

const STAFF_A = '00000000-0000-4000-8000-0000000000a1';
const STAFF_B = '00000000-0000-4000-8000-0000000000b2';
const staffNameById = new Map([
  [STAFF_A, '川名'],
  [STAFF_B, '髙梨'],
]);

const UPDATE: CockpitMarker = {
  kind: 'visit',
  action: 'update',
  externalId: 'i1',
  title: '伊藤 様',
  patient_name: '伊藤 様',
  course_label: '稲毛B',
  start: '16:00',
  end: '17:00',
  beforeStart: '15:00',
  beforeEnd: '16:00',
  before: {
    staff_id: STAFF_A,
    staff_name: '川名',
    date: '2026-08-18',
    start: '15:00',
    end: '16:00',
    course_label: '稲毛B',
  },
  after: {
    staff_id: STAFF_B,
    staff_name: '髙梨',
    date: '2026-08-18',
    start: '16:00',
    end: '17:00',
    course_label: '稲毛B',
  },
};

/** 「日付」「時刻」などの行を label で引く。 */
function row(label: string): HTMLElement {
  const cell = screen.getByText(label);
  return cell.closest('tr') as HTMLElement;
}

describe('DiffDetailCard', () => {
  it('inbound は before=らく助 / after=カイポケ で並ぶ', () => {
    render(<DiffDetailCard marker={UPDATE} direction="inbound" staffNameById={staffNameById} />);
    expect(screen.getByText('らく助(今)')).toBeInTheDocument();
    expect(screen.getByText('カイポケ')).toBeInTheDocument();
    const time = row('時刻');
    const cells = within(time).getAllByRole('cell');
    expect(cells[1]).toHaveTextContent('15:00〜16:00'); // らく助
    expect(cells[2]).toHaveTextContent('→'); // 変わっている
    expect(cells[3]).toHaveTextContent('16:00〜17:00'); // カイポケ
  });

  it('outbound は before=カイポケ / after=らく助 に読み替える', () => {
    render(<DiffDetailCard marker={UPDATE} direction="outbound" staffNameById={staffNameById} />);
    const cells = within(row('時刻')).getAllByRole('cell');
    expect(cells[1]).toHaveTextContent('16:00〜17:00'); // らく助 = after
    expect(cells[3]).toHaveTextContent('15:00〜16:00'); // カイポケ = before
  });

  it('変わっていない項目は ＝ で並ぶ', () => {
    render(<DiffDetailCard marker={UPDATE} direction="inbound" staffNameById={staffNameById} />);
    expect(within(row('日付')).getAllByRole('cell')[2]).toHaveTextContent('＝');
  });

  it('コースが変わったら コース行にも → が付く', () => {
    const moved: CockpitMarker = {
      ...UPDATE,
      after: { ...UPDATE.after!, course_label: '都賀A' },
    };
    render(<DiffDetailCard marker={moved} direction="inbound" staffNameById={staffNameById} />);
    const cells = within(row('コース')).getAllByRole('cell');
    expect(cells[1]).toHaveTextContent('稲毛B');
    expect(cells[2]).toHaveTextContent('→');
    expect(cells[3]).toHaveTextContent('都賀A');
  });

  it('コースが同じなら ＝ のまま', () => {
    render(<DiffDetailCard marker={UPDATE} direction="inbound" staffNameById={staffNameById} />);
    expect(within(row('コース')).getAllByRole('cell')[2]).toHaveTextContent('＝');
  });

  it('新規 (カイポケにだけある) は らく助側が「（無い）」', () => {
    const add: CockpitMarker = { ...UPDATE, action: 'add', before: undefined, beforeStart: null };
    render(<DiffDetailCard marker={add} direction="inbound" staffNameById={staffNameById} />);
    expect(screen.getByTestId('diff-detail-kind')).toHaveTextContent('新規');
    expect(screen.getByTestId('diff-detail-head')).toHaveTextContent(
      'カイポケにだけある予定 → 取り込むとらく助に入ります',
    );
    expect(within(row('時刻')).getAllByRole('cell')[1]).toHaveTextContent('（無い）');
    expect(within(row('時刻')).getAllByRole('cell')[3]).toHaveTextContent('16:00〜17:00');
  });

  it('取消 (カイポケで消えている) は カイポケ側が「（無い）」', () => {
    const del: CockpitMarker = { ...UPDATE, action: 'delete', after: undefined };
    render(<DiffDetailCard marker={del} direction="inbound" staffNameById={staffNameById} />);
    expect(screen.getByTestId('diff-detail-kind')).toHaveTextContent('取消');
    expect(within(row('時刻')).getAllByRole('cell')[1]).toHaveTextContent('15:00〜16:00');
    expect(within(row('時刻')).getAllByRole('cell')[3]).toHaveTextContent('（無い）');
  });

  it('未送信 (outbound) の文言はらく助側を主語にする', () => {
    render(<DiffDetailCard marker={UPDATE} direction="outbound" />);
    expect(screen.getByTestId('diff-detail-head')).toHaveTextContent(
      'らく助で変えました → 送信するとカイポケが揃います',
    );
  });

  it('イベントは「コース」行を出さない', () => {
    const ev: CockpitMarker = { ...UPDATE, kind: 'event', patient_name: undefined };
    render(<DiffDetailCard marker={ev} direction="inbound" />);
    expect(screen.queryByText('コース')).toBeNull();
  });
});
