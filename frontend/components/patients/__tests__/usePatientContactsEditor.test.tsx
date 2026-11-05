/**
 * 連絡先の中身を持ち、患者の保存の後に PUT する hook。
 * 失敗したら入力を残して誤りを出す (設計 §9)。
 */
import { describe, expect, it, vi, beforeEach, type Mock } from 'vitest';
import { act, renderHook } from '@testing-library/react';

vi.mock('@/lib/queries/patientContacts', () => ({
  usePatientContacts: vi.fn(),
  useSavePatientContacts: vi.fn(),
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { toast } from '@/components/ui/sonner';
import { usePatientContacts, useSavePatientContacts } from '@/lib/queries/patientContacts';
import { addContact } from '@/lib/contacts/patientContacts';

import { usePatientContactsEditor } from '../usePatientContactsEditor';

const READ = {
  id: 'c-1',
  sort_order: 0,
  relation: 'self',
  name: null,
  relation_note: null,
  phone: '08022223333',
  phone_display: '080-2222-3333',
  phone_kind: 'mobile',
  sms_capable: true,
  email: null,
  email_carrier: null,
  is_notify_target: true,
  notify_channel: 'sms',
  effective_channel: 'sms',
  consent_status: 'unconfirmed',
  consent_on: null,
};

let mutateAsync: Mock;

beforeEach(() => {
  vi.clearAllMocks();
  mutateAsync = vi.fn();
  (useSavePatientContacts as Mock).mockReturnValue({ mutateAsync, isPending: false });
  (usePatientContacts as Mock).mockReturnValue({ data: [READ], error: null });
});

describe('usePatientContactsEditor', () => {
  it('読み込んだ連絡先を下書きにし、変えなければ PUT しない', async () => {
    const { result } = renderHook(() =>
      usePatientContactsEditor({ patientId: 'p-1', enabled: true }),
    );
    expect(result.current.loaded).toBe(true);
    expect(result.current.drafts[0]).toMatchObject({ id: 'c-1', phone: '080-2222-3333' });
    expect(result.current.dirty).toBe(false);
    let ok = false;
    await act(async () => {
      ok = await result.current.saveTo('p-1');
    });
    expect(ok).toBe(true);
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('変えたら PUT し、返ってきた知らせを出す', async () => {
    mutateAsync.mockResolvedValue({
      contacts: [READ, { ...READ, id: 'c-2', relation: 'family', sort_order: 1 }],
      notices: ['送る先が無くならないよう、ご本人を送る先にしました'],
    });
    const { result } = renderHook(() =>
      usePatientContactsEditor({ patientId: 'p-1', enabled: true }),
    );
    act(() => result.current.setDrafts(addContact(result.current.drafts).contacts));
    expect(result.current.dirty).toBe(true);
    await act(async () => {
      await result.current.saveTo('p-1');
    });
    expect(mutateAsync).toHaveBeenCalledWith({
      patientId: 'p-1',
      contacts: [
        expect.objectContaining({ id: 'c-1', relation: 'self', phone: '080-2222-3333' }),
        expect.objectContaining({ relation: 'family', is_notify_target: false }),
      ],
    });
    expect(toast.info).toHaveBeenCalledWith('送る先が無くならないよう、ご本人を送る先にしました');
    expect(result.current.dirty).toBe(false);
  });

  it('PUT が失敗したら入力を残して誤りを出す', async () => {
    mutateAsync.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() =>
      usePatientContactsEditor({ patientId: 'p-1', enabled: true }),
    );
    act(() => result.current.setDrafts(addContact(result.current.drafts).contacts));
    let ok = true;
    await act(async () => {
      ok = await result.current.saveTo('p-1');
    });
    expect(ok).toBe(false);
    expect(result.current.drafts).toHaveLength(2);
    expect(result.current.failed).toBe(true);
    expect(result.current.error).toContain('入力はそのまま残しています');
  });

  it('形の違う電話は保存の前に止める', () => {
    const { result } = renderHook(() =>
      usePatientContactsEditor({ patientId: 'p-1', enabled: true }),
    );
    act(() => result.current.setDrafts([{ ...result.current.drafts[0]!, phone: '090-123-456' }]));
    let ok = true;
    act(() => {
      ok = result.current.validate();
    });
    expect(ok).toBe(false);
    expect(result.current.error).toBe('1 件目の電話番号の形が違います。');
  });

  it('新規 (patientId なし) は最初から読み終えた扱いで、足せば PUT する', async () => {
    (usePatientContacts as Mock).mockReturnValue({ data: undefined, error: null });
    mutateAsync.mockResolvedValue({ contacts: [READ], notices: [] });
    const { result } = renderHook(() => usePatientContactsEditor({ enabled: true }));
    expect(result.current.loaded).toBe(true);
    act(() => result.current.setDrafts(addContact([]).contacts));
    await act(async () => {
      await result.current.saveTo('new-id');
    });
    expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ patientId: 'new-id' }));
  });

  it('管理者以外は何もしない', async () => {
    const { result } = renderHook(() =>
      usePatientContactsEditor({ patientId: 'p-1', enabled: false }),
    );
    expect(usePatientContacts).toHaveBeenCalledWith('p-1', false);
    let ok = false;
    await act(async () => {
      ok = await result.current.saveTo('p-1');
    });
    expect(ok).toBe(true);
    expect(mutateAsync).not.toHaveBeenCalled();
  });
});
