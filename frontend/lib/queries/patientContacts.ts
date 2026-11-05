/**
 * 患者様の連絡先 (管理者だけ) の TanStack Query hooks。
 *
 *   GET /api/v1/patients/contacts/summary
 *   GET /api/v1/patients/{patient_id}/contacts
 *   PUT /api/v1/patients/{patient_id}/contacts   body {contacts:[ContactWrite]}
 *
 * 設計 docs/plans/contacts-phone-email-design-2026-11-05.md §5。
 */
'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  contactListSchema,
  contactSummarySchema,
  type ContactList,
  type ContactRead,
  type ContactSummaryItem,
  type ContactWrite,
} from '@/lib/schemas/patientContacts';

export const PATIENT_CONTACTS_KEY = (patientId: string) => ['patient-contacts', patientId] as const;
export const PATIENT_CONTACTS_SUMMARY_KEY = ['patient-contacts', 'summary'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** 1 人の連絡先 (sort_order 順)。`enabled=false` (管理者以外) では取りに行かない。 */
export function usePatientContacts(
  patientId: string | null | undefined,
  enabled: boolean,
): UseQueryResult<ContactRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<ContactRead[], Error>({
    queryKey: PATIENT_CONTACTS_KEY(patientId ?? ''),
    enabled: status === 'authenticated' && !!patientId && enabled,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`/api/v1/patients/${patientId}/contacts`, {
        accessToken,
        refreshToken,
      });
      return contactListSchema.parse(raw).contacts;
    },
  });
}

/** 一覧の「お知らせ」列用。全員分を 1 回で取る。 */
export function usePatientContactsSummary(
  enabled: boolean,
): UseQueryResult<ContactSummaryItem[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useQuery<ContactSummaryItem[], Error>({
    queryKey: PATIENT_CONTACTS_SUMMARY_KEY,
    enabled: status === 'authenticated' && enabled,
    queryFn: async () => {
      const raw = await fetcher<unknown>('/api/v1/patients/contacts/summary', {
        accessToken,
        refreshToken,
      });
      return contactSummarySchema.parse(raw).items;
    },
  });
}

export interface SavePatientContactsVariables {
  patientId: string;
  contacts: ContactWrite[];
}

/** 連絡先を全部置きかえる。返り値の notices は自動で移したときの知らせ。 */
export function useSavePatientContacts(): UseMutationResult<
  ContactList,
  Error,
  SavePatientContactsVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  return useMutation<ContactList, Error, SavePatientContactsVariables>({
    mutationFn: async ({ patientId, contacts }) => {
      const raw = await fetcher<unknown>(`/api/v1/patients/${patientId}/contacts`, {
        method: 'PUT',
        body: JSON.stringify({ contacts }),
        accessToken,
        refreshToken,
      });
      return contactListSchema.parse(raw);
    },
    onSuccess: (data, vars) => {
      qc.setQueryData(PATIENT_CONTACTS_KEY(vars.patientId), data.contacts);
      void qc.invalidateQueries({ queryKey: PATIENT_CONTACTS_SUMMARY_KEY });
    },
  });
}
