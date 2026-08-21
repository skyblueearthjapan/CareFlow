/**
 * TanStack Query hooks for the Patient master.
 *
 * Endpoints (backend `app/api/v1/patients.py`)
 * ────────────────────────────────────────────
 *   GET    /api/v1/patients?limit&offset
 *   GET    /api/v1/patients/{id}
 *   POST   /api/v1/patients
 *   PATCH  /api/v1/patients/{id}
 *   DELETE /api/v1/patients/{id}
 *
 * NOTE: backend list takes `limit/offset` (not `page`) and has no
 * server-side `search`. We accept `page/search` here for caller ergonomics
 * and apply search client-side after fetch. When the backend grows search
 * support, swap `clientFilter` for a query param.
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
  normalizePatientInsurance,
  normalizePatientStatus,
  patientCreateSchema,
  patientUpdateSchema,
  type PatientCreate,
  type PatientFormValues,
  type PatientRead,
  type PatientUpdate,
} from '@/lib/schemas/patient';
import { compareByKana } from '@/lib/kana-sort';

export interface PatientsListParams {
  /** 1-indexed UI page. */
  page?: number;
  /** Items per page (default 20). */
  limit?: number;
  /** Free-text filter (matched against name/kana/code, client-side). */
  search?: string;
  /** When true, include soft-deleted rows (currently filtered server-side). */
  includeDeleted?: boolean;
  /** Filter by insurance label. */
  insurance?: string;
  /**
   * ステータスタブ (PO 決定 2026-08-09): 'active' 等の 5 値 or 'all'。
   * insurance と同じくクライアント側フィルタ。省略時は 'all' 相当。
   */
  status?: string;
  /**
   * 並び順 (PO要望 2026-08-21): 'code' (既定・登録ナンバー順) or
   * 'kana' (あいうえお順・kana昇順/未設定は末尾)。クライアント側ソート。
   */
  sort?: 'code' | 'kana';
  /**
   * 取得ゲート (default true)。`false` のときは fetch を抑止する。
   *
   * 現場ボード `/m` の提案シートでは「既存のお客様」モードのときだけ患者一覧
   * (最大 500 件) を取得すれば十分なため、新規モードでは `enabled: false` で
   * 無駄な取得を抑える (StageA MEDIUM 指摘の対処)。`authenticated` ゲートと
   * AND されるため、既存呼び出し (引数なし) は従来どおり取得する。
   */
  enabled?: boolean;
}

export interface PatientsListResult {
  items: PatientRead[];
  total: number;
  page: number;
  limit: number;
  /** True when the backend list returned exactly the hard cap (500), so the
   * client view may be silently truncated. */
  truncated: boolean;
  /**
   * ステータス別件数 (検索・保険区分フィルタ適用後 / ステータス絞り込み前)。
   * タブの件数バッジ用。key はステータス値 + 'all'。
   */
  statusCounts: Record<string, number>;
}

/** Backend hard cap for the list endpoint — keep in sync with the API. */
const PATIENT_LIST_HARD_CAP = 500;

const PATIENTS_KEY = ['patients'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * Drop `undefined` keys so PATCH semantics aren't polluted (only fields
 * the user actually changed go on the wire).
 */
function dropUndefined(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(payload)) {
    if (v !== undefined) out[k] = v;
  }
  return out;
}

/**
 * Pre-process raw form values into a shape that matches the zod schemas:
 *
 * - `weekly_pattern` is a structured `WeeklyPattern` dict (W3-C) bound by
 *   `WeeklyPatternEditor`. We pass it through as a plain object.
 * - `special_week` (form checkbox boolean) → v2 backend では
 *   `special_weekly_pattern` (JSONB) として送る。`true` のときは
 *   `weekly_pattern` のコピーを設定し、`false` で初期も `false` なら
 *   送信しない (undefined)、`true→false` の差分時のみ明示的に `null` で
 *   クリアする。
 *
 * Clear-vs-unchanged semantics (PATCH only):
 * If `initial` is provided and a previously set field is now empty, we emit
 * explicit `null` so the backend clears the column. Without `initial`
 * (Create flow), empty values stay `undefined` and `dropUndefined` removes
 * them from the payload.
 */
function prepareFormPayload(
  values: PatientFormValues,
  initial?: PatientFormValues,
): Record<string, unknown> {
  // `special_week` (boolean) は `special_weekly_pattern` (JSONB) に変換するので
  // wire 上には含めない。rest だけを通す。
  const { special_week, ...rest } = values;
  void special_week;

  // Treat the structured weekly_pattern as a JSONB dict. We always emit it
  // (it has sane defaults from `emptyWeeklyPattern`); on Create the schema
  // accepts the dict, on Update it overwrites.
  const weekly_pattern: Record<string, unknown> | null | undefined =
    values.weekly_pattern as unknown as Record<string, unknown>;

  let special_weekly_pattern: Record<string, unknown> | null | undefined;
  if (values.special_week) {
    // チェック済み → 通常パターンと同じ shape を special にも適用
    special_weekly_pattern = values.weekly_pattern as unknown as Record<string, unknown>;
  } else if (initial && initial.special_week) {
    // Was true, now unchecked → explicit null to clear the JSONB column.
    special_weekly_pattern = null;
  }

  return { ...rest, weekly_pattern, special_weekly_pattern };
}

/** GET /api/v1/patients — list (with client-side search/pagination wrapper). */
export function usePatients(
  params: PatientsListParams = {},
): UseQueryResult<PatientsListResult, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const page = Math.max(1, params.page ?? 1);
  const limit = Math.max(1, params.limit ?? 20);
  const search = params.search?.trim().toLowerCase() ?? '';
  const insurance = params.insurance ?? '';
  const statusFilter = params.status && params.status !== 'all' ? params.status : '';
  const sort = params.sort ?? 'code';
  const gate = params.enabled ?? true;

  return useQuery<PatientsListResult, Error>({
    queryKey: [...PATIENTS_KEY, { page, limit, search, insurance, statusFilter, sort }],
    enabled: status === 'authenticated' && gate,
    queryFn: async () => {
      // Fetch a generous window so the client-side search across pages
      // remains usable until backend search lands. Capped at the backend
      // hard limit (500) to keep payloads bounded.
      const all = await fetcher<PatientRead[]>(
        `/api/v1/patients?limit=${PATIENT_LIST_HARD_CAP}&offset=0`,
        { accessToken, refreshToken },
      );
      const truncated = all.length === PATIENT_LIST_HARD_CAP;
      const filtered = all.filter((p) => {
        if (insurance) {
          // 旧 v1 値 (``医療保険`` / ``介護保険``) も v2 値に正規化して比較
          const norm = normalizePatientInsurance(p.insurance as string | null | undefined);
          if (norm !== insurance) return false;
        }
        if (!search) return true;
        const hay = `${p.name ?? ''} ${p.kana ?? ''} ${p.code ?? ''}`.toLowerCase();
        return hay.includes(search);
      });
      // ステータス別件数 (タブのバッジ用)。検索・保険区分の適用後に数える =
      // バッジの数字と切替後の表示件数が常に一致する。
      const statusCounts: Record<string, number> = { all: filtered.length };
      for (const p of filtered) {
        const st = normalizePatientStatus(p.status as string | null | undefined);
        statusCounts[st] = (statusCounts[st] ?? 0) + 1;
      }
      const byStatus = statusFilter
        ? filtered.filter(
            (p) => normalizePatientStatus(p.status as string | null | undefined) === statusFilter,
          )
        : filtered;
      // あいうえお順 (PO要望 2026-08-21)。'code' は BE の既定順 (code昇順) のまま。
      const ordered = sort === 'kana' ? [...byStatus].sort(compareByKana) : byStatus;
      const start = (page - 1) * limit;
      const slice = ordered.slice(start, start + limit);
      return {
        items: slice,
        total: byStatus.length,
        page,
        limit,
        truncated,
        statusCounts,
      };
    },
  });
}

/** GET /api/v1/patients/{id} — single record. */
export function usePatient(id: string | null | undefined): UseQueryResult<PatientRead, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<PatientRead, Error>({
    queryKey: [...PATIENTS_KEY, id],
    enabled: status === 'authenticated' && !!id,
    queryFn: () => fetcher<PatientRead>(`/api/v1/patients/${id}`, { accessToken, refreshToken }),
  });
}

/** POST /api/v1/patients — create. */
export function useCreatePatient(): UseMutationResult<PatientRead, Error, PatientFormValues> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientRead, Error, PatientFormValues>({
    mutationFn: async (values) => {
      const prepared = prepareFormPayload(values);
      const parsed: PatientCreate = patientCreateSchema.parse(prepared);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
      return fetcher<PatientRead>('/api/v1/patients', {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PATIENTS_KEY });
    },
  });
}

/** PATCH /api/v1/patients/{id} — update.
 *
 * Pass `initial` (the form values derived from the loaded record) so we can
 * tell "user cleared a previously-set JSON field" apart from "field stays
 * unchanged" and emit explicit `null` to clear it server-side.
 */
export function useUpdatePatient(
  id: string,
  initial?: PatientFormValues,
): UseMutationResult<PatientRead, Error, PatientFormValues> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientRead, Error, PatientFormValues>({
    mutationFn: async (values) => {
      const prepared = prepareFormPayload(values, initial);
      const parsed: PatientUpdate = patientUpdateSchema.parse(prepared);
      const body = dropUndefined(parsed as unknown as Record<string, unknown>);
      return fetcher<PatientRead>(`/api/v1/patients/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: PATIENTS_KEY });
      qc.setQueryData([...PATIENTS_KEY, id], data);
    },
  });
}

/** DELETE /api/v1/patients/{id} — soft-delete (admin only). */
export function useDeletePatient(): UseMutationResult<void, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`/api/v1/patients/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PATIENTS_KEY });
    },
  });
}
