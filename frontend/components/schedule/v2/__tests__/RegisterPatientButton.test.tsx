/**
 * RegisterPatientButton — W-4 (D-4) 振る舞いテスト.
 *
 * カバー:
 *   - ツールバーに「＋新規患者登録」ボタン (data-testid=register-patient-button) が表示される。
 *   - クリックで CreatePatientDialog が開き、患者マスタの登録フォーム (PatientForm) が現れる。
 *
 * PatientForm 本体は重い依存 (react-hook-form / offices / geocode) を持つため stub 化し、
 * ここでは「ボタン → フォームが開く」導線のみを検証する。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// ─── Radix Dialog を open に応じて素通し化 (jsdom portal 回避) ──────────────
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div data-testid="dialog-root">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// ─── 患者登録フォームは stub (実体テストは PatientForm.test.tsx で担保) ──────
vi.mock('@/app/(app)/patients/_components/PatientForm', () => ({
  PatientForm: ({ submitLabel }: { submitLabel?: string }) => (
    <form data-testid="patient-form">
      <button type="submit">{submitLabel}</button>
    </form>
  ),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/lib/queries/patients', () => ({
  useCreatePatient: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { RegisterPatientButton } from '../RegisterPatientButton';

describe('RegisterPatientButton (W-4 / D-4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('「＋新規患者登録」ボタンが表示される', () => {
    render(<RegisterPatientButton />);
    const btn = screen.getByTestId('register-patient-button');
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveTextContent('新規患者登録');
  });

  it('初期状態では登録フォームは開いていない', () => {
    render(<RegisterPatientButton />);
    expect(screen.queryByTestId('patient-form')).not.toBeInTheDocument();
  });

  it('ボタンをクリックすると患者登録フォームが開く', () => {
    render(<RegisterPatientButton />);
    fireEvent.click(screen.getByTestId('register-patient-button'));
    expect(screen.getByTestId('create-patient-dialog-body')).toBeInTheDocument();
    expect(screen.getByTestId('patient-form')).toBeInTheDocument();
    // 登録ボタンのラベルが「登録」であること (作成モード)。
    expect(screen.getByRole('button', { name: '登録' })).toBeInTheDocument();
  });
});
