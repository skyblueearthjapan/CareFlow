'use client';

/**
 * TanStack Query hooks for /api/v1/course-templates — W15-FE (Wave 15 Phase 3).
 *
 * Endpoints (W15-BE1):
 *   GET    /api/v1/course-templates?office_id=...   一覧 (admin/manager/staff)
 *   POST   /api/v1/course-templates                 新規 (admin/manager)
 *   PATCH  /api/v1/course-templates/{id}            更新 (admin/manager)
 *   DELETE /api/v1/course-templates/{id}            論理削除 (admin)
 *
 * メイン UI (ScheduleUnifiedView) では list のみを使用する。CRUD は
 * 別画面 (W15 後続フェーズ) で利用する想定。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  CourseTemplateCreate,
  CourseTemplateRead,
  CourseTemplateUpdate,
} from '@/lib/schemas/v2/course_template';

const COURSE_TEMPLATES_PATH = '/api/v1/course-templates';
const COURSE_TEMPLATES_KEY = ['course-templates'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface UseCourseTemplatesParams {
  office_id?: string | null;
  /** 全拠点モード時に複数 office_id をまとめて取得するための拡張 (将来用). */
  enabled?: boolean;
}

/**
 * GET /api/v1/course-templates?office_id=...
 *
 * office_id が空の場合は fetch しない (要件: office_id は必須)。
 * 「全拠点」モード対応は呼び出し側で複数の hook を並列実行する。
 */
export function useCourseTemplates(
  params: UseCourseTemplatesParams,
): UseQueryResult<CourseTemplateRead[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const officeId = params.office_id ?? null;

  return useQuery<CourseTemplateRead[], Error>({
    queryKey: [...COURSE_TEMPLATES_KEY, 'list', officeId ?? '__none__'],
    enabled:
      status === 'authenticated' &&
      Boolean(officeId) &&
      (params.enabled === undefined || params.enabled),
    queryFn: () =>
      fetcher<CourseTemplateRead[]>(
        `${COURSE_TEMPLATES_PATH}?office_id=${encodeURIComponent(officeId!)}`,
        { accessToken, refreshToken },
      ),
  });
}

export function useCreateCourseTemplate(): UseMutationResult<
  CourseTemplateRead,
  Error,
  CourseTemplateCreate
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<CourseTemplateRead, Error, CourseTemplateCreate>({
    mutationFn: (payload) =>
      fetcher<CourseTemplateRead>(COURSE_TEMPLATES_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COURSE_TEMPLATES_KEY });
    },
  });
}

export function useUpdateCourseTemplate(): UseMutationResult<
  CourseTemplateRead,
  Error,
  { id: string; patch: CourseTemplateUpdate }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<CourseTemplateRead, Error, { id: string; patch: CourseTemplateUpdate }>({
    mutationFn: ({ id, patch }) =>
      fetcher<CourseTemplateRead>(`${COURSE_TEMPLATES_PATH}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COURSE_TEMPLATES_KEY });
    },
  });
}

export function useDeleteCourseTemplate(): UseMutationResult<void, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${COURSE_TEMPLATES_PATH}/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COURSE_TEMPLATES_KEY });
    },
  });
}
