/**
 * PatientFixedVisitsPanel vitest unit tests (W9-FE1 / W22 拡張).
 *
 * テストケース:
 * 1. 初期値 (空) → 全曜日 OFF
 * 2. normal タブで月曜 ON + 14:00 + 30 → 「保存」で PUT が呼ばれる
 * 3. 重複 weekday → エラー表示
 * 4. 「希望から自動生成」→ form state にコピー
 * 5. staff role → readonly (フィールド disable)
 * 6. DELETE 確認ダイアログ
 * 7. (W22) 曜日 ON → コース dropdown が表示される
 * 8. (W22) office に紐付く course_templates のみ option に含む
 * 9. (W22) コース選択後「保存」→ course_template_id が payload に含まれる
 * 10. (W22) コース「未指定」選択 → payload に null が入る
 * 11. (W22) primaryOfficeId が null → "未指定" のみの dropdown が表示される
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// ─── Mock next-auth ───────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

// ─── Mock query hooks ─────────────────────────────────────────────────────────
vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: vi.fn(),
  useUpdateFixedVisits: vi.fn(),
  useDeleteFixedVisits: vi.fn(),
  useApplyFromWeek: vi.fn(),
}));

// ─── Mock course_templates query ──────────────────────────────────────────────
vi.mock('@/lib/queries/course_templates', () => ({
  useCourseTemplates: vi.fn(),
}));

// ─── Mock toast ───────────────────────────────────────────────────────────────
vi.mock('@/components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { useSession } from 'next-auth/react';
import {
  useFixedVisits,
  useUpdateFixedVisits,
  useDeleteFixedVisits,
  useApplyFromWeek,
} from '@/lib/queries/patient_fixed_visits';
import { useCourseTemplates } from '@/lib/queries/course_templates';
import { toast } from '@/components/ui/sonner';

import { PatientFixedVisitsPanel } from '../PatientFixedVisitsPanel';

// ─── Helpers ──────────────────────────────────────────────────────────────────

const PATIENT_ID = '00000000-0000-0000-0000-000000000001';

function makeSession(role: 'admin' | 'manager' | 'staff') {
  return { data: { user: { role } }, status: 'authenticated' };
}

function makeQueryResult<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null };
}

function makeMutation(mutateAsyncFn: Mock = vi.fn().mockResolvedValue([])) {
  return {
    mutateAsync: mutateAsyncFn,
    isPending: false,
    isError: false,
  };
}

function setupMocks(
  opts: {
    role?: 'admin' | 'manager' | 'staff';
    reads?: unknown[];
    updateFn?: Mock;
    deleteFn?: Mock;
    fromWeekFn?: Mock;
    courseTemplates?: { id: string; label: string; office_id: string }[];
  } = {},
) {
  const role = opts.role ?? 'admin';
  (useSession as Mock).mockReturnValue(makeSession(role));
  (useFixedVisits as Mock).mockReturnValue(makeQueryResult(opts.reads ?? []));
  (useUpdateFixedVisits as Mock).mockReturnValue(makeMutation(opts.updateFn));
  (useDeleteFixedVisits as Mock).mockReturnValue(makeMutation(opts.deleteFn));
  (useApplyFromWeek as Mock).mockReturnValue(makeMutation(opts.fromWeekFn));
  (useCourseTemplates as Mock).mockReturnValue(makeQueryResult(opts.courseTemplates ?? []));
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('PatientFixedVisitsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 初期値 (空) → 全曜日 OFF (訪問なし が表示される)', () => {
    setupMocks({ reads: [] });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} />);

    // 7 曜日すべてに「訪問なし」が表示されるはず
    const noVisitElements = screen.getAllByText('訪問なし');
    expect(noVisitElements).toHaveLength(7);
  });

  it('2. normal タブ: 月曜 ON + 14:00 + 30 min → 保存で PUT が呼ばれる', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ reads: [], updateFn });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} />);

    // 月曜 (weekday=0) のチェックボックスを ON にする
    const checkboxes = screen.getAllByRole('checkbox');
    // チェックボックスは 7 つ (月〜日)
    await userEvent.click(checkboxes[0]);

    // 開始時刻を 14:00 に変更
    const timeSelects = screen.getAllByLabelText(/開始時刻/);
    fireEvent.change(timeSelects[0], { target: { value: '14:00' } });

    // 所要時間を 30 に変更 (デフォルト値だが明示)
    const durationSelects = screen.getAllByLabelText(/所要時間/);
    fireEvent.change(durationSelects[0], { target: { value: '30' } });

    // 保存ボタンをクリック
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const call = updateFn.mock.calls[0][0] as { mode: string; items: unknown[] };
    expect(call.mode).toBe('normal');
    expect(call.items).toHaveLength(1);
    expect((call.items[0] as { weekday: number; start_time: string }).weekday).toBe(0);
    expect((call.items[0] as { weekday: number; start_time: string }).start_time).toBe('14:00');
  });

  it('3. 重複 weekday → zod エラーが表示される', async () => {
    // 月曜が 2 件あるデータをサーバーから返す（正常にはありえないが form 操作でエラーを起こす）
    // 実際には UI 上で同じ曜日を 2 回 ON にできないため、
    // schema の superRefine をバイパスして直接 validate をテストするのが現実的。
    // ここでは週間グリッドでなくビジネスロジック的に重複チェックを検証する。
    const { patientFixedVisitsBulkPutSchema } = await import(
      '@/lib/schemas/v2/patient_fixed_visit'
    );

    const result = patientFixedVisitsBulkPutSchema.safeParse({
      mode: 'normal',
      items: [
        { weekday: 0, start_time: '09:00', duration_min: 30 },
        { weekday: 0, start_time: '14:00', duration_min: 60 },
      ],
    });

    expect(result.success).toBe(false);
    if (!result.success) {
      const dupIssue = result.error.issues.find((i) => i.message === '同じ曜日が重複しています');
      expect(dupIssue).toBeDefined();
    }
  });

  it('4. 「希望から自動生成」→ preferred_weekdays が form state にコピーされる', async () => {
    setupMocks({ reads: [] });

    const weeklyPattern = {
      frequency_per_week: 2,
      visit_frequency: null,
      visit_weeks: null,
      preferred_weekdays: ['Mon', 'Wed'] as const,
      weekday_priority: '中' as const,
      service_minutes: 60,
      time_type: '固定' as const,
      preferred_start: '10:00',
      preferred_end: null,
      ng_weekdays: null,
    };

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} weeklyPattern={weeklyPattern} />);

    const autoBtn = screen.getByRole('button', { name: '希望から自動生成' });
    await userEvent.click(autoBtn);

    // toast.success が呼ばれることを確認
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining('希望パターンをフォームに反映しました'),
    );

    // 月曜・水曜のチェックボックスが ON になっているはず
    // (「訪問なし」が 5 つに減っているはず: 火・木・金・土・日)
    const noVisitElements = screen.getAllByText('訪問なし');
    expect(noVisitElements).toHaveLength(5);
  });

  it('5. staff role → 閲覧のみ (フィールドが disabled)', () => {
    setupMocks({ role: 'staff', reads: [] });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} />);

    // 「閲覧のみ」バッジが表示される
    expect(screen.getByText('閲覧のみ')).toBeInTheDocument();

    // チェックボックスが disabled
    const checkboxes = screen.getAllByRole('checkbox');
    checkboxes.forEach((cb) => {
      expect(cb).toBeDisabled();
    });

    // 保存ボタンが存在しない
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument();
  });

  it('6. 「リセット」ボタン → 確認ダイアログが表示され、削除を実行できる', async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({ reads: [], deleteFn });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} />);

    // リセットボタンをクリック
    const resetBtn = screen.getByRole('button', { name: 'リセット (全削除)' });
    await userEvent.click(resetBtn);

    // 確認ダイアログが開く
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('固定枠を削除しますか？')).toBeInTheDocument();

    // 「削除する」ボタンをクリック
    const deleteBtn = screen.getByRole('button', { name: '削除する' });
    await userEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteFn).toHaveBeenCalledTimes(1);
      expect(deleteFn).toHaveBeenCalledWith('normal');
    });

    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('削除しました'));
  });

  // ─── W22: course_template_id tests ────────────────────────────────────────

  const OFFICE_ID = '00000000-0000-0000-0000-000000000010';
  const COURSE_TEMPLATES = [
    {
      id: 'aaaaaaaa-0000-0000-0000-000000000001',
      label: 'A',
      office_id: OFFICE_ID,
      capacity_mon: 5,
      capacity_tue: 5,
      capacity_wed: 5,
      capacity_thu: 5,
      capacity_fri: 5,
      capacity_sat: 0,
      capacity_sun: 0,
      notes: null,
      created_at: '2026-01-01T00:00:00',
      updated_at: '2026-01-01T00:00:00',
    },
    {
      id: 'bbbbbbbb-0000-0000-0000-000000000002',
      label: 'B',
      office_id: OFFICE_ID,
      capacity_mon: 3,
      capacity_tue: 3,
      capacity_wed: 3,
      capacity_thu: 3,
      capacity_fri: 3,
      capacity_sat: 0,
      capacity_sun: 0,
      notes: null,
      created_at: '2026-01-01T00:00:00',
      updated_at: '2026-01-01T00:00:00',
    },
  ];

  it('7. (W22) 曜日 ON にしたときコース dropdown が表示される', async () => {
    setupMocks({ reads: [], courseTemplates: COURSE_TEMPLATES });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={OFFICE_ID} />);

    // 月曜を ON にする
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    // コース dropdown (aria-label: "月 コース") が表示される
    expect(screen.getByLabelText('月 コース')).toBeInTheDocument();
  });

  it('8. (W22) office に紐付く course_templates のみ option に含む', async () => {
    setupMocks({ reads: [], courseTemplates: COURSE_TEMPLATES });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={OFFICE_ID} />);

    // 月曜 ON
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    const courseSelect = screen.getByLabelText('月 コース');
    // "未指定" + "A" + "B" の 3 options
    const options = Array.from((courseSelect as HTMLSelectElement).options).map((o) => o.text);
    expect(options).toContain('未指定');
    expect(options).toContain('A');
    expect(options).toContain('B');
    expect(options).toHaveLength(3);
  });

  it('9. (W22) コース選択後「保存」→ course_template_id が payload に含まれる', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ reads: [], updateFn, courseTemplates: COURSE_TEMPLATES });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={OFFICE_ID} />);

    // 月曜を ON
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    // コース A を選択
    const courseSelect = screen.getByLabelText('月 コース');
    fireEvent.change(courseSelect, { target: { value: 'aaaaaaaa-0000-0000-0000-000000000001' } });

    // 保存
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const call = updateFn.mock.calls[0][0] as {
      mode: string;
      items: { weekday: number; course_template_id: string | null }[];
    };
    expect(call.items).toHaveLength(1);
    expect(call.items[0].course_template_id).toBe('aaaaaaaa-0000-0000-0000-000000000001');
  });

  it('10. (W22) コース「未指定」選択 → payload に null が入る', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    // 月曜に既にコース A が設定された状態でロード
    setupMocks({
      reads: [
        {
          id: 'read-0001',
          patient_id: PATIENT_ID,
          weekday: 0,
          start_time: '09:00',
          duration_min: 30,
          mode: 'normal',
          course_template_id: 'aaaaaaaa-0000-0000-0000-000000000001',
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        },
      ],
      updateFn,
      courseTemplates: COURSE_TEMPLATES,
    });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={OFFICE_ID} />);

    // コース dropdown が表示されるまで待機
    await waitFor(() => {
      expect(screen.getByLabelText('月 コース')).toBeInTheDocument();
    });

    // "未指定" に変更
    const courseSelect = screen.getByLabelText('月 コース');
    fireEvent.change(courseSelect, { target: { value: '' } });

    // 保存
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const call = updateFn.mock.calls[0][0] as {
      mode: string;
      items: { weekday: number; course_template_id: string | null }[];
    };
    expect(call.items[0].course_template_id).toBeNull();
  });

  it('11. (W22) primaryOfficeId が null → コース dropdown に "未指定" のみ表示', async () => {
    setupMocks({ reads: [], courseTemplates: [] });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={null} />);

    // 月曜 ON
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    const courseSelect = screen.getByLabelText('月 コース');
    const options = Array.from((courseSelect as HTMLSelectElement).options).map((o) => o.text);
    expect(options).toEqual(['未指定']);
  });
});
