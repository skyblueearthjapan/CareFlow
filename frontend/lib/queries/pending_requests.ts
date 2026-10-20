/**
 * TanStack Query hooks for `/api/v1/pending-requests` (W3-FE6).
 *
 * 設計仕様書 v0.9 §3.5 / API 契約 v0.1 §7 に対応する申請履歴 (`pending_requests`)
 * の操作フック。Backend `app/api/v1/pending_requests.py` のエンドポイントを
 * 1:1 で写像する。
 *
 * Endpoints:
 *   GET    /api/v1/pending-requests                          — 一覧 (filter)
 *   POST   /api/v1/pending-requests                          — 申請作成
 *   PATCH  /api/v1/pending-requests/{id}/approve             — 承認
 *   PATCH  /api/v1/pending-requests/{id}/approve-with-edit   — 編集承認
 *   PATCH  /api/v1/pending-requests/{id}/reject              — 却下
 *   DELETE /api/v1/pending-requests/{id}                     — 取り下げ (申請者staff/admin)
 *
 * RBAC (§3.5.3):
 *   - GET / POST: admin / manager / staff (Staff は自分軸のみ)
 *   - PATCH approve / reject: admin / manager のみ — クライアント側でも
 *     `enabled` フラグでガードし、UI からの誤操作を抑止する
 *
 * 注意:
 *   ファイル所有: 本ファイルは W3-FE6 所有。W5-FE11 は **新関数の追加** のみ
 *   許可される（既存関数のシグネチャ変更は禁止 — `v2-implementation-plan.md`
 *   §4 W5-FE11 のファイル所有共有ルール参照）。
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
import type { Paginated } from '@/lib/schemas/integration';
import {
  pendingRequestApproveSchema,
  pendingRequestRejectSchema,
  pendingRequestV2CreateSchema,
  pendingRequestV2ReadSchema,
  type PendingRequestApprove,
  type PendingRequestReject,
  type PendingRequestV2Create,
  type PendingRequestV2Read,
  type RequestStatus,
  type RequestType,
} from '@/lib/schemas/pending_request';

const PENDING_REQUESTS_BASE = '/api/v1/pending-requests';
const PENDING_REQUESTS_KEY = ['pending-requests'] as const;

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

// ─────────────────────────────────────────────────────────────────────────
// List filters
// ─────────────────────────────────────────────────────────────────────────

/**
 * GET /api/v1/pending-requests のクエリ引数 (W2-BE5 と一致).
 *
 * すべてオプション。空オブジェクトを渡すと全申請を新着順 (`created_at desc`) で
 * 100 件取得する。Backend `list_pending_requests` の引数を参照。
 */
export interface UsePendingRequestsParams {
  status?: RequestStatus;
  request_type?: RequestType;
  target_staff_id?: string;
  target_patient_id?: string;
  /** YYYY-MM-DD inclusive */
  target_date_from?: string;
  /** YYYY-MM-DD inclusive */
  target_date_to?: string;
  limit?: number;
  offset?: number;
}

function buildListUrl(params: UsePendingRequestsParams): string {
  const usp = new URLSearchParams();
  if (params.status) usp.set('status', params.status);
  if (params.request_type) usp.set('request_type', params.request_type);
  if (params.target_staff_id) usp.set('target_staff_id', params.target_staff_id);
  if (params.target_patient_id) usp.set('target_patient_id', params.target_patient_id);
  if (params.target_date_from) usp.set('target_date_from', params.target_date_from);
  if (params.target_date_to) usp.set('target_date_to', params.target_date_to);
  usp.set('limit', String(params.limit ?? 100));
  usp.set('offset', String(params.offset ?? 0));
  return `${PENDING_REQUESTS_BASE}?${usp.toString()}`;
}

/**
 * GET /api/v1/pending-requests — 申請一覧.
 *
 * Backend は `Paginated[PendingRequestV2Read]` を返す。Staff ロールの場合は
 * 自動で「自分の申請 + 自分宛」のみにフィルタされる (§3.5.3)。UI 側では
 * 追加で `status` / `request_type` などのフィルタを掛けて表示する。
 *
 * Response の各 item は zod で再パースしているため、Backend が新しい列を
 * 追加してもクライアント型は古いまま安全に扱える (forward compat)。
 */
export function usePendingRequests(
  params: UsePendingRequestsParams = {},
): UseQueryResult<Paginated<PendingRequestV2Read>, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<Paginated<PendingRequestV2Read>, Error>({
    queryKey: [...PENDING_REQUESTS_KEY, 'list', params],
    enabled: status === 'authenticated',
    queryFn: async () => {
      const raw = await fetcher<{
        items: unknown[];
        total: number;
        limit: number;
        offset: number;
      }>(buildListUrl(params), { accessToken, refreshToken });
      return {
        ...raw,
        items: raw.items.map((i) => pendingRequestV2ReadSchema.parse(i)),
      } satisfies Paginated<PendingRequestV2Read>;
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────
// Create
// ─────────────────────────────────────────────────────────────────────────

/**
 * POST /api/v1/pending-requests — 申請作成.
 *
 * 現場ボードの手動申請（新規カルテ提案・配置シート）から `pending` として
 * 作成するために利用する（サーバは常に `pending` で作成する仕様）。
 *
 * Backend の RBAC (`_enforce_staff_self_scope`) に依存するため、フロント側では
 * Staff の自分軸チェックは行わない（403 が返ったら toast 表示で十分）。
 */
export function useCreatePendingRequest(): UseMutationResult<
  PendingRequestV2Read,
  Error,
  PendingRequestV2Create
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PendingRequestV2Read, Error, PendingRequestV2Create>({
    mutationFn: async (values) => {
      const parsed = pendingRequestV2CreateSchema.parse(values);
      const raw = await fetcher<unknown>(PENDING_REQUESTS_BASE, {
        method: 'POST',
        body: JSON.stringify(parsed),
        accessToken,
        refreshToken,
      });
      return pendingRequestV2ReadSchema.parse(raw);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PENDING_REQUESTS_KEY });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────
// Approve / Approve-with-edit / Reject
// ─────────────────────────────────────────────────────────────────────────

/**
 * PATCH /api/v1/pending-requests/{id}/approve — 内容そのまま承認.
 *
 * Body はオプションで `{ edited_payload: null }`（明示的に「編集なし」）を
 * 送る。Applier が業務テーブルに反映 + 自身の status を `approved` に更新
 * する処理を **同一トランザクション** で実行する (W2-BE5 §3.5.3 監査要件)。
 *
 * 既に approved / rejected の申請に対しては Backend が 409 を返す（冪等性
 * ガード）。フックではエラーを呼び出し側に伝播させ、UI 側で toast を出す
 * 想定。
 */
export function useApproveRequest(): UseMutationResult<PendingRequestV2Read, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PendingRequestV2Read, Error, string>({
    mutationFn: async (id) => {
      // edited_payload を明示的に null で送ることで「編集なしの承認」を表現。
      // Backend は edited_payload が undefined / null どちらでも内容そのまま
      // 反映するが、契約 (PendingRequestApprove) を経由して JSON 化する。
      const body: PendingRequestApprove = pendingRequestApproveSchema.parse({
        edited_payload: null,
      });
      const raw = await fetcher<unknown>(`${PENDING_REQUESTS_BASE}/${id}/approve`, {
        method: 'PATCH',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return pendingRequestV2ReadSchema.parse(raw);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PENDING_REQUESTS_KEY });
    },
  });
}

interface ApproveWithEditVariables {
  id: string;
  edited_payload: Record<string, unknown>;
}

/**
 * PATCH /api/v1/pending-requests/{id}/approve-with-edit — 編集して承認.
 *
 * 管理者が申請内容を一部修正した上で承認するケース (§3.5.4)。Backend は
 * `edited_payload` が null / 未指定だと 422 を返すため、必ず編集後の dict を
 * 渡すこと。
 */
export function useApproveWithEdit(): UseMutationResult<
  PendingRequestV2Read,
  Error,
  ApproveWithEditVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PendingRequestV2Read, Error, ApproveWithEditVariables>({
    mutationFn: async ({ id, edited_payload }) => {
      const body: PendingRequestApprove = pendingRequestApproveSchema.parse({
        edited_payload,
      });
      const raw = await fetcher<unknown>(`${PENDING_REQUESTS_BASE}/${id}/approve-with-edit`, {
        method: 'PATCH',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return pendingRequestV2ReadSchema.parse(raw);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PENDING_REQUESTS_KEY });
    },
  });
}

/**
 * DELETE /api/v1/pending-requests/{id} — 取り下げ (mobile-leave-request-design.md).
 *
 * pending の申請のみ取り下げ可能。staff は自分が申請したもののみ (他人は 404)、
 * admin は任意の pending。承認/却下済みは Backend が 409 を返す。
 * モバイル休み申請のポチポチ誤操作の自己修正手段。
 */
export function useWithdrawPendingRequest(): UseMutationResult<void, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${PENDING_REQUESTS_BASE}/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PENDING_REQUESTS_KEY });
    },
  });
}

interface RejectVariables {
  id: string;
  rejection_reason: string;
}

/**
 * PATCH /api/v1/pending-requests/{id}/reject — 却下.
 *
 * `rejection_reason` は **必須** (§3.5.4 / §4.4)。zod schema 側でも min(1) を
 * 要求しているため、空文字列を渡すと parse 段階で例外になる。UI 側のバリ
 * デーションと Backend の双方でガードする。
 */
export function useRejectRequest(): UseMutationResult<
  PendingRequestV2Read,
  Error,
  RejectVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PendingRequestV2Read, Error, RejectVariables>({
    mutationFn: async ({ id, rejection_reason }) => {
      const body: PendingRequestReject = pendingRequestRejectSchema.parse({
        rejection_reason,
      });
      const raw = await fetcher<unknown>(`${PENDING_REQUESTS_BASE}/${id}/reject`, {
        method: 'PATCH',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return pendingRequestV2ReadSchema.parse(raw);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PENDING_REQUESTS_KEY });
    },
  });
}
