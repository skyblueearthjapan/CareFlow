/** 「やること」ページの「毎月のやること」タブ (patient-tasks-recurring-design §3-1)。 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import type {
  RecurringCandidate,
  RecurringPreview,
  RecurringTaskRuleRead,
} from '@/lib/schemas/recurring-task';

const saveMutate = vi.fn();
const cancelOpenMutate = vi.fn();
let rules: RecurringTaskRuleRead[] = [];
let candidates: RecurringCandidate[] = [];
let preview: RecurringPreview | undefined;

const ITEM = '00000000-0000-0000-0000-0000000000a1';
const P1 = '00000000-0000-0000-0000-000000000001';
const P2 = '00000000-0000-0000-0000-000000000002';
const P3 = '00000000-0000-0000-0000-000000000003';

vi.mock('@/lib/queries/recurringTasks', () => ({
  useRecurringRules: () => ({ data: rules, isLoading: false, isError: false }),
  useRecurringCandidates: (cats: string[]) => ({
    data: candidates.filter((c) => cats.includes(c.visit_category)),
    isLoading: false,
  }),
  useRecurringPreview: () => ({ data: preview, isLoading: false, isError: false }),
  useSaveRecurringRule: () => ({ mutate: saveMutate, isPending: false }),
  useCancelRecurringOpen: () => ({ mutate: cancelOpenMutate, isPending: false }),
}));

vi.mock('@/lib/queries/tasks', () => ({
  useTaskItemTypes: () => ({
    data: [{ id: ITEM, name: '評価の入力', sort_order: 0, is_active: true }],
  }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { RecurringTasksPanel } from '../RecurringTasksPanel';

function makeRule(over: Partial<RecurringTaskRuleRead> = {}): RecurringTaskRuleRead {
  return {
    id: '00000000-0000-0000-0000-0000000000f1',
    item_type_id: ITEM,
    item_type_name: '評価の入力',
    item_type_active: true,
    memo: null,
    day_of_month: 1,
    target_categories: ['psychiatric'],
    start_month: '2026-12-01',
    is_active: true,
    excluded_patients: [{ patient_id: P2, name: '石田 健' }],
    target_count: 84,
    open_count: 0,
    created_at: '2026-11-02T00:00:00Z',
    ...over,
  };
}

describe('RecurringTasksPanel', () => {
  beforeEach(() => {
    saveMutate.mockReset();
    cancelOpenMutate.mockReset();
    rules = [];
    candidates = [
      {
        patient_id: P1,
        name: '青木 さくら',
        kana: null,
        visit_category: 'psychiatric',
        status: 'active',
      },
      {
        patient_id: P2,
        name: '石田 健',
        kana: null,
        visit_category: 'psychiatric',
        status: 'active',
      },
      {
        patient_id: P3,
        name: '加藤 みのり',
        kana: null,
        visit_category: 'general',
        status: 'active',
      },
    ];
    preview = undefined;
  });

  it('設定の一覧に項目・対象・何日から・人数・状態を出す', () => {
    rules = [makeRule()];
    render(<RecurringTasksPanel />);
    const row = screen.getByTestId(`recurring-row-${rules[0]!.id}`);
    expect(row.textContent).toContain('評価の入力');
    expect(row.textContent).toContain('精神科の方・1 名を外す');
    expect(row.textContent).toContain('毎月 1 日から');
    expect(row.textContent).toContain('84 名');
    expect(row.textContent).toContain('有効');
    // 有効な設定には「まとめて取り消す」を出さない
    expect(screen.queryByTestId(`recurring-cancel-open-${rules[0]!.id}`)).toBeNull();
  });

  it('停止中で未完了が残る設定は、確かめてからまとめて取り消す', () => {
    rules = [makeRule({ is_active: false, open_count: 3 })];
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<RecurringTasksPanel />);
    fireEvent.click(screen.getByTestId(`recurring-cancel-open-${rules[0]!.id}`));
    expect(confirm).toHaveBeenCalled();
    expect(cancelOpenMutate).toHaveBeenCalledWith(rules[0]!.id, expect.any(Object));
    confirm.mockRestore();
  });

  it('作る欄: 区分と外す方で人数が変わり、保存で送る', () => {
    render(<RecurringTasksPanel />);
    fireEvent.click(screen.getByTestId('recurring-add-open'));
    expect(screen.getByTestId('recurring-active-count').textContent).toContain('2 名');

    fireEvent.click(screen.getByTestId(`recurring-patient-${P2}`)); // 外す
    expect(screen.getByTestId('recurring-active-count').textContent).toContain('1 名');
    fireEvent.click(screen.getByTestId('recurring-category-general'));
    expect(screen.getByTestId('recurring-active-count').textContent).toContain('2 名');

    // 項目も伝言も空なら保存できない
    expect(screen.getByTestId('recurring-save')).toBeDisabled();
    fireEvent.change(screen.getByTestId('recurring-item'), { target: { value: ITEM } });
    fireEvent.change(screen.getByTestId('recurring-day'), { target: { value: '3' } });
    fireEvent.click(screen.getByTestId('recurring-save'));
    expect(saveMutate).toHaveBeenCalledTimes(1);
    const [vars] = saveMutate.mock.calls[0]!;
    expect(vars.id).toBeNull();
    expect(vars.payload).toMatchObject({
      item_type_id: ITEM,
      memo: null,
      day_of_month: 3,
      target_categories: ['psychiatric', 'general'],
      is_active: true,
      excluded_patient_ids: [P2],
    });
    expect(vars.payload.start_month).toMatch(/^\d{4}-\d{2}-01$/);
  });

  it('来月の試し表示: 出る訪問・訪問が無い方・外す方・先月の残り', () => {
    rules = [makeRule()];
    preview = {
      rule_id: rules[0]!.id,
      month: '2026-12-01',
      start_date: '2026-12-01',
      is_active: true,
      item_is_active: true,
      counts: { create: 2, excluded: 1 },
      prev_open_count: 1,
      rows: [
        {
          patient_id: P1,
          patient_name: '青木 さくら',
          status: 'active',
          visit_category: 'psychiatric',
          result: 'create',
          visit: {
            visit_id: '00000000-0000-0000-0000-00000000aa01',
            visit_date: '2026-12-01',
            start_time: '10:00',
            end_time: '10:30',
            staff: [
              {
                staff_id: '00000000-0000-0000-0000-00000000bb01',
                name: '佐藤 花子',
                role: 'primary',
              },
            ],
          },
          prev_open: true,
        },
        {
          patient_id: P3,
          patient_name: '上野 ゆり',
          status: 'active',
          visit_category: 'psychiatric',
          result: 'create',
          visit: null,
          prev_open: false,
        },
        {
          patient_id: P2,
          patient_name: '石田 健',
          status: 'active',
          visit_category: 'psychiatric',
          result: 'excluded',
          visit: null,
          prev_open: false,
        },
      ],
    };
    render(<RecurringTasksPanel />);
    const box = screen.getByTestId('recurring-preview');
    expect(box.textContent).toContain('作る 2 名');
    expect(screen.getByTestId(`recurring-preview-row-${P1}`).textContent).toContain(
      '12/1（火） 10:00・佐藤 花子',
    );
    expect(screen.getByTestId(`recurring-preview-row-${P1}`).textContent).toContain(
      '先月までの分が残っています',
    );
    expect(screen.getByTestId(`recurring-preview-row-${P3}`).textContent).toContain(
      '訪問が入ったら出ます',
    );
    expect(screen.getByTestId(`recurring-preview-row-${P2}`).textContent).toContain(
      '個別に外した方',
    );
  });
});
