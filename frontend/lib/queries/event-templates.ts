/**
 * TanStack Query hooks for event templates (イベントひな形).
 *
 *   GET    /api/v1/event-templates                        (全ロール閲覧可)
 *   POST   /api/v1/event-templates                        (admin)
 *   PATCH  /api/v1/event-templates/{id}                   (admin)
 *   DELETE /api/v1/event-templates/{id}                   (admin)
 *   PUT    /api/v1/event-templates/reorder                (admin)
 *   GET    /api/v1/event-templates/history-suggestions    (全ロール閲覧可)
 *
 * 正典 = docs/plans/staff-event-history-design.md §2 Phase 2。
 * staff_id === null → 事業所共通ひな形 / 値あり → そのスタッフ個人のひな形。
 * `is_shared` (BE 計算フィールド) で共通/個人セクションを分ける。
 * sort_order はスコープ内で独立した 0 始まりの連番 — フラット配列を
 * そのまま並べず、必ず is_shared でグループ分けしてから表示すること。
 *
 * ひな形は「入力の型」: 選択後にイベントを作っても FK は張られず、
 * ひな形を後から編集しても作成済みイベントには影響しない。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const BASE = '/api/v1/event-templates';

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

export const eventTemplateReadSchema = z.object({
  id: z.string().uuid(),
  staff_id: z.string().uuid().nullable(),
  title: z.string(),
  event_type: z.enum(['event', 'training']),
  start_time: z.string().nullable(),
  end_time: z.string().nullable(),
  blocking: z.boolean(),
  note: z.string().nullable(),
  sort_order: z.number().int(),
  is_active: z.boolean(),
  /** staff_id === null のとき true (共通ひな形)。セクション分けはこれで行う。 */
  is_shared: z.boolean(),
});
export type EventTemplateRead = z.infer<typeof eventTemplateReadSchema>;

/**
 * start_time / end_time は「両方指定 or 両方 null」。PATCH で時刻を消す時も
 * 2 キー同時に null を送ること (片方だけは BE 422)。
 */
const eventTemplateBaseSchema = z.object({
  staff_id: z.string().uuid().nullable().optional(),
  title: z.string().trim().min(1).max(255),
  event_type: z.enum(['event', 'training']).optional(),
  start_time: z.string().regex(HHMM).nullable().optional(),
  end_time: z.string().regex(HHMM).nullable().optional(),
  blocking: z.boolean().optional(),
  note: z.string().max(500).nullable().optional(),
  sort_order: z.number().int().optional(),
  is_active: z.boolean().optional(),
});

/** BE と同じ時刻ペア検証 (両方 or 両方なし・end > start) をフォーム段階で弾く。 */
function refineTimePair<T extends z.ZodTypeAny>(schema: T) {
  return schema
    .refine(
      (v: { start_time?: string | null; end_time?: string | null }) =>
        (v.start_time == null) === (v.end_time == null),
      { message: '開始と終了は両方入力するか両方空にしてください', path: ['end_time'] },
    )
    .refine(
      (v: { start_time?: string | null; end_time?: string | null }) =>
        v.start_time == null || v.end_time == null || v.start_time < v.end_time,
      { message: '終了時刻は開始時刻より後にしてください', path: ['end_time'] },
    );
}

export const eventTemplateCreateSchema = refineTimePair(eventTemplateBaseSchema);
export type EventTemplateCreate = z.infer<typeof eventTemplateBaseSchema>;

const eventTemplateUpdateBase = eventTemplateBaseSchema.omit({ staff_id: true });
export const eventTemplateUpdateSchema = refineTimePair(eventTemplateUpdateBase);
export type EventTemplateUpdate = z.infer<typeof eventTemplateUpdateBase>;

export const historySuggestionSchema = z.object({
  title: z.string(),
  count: z.number().int(),
  last_date: z.string(),
  last_start_time: z.string().nullable(),
  last_end_time: z.string().nullable(),
  event_type: z.enum(['event', 'training']),
});
export type HistorySuggestion = z.infer<typeof historySuggestionSchema>;

export interface EventTemplatesParams {
  /** 指定するとそのスタッフの個人ひな形も返る (共通は常に含まれる)。 */
  staffId?: string | null;
  /** 管理カード用: 無効化済みも含める。 */
  includeInactive?: boolean;
}

export const eventTemplatesKey = (p: EventTemplatesParams = {}) =>
  ['event-templates', p.staffId ?? null, p.includeInactive ?? false] as const;

export const historySuggestionsKey = (staffId: string | null, months: number) =>
  ['event-template-history-suggestions', staffId, months] as const;

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useEventTemplates(params: EventTemplatesParams = {}) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const search = new URLSearchParams();
  if (params.staffId) search.set('staff_id', params.staffId);
  if (params.includeInactive) search.set('include_inactive', 'true');
  const qs = search.toString();

  return useQuery<EventTemplateRead[]>({
    queryKey: eventTemplatesKey(params),
    queryFn: () =>
      fetcher<EventTemplateRead[]>(`${BASE}${qs ? `?${qs}` : ''}`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated,
  });
}

export function useEventTemplateHistorySuggestions(
  staffId: string | null | undefined,
  options: { months?: number; enabled?: boolean } = {},
) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const months = options.months ?? 6;
  const search = new URLSearchParams();
  if (staffId) search.set('staff_id', staffId);
  search.set('months', String(months));

  return useQuery<HistorySuggestion[]>({
    queryKey: historySuggestionsKey(staffId ?? null, months),
    queryFn: () =>
      fetcher<HistorySuggestion[]>(`${BASE}/history-suggestions?${search.toString()}`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated && (options.enabled ?? true),
  });
}

function useInvalidateTemplates() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ['event-templates'] });
    void qc.invalidateQueries({ queryKey: ['event-template-history-suggestions'] });
  };
}

type CreateOptions = UseMutationOptions<EventTemplateRead, Error, EventTemplateCreate>;

export function useCreateEventTemplate(options: CreateOptions = {}) {
  const invalidate = useInvalidateTemplates();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<EventTemplateRead, Error, EventTemplateCreate>({
    mutationFn: (payload) =>
      fetcher<EventTemplateRead>(BASE, {
        method: 'POST',
        body: JSON.stringify(eventTemplateCreateSchema.parse(payload)),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      invalidate();
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type UpdateVars = { id: string; payload: EventTemplateUpdate };
type UpdateOptions = UseMutationOptions<EventTemplateRead, Error, UpdateVars>;

export function useUpdateEventTemplate(options: UpdateOptions = {}) {
  const invalidate = useInvalidateTemplates();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<EventTemplateRead, Error, UpdateVars>({
    mutationFn: ({ id, payload }) =>
      fetcher<EventTemplateRead>(`${BASE}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(eventTemplateUpdateSchema.parse(payload)),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      invalidate();
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type DeleteOptions = UseMutationOptions<void, Error, string>;

export function useDeleteEventTemplate(options: DeleteOptions = {}) {
  const invalidate = useInvalidateTemplates();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${BASE}/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      invalidate();
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type ReorderVars = { staffId: string | null; orderedIds: string[] };
type ReorderOptions = UseMutationOptions<EventTemplateRead[], Error, ReorderVars>;

/**
 * スコープ (共通 or 1スタッフ) の並びを一括更新。422 はクライアントの
 * 並びが古い合図 — 呼び出し側で refetch すること (invalidate は成功時のみ)。
 */
export function useReorderEventTemplates(options: ReorderOptions = {}) {
  const invalidate = useInvalidateTemplates();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<EventTemplateRead[], Error, ReorderVars>({
    mutationFn: ({ staffId, orderedIds }) =>
      fetcher<EventTemplateRead[]>(`${BASE}/reorder`, {
        method: 'PUT',
        body: JSON.stringify({ staff_id: staffId, ordered_ids: orderedIds }),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      invalidate();
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
