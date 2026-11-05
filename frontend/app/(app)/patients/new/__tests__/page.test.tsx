/**
 * NewPatientPage — 患者は作れたが連絡先だけ失敗した後の、もう一度の「登録」。
 *
 * 2 回目の「登録」で患者の欄の変更を落とさない: 患者を PATCH してから連絡先を送る
 * (contacts-phone-email-design-2026-11-05 §9)。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// ─── Mocks ────────────────────────────────────────────────────────────────────

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('@/lib/queries/patients', () => ({
  useCreatePatient: vi.fn(),
  useUpdatePatient: vi.fn(),
}));

vi.mock('@/components/patients/usePatientContactsEditor', () => ({
  usePatientContactsEditor: vi.fn(),
}));

vi.mock('@/components/brand/Rakusuke', () => ({
  RakusukeTitle: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

// フォームは「登録」を押すたびに、いまの欄の値で onSubmit を呼ぶだけの見本にする。
let formName = '見本 太郎';
vi.mock('../../_components/PatientForm', () => ({
  PatientForm: ({ onSubmit }: { onSubmit: (v: unknown) => Promise<void> }) => (
    <button type="button" onClick={() => void onSubmit({ name: formName })}>
      登録
    </button>
  ),
}));

// ─── Imports after mocks ──────────────────────────────────────────────────────
import { useSession } from 'next-auth/react';
import { usePatientContactsEditor } from '@/components/patients/usePatientContactsEditor';
import { useCreatePatient, useUpdatePatient } from '@/lib/queries/patients';
import NewPatientPage from '../page';

function setupMocks() {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: 'admin' } },
    status: 'authenticated',
  });
  const create = vi.fn().mockResolvedValue({ id: 'p-1' });
  const update = vi.fn().mockResolvedValue({ id: 'p-1' });
  (useCreatePatient as Mock).mockReturnValue({ mutateAsync: create, isPending: false });
  (useUpdatePatient as Mock).mockReturnValue({ mutateAsync: update, isPending: false });
  // 1 回目の連絡先の保存は失敗、2 回目は成功。
  const saveTo = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  (usePatientContactsEditor as Mock).mockReturnValue({
    enabled: true,
    drafts: [],
    setDrafts: vi.fn(),
    error: null,
    failed: true,
    saving: false,
    validate: () => true,
    saveTo,
  });
  return { create, update, saveTo };
}

async function clickSubmit() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '登録' }));
  });
}

describe('NewPatientPage — 連絡先だけ失敗した後の登録', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    formName = '見本 太郎';
  });

  it('2 回目は患者を作り直さず、欄の変更を PATCH してから連絡先を送る', async () => {
    const { create, update, saveTo } = setupMocks();
    render(<NewPatientPage />);

    await clickSubmit();
    expect(create).toHaveBeenCalledTimes(1);
    expect(saveTo).toHaveBeenCalledWith('p-1');
    expect(update).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    // PATCH は作った患者に向け、差分の元は作ったときの値。
    expect(useUpdatePatient as Mock).toHaveBeenLastCalledWith('p-1', { name: '見本 太郎' });

    // 連絡先の失敗の後に患者の欄を変えて、もう一度「登録」。
    formName = '見本 次郎';
    await clickSubmit();
    expect(create).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledWith({ name: '見本 次郎' });
    expect(update.mock.invocationCallOrder[0]).toBeLessThan(
      saveTo.mock.invocationCallOrder[1] ?? 0,
    );
    // 保存できた値が次の差分の元になる。
    expect(useUpdatePatient as Mock).toHaveBeenLastCalledWith('p-1', { name: '見本 次郎' });
    expect(push).toHaveBeenCalledWith('/patients/p-1');
  });

  it('PATCH が失敗したら連絡先を送らず、画面に残る', async () => {
    const { update, saveTo } = setupMocks();
    update.mockRejectedValueOnce(new Error('保存できません'));
    render(<NewPatientPage />);

    await clickSubmit();
    await clickSubmit();
    expect(update).toHaveBeenCalledTimes(1);
    expect(saveTo).toHaveBeenCalledTimes(1);
    expect(push).not.toHaveBeenCalled();
  });
});
