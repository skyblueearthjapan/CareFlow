/**
 * AddVisitResultDialog — 「＋訪問」の結果画面 (add-visit-anywhere-design.md §3-3 ⑤ / Phase 4)。
 *
 * 契約:
 *   ① 日付ごとに 1 行 (`avr-row-<date>`)・結果は ✓登録 / ✓移動 / ✓型を更新 / ✗失敗 / —未実行
 *   ② 失敗した日で止まり、以降は「未実行」で並ぶ
 *   ③ 今週だけの操作 (登録/移動) があるときだけ「元に戻す」が出る
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import {
  AddVisitResultDialog,
  buildAddVisitResultRows,
  MANUAL_VISIT_WARN,
  type AddVisitResultRow,
} from '../AddVisitResultDialog';
import type { AddVisitPlanItem } from '@/lib/scheduling/addVisitPlan';

function item(over: Partial<AddVisitPlanItem> = {}): AddVisitPlanItem {
  return {
    date: '2026-09-14',
    isoYear: 2026,
    isoWeek: 38,
    weekday: 0,
    startHM: '12:00',
    minutes: 35,
    officeId: 'office-honten',
    courseTemplateId: 'tpl-a',
    courseLabel: '稲毛A',
    isM: false,
    isOtherOffice: false,
    staffCount: 1,
    partnerCourseTemplateId: null,
    reason: null,
    scope: 'new',
    sourceVisit: null,
    noCandidateReason: null,
    ...over,
  };
}

function renderDialog(rows: AddVisitResultRow[], onUndo?: () => void) {
  const onOpenChange = vi.fn();
  render(
    <AddVisitResultDialog
      open
      onOpenChange={onOpenChange}
      patientName="伊藤"
      rows={rows}
      onUndo={onUndo}
    />,
  );
  return { onOpenChange };
}

describe('buildAddVisitResultRows', () => {
  it('done / failed / skipped を日付順の行に畳む', () => {
    const a = item({ date: '2026-09-14' });
    const b = item({ date: '2026-09-15' });
    const c = item({ date: '2026-09-16' });
    const rows = buildAddVisitResultRows({
      ordered: [a, b, c],
      done: [{ item: a, kind: 'new', visitIds: ['v1'] }],
      failed: {
        item: b,
        kind: 'error',
        index: 1,
        error: new Error('boom'),
        message: '定員がいっぱいです',
        detail: null,
      },
      skipped: [c],
    });
    expect(rows.map((r) => r.status)).toEqual(['new', 'failed', 'skipped']);
    expect(rows[1]?.message).toBe('定員がいっぱいです');
  });

  it("kind 'week' / 'pattern' はそれぞれ移動 / 型を更新になる", () => {
    const a = item({ date: '2026-09-14', scope: 'week' });
    const b = item({ date: '2026-09-15', scope: 'pattern' });
    const rows = buildAddVisitResultRows({
      ordered: [a, b],
      done: [
        { item: a, kind: 'week', visitIds: [] },
        { item: b, kind: 'pattern' },
      ],
      skipped: [],
    });
    expect(rows.map((r) => r.status)).toEqual(['week', 'pattern']);
    // 型の更新には §8 の「今週だけ」注記を付けない。
    expect(rows[1]?.note ?? null).toBeNull();
  });

  it('臨 (コース未所属) は「盤面に出ない・undo でも消えない」注意書きを付ける (M3)', () => {
    const a = item({ courseTemplateId: null, courseLabel: '臨（コースなし）' });
    const rows = buildAddVisitResultRows({
      ordered: [a],
      done: [{ item: a, kind: 'new_manual', visitIds: ['v1'] }],
      skipped: [],
    });
    expect(rows[0]?.status).toBe('new_manual');
    expect(rows[0]?.warn).toBe(MANUAL_VISIT_WARN);
  });
});

describe('AddVisitResultDialog', () => {
  const okRow: AddVisitResultRow = {
    date: '2026-09-14',
    startHM: '12:00',
    minutes: 35,
    courseLabel: '稲毛A',
    status: 'new',
  };

  it('① 日付ごとに 1 行を出し、結果ラベルを表示する', () => {
    renderDialog([
      okRow,
      {
        date: '2026-09-15',
        startHM: '12:00',
        minutes: 35,
        courseLabel: 'M（担当なし）',
        status: 'failed',
        message: '定員がいっぱいです',
      },
      {
        date: '2026-09-16',
        startHM: '12:00',
        minutes: 35,
        courseLabel: '稲毛C',
        status: 'skipped',
      },
    ]);
    expect(screen.getByTestId('avr-row-2026-09-14')).toHaveTextContent('✓ 登録');
    expect(screen.getByTestId('avr-row-2026-09-15')).toHaveTextContent(
      '✗ 失敗: 定員がいっぱいです',
    );
    expect(screen.getByTestId('avr-row-2026-09-16')).toHaveTextContent('— 未実行');
  });

  it('③ 今週だけの操作があるときだけ「元に戻す」を出す', () => {
    const onUndo = vi.fn();
    const { onOpenChange } = renderDialog([okRow], onUndo);
    fireEvent.click(screen.getByTestId('avr-undo'));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('③ 臨 (new_manual) だけなら「元に戻す」は出さない (M3)', () => {
    renderDialog(
      [
        {
          date: '2026-09-14',
          startHM: '12:00',
          minutes: 35,
          courseLabel: '臨（コースなし）',
          status: 'new_manual',
          warn: MANUAL_VISIT_WARN,
        },
      ],
      vi.fn(),
    );
    expect(screen.queryByTestId('avr-undo')).toBeNull();
    expect(screen.getByTestId('avr-warn-2026-09-14')).toHaveTextContent('盤面に出ません');
  });

  it('③ 型の更新だけなら「元に戻す」は出さない', () => {
    renderDialog(
      [
        {
          date: '2026-09-14',
          startHM: '12:00',
          minutes: 35,
          courseLabel: '稲毛A',
          status: 'pattern',
        },
      ],
      vi.fn(),
    );
    expect(screen.queryByTestId('avr-undo')).toBeNull();
  });

  it('「閉じる」で onOpenChange(false)', () => {
    const { onOpenChange } = renderDialog([okRow]);
    fireEvent.click(screen.getByTestId('avr-close'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
