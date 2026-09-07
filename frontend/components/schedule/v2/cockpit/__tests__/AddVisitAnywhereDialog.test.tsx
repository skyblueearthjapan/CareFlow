/**
 * AddVisitAnywhereDialog — 「＋訪問（任意日付の訪問追加）」モーダル。
 *
 * 観点 (`docs/plans/add-visit-anywhere-design.md` §9):
 *   ① 所要の初期値 = 患者の基本時間
 *   ② (a) 型も変える は日付 1 つのときだけ選べる
 *   ③ `propose-slots` は ISO 週ごとに 1 回・`time_type='固定'`・曜日が正しい
 *   ④ 候補ありの日は 1 位が既定選択
 *   ⑤ 0 件 + サブ担当拠点あり → 2 回目の呼び出しと「他拠点（要確認）」(チェックで解錠)
 *   ⑥ 0 件 + サブ拠点なし → M 既定 + 理由欄
 *   ⑦ (b) その週を変える → 同曜日の訪問が既定の「動かす元」
 *   ⑧ 登録 → onExecute に渡る plan の中身
 *
 * 日付の選択はカレンダーのクリックではなく `initial.dates` で与える (§9 の
 * 「カレンダー操作は最小限にモック」)。
 */
import * as React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import {
  AddVisitAnywhereDialog,
  type AddVisitAnywhereDialogProps,
  type AddVisitPatientOption,
} from '../AddVisitAnywhereDialog';
import type { AddVisitPlan, VisitLite } from '@/lib/scheduling/addVisitPlan';
import type { ProposeSlotItem, ProposeSlotsResponse } from '@/lib/schemas/v2/propose_slots';

const OFFICE_A = '0a000000-0000-4000-8000-000000000001';
const OFFICE_B = '0b000000-0000-4000-8000-000000000002';
const PATIENT_ID = '0c000000-0000-4000-8000-000000000003';
const PAIR_ID = '0d000000-0000-4000-8000-000000000004';

const WD_CODES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

const ITO: AddVisitPatientOption = {
  id: PATIENT_ID,
  name: '伊藤',
  status: 'active',
  primary_office_id: OFFICE_A,
  lat: 35.6,
  lng: 140.1,
  service_minutes: 35,
  hint: '希望 月/木 午前',
  sex_restriction: null,
  requires_multiple_staff: false,
};

/** 座標なし患者 = 提案を経ずに M で決められる (⑤ の検証に使う)。 */
const NO_COORDS: AddVisitPatientOption = {
  ...ITO,
  id: '0e000000-0000-4000-8000-000000000005',
  name: '座標なし',
  lat: null,
  lng: null,
};

function slot(over: Partial<ProposeSlotItem> & { weekday: number }): ProposeSlotItem {
  return {
    office_id: OFFICE_A,
    weekday_code: WD_CODES[over.weekday] ?? 'Mon',
    course_code: 'A',
    course_label: '稲毛A',
    staff_name: '髙梨',
    start_time: '12:00',
    end_time: '12:35',
    score: 1,
    reasons: [],
    warnings: [],
    is_pair: false,
    mini_schedule: [],
    is_efficiency_alternative: false,
    overcapacity: false,
    event_conflicts: [],
    ...over,
  };
}

function response(over: Partial<ProposeSlotsResponse> = {}): ProposeSlotsResponse {
  return {
    iso_year: 2026,
    iso_week: 38,
    slots: [],
    excluded_summary: [],
    overcapacity_slots: [],
    ...over,
  };
}

function visit(over: Partial<VisitLite> & { id: string; visit_date: string }): VisitLite {
  return {
    start_time: '09:30',
    end_time: '10:05',
    primary_staff_id: null,
    staff_name: '高岡',
    week_pinned: false,
    status: 'planned',
    source: 'auto',
    ...over,
  };
}

function renderDialog(props: Partial<AddVisitAnywhereDialogProps> = {}) {
  const proposeSlots = vi.fn(async () => response());
  const loadPatientWeekVisits = vi.fn(async () => [] as VisitLite[]);
  const onExecute = vi.fn(async () => {});
  const onOpenChange = vi.fn();
  const merged: AddVisitAnywhereDialogProps = {
    open: true,
    onOpenChange,
    isoYear: 2026,
    isoWeek: 37,
    patients: [ITO],
    poolPatientIds: new Set<string>(),
    staffOptions: [{ id: 'staff-1', name: '熊澤' }],
    offices: [
      { id: OFFICE_A, name: '稲毛' },
      { id: OFFICE_B, name: '都賀' },
    ],
    courseTemplates: [
      { id: 'tpl-a', label: 'A', office_id: OFFICE_A },
      { id: 'tpl-m', label: 'M', office_id: OFFICE_A },
      { id: 'tpl-b-a', label: 'A', office_id: OFFICE_B },
    ],
    todayIso: '2026-09-07',
    proposeSlots,
    loadPatientWeekVisits,
    onExecute,
    ...props,
  };
  const view = render(<AddVisitAnywhereDialog {...merged} />);
  return {
    view,
    proposeSlots: merged.proposeSlots as ReturnType<typeof vi.fn>,
    loadPatientWeekVisits: merged.loadPatientWeekVisits as ReturnType<typeof vi.fn>,
    onExecute: merged.onExecute as ReturnType<typeof vi.fn>,
    onOpenChange,
  };
}

describe('AddVisitAnywhereDialog', () => {
  it('① 所要の初期値は患者の基本時間 (35 分) で、5 分刻みが選べる', () => {
    renderDialog({ initial: { patientId: PATIENT_ID } });
    const minutes = screen.getByTestId('ava-minutes') as HTMLSelectElement;
    expect(minutes.value).toBe('35');
    expect(Array.from(minutes.options).map((o) => o.value)).toContain('25');
    expect(screen.getByTestId('ava-patient-hint')).toHaveTextContent('基本 35 分');
  });

  it('② 型も変える は日付が 2 つだと選べず、1 つなら選べる', () => {
    const { view } = renderDialog({
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14', '2026-09-17'] },
    });
    expect(screen.getByTestId('ava-scope-pattern')).toBeDisabled();
    expect(screen.getByText(/日付が 1 つのときだけ選べます/)).toBeInTheDocument();
    view.unmount();

    renderDialog({ initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] } });
    expect(screen.getByTestId('ava-scope-pattern')).toBeEnabled();
  });

  it('③④ 提案は ISO 週ごとに 1 回 (固定・曜日) で、BE の 1 位が既定選択になる', async () => {
    const proposeSlots = vi.fn(async (req: { iso_week: number }) =>
      req.iso_week === 38
        ? response({
            // BE のランキング順 = この配列順。score では並べ替えない。
            slots: [
              slot({ weekday: 0, course_label: '稲毛A', score: 0.4 }),
              slot({ weekday: 0, course_label: '稲毛C', staff_name: '熊澤', score: 0.9 }),
            ],
          })
        : response({ iso_week: 39, slots: [slot({ weekday: 1, course_label: '稲毛B' })] }),
    );
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14', '2026-09-22'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    await waitFor(() => expect(proposeSlots).toHaveBeenCalledTimes(2));

    // 2026-09-14 = W38 (月), 2026-09-22 = W39 (火)
    expect(proposeSlots.mock.calls[0]?.[0]).toMatchObject({
      iso_year: 2026,
      iso_week: 38,
      time_type: '固定',
      preferred_start: '12:00',
      preferred_weekdays: ['Mon'],
      service_minutes: 35,
      office_ids: [OFFICE_A],
      existing_patient_id: PATIENT_ID,
    });
    expect(proposeSlots.mock.calls[1]?.[0]).toMatchObject({
      iso_week: 39,
      preferred_weekdays: ['Tue'],
    });

    // BE の 1 位 (稲毛A) が p0 = 既定選択。score が高い稲毛C を繰り上げない。
    const row = await screen.findByTestId('ava-row-2026-09-14');
    expect(row).toHaveTextContent('稲毛A（髙梨）');
    expect(row).toHaveTextContent('稲毛C（熊澤）');
    const labels = Array.from(row.querySelectorAll('label')).map((el) => el.textContent ?? '');
    expect(labels[0]).toContain('稲毛A');
    expect(labels[1]).toContain('稲毛C');
    expect(screen.getByTestId('ava-cand-2026-09-14-p0')).toBeChecked();
    expect(screen.getByTestId('ava-cand-2026-09-14-__M__')).not.toBeChecked();
  });

  it('⑤ 主担当拠点 0 件 + サブ担当拠点あり → 2 回目の呼び出しと「他拠点（要確認）」', async () => {
    const proposeSlots = vi.fn(async (req: { office_ids: string[] }) =>
      req.office_ids[0] === OFFICE_B
        ? response({
            slots: [
              slot({ weekday: 0, office_id: OFFICE_B, course_label: '都賀A', staff_name: '熊澤' }),
            ],
          })
        : response({ excluded_summary: [{ reason: 'capacity_full', count: 2, weekday: 0 }] }),
    );
    const loadPatientSubOfficeIds = vi.fn(async () => [OFFICE_A, OFFICE_B]);
    const { onExecute } = renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      loadPatientSubOfficeIds,
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    await waitFor(() => expect(proposeSlots).toHaveBeenCalledTimes(2));

    // 2 回目は主担当拠点を除いたサブ拠点だけ
    expect(proposeSlots.mock.calls[1]?.[0]).toMatchObject({ office_ids: [OFFICE_B] });
    expect(loadPatientSubOfficeIds).toHaveBeenCalledWith(PATIENT_ID);

    const row = await screen.findByTestId('ava-row-2026-09-14');
    expect(row).toHaveTextContent('主担当拠点に空きがありません（理由: 定員いっぱい）');
    expect(row).toHaveTextContent('他拠点（要確認）');

    // H3: 反映先が (c) 新規追加のあいだは他拠点そのものが選べない
    expect(screen.getByTestId('ava-other-blocked-2026-09-14')).toHaveTextContent(
      '新規追加では他拠点へ入れられません',
    );
    expect(screen.getByTestId('ava-cand-2026-09-14-o0')).toBeDisabled();
    // (a) 型も変える に切り替えると「拠点跨ぎを承知で入れる」が出る
    fireEvent.click(screen.getByTestId('ava-scope-pattern'));

    // チェックするまで他拠点の候補は選べない
    const cand = screen.getByTestId('ava-cand-2026-09-14-o0');
    expect(cand).toBeDisabled();
    fireEvent.click(screen.getByTestId('ava-other-office-2026-09-14'));
    expect(cand).toBeEnabled();
    fireEvent.click(cand);
    expect(cand).toBeChecked();

    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    const plan = onExecute.mock.calls[0]?.[0] as AddVisitPlan;
    expect(plan.items[0]).toMatchObject({
      officeId: OFFICE_B,
      courseTemplateId: 'tpl-b-a',
      courseLabel: '都賀A',
      isOtherOffice: true,
      isM: false,
      noCandidateReason: 'capacity_full',
    });
  });

  it('⑥ 0 件 + サブ拠点なし → M が既定選択で理由欄が出る', async () => {
    const proposeSlots = vi.fn(async () =>
      response({ excluded_summary: [{ reason: 'no_gap', count: 1, weekday: 5 }] }),
    );
    const { onExecute } = renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-19'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    const row = await screen.findByTestId('ava-row-2026-09-19');
    expect(proposeSlots).toHaveBeenCalledTimes(1); // サブ拠点が無いので 2 回目は無い
    expect(row).toHaveTextContent('主担当拠点に空きがありません（理由: 空き時間が無い）');
    expect(screen.getByTestId('ava-cand-2026-09-19-__M__')).toBeChecked();

    fireEvent.change(screen.getByTestId('ava-reason-2026-09-19'), {
      target: { value: '定員いっぱいのため' },
    });
    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    const plan = onExecute.mock.calls[0]?.[0] as AddVisitPlan;
    expect(plan.items[0]).toMatchObject({
      isM: true,
      courseTemplateId: 'tpl-m',
      courseLabel: 'M（担当なし）',
      officeId: OFFICE_A,
      reason: '定員いっぱいのため',
      noCandidateReason: 'no_gap',
    });
  });

  it('⑦ その週を変える → 同じ曜日の訪問が既定の「動かす元」', async () => {
    const loadPatientWeekVisits = vi.fn(async () => [
      visit({ id: 'v-wed', visit_date: '2026-09-16' }),
      visit({ id: 'v-mon', visit_date: '2026-09-14' }),
      visit({ id: 'v-pinned', visit_date: '2026-09-14', week_pinned: true }),
    ]);
    renderDialog({
      loadPatientWeekVisits,
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'], lockedScope: 'week' },
    });

    await waitFor(() => expect(loadPatientWeekVisits).toHaveBeenCalledWith(PATIENT_ID, 2026, 38));
    const select = (await screen.findByTestId('ava-source-2026-09-14')) as HTMLSelectElement;
    expect(select.value).toBe('v-mon');
    // 青ピンは動かす元に出さない
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['v-wed', 'v-mon']);
    // lockedScope のときは反映先を切り替えられない
    expect(screen.getByTestId('ava-scope-new')).toBeDisabled();
    expect(screen.getByTestId('ava-scope-week')).toBeChecked();
  });

  it('⑧ 登録すると plan が onExecute に渡り、日付・時刻・コースが載る', async () => {
    const proposeSlots = vi.fn(async () => response({ slots: [slot({ weekday: 0 })] }));
    const { onExecute, onOpenChange } = renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    await screen.findByTestId('ava-row-2026-09-14');
    fireEvent.click(screen.getByTestId('ava-submit'));

    await waitFor(() => expect(onExecute).toHaveBeenCalledTimes(1));
    expect(onExecute.mock.calls[0]?.[0]).toEqual({
      patientId: PATIENT_ID,
      items: [
        {
          date: '2026-09-14',
          isoYear: 2026,
          isoWeek: 38,
          weekday: 0,
          startHM: '12:00',
          minutes: 35,
          officeId: OFFICE_A,
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
        },
      ],
    });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  // ─── H1: 📅「曜日を移動…」で押した予定を動かす元に固定する ────────────────

  it('H1 押した予定が「動かす元」の既定になる (同曜日ルールより優先・週の一覧に無くても選べる)', async () => {
    // 押した予定 = 火曜 09:00。移動先は水曜で、その週には水曜の予定も既にある
    // (同曜日ルールだけなら水曜が既定になってしまう)。
    const clicked = visit({ id: 'v-clicked', visit_date: '2026-09-15', start_time: '09:00' });
    const loadPatientWeekVisits = vi.fn(async () => [
      visit({ id: 'v-wed', visit_date: '2026-09-16', start_time: '10:00' }),
    ]);
    const proposeSlots = vi.fn(async () => response({ slots: [slot({ weekday: 2 })] }));
    const { onExecute } = renderDialog({
      loadPatientWeekVisits,
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: {
        patientId: PATIENT_ID,
        lockedScope: 'week',
        sourceVisit: clicked,
        dates: ['2026-09-16'],
      },
    });

    await waitFor(() => expect(loadPatientWeekVisits).toHaveBeenCalled());
    const select = (await screen.findByTestId('ava-source-2026-09-16')) as HTMLSelectElement;
    // 週の一覧に無くても候補に入り、既定になる。
    expect(Array.from(select.options).map((o) => o.value)).toContain('v-clicked');
    expect(select.value).toBe('v-clicked');

    fireEvent.click(screen.getByTestId('ava-propose'));
    await screen.findByTestId('ava-row-2026-09-16');
    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    const plan = onExecute.mock.calls[0]?.[0] as AddVisitPlan;
    expect(plan.items[0]?.scope).toBe('week');
    expect(plan.items[0]?.sourceVisit?.id).toBe('v-clicked');
    expect(plan.items[0]?.sourceVisit?.start_time).toBe('09:00');
  });

  it('H1 押した予定が動かせない (青ピン) ときは行エラーで登録を止める', async () => {
    const clicked = visit({
      id: 'v-pinned',
      visit_date: '2026-09-15',
      start_time: '09:00',
      week_pinned: true,
    });
    const proposeSlots = vi.fn(async () => response({ slots: [slot({ weekday: 2 })] }));
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: {
        patientId: PATIENT_ID,
        lockedScope: 'week',
        sourceVisit: clicked,
        dates: ['2026-09-16'],
      },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    await screen.findByTestId('ava-row-2026-09-16');
    expect(screen.getByTestId('ava-row-error-2026-09-16')).toHaveTextContent(
      'この予定は動かせません（当日以前/固定/予定外）',
    );
    expect(screen.getByTestId('ava-submit')).toBeDisabled();
  });

  // ─── H2: 2 名体制 × M（担当なし）は作れない ──────────────────────────────

  it('H2 2 名体制の患者を M へ入れようとすると行エラーで止まる', async () => {
    const twoStaff: AddVisitPatientOption = {
      ...ITO,
      id: PAIR_ID,
      name: '2名体制',
      requires_multiple_staff: true,
    };
    const proposeSlots = vi.fn(async () =>
      response({ excluded_summary: [{ reason: 'no_pair_slot', count: 1, weekday: 0 }] }),
    );
    renderDialog({
      patients: [ITO, twoStaff],
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PAIR_ID, dates: ['2026-09-14'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    await screen.findByTestId('ava-row-2026-09-14');
    // 0 件なので M が既定選択 → そのままでは登録できない。
    expect(screen.getByTestId('ava-cand-2026-09-14-__M__')).toBeChecked();
    expect(screen.getByTestId('ava-row-error-2026-09-14')).toHaveTextContent(
      '2名体制の患者は担当なし(M)へ入れられません',
    );
    expect(screen.getByTestId('ava-submit')).toBeDisabled();
  });

  // ── レビュー指摘の回帰 ───────────────────────────────────────────────

  it('H1 同じ訪問を 2 つの日付の元にしない (足りない日は新規追加になる)', async () => {
    const loadPatientWeekVisits = vi.fn(async () => [
      visit({ id: 'v-mon', visit_date: '2026-09-14' }),
    ]);
    const { onExecute } = renderDialog({
      patients: [NO_COORDS], // 提案不要 = 座標なし患者で ⑤ だけを見る
      loadPatientWeekVisits,
      initial: {
        patientId: NO_COORDS.id,
        dates: ['2026-09-14', '2026-09-16', '2026-09-18'],
        lockedScope: 'week',
      },
    });

    await waitFor(() => expect(loadPatientWeekVisits).toHaveBeenCalled());
    const select = (await screen.findByTestId('ava-source-2026-09-14')) as HTMLSelectElement;
    expect(select.value).toBe('v-mon');
    expect(await screen.findByTestId('ava-source-exhausted-2026-09-16')).toHaveTextContent(
      'この日は新規追加になります（動かせる予定が残っていません）',
    );
    expect(screen.getByTestId('ava-source-exhausted-2026-09-18')).toBeInTheDocument();
    expect(screen.queryByTestId('ava-source-2026-09-16')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    const plan = onExecute.mock.calls[0]?.[0] as AddVisitPlan;
    expect(plan.items.map((i) => [i.date, i.scope, i.sourceVisit?.id ?? null])).toEqual([
      ['2026-09-14', 'week', 'v-mon'],
      ['2026-09-16', 'new', null],
      ['2026-09-18', 'new', null],
    ]);
  });

  it('H1 手動で元を選び直しても他の日付と重複しない', async () => {
    const loadPatientWeekVisits = vi.fn(async () => [
      visit({ id: 'v-mon', visit_date: '2026-09-14' }),
      visit({ id: 'v-wed', visit_date: '2026-09-16' }),
    ]);
    renderDialog({
      patients: [NO_COORDS],
      loadPatientWeekVisits,
      initial: {
        patientId: NO_COORDS.id,
        dates: ['2026-09-14', '2026-09-16'],
        lockedScope: 'week',
      },
    });

    const first = (await screen.findByTestId('ava-source-2026-09-14')) as HTMLSelectElement;
    const second = screen.getByTestId('ava-source-2026-09-16') as HTMLSelectElement;
    expect(first.value).toBe('v-mon');
    expect(second.value).toBe('v-wed');
    // 他の日で使用中の選択肢は選べない
    expect(Array.from(first.options).find((o) => o.value === 'v-wed')).toBeDisabled();

    fireEvent.change(second, { target: { value: 'v-mon' } });
    await waitFor(() =>
      expect((screen.getByTestId('ava-source-2026-09-16') as HTMLSelectElement).value).toBe(
        'v-mon',
      ),
    );
    // 9/14 は押し出されて別の訪問へ (重複しない)
    expect((screen.getByTestId('ava-source-2026-09-14') as HTMLSelectElement).value).toBe('v-wed');
  });

  it('H2 型も変えるを選んだあと日付が増えたら新規追加へ落ちる', async () => {
    const { onExecute } = renderDialog({
      patients: [NO_COORDS],
      initial: { patientId: NO_COORDS.id, dates: ['2026-09-14'] },
    });
    fireEvent.click(screen.getByTestId('ava-scope-pattern'));
    expect(screen.getByTestId('ava-scope-pattern')).toBeChecked();

    // カレンダーで 9/16 を足す → 日付 2 つ = 型は選べない
    // (react-day-picker v8 の日セルは role="gridcell" の button・名前は日付の数字)
    fireEvent.click(screen.getByRole('gridcell', { name: '16' }));
    await waitFor(() => expect(screen.getByTestId('ava-scope-pattern')).toBeDisabled());
    expect(screen.getByTestId('ava-scope-pattern')).not.toBeChecked();
    expect(screen.getByTestId('ava-scope-new')).toBeChecked();

    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    const plan = onExecute.mock.calls[0]?.[0] as AddVisitPlan;
    expect(plan.items.map((i) => i.scope)).toEqual(['new', 'new']);
  });

  it('H3 コースを特定できない候補は行にエラーを出し登録させない', async () => {
    const proposeSlots = vi.fn(async () =>
      response({ slots: [slot({ weekday: 0, course_code: 'ZZ', course_label: '謎コース' })] }),
    );
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });
    fireEvent.click(screen.getByTestId('ava-propose'));
    expect(await screen.findByTestId('ava-row-error-2026-09-14')).toHaveTextContent(
      'コースを特定できません（謎コース）',
    );
    expect(screen.getByTestId('ava-submit')).toBeDisabled();

    // M へ切り替えれば登録できる
    fireEvent.click(screen.getByTestId('ava-cand-2026-09-14-__M__'));
    expect(screen.queryByTestId('ava-row-error-2026-09-14')).not.toBeInTheDocument();
    expect(screen.getByTestId('ava-submit')).toBeEnabled();
  });

  it('H3 M2 (M 溢れ) は M テンプレートへ解決し isM で扱う', async () => {
    const proposeSlots = vi.fn(async () =>
      response({ slots: [slot({ weekday: 0, course_code: 'M2', course_label: '稲毛M2' })] }),
    );
    const { onExecute } = renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });
    fireEvent.click(screen.getByTestId('ava-propose'));
    await screen.findByTestId('ava-row-2026-09-14');
    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    expect((onExecute.mock.calls[0]?.[0] as AddVisitPlan).items[0]).toMatchObject({
      courseTemplateId: 'tpl-m',
      isM: true,
    });
  });

  it('H4 定員超の候補は既定選択にしない (選ぶことはできる)', async () => {
    const proposeSlots = vi.fn(async () =>
      response({
        slots: [],
        overcapacity_slots: [
          slot({ weekday: 0, course_label: '稲毛A', overcapacity: true, score: 9 }),
        ],
      }),
    );
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });
    fireEvent.click(screen.getByTestId('ava-propose'));
    const row = await screen.findByTestId('ava-row-2026-09-14');
    expect(row).toHaveTextContent('定員超');
    expect(screen.getByTestId('ava-cand-2026-09-14-__M__')).toBeChecked();
    const over = screen.getByTestId('ava-cand-2026-09-14-p0');
    expect(over).not.toBeChecked();
    expect(over).toBeEnabled();
  });

  it('H5 2名体制: 相方コースが無い候補は登録できない / あれば plan に載る', async () => {
    const pair = { id: PAIR_ID, name: '加藤', requires_multiple_staff: true } as const;
    const patients = [{ ...ITO, ...pair }];
    const proposeSlots = vi.fn(async () =>
      response({
        slots: [
          slot({ weekday: 0, course_label: '稲毛A' }), // 相方なし
          slot({
            weekday: 0,
            course_code: 'B',
            course_label: '稲毛B',
            partner_course_template_id: 'tpl-b-pair',
            partner_course_label: '稲毛C',
          }),
        ],
      }),
    );
    const { onExecute } = renderDialog({
      patients,
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      courseTemplates: [
        { id: 'tpl-a', label: 'A', office_id: OFFICE_A },
        { id: 'tpl-b', label: 'B', office_id: OFFICE_A },
        { id: 'tpl-m', label: 'M', office_id: OFFICE_A },
      ],
      initial: { patientId: PAIR_ID, dates: ['2026-09-14'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    expect(await screen.findByTestId('ava-row-error-2026-09-14')).toHaveTextContent(
      '2名体制の相方コースが特定できません',
    );
    expect(screen.getByTestId('ava-submit')).toBeDisabled();

    fireEvent.click(screen.getByTestId('ava-cand-2026-09-14-p1'));
    expect(screen.queryByTestId('ava-row-error-2026-09-14')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(onExecute).toHaveBeenCalled());
    expect((onExecute.mock.calls[0]?.[0] as AddVisitPlan).items[0]).toMatchObject({
      staffCount: 2,
      partnerCourseTemplateId: 'tpl-b-pair',
      courseTemplateId: 'tpl-b',
    });
  });

  it('H6 主担当拠点が無い患者は提案を呼ばない', () => {
    const proposeSlots = vi.fn(async () => response());
    renderDialog({
      patients: [{ ...ITO, primary_office_id: null }],
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });
    expect(screen.getByTestId('ava-no-office')).toHaveTextContent(
      '主担当拠点が未設定のため提案できません',
    );
    expect(screen.getByTestId('ava-propose')).toBeDisabled();
    fireEvent.click(screen.getByTestId('ava-propose'));
    expect(proposeSlots).not.toHaveBeenCalled();
  });

  it('M1 週に候補がある中での 0 件日は、その曜日だけで理由を聞き直す', async () => {
    const proposeSlots = vi.fn(async (req: { preferred_weekdays: string[] }) =>
      req.preferred_weekdays.length > 1
        ? response({ slots: [slot({ weekday: 0 })] }) // 月だけ候補・木は 0 件
        : response({ excluded_summary: [{ reason: 'capacity_full', count: 2, weekday: 3 }] }),
    );
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14', '2026-09-17'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    await waitFor(() => expect(proposeSlots).toHaveBeenCalledTimes(2));
    expect(proposeSlots.mock.calls[0]?.[0]).toMatchObject({ preferred_weekdays: ['Mon', 'Thu'] });
    expect(proposeSlots.mock.calls[1]?.[0]).toMatchObject({
      preferred_weekdays: ['Thu'],
      office_ids: [OFFICE_A],
    });
    expect(await screen.findByTestId('ava-row-2026-09-17')).toHaveTextContent(
      '主担当拠点に空きがありません（理由: 定員いっぱい）',
    );
  });

  it('M1 理由が取れなければその旨を出す', async () => {
    const proposeSlots = vi.fn(async () => response());
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });
    fireEvent.click(screen.getByTestId('ava-propose'));
    expect(await screen.findByTestId('ava-row-2026-09-14')).toHaveTextContent(
      '主担当拠点に空きがありません（理由は取得できませんでした）',
    );
  });

  it('M2 候補が上限に達した週は「空き無し」と断定せず 2 段目も呼ばない', async () => {
    const proposeSlots = vi.fn(async () =>
      response({
        slots: Array.from({ length: 50 }, (_, i) => slot({ weekday: 0, course_label: `稲毛${i}` })),
      }),
    );
    const loadPatientSubOfficeIds = vi.fn(async () => [OFFICE_B]);
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      loadPatientSubOfficeIds,
      // 9/14(月) は候補あり・9/17(木) は 0 件だが打ち切りのため理由も 2 段目も出さない
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14', '2026-09-17'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    const row = await screen.findByTestId('ava-row-2026-09-17');
    expect(screen.getByTestId('ava-truncated-2026-09-17')).toBeInTheDocument();
    expect(row).not.toHaveTextContent('主担当拠点に空きがありません');
    expect(proposeSlots).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('ava-cand-2026-09-17-__M__')).toBeChecked();
  });

  it('M3 他拠点の候補から M は除く', async () => {
    const proposeSlots = vi.fn(async (req: { office_ids: string[] }) =>
      req.office_ids[0] === OFFICE_B
        ? response({
            slots: [
              slot({ weekday: 0, office_id: OFFICE_B, course_code: 'M', course_label: '都賀M' }),
              slot({ weekday: 0, office_id: OFFICE_B, course_label: '都賀A' }),
            ],
          })
        : response(),
    );
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      loadPatientSubOfficeIds: vi.fn(async () => [OFFICE_B]),
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });
    fireEvent.click(screen.getByTestId('ava-propose'));
    const row = await screen.findByTestId('ava-row-2026-09-14');
    await waitFor(() => expect(screen.getByTestId('ava-cand-2026-09-14-o0')).toBeInTheDocument());
    expect(row).not.toHaveTextContent('都賀M');
    expect(screen.queryByTestId('ava-cand-2026-09-14-o1')).not.toBeInTheDocument();
  });

  it('M8 承知チェックを外すと他拠点の選択が既定へ戻る', async () => {
    const proposeSlots = vi.fn(async (req: { office_ids: string[] }) =>
      req.office_ids[0] === OFFICE_B
        ? response({ slots: [slot({ weekday: 0, office_id: OFFICE_B, course_label: '都賀A' })] })
        : response(),
    );
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      loadPatientSubOfficeIds: vi.fn(async () => [OFFICE_B]),
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });
    fireEvent.click(screen.getByTestId('ava-propose'));
    await waitFor(() => expect(screen.getByTestId('ava-cand-2026-09-14-o0')).toBeInTheDocument());

    // 他拠点は (c) 新規追加では選べない (H3)。型も変える へ切り替える。
    fireEvent.click(screen.getByTestId('ava-scope-pattern'));
    fireEvent.click(screen.getByTestId('ava-other-office-2026-09-14'));
    fireEvent.click(screen.getByTestId('ava-cand-2026-09-14-o0'));
    expect(screen.getByTestId('ava-cand-2026-09-14-o0')).toBeChecked();

    fireEvent.click(screen.getByTestId('ava-other-office-2026-09-14'));
    expect(screen.getByTestId('ava-cand-2026-09-14-__M__')).toBeChecked();
    expect(screen.getByTestId('ava-cand-2026-09-14-o0')).not.toBeChecked();
    expect(screen.getByTestId('ava-submit')).toBeEnabled();
  });

  it('M4 実行中は登録ボタンを止める (二重送信ガード)', async () => {
    let resolveExecute: (() => void) | null = null;
    const onExecute = vi.fn(
      () =>
        new Promise<void>((res) => {
          resolveExecute = res;
        }),
    );
    renderDialog({
      patients: [NO_COORDS],
      onExecute: onExecute as unknown as AddVisitAnywhereDialogProps['onExecute'],
      initial: { patientId: NO_COORDS.id, dates: ['2026-09-14'] },
    });

    fireEvent.click(screen.getByTestId('ava-submit'));
    await waitFor(() => expect(screen.getByTestId('ava-submit')).toBeDisabled());
    fireEvent.click(screen.getByTestId('ava-submit'));
    expect(onExecute).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveExecute?.();
    });
  });

  it('M6 過去日・日曜が initial に混ざっていても落とす', () => {
    renderDialog({
      initial: {
        patientId: PATIENT_ID,
        dates: ['2026-09-01', '2026-09-13', '2026-09-14'], // 過去・日曜・有効
      },
    });
    expect(screen.getByTestId('ava-selected-dates')).toHaveTextContent('選択中: 9/14(月)');
    expect(screen.getByTestId('ava-submit')).toHaveTextContent('1 件を登録する');
  });

  it('条件を変えると提案は失効し、登録できなくなる', async () => {
    const proposeSlots = vi.fn(async () => response({ slots: [slot({ weekday: 0 })] }));
    renderDialog({
      proposeSlots: proposeSlots as unknown as AddVisitAnywhereDialogProps['proposeSlots'],
      initial: { patientId: PATIENT_ID, dates: ['2026-09-14'] },
    });

    fireEvent.click(screen.getByTestId('ava-propose'));
    await screen.findByTestId('ava-row-2026-09-14');
    expect(screen.getByTestId('ava-submit')).toBeEnabled();

    fireEvent.change(screen.getByTestId('ava-start'), { target: { value: '13:00' } });
    expect(screen.getByTestId('ava-stale')).toBeInTheDocument();
    expect(screen.getByTestId('ava-submit')).toBeDisabled();
  });

  it('特別訪問週間から開くと反映先は「新しく 1 件追加」に固定・日付も動かせない', () => {
    renderDialog({
      initial: {
        patientId: PATIENT_ID,
        dates: ['2026-09-14'],
        lockedScope: 'new',
        lockedDates: true,
      },
    });

    // ⑤ 反映先は 3 つとも触れず、new のまま。
    expect(screen.getByTestId('ava-scope-new')).toBeChecked();
    expect(screen.getByTestId('ava-scope-new')).toBeDisabled();
    expect(screen.getByTestId('ava-scope-week')).toBeDisabled();
    expect(screen.getByTestId('ava-scope-pattern')).toBeDisabled();
    expect(screen.getByTestId('ava-scope-locked-note')).toHaveTextContent(
      '特別訪問週間の追加枠として登録します（型は変えません）',
    );

    // ② 日付はカレンダーを出さず、渡された 1 日だけ。
    expect(screen.queryByTestId('ava-calendar')).toBeNull();
    expect(screen.queryByTestId('ava-clear-dates')).toBeNull();
    expect(screen.getByTestId('ava-dates-locked')).toBeInTheDocument();
    expect(screen.getByTestId('ava-selected-dates')).toHaveTextContent('選択中: 9/14(月)');
  });

  it('日付固定なら当日以前でも呼出元の日付をそのまま使う', () => {
    renderDialog({
      initial: {
        patientId: PATIENT_ID,
        dates: ['2026-09-01'], // todayIso=2026-09-07 より前
        lockedScope: 'new',
        lockedDates: true,
      },
    });

    expect(screen.getByTestId('ava-selected-dates')).toHaveTextContent('9/1');
  });
});
