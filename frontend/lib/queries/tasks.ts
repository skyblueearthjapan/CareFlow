/**
 * TanStack Query hooks for 事務所からのやること (看護師へのタスク).
 *
 *   GET   /api/v1/task-item-types               (全員・include_inactive は管理者だけ効く)
 *   POST  /api/v1/task-item-types               (admin)
 *   PATCH /api/v1/task-item-types/{id}          (admin)
 *   GET   /api/v1/tasks                         (admin・status/staff_id/patient_id/date_from/date_to/done_from)
 *   POST  /api/v1/tasks                         (admin)
 *   GET   /api/v1/tasks/counts?date=YYYY-MM-DD  (admin・staff は自分の訪問だけ)
 *   GET   /api/v1/tasks/held                    (全員・ログインしている人が預かっている未完了)
 *   POST  /api/v1/tasks/{id}/complete           (admin・その訪問の担当の職員・預かっている職員)
 *   POST  /api/v1/tasks/{id}/reopen             (admin・自分で完了にした職員)
 *   POST  /api/v1/tasks/{id}/cancel             (admin)
 *   PATCH /api/v1/tasks/{id}/due                (admin・期日を変える／なしに戻す)
 *   GET   /api/v1/visits/{visitId}/tasks        (admin・その訪問の担当の職員)
 *
 * 正典 = docs/plans/patient-tasks-design-2026-11-01.md。
 * 変更系はどれも成功時に ['tasks'] 配下 (一覧・件数・訪問ごと) をまとめて無効化し、
 * 読み直しが終わるまで isPending を続ける (完了直後の二度押しで 409 を出さないため)。
 */
'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  patientTaskCreateSchema,
  patientTaskDueUpdateSchema,
  taskItemTypeCreateSchema,
  taskItemTypeUpdateSchema,
  type PatientTaskCreate,
  type PatientTaskCreateResult,
  type PatientTaskRead,
  type TaskItemTypeCreate,
  type TaskItemTypeRead,
  type TaskItemTypeUpdate,
  type TaskListStatus,
  type VisitTaskCounts,
} from '@/lib/schemas/patient-task';

const TASKS = '/api/v1/tasks';
const ITEM_TYPES = '/api/v1/task-item-types';

export interface TasksParams {
  /** 既定 open。 */
  status?: TaskListStatus;
  /** 看護師: 出す訪問 (完了は完了した訪問・完了した人) の担当で絞る。 */
  staffId?: string | null;
  patientId?: string | null;
  /** 日付 (この日から) の範囲 YYYY-MM-DD。 */
  dateFrom?: string | null;
  dateTo?: string | null;
  /** 完了した日 (JST) の下限 YYYY-MM-DD。完了していないものは外れる。 */
  doneFrom?: string | null;
}

export const tasksKeys = {
  all: ['tasks'] as const,
  list: (p: TasksParams = {}) =>
    [
      'tasks',
      'list',
      p.status ?? 'open',
      p.staffId ?? null,
      p.patientId ?? null,
      p.dateFrom ?? null,
      p.dateTo ?? null,
      p.doneFrom ?? null,
    ] as const,
  counts: (date: string, staffId: string | null = null) =>
    ['tasks', 'counts', date, staffId] as const,
  visit: (visitId: string) => ['tasks', 'visit', visitId] as const,
  held: () => ['tasks', 'held'] as const,
  itemTypes: (includeInactive: boolean) => ['task-item-types', includeInactive] as const,
};

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

// ---------------------------------------------------------------------------
// 読み出し
// ---------------------------------------------------------------------------

/** 管理者の一覧 (やることのページ)。 */
export function useTasks(
  params: TasksParams = {},
  options: { enabled?: boolean; refetchInterval?: number | false } = {},
) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const search = new URLSearchParams();
  search.set('status', params.status ?? 'open');
  if (params.staffId) search.set('staff_id', params.staffId);
  if (params.patientId) search.set('patient_id', params.patientId);
  if (params.dateFrom) search.set('date_from', params.dateFrom);
  if (params.dateTo) search.set('date_to', params.dateTo);
  if (params.doneFrom) search.set('done_from', params.doneFrom);

  return useQuery<PatientTaskRead[]>({
    queryKey: tasksKeys.list(params),
    queryFn: () =>
      fetcher<PatientTaskRead[]>(`${TASKS}?${search.toString()}`, { accessToken, refreshToken }),
    enabled: isAuthenticated && (options.enabled ?? true),
    refetchInterval: options.refetchInterval,
  });
}

/**
 * 1 日の訪問ごとの件数 (訪問モニターのカード・スマホの今日の一覧)。
 * `counts[visitId]` が無い訪問は、やることが出ない訪問。
 */
export function useVisitTaskCounts(
  date: string | null | undefined,
  options: { staffId?: string | null; enabled?: boolean; refetchInterval?: number | false } = {},
) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const staffId = options.staffId ?? null;
  const search = new URLSearchParams();
  if (date) search.set('date', date);
  if (staffId) search.set('staff_id', staffId);

  return useQuery<VisitTaskCounts>({
    queryKey: tasksKeys.counts(date ?? '', staffId),
    queryFn: () =>
      fetcher<VisitTaskCounts>(`${TASKS}/counts?${search.toString()}`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated && !!date && (options.enabled ?? true),
    refetchInterval: options.refetchInterval,
  });
}

/** その訪問に出るやること (訪問モニターの詳細・スマホの訪問詳細)。 */
export function useVisitTasks(
  visitId: string | null | undefined,
  options: { enabled?: boolean } = {},
) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  return useQuery<PatientTaskRead[]>({
    queryKey: tasksKeys.visit(visitId ?? ''),
    queryFn: () =>
      fetcher<PatientTaskRead[]>(`/api/v1/visits/${visitId}/tasks`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated && !!visitId && (options.enabled ?? true),
  });
}

/**
 * ログインしている人が預かっている未完了のやること (スマホの今日の画面の帯・
 * patient-tasks-hold-first-visitor-design-2026-11-04 §3)。キーは ['tasks'] 配下なので、
 * 完了などの変更で一緒に読み直す。
 */
export function useHeldTasks(options: { enabled?: boolean } = {}) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  return useQuery<PatientTaskRead[]>({
    queryKey: tasksKeys.held(),
    queryFn: () => fetcher<PatientTaskRead[]>(`${TASKS}/held`, { accessToken, refreshToken }),
    enabled: isAuthenticated && (options.enabled ?? true),
  });
}

/** 項目の一覧。includeInactive は管理者の設定画面用 (職員には効かない)。 */
export function useTaskItemTypes(options: { includeInactive?: boolean; enabled?: boolean } = {}) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const includeInactive = options.includeInactive ?? false;
  return useQuery<TaskItemTypeRead[]>({
    queryKey: tasksKeys.itemTypes(includeInactive),
    queryFn: () =>
      fetcher<TaskItemTypeRead[]>(
        `${ITEM_TYPES}${includeInactive ? '?include_inactive=true' : ''}`,
        {
          accessToken,
          refreshToken,
        },
      ),
    enabled: isAuthenticated && (options.enabled ?? true),
  });
}

// ---------------------------------------------------------------------------
// 変更
// ---------------------------------------------------------------------------

function useInvalidateTasks() {
  const qc = useQueryClient();
  // promise を返す: mutation の onSuccess/onError から返すと、読み直しが終わるまで isPending が続く。
  return () => qc.invalidateQueries({ queryKey: tasksKeys.all });
}

type CreateOptions = UseMutationOptions<PatientTaskCreateResult, Error, PatientTaskCreate>;

/** やることを付ける (管理者)。結果の next_visit が「出す訪問」(無ければ null)。 */
export function useCreateTasks(options: CreateOptions = {}) {
  const invalidate = useInvalidateTasks();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<PatientTaskCreateResult, Error, PatientTaskCreate>({
    mutationFn: (payload) =>
      fetcher<PatientTaskCreateResult>(TASKS, {
        method: 'POST',
        body: JSON.stringify(patientTaskCreateSchema.parse(payload)),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: async (data, variables, onMutateResult, context) => {
      await invalidate();
      return options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type CompleteVars = { taskId: string; visitId?: string | null };
type CompleteOptions = UseMutationOptions<PatientTaskRead, Error, CompleteVars>;

/**
 * 完了にする。職員はその訪問の id を渡す (省くと今日の自分の訪問から探す)。
 * 管理者は visitId を省くと訪問を指さずに完了になる。
 * 預かり中のやることは、預かっている職員か管理者が visitId を省いて完了にできる
 * (完了の訪問は最初の訪問になる)。
 */
export function useCompleteTask(options: CompleteOptions = {}) {
  const invalidate = useInvalidateTasks();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<PatientTaskRead, Error, CompleteVars>({
    mutationFn: ({ taskId, visitId }) =>
      fetcher<PatientTaskRead>(`${TASKS}/${taskId}/complete`, {
        method: 'POST',
        body: JSON.stringify({ visit_id: visitId ?? null }),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: async (data, variables, onMutateResult, context) => {
      await invalidate();
      return options.onSuccess?.(data, variables, onMutateResult, context);
    },
    // 409 (ほかの人が先に完了) / 422 (この訪問には出ていない) のときも、いまの状態を読み直す。
    onError: async (error, variables, onMutateResult, context) => {
      await invalidate();
      return options.onError?.(error, variables, onMutateResult, context);
    },
  });
}

type TaskIdOptions = UseMutationOptions<PatientTaskRead, Error, string>;

function useTaskAction(action: 'reopen' | 'cancel', options: TaskIdOptions) {
  const invalidate = useInvalidateTasks();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<PatientTaskRead, Error, string>({
    mutationFn: (taskId) =>
      fetcher<PatientTaskRead>(`${TASKS}/${taskId}/${action}`, {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: async (data, variables, onMutateResult, context) => {
      await invalidate();
      return options.onSuccess?.(data, variables, onMutateResult, context);
    },
    // 409 (すでに戻した・取り消した) などのときも、いまの状態を読み直す。
    onError: async (error, variables, onMutateResult, context) => {
      await invalidate();
      return options.onError?.(error, variables, onMutateResult, context);
    },
  });
}

/** 完了を戻す (管理者・自分で完了にした職員)。引数は taskId。 */
export function useReopenTask(options: TaskIdOptions = {}) {
  return useTaskAction('reopen', options);
}

/** 取り消す (管理者)。引数は taskId。 */
export function useCancelTask(options: TaskIdOptions = {}) {
  return useTaskAction('cancel', options);
}

type SetDueVars = { taskId: string; dueDate: string | null };
type SetDueOptions = UseMutationOptions<PatientTaskRead, Error, SetDueVars>;

/** 期日を変える (管理者)。dueDate = null で期日なしに戻す。日付より前は 422。 */
export function useSetTaskDue(options: SetDueOptions = {}) {
  const invalidate = useInvalidateTasks();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<PatientTaskRead, Error, SetDueVars>({
    mutationFn: ({ taskId, dueDate }) =>
      fetcher<PatientTaskRead>(`${TASKS}/${taskId}/due`, {
        method: 'PATCH',
        body: JSON.stringify(patientTaskDueUpdateSchema.parse({ due_date: dueDate })),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: async (data, variables, onMutateResult, context) => {
      await invalidate();
      return options.onSuccess?.(data, variables, onMutateResult, context);
    },
    // 422 (完了・取消の後・日付より前) のときも、いまの状態を読み直す。
    onError: async (error, variables, onMutateResult, context) => {
      await invalidate();
      return options.onError?.(error, variables, onMutateResult, context);
    },
  });
}

function useInvalidateItemTypes() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ['task-item-types'] });
    // 項目の名前は一覧・訪問ごとの読み出しにも出る。
    void qc.invalidateQueries({ queryKey: tasksKeys.all });
  };
}

type CreateItemOptions = UseMutationOptions<TaskItemTypeRead, Error, TaskItemTypeCreate>;

/** 項目を足す (管理者)。同じ名前は 409。 */
export function useCreateTaskItemType(options: CreateItemOptions = {}) {
  const invalidate = useInvalidateItemTypes();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<TaskItemTypeRead, Error, TaskItemTypeCreate>({
    mutationFn: (payload) =>
      fetcher<TaskItemTypeRead>(ITEM_TYPES, {
        method: 'POST',
        body: JSON.stringify(taskItemTypeCreateSchema.parse(payload)),
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

type UpdateItemVars = { id: string; payload: TaskItemTypeUpdate };
type UpdateItemOptions = UseMutationOptions<TaskItemTypeRead, Error, UpdateItemVars>;

/** 項目の名前・並び順・使う/使わないを変える (管理者)。 */
export function useUpdateTaskItemType(options: UpdateItemOptions = {}) {
  const invalidate = useInvalidateItemTypes();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<TaskItemTypeRead, Error, UpdateItemVars>({
    mutationFn: ({ id, payload }) =>
      fetcher<TaskItemTypeRead>(`${ITEM_TYPES}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(taskItemTypeUpdateSchema.parse(payload)),
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
