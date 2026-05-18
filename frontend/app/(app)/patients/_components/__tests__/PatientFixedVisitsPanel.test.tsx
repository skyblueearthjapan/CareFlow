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

// ─── Mock offices query (Phase E-5) ───────────────────────────────────────────
vi.mock('@/lib/queries/offices', () => ({
  useOffices: vi.fn(),
}));

// ─── Mock @tanstack/react-query useQueries (Phase E-5) ────────────────────────
// eslint-disable-next-line @typescript-eslint/consistent-type-imports
type TanstackQueryModule = typeof import('@tanstack/react-query');
vi.mock('@tanstack/react-query', async () => {
  const actual = await vi.importActual<TanstackQueryModule>('@tanstack/react-query');
  return {
    ...actual,
    useQueries: vi.fn(),
  };
});

// ─── Mock toast ───────────────────────────────────────────────────────────────
vi.mock('@/components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { useSession } from 'next-auth/react';
import { useQueries } from '@tanstack/react-query';
import {
  useFixedVisits,
  useUpdateFixedVisits,
  useDeleteFixedVisits,
  useApplyFromWeek,
} from '@/lib/queries/patient_fixed_visits';
import { useCourseTemplates } from '@/lib/queries/course_templates';
import { useOffices } from '@/lib/queries/offices';
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
    offices?: { id: string; name: string; code?: string | null }[];
    subOfficeCourseTemplates?: { id: string; label: string; office_id: string }[];
  } = {},
) {
  const role = opts.role ?? 'admin';
  (useSession as Mock).mockReturnValue(makeSession(role));
  (useFixedVisits as Mock).mockReturnValue(makeQueryResult(opts.reads ?? []));
  (useUpdateFixedVisits as Mock).mockReturnValue(makeMutation(opts.updateFn));
  (useDeleteFixedVisits as Mock).mockReturnValue(makeMutation(opts.deleteFn));
  (useApplyFromWeek as Mock).mockReturnValue(makeMutation(opts.fromWeekFn));
  (useCourseTemplates as Mock).mockReturnValue(makeQueryResult(opts.courseTemplates ?? []));
  // Phase E-5 (項目 ⑥B): useOffices と useQueries (sub-office course templates 用) を mock.
  (useOffices as Mock).mockReturnValue({
    offices: opts.offices ?? [],
    allOffices: opts.offices ?? [],
    data: opts.offices ?? [],
    isLoading: false,
    isError: false,
    error: null,
  });
  // useQueries は queries 配列の長さに応じた配列 (data 入り) を返すスタブ.
  // sub_office_id 単位で個別 fetch されるが、本テストでは subOfficeCourseTemplates
  // をそのまま全 query に流す簡易 mock とする.
  (useQueries as Mock).mockImplementation((options: { queries: { queryKey: unknown[] }[] }) =>
    options.queries.map(() => ({ data: opts.subOfficeCourseTemplates ?? [] })),
  );
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
      // W37 Phase 3-A: メッセージは「同じ曜日・スロットが重複しています」に変更.
      // (weekday, slot_index) ペアで重複検出する.
      const dupIssue = result.error.issues.find((i) =>
        i.message.includes('同じ曜日・スロットが重複'),
      );
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

  it('5. staff role → 閲覧のみ (ReadOnlyWeekGrid 表示 + 保存ボタンなし)', () => {
    // W37 Phase 3-D: readonly 時は ReadOnlyWeekGrid に切り替わるためチェックボックスなし.
    setupMocks({ role: 'staff', reads: [] });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} />);

    // 「閲覧のみ」バッジが表示される
    expect(screen.getByText('閲覧のみ')).toBeInTheDocument();

    // ReadOnlyWeekGrid が表示されチェックボックスなし
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);

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

  // ─── W26: readOnly prop tests ──────────────────────────────────────────────

  it('12. (W26) readOnly=true → ReadOnlyWeekGrid が表示され、チェックボックスなし', () => {
    // W37 Phase 3-D: readOnly=true の場合は ReadOnlyWeekGrid (テキスト表示) に切り替わる.
    // チェックボックスは表示されない.
    setupMocks({ role: 'admin', reads: [] });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} readOnly={true} />);

    // チェックボックスなし
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    // 「訪問なし」テキストが 7 つ表示される (ReadOnlyWeekGrid の各行)
    const noVisits = screen.getAllByText('訪問なし');
    expect(noVisits).toHaveLength(7);
  });

  it('13. (W26) readOnly=true → 「保存」ボタンが非表示', () => {
    // admin role でも readOnly=true なら保存ボタンなし
    setupMocks({ role: 'admin', reads: [] });

    render(<PatientFixedVisitsPanel patientId={PATIENT_ID} readOnly={true} />);

    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument();
  });

  // ─── W37 Phase 3-A: requires_multiple_staff (slot 0/1) tests ───────────────

  it('14. (W37) requiresMultipleStaff=true: コース 2 セレクタが enabled', async () => {
    setupMocks({ reads: [], courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    // 月曜を ON にする
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    const course1 = screen.getByLabelText('月 コース 1') as HTMLSelectElement;
    const course2 = screen.getByLabelText('月 コース 2') as HTMLSelectElement;

    expect(course1).toBeInTheDocument();
    expect(course2).toBeInTheDocument();
    expect(course2).not.toBeDisabled();
  });

  it('15. (W37) requiresMultipleStaff=false: コース 2 セレクタが disabled', async () => {
    setupMocks({ reads: [], courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={false}
      />,
    );

    // 月曜を ON にする
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    // フラグ OFF: aria-label は "月 コース" (コース 1 ラベルではない単一コース表記)
    expect(screen.getByLabelText('月 コース')).toBeInTheDocument();
    const course2 = screen.getByLabelText('月 コース 2') as HTMLSelectElement;
    expect(course2).toBeDisabled();
  });

  it('16. (W37) コース 1 と コース 2 が同一 → エラー表示で保存ブロック', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ reads: [], updateFn, courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    // 月曜を ON にして 同じ UUID をコース 1 / 2 に設定
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    const course1 = screen.getByLabelText('月 コース 1');
    const course2 = screen.getByLabelText('月 コース 2');
    fireEvent.change(course1, { target: { value: 'aaaaaaaa-0000-0000-0000-000000000001' } });
    fireEvent.change(course2, { target: { value: 'aaaaaaaa-0000-0000-0000-000000000001' } });

    // エラーメッセージが表示される (UI 行内)
    await waitFor(() => {
      expect(screen.getByText('異なるコースを選択してください')).toBeInTheDocument();
    });

    // 保存ボタンを押しても updateFn は呼ばれない
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    expect(updateFn).not.toHaveBeenCalled();
  });

  it('17. (W37) コース 2 空のまま保存 → 警告は出るが保存自体は通る (slot_index=0 のみ送信)', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ reads: [], updateFn, courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    // 月曜を ON にして コース 1 のみ設定 (コース 2 は空のまま)
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    const course1 = screen.getByLabelText('月 コース 1');
    fireEvent.change(course1, { target: { value: 'aaaaaaaa-0000-0000-0000-000000000001' } });

    // 警告が表示される
    await waitFor(() => {
      expect(screen.getByText('2 名対応の片方未設定')).toBeInTheDocument();
    });

    // 保存ボタンクリック → 通る
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const call = updateFn.mock.calls[0][0] as {
      mode: string;
      items: { weekday: number; slot_index: number; course_template_id: string | null }[];
    };
    // 寛容モード: slot_index=0 の 1 件のみ送る
    expect(call.items).toHaveLength(1);
    expect(call.items[0].slot_index).toBe(0);
    expect(call.items[0].course_template_id).toBe('aaaaaaaa-0000-0000-0000-000000000001');
  });

  it('18. (W37) コース 1/2 両方設定 → bulk PUT で slot_index 0/1 のペアが送られる', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ reads: [], updateFn, courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    // 月曜を ON
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    // コース 1 = A, コース 2 = B を選択
    const course1 = screen.getByLabelText('月 コース 1');
    const course2 = screen.getByLabelText('月 コース 2');
    fireEvent.change(course1, { target: { value: 'aaaaaaaa-0000-0000-0000-000000000001' } });
    fireEvent.change(course2, { target: { value: 'bbbbbbbb-0000-0000-0000-000000000002' } });

    // 保存
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const call = updateFn.mock.calls[0][0] as {
      mode: string;
      items: {
        weekday: number;
        slot_index: number;
        course_template_id: string | null;
        start_time: string;
        duration_min: number;
      }[];
    };
    expect(call.items).toHaveLength(2);

    // slot 0 (course A)
    const slot0 = call.items.find((it) => it.slot_index === 0);
    expect(slot0).toBeDefined();
    expect(slot0?.weekday).toBe(0);
    expect(slot0?.course_template_id).toBe('aaaaaaaa-0000-0000-0000-000000000001');

    // slot 1 (course B)
    const slot1 = call.items.find((it) => it.slot_index === 1);
    expect(slot1).toBeDefined();
    expect(slot1?.weekday).toBe(0);
    expect(slot1?.course_template_id).toBe('bbbbbbbb-0000-0000-0000-000000000002');

    // start_time / duration_min は slot 0/1 で共通
    expect(slot0?.start_time).toBe(slot1?.start_time);
    expect(slot0?.duration_min).toBe(slot1?.duration_min);
  });

  it('19. (W37) requiresMultipleStaff=false でも slot_index=0 が payload に含まれる (regression)', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ reads: [], updateFn, courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={false}
      />,
    );

    // 月曜を ON してコースのみ設定
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    // 保存
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const call = updateFn.mock.calls[0][0] as {
      mode: string;
      items: { weekday: number; slot_index: number }[];
    };
    expect(call.items).toHaveLength(1);
    expect(call.items[0].slot_index).toBe(0);
  });

  it('20. (W37) サーバーから slot 0/1 のペアが返る → コース 1/2 dropdown に反映', async () => {
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
          slot_index: 0,
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        },
        {
          id: 'read-0002',
          patient_id: PATIENT_ID,
          weekday: 0,
          start_time: '09:00',
          duration_min: 30,
          mode: 'normal',
          course_template_id: 'bbbbbbbb-0000-0000-0000-000000000002',
          slot_index: 1,
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        },
      ],
      courseTemplates: COURSE_TEMPLATES,
    });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText('月 コース 1')).toBeInTheDocument();
    });

    const course1 = screen.getByLabelText('月 コース 1') as HTMLSelectElement;
    const course2 = screen.getByLabelText('月 コース 2') as HTMLSelectElement;
    expect(course1.value).toBe('aaaaaaaa-0000-0000-0000-000000000001');
    expect(course2.value).toBe('bbbbbbbb-0000-0000-0000-000000000002');
  });

  it('21. (W37) ヘルプテキスト: requiresMultipleStaff=true で表示, false で非表示', () => {
    setupMocks({ reads: [], courseTemplates: COURSE_TEMPLATES });

    const { rerender } = render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    // フラグ ON: ヘルプ表示
    expect(screen.getAllByText(/2 名体制/).length).toBeGreaterThan(0);

    rerender(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={false}
      />,
    );

    // フラグ OFF: ヘルプテキスト本文 (「同時刻に異なるコースを 2 つ設定する必要があります。」) は無い
    expect(
      screen.queryByText(/同時刻に異なるコースを 2 つ設定する必要があります/),
    ).not.toBeInTheDocument();
  });

  // ─── W37 hotfix M-4: コース 1 空 + コース 2 のみ → コース 2 を slot 0 に格上げ ─
  it('M4. (W37) コース 1 空 + コース 2 のみ設定 → コース 2 を slot 0 に格上げ (1 行のみ送信)', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ reads: [], updateFn, courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    // 月曜を ON
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    // コース 1 は空のまま, コース 2 = B のみ選択
    const course2 = screen.getByLabelText('月 コース 2');
    fireEvent.change(course2, { target: { value: 'bbbbbbbb-0000-0000-0000-000000000002' } });

    // 保存
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const call = updateFn.mock.calls[0][0] as {
      mode: string;
      items: { weekday: number; slot_index: number; course_template_id: string | null }[];
    };

    // 1 行のみ送信 (= slot 0=NULL + slot 1=B の 2 行送信ではない)
    expect(call.items).toHaveLength(1);
    expect(call.items[0].slot_index).toBe(0);
    // コース 2 (B) が slot 0 に格上げされている
    expect(call.items[0].course_template_id).toBe('bbbbbbbb-0000-0000-0000-000000000002');
    expect(call.items[0].weekday).toBe(0);
  });

  it('M4-warn. (W37) コース 1 空 + コース 2 のみ → 警告 「2 名対応の片方未設定」 表示は維持', async () => {
    setupMocks({ reads: [], courseTemplates: COURSE_TEMPLATES });

    render(
      <PatientFixedVisitsPanel
        patientId={PATIENT_ID}
        primaryOfficeId={OFFICE_ID}
        requiresMultipleStaff={true}
      />,
    );

    // 月曜を ON
    const checkboxes = screen.getAllByRole('checkbox');
    await userEvent.click(checkboxes[0]);

    // コース 1 は空, コース 2 = B のみ
    const course2 = screen.getByLabelText('月 コース 2');
    fireEvent.change(course2, { target: { value: 'bbbbbbbb-0000-0000-0000-000000000002' } });

    // 警告が表示される
    expect(await screen.findByTestId('row-warning-0')).toHaveTextContent('2 名対応の片方未設定');
  });

  // ─── Phase E-5 (項目 ⑥B): サブ拠点 (sub_office_id) selector tests ──────────
  describe('Phase E-5 (sub_office)', () => {
    const INAGE_ID = '11111111-0000-0000-0000-000000000001';
    const TSUGA_ID = '22222222-0000-0000-0000-000000000002';
    const OFFICES = [
      { id: INAGE_ID, name: '稲毛', code: 'INAGE' },
      { id: TSUGA_ID, name: '都賀', code: 'TSUGA' },
    ];

    it('E5-1. 主担当拠点以外の office が複数ある場合 サブ拠点 selector が表示される', () => {
      setupMocks({ reads: [], offices: OFFICES });
      render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={INAGE_ID} />);
      // 月曜 ON にしないと selector は出ない
      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);
      const subSelect = screen.getByTestId('sub-office-select-0');
      // 主担当 (稲毛) を除いた候補 (都賀) のみが含まれる
      const options = Array.from(subSelect.querySelectorAll('option')).map((o) => o.textContent);
      expect(options).toContain('主担当拠点');
      expect(options).toContain('都賀');
      expect(options).not.toContain('稲毛');
    });

    it('E5-2. サブ拠点を選択して保存すると sub_office_id が payload に含まれる', async () => {
      const updateFn = vi.fn().mockResolvedValue([]);
      setupMocks({ reads: [], offices: OFFICES, updateFn });
      render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={INAGE_ID} />);

      // 月曜 ON
      const checkboxes = screen.getAllByRole('checkbox');
      await userEvent.click(checkboxes[0]);

      // サブ拠点 = 都賀
      const subSelect = screen.getByTestId('sub-office-select-0');
      fireEvent.change(subSelect, { target: { value: TSUGA_ID } });

      // 保存
      const saveBtn = screen.getByRole('button', { name: '保存' });
      await userEvent.click(saveBtn);

      await waitFor(() => expect(updateFn).toHaveBeenCalledTimes(1));
      const call = updateFn.mock.calls[0][0] as {
        items: { sub_office_id?: string | null }[];
      };
      expect(call.items[0]?.sub_office_id).toBe(TSUGA_ID);
    });

    it('E5-3. サブ拠点未選択 (主担当拠点) で保存すると sub_office_id=null', async () => {
      const updateFn = vi.fn().mockResolvedValue([]);
      setupMocks({ reads: [], offices: OFFICES, updateFn });
      render(<PatientFixedVisitsPanel patientId={PATIENT_ID} primaryOfficeId={INAGE_ID} />);

      const checkboxes = screen.getAllByRole('checkbox');
      await userEvent.click(checkboxes[0]);

      const saveBtn = screen.getByRole('button', { name: '保存' });
      await userEvent.click(saveBtn);

      await waitFor(() => expect(updateFn).toHaveBeenCalledTimes(1));
      const call = updateFn.mock.calls[0][0] as {
        items: { sub_office_id?: string | null }[];
      };
      expect(call.items[0]?.sub_office_id ?? null).toBeNull();
    });

    it('E5-4. patientFixedVisitV2BaseSchema が sub_office_id を受理する', async () => {
      const { patientFixedVisitV2BaseSchema } = await import(
        '@/lib/schemas/v2/patient_fixed_visit'
      );
      const parsed = patientFixedVisitV2BaseSchema.parse({
        weekday: 0,
        start_time: '09:00',
        duration_min: 30,
        sub_office_id: TSUGA_ID,
      });
      expect(parsed.sub_office_id).toBe(TSUGA_ID);
    });
  });
});
