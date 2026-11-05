/**
 * 患者の新規・編集ページで「連絡先」カードの中身を持ち、患者の保存の後に PUT する。
 *
 * 連絡先だけ保存に失敗したときは、誤りを出して入力をそのまま残す (設計 §9)。
 */
'use client';

import * as React from 'react';

import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import {
  contactErrors,
  contactsChanged,
  draftToWrite,
  draftsFromRead,
  type ContactDraft,
} from '@/lib/contacts/patientContacts';
import { usePatientContacts, useSavePatientContacts } from '@/lib/queries/patientContacts';

export interface PatientContactsEditor {
  /** 管理者のときだけ true (カードを出す・保存する)。 */
  enabled: boolean;
  /** 既存の連絡先を読み終えたか (新規は最初から true)。 */
  loaded: boolean;
  loadError: Error | null;
  drafts: ContactDraft[];
  setDrafts: (next: ContactDraft[]) => void;
  dirty: boolean;
  error: string | null;
  /** 直前の保存 (PUT) が失敗した (「もう一度保存する」を出す)。 */
  failed: boolean;
  saving: boolean;
  /** 保存の前の形の確かめ。誤りがあればカードに出して false。 */
  validate: () => boolean;
  /** 変わっていれば PUT。成功 (または変更なし) で true。 */
  saveTo: (patientId: string) => Promise<boolean>;
  reset: () => void;
}

export function usePatientContactsEditor({
  patientId,
  enabled,
}: {
  /** 編集なら患者の id。新規は undefined。 */
  patientId?: string;
  enabled: boolean;
}): PatientContactsEditor {
  const query = usePatientContacts(patientId, enabled);
  const save = useSavePatientContacts();
  const [initial, setInitial] = React.useState<ContactDraft[]>([]);
  const [drafts, setDrafts] = React.useState<ContactDraft[]>([]);
  const [loaded, setLoaded] = React.useState(!patientId);
  const [error, setError] = React.useState<string | null>(null);
  const [failed, setFailed] = React.useState(false);

  React.useEffect(() => {
    if (loaded || !query.data) return;
    const d = draftsFromRead(query.data);
    setInitial(d);
    setDrafts(d);
    setLoaded(true);
  }, [loaded, query.data]);

  const dirty = enabled && contactsChanged(drafts, initial);

  const validate = React.useCallback(() => {
    if (!enabled) return true;
    const errs = contactErrors(drafts);
    if (errs.length > 0) {
      setError(errs.join('\n'));
      return false;
    }
    setError(null);
    return true;
  }, [enabled, drafts]);

  const saveTo = React.useCallback(
    async (id: string) => {
      if (!enabled || !contactsChanged(drafts, initial)) return true;
      try {
        const res = await save.mutateAsync({ patientId: id, contacts: drafts.map(draftToWrite) });
        const d = draftsFromRead(res.contacts);
        setInitial(d);
        setDrafts(d);
        setError(null);
        setFailed(false);
        for (const n of res.notices) toast.info(n);
        return true;
      } catch (e) {
        const msg = apiErrorMessage(e);
        setFailed(true);
        setError(`連絡先を保存できませんでした。入力はそのまま残しています。\n${msg}`);
        toast.error(`連絡先の保存に失敗しました: ${msg}`);
        return false;
      }
    },
    [enabled, drafts, initial, save],
  );

  const reset = React.useCallback(() => {
    setDrafts(initial);
    setError(null);
    setFailed(false);
  }, [initial]);

  return {
    enabled,
    loaded,
    loadError: query.error ?? null,
    drafts,
    setDrafts,
    dirty,
    error,
    failed,
    saving: save.isPending,
    validate,
    saveTo,
    reset,
  };
}
