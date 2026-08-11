/**
 * PatientEditDialog — Phase G-20 テスト.
 *
 * シナリオ:
 *   1. open=false → ダイアログが描画されない
 *   2. open=true / canEdit=true → タイトル "患者情報を編集" + PatientForm + PFV パネルが描画される
 *   3. canEdit=false → 「権限がありません」アラート + PatientForm 非表示
 *   4. patientId=null → ボディは空 (loading にも error にもならない)
 *   5. 保存ボタンクリック → useUpdatePatient.mutateAsync が呼ばれ、success 後に onClose
 *   6. キャンセル (isDirty=false) → confirm 無しで onClose
 *
 * 重い子コンポーネント (PatientForm / PatientFixedVisitsPanel) は本テストの責務外
 * (= 各自のテストで確認済) なので shallow に mock する。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// ─── Mocks (vi.mock は import より hoist される) ─────────────────────────────

const { mockToast, mockInvalidate, mockUpdate, hooks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mockInvalidate: vi.fn(),
  mockUpdate: vi.fn(),
  hooks: {
    usePatientResult: {
      data: {
        id: 'p-1',
        code: 'P-0001',
        name: '山田 太郎',
        kana: '',
        sex: null,
        status: 'active',
        insurance: null,
        address: '',
        lat: null,
        lng: null,
        primary_office_id: null,
        sex_restriction: null,
        requires_multiple_staff: false,
        note: '',
        weekly_pattern: null,
        special_weekly_pattern: null,
      } as unknown,
      isLoading: false,
      isError: false,
      error: null,
    },
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('@/components/ui/sonner', () => ({ toast: mockToast }));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidate }),
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatient: () => hooks.usePatientResult,
  useUpdatePatient: () => ({ mutateAsync: mockUpdate, isPending: false }),
}));

vi.mock('@/lib/schemas/patient', () => ({
  patientReadToFormValues: (p: { name?: string; code?: string }) => ({
    code: p.code ?? '',
    name: p.name ?? '',
  }),
}));

vi.mock('lucide-react', () => ({
  Loader2: () => <span data-testid="icon-loader" />,
  Pencil: () => <span data-testid="icon-pencil" />,
  X: () => <span data-testid="icon-x" />,
}));

// PatientForm: 「更新」ボタン + 「キャンセル」ボタンだけ持つ shallow mock.
//   - props.onSubmit を「更新」ボタンクリックで呼ぶ
//   - props.onCancel を「キャンセル」ボタンクリックで呼ぶ
//   - props.onDirtyChange を mount 時に false で呼ぶ (= 親側 isDirty=false)
interface FormProps {
  onSubmit?: (v: unknown) => Promise<void> | void;
  onCancel?: () => void;
  onDirtyChange?: (b: boolean) => void;
  submitLabel?: string;
  submitting?: boolean;
  errorMessage?: string | null;
  /** 回帰テスト用: 親が patientId を渡しているか (= 訪問条件セクションが出るか)。 */
  patientId?: string;
}
vi.mock('@/app/(app)/patients/_components/PatientForm', () => ({
  PatientForm: (props: FormProps) => {
    React.useEffect(() => {
      props.onDirtyChange?.(false);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div data-testid="mock-patient-form" data-patient-id={props.patientId ?? ''}>
        {/* patientId が渡ったときだけ描画される「訪問条件」内セクション
            (本物の PatientForm と同じ条件) を再現する。 */}
        {props.patientId ? (
          <>
            <div data-testid="mock-same-address-links-section" />
            <div data-testid="mock-patient-ng-staff-section" />
          </>
        ) : null}
        {props.errorMessage ? (
          <p data-testid="mock-patient-form-error">{props.errorMessage}</p>
        ) : null}
        <button
          type="button"
          data-testid="mock-patient-form-submit"
          disabled={props.submitting}
          onClick={() => {
            void props.onSubmit?.({ name: '更新後' });
          }}
        >
          {props.submitLabel ?? '保存'}
        </button>
        <button
          type="button"
          data-testid="mock-patient-form-cancel"
          onClick={() => props.onCancel?.()}
        >
          キャンセル
        </button>
      </div>
    );
  },
}));

vi.mock('@/app/(app)/patients/_components/PatientFixedVisitsPanel', () => ({
  PatientFixedVisitsPanel: (props: { patientId: string }) => (
    <div data-testid="mock-pfv-panel" data-patient-id={props.patientId}>
      mock-pfv
    </div>
  ),
}));

// ─── Subject under test (import 後) ──────────────────────────────────────────

import { PatientEditDialog } from '../PatientEditDialog';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function setup(overrides: Partial<React.ComponentProps<typeof PatientEditDialog>> = {}) {
  const onClose = vi.fn();
  const props: React.ComponentProps<typeof PatientEditDialog> = {
    patientId: 'p-1',
    open: true,
    onClose,
    canEdit: true,
    ...overrides,
  };
  const utils = render(<PatientEditDialog {...props} />);
  return { ...utils, onClose };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('PatientEditDialog — Phase G-20', () => {
  beforeEach(() => {
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockInvalidate.mockReset();
    mockUpdate.mockReset();
    mockUpdate.mockResolvedValue({ id: 'p-1' });
  });

  it('1. open=false → ダイアログが描画されない', () => {
    setup({ open: false });
    expect(screen.queryByTestId('patient-edit-dialog')).not.toBeInTheDocument();
  });

  it('2. open=true / canEdit=true → タイトル + PatientForm + PFV パネルが描画される', () => {
    setup({ open: true, canEdit: true });
    expect(screen.getByTestId('patient-edit-dialog')).toBeInTheDocument();
    // ダイアログタイトル
    expect(screen.getByText(/患者情報を編集/)).toBeInTheDocument();
    // 患者氏名 (= サブテキスト)
    expect(screen.getByText(/山田 太郎/)).toBeInTheDocument();
    // 子コンポーネント
    expect(screen.getByTestId('mock-patient-form')).toBeInTheDocument();
    expect(screen.getByTestId('mock-pfv-panel')).toBeInTheDocument();
    expect(screen.getByTestId('mock-pfv-panel').getAttribute('data-patient-id')).toBe('p-1');
  });

  // ─── 回帰テスト (2026-08-11 PO 実機確認で発覚したバグ) ────────────────────
  // PatientForm に patientId を渡し忘れると、「訪問条件」内の
  // 同住所紐付け / NGスタッフ セクションがスケジュール画面の編集ポップアップから
  // まるごと消える。props 伝搬とセクション描画の両方を固定する。
  it('2-b. PatientForm に patientId が伝搬し、訪問条件セクション (同住所/NG) が描画される', () => {
    setup({ open: true, canEdit: true, patientId: 'p-1' });

    expect(screen.getByTestId('mock-patient-form').getAttribute('data-patient-id')).toBe('p-1');
    expect(screen.getByTestId('mock-same-address-links-section')).toBeInTheDocument();
    expect(screen.getByTestId('mock-patient-ng-staff-section')).toBeInTheDocument();
  });

  it('3. canEdit=false → 「権限がありません」 + PatientForm 非表示', () => {
    setup({ open: true, canEdit: false });
    expect(screen.getByText('権限がありません')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-patient-form')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mock-pfv-panel')).not.toBeInTheDocument();
  });

  it('5. 保存 → useUpdatePatient.mutateAsync が呼ばれ、成功後に onClose が呼ばれる', async () => {
    const { onClose } = setup({ open: true, canEdit: true });

    await act(async () => {
      fireEvent.click(screen.getByTestId('mock-patient-form-submit'));
    });

    expect(mockUpdate).toHaveBeenCalledTimes(1);
    expect(mockUpdate).toHaveBeenCalledWith({ name: '更新後' });
    expect(mockToast.success).toHaveBeenCalledWith('患者情報を更新しました');
    expect(onClose).toHaveBeenCalledTimes(1);
    // visits / courses query が invalidate される (patients は useUpdatePatient 内で実行)
    const invalidateKeys = mockInvalidate.mock.calls.map((c) => c[0]?.queryKey?.[0]);
    expect(invalidateKeys).toContain('visits');
    expect(invalidateKeys).toContain('courses');
  });

  it('6. キャンセル (isDirty=false) → confirm 無しで onClose', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { onClose } = setup({ open: true, canEdit: true });

    fireEvent.click(screen.getByTestId('mock-patient-form-cancel'));

    // isDirty=false なので confirm は呼ばれない
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);

    confirmSpy.mockRestore();
  });
});
