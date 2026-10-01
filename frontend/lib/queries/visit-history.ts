/**
 * 打刻履歴 — 型・zod スキーマ・TanStack Query hooks
 * （設計 `docs/plans/visit-history-design-2026-09-30.md` §2・§3）。
 *
 * エンドポイント（すべて admin / staff。staff は BE が自分の分に固定する）:
 *   GET /api/v1/visit-history          → {items, total, summary}
 *   GET /api/v1/visit-history/export   → .xlsx（Content-Disposition: attachment）
 *   GET /api/v1/visit-history/report   → A4 縦の HTML
 *
 * 実績の時刻を合わせる（設計 `docs/plans/actual-time-adjust-design-2026-09-30.md` §6）:
 *   PUT    /api/v1/visits/{visit_id}/actual-time         → VisitRead
 *   DELETE /api/v1/visits/{visit_id}/actual-time?kind=   → VisitRead（読取時刻に戻す）
 *
 * 一覧は**行単位で検証する**（`parseVisitHistory`）。BE の項目欠落 1 行で表が丸ごと
 * 落ちるのは割に合わないので、読めない行だけ warn して捨てる
 * （`lib/queries/visit-recordings.ts` の `parseVisitRecordings` と同じ流儀）。
 */
'use client';

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';

export const VISIT_HISTORY_PATH = '/api/v1/visit-history';

/** 行の状態（設計 §2）。 */
export type VisitHistoryState = 'done' | 'in_progress' | 'no_departure' | 'none' | 'future';

/** 打刻の絞り込み（設計 §3 の `state`）。'' = すべて。 */
export type VisitHistoryStateFilter = '' | 'in' | 'nodep' | 'none' | 'adjusted' | 'special';

/** 並び（設計 §3 の `sort`。A4 の `group` も同じ 3 値）。 */
export type VisitHistorySort = 'date' | 'staff' | 'patient';

/** 実績のどちらの時刻か（設計 2026-09-30 §6-1）。 */
export type ActualTimeKind = 'arrival' | 'departure';

/** 効いている調整 1 件（設計 §6-3 の `adjustments`）。 */
export const visitHistoryAdjustmentSchema = z.object({
  /** `arrival` / `departure`。 */
  kind: z.string(),
  /**
   * 理由。画面からは尋ねなくなった（PO 決定 2026-10-01・設計 §12）が、過去の調整には
   * 残っているので読む（あれば履歴に出す）。古い応答には無い。
   */
  reason_code: z.string().nullable().optional(),
  reason_label: z.string().nullable().optional(),
  reason_text: z.string().nullable().optional(),
  by_name: z.string().nullable().optional(),
  /** 合わせた日時（UTC の ISO 文字列）。 */
  created_at: z.string().nullable().optional(),
});

export type VisitHistoryAdjustment = z.infer<typeof visitHistoryAdjustmentSchema>;

export const visitHistoryRowSchema = z.object({
  visit_id: z.string(),
  /** `YYYY-MM-DD`（JST の日付）。 */
  visit_date: z.string(),
  office_id: z.string().nullable().optional(),
  office_name: z.string().nullable().optional(),
  patient_id: z.string().nullable().optional(),
  patient_name: z.string().nullable().optional(),
  /** 予定。予定外の訪問は null。 */
  start_time: z.string().nullable().optional(),
  end_time: z.string().nullable().optional(),
  planned_staff_id: z.string().nullable().optional(),
  planned_staff_name: z.string().nullable().optional(),
  actual_staff_id: z.string().nullable().optional(),
  actual_staff_name: z.string().nullable().optional(),
  /**
   * 実績時刻（調整後。無ければ読取時刻）。UTC の ISO 文字列。
   * 表示は必ず `lib/format/actualTime.ts` を通す。
   */
  arrival_at: z.string().nullable().optional(),
  departure_at: z.string().nullable().optional(),
  /** 読取時刻（QR を読んだ時刻）。読み取りが無ければ null。 */
  arrival_read_at: z.string().nullable().optional(),
  departure_read_at: z.string().nullable().optional(),
  arrival_adjusted: z.boolean().nullable().optional(),
  departure_adjusted: z.boolean().nullable().optional(),
  /** 読み取りの無い退出（手で入れた時刻）。 */
  departure_manual: z.boolean().nullable().optional(),
  /** 圏外で退避して遅れて届いた打刻の受信時刻（遅れていなければ null）。 */
  arrival_late_received_at: z.string().nullable().optional(),
  departure_late_received_at: z.string().nullable().optional(),
  // 調整の形が崩れていても行は捨てない（時刻そのものは読める）。
  adjustments: z.array(visitHistoryAdjustmentSchema).nullish().catch(null),
  /** 今のユーザーがこの訪問の実績を合わせられるか（権限は BE が判定）。 */
  adjust_allowed: z.boolean().nullable().optional(),
  stay_minutes: z.number().nullable().optional(),
  checkin_source: z.string().nullable().optional(),
  match_status: z.string().nullable().optional(),
  is_substitute: z.boolean().nullable().optional(),
  is_unplanned: z.boolean().nullable().optional(),
  is_cancelled: z.boolean().nullable().optional(),
  // 未知の状態でも行を捨てない（表示側が既定の見た目に落とす）。
  state: z.string(),
  remarks: z.array(z.string()).nullable().optional(),
});

export type VisitHistoryRow = z.infer<typeof visitHistoryRowSchema>;

/** 集計帯（ページングする前の絞り込み結果全体・`future` を除く）。 */
export interface VisitHistorySummary {
  visits: number;
  with_arrival: number;
  with_departure: number;
  no_departure: number;
  none: number;
  /** 時刻の調整がある件数。応答に無い（古い BE）ときは持たない = 画面に出さない。 */
  adjusted?: number;
}

/** 見出し行の件数（ページングする前の絞り込み結果全体・設計 2026-09-30 §6-3）。 */
export interface VisitHistoryGroupCount {
  /** 看護師名または患者名。 */
  label: string;
  count: number;
  with_arrival: number;
}

export interface VisitHistoryList {
  items: VisitHistoryRow[];
  total: number;
  summary: VisitHistorySummary;
  /** `sort=date` は空配列。応答に無い（古い BE）ときは null = 表示中の行から数える。 */
  groups: VisitHistoryGroupCount[] | null;
}

const SUMMARY_KEYS = ['visits', 'with_arrival', 'with_departure', 'no_departure', 'none'] as const;

/** 一覧応答の寛容パース。読めない行は捨て、集計の欠けは 0 に落とす。 */
export function parseVisitHistory(raw: unknown): VisitHistoryList {
  const obj = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const rows: unknown[] = Array.isArray(obj.items) ? obj.items : [];
  const items: VisitHistoryRow[] = [];
  for (const row of rows) {
    const parsed = visitHistoryRowSchema.safeParse(row);
    if (parsed.success) {
      items.push(parsed.data);
    } else {
      console.warn('[visit-history] 読めない行を無視しました', { issues: parsed.error.issues });
    }
  }
  const rawSummary =
    obj.summary && typeof obj.summary === 'object' ? (obj.summary as Record<string, unknown>) : {};
  const summary: VisitHistorySummary = {
    visits: 0,
    with_arrival: 0,
    with_departure: 0,
    no_departure: 0,
    none: 0,
  };
  for (const key of SUMMARY_KEYS) {
    const v = rawSummary[key];
    if (typeof v === 'number' && Number.isFinite(v)) summary[key] = v;
  }
  const adjusted = rawSummary.adjusted;
  if (typeof adjusted === 'number' && Number.isFinite(adjusted)) summary.adjusted = adjusted;

  let groups: VisitHistoryGroupCount[] | null = null;
  if (Array.isArray(obj.groups)) {
    groups = [];
    for (const g of obj.groups as unknown[]) {
      const rec = g && typeof g === 'object' ? (g as Record<string, unknown>) : null;
      if (rec && typeof rec.label === 'string' && typeof rec.count === 'number') {
        groups.push({
          label: rec.label,
          count: rec.count,
          with_arrival: typeof rec.with_arrival === 'number' ? rec.with_arrival : 0,
        });
      }
    }
  }
  return {
    items,
    total: typeof obj.total === 'number' ? obj.total : items.length,
    summary,
    groups,
  };
}

/** 一覧・Excel・A4 で共通の絞り込み（設計 §3 の共通クエリ）。 */
export interface VisitHistoryFilters {
  /** `YYYY-MM-DD`（inclusive）。 */
  from: string;
  to: string;
  patientId?: string | null;
  officeId?: string | null;
  /** 予定の担当または実際の打刻者。 */
  staffId?: string | null;
  state?: VisitHistoryStateFilter | null;
  /** 患者名・予定担当名・打刻者名の部分一致。2 文字未満は送らない。 */
  q?: string | null;
  sort?: VisitHistorySort | null;
}

export interface UseVisitHistoryParams extends VisitHistoryFilters {
  limit?: number;
  offset?: number;
  /** false の間は問い合わせない（期間が長すぎるときなど）。 */
  enabled?: boolean;
}

/** A4 の出力オプション（設計 §3-3）。 */
export interface VisitHistoryReportOptions {
  group: VisitHistorySort;
  /** 打刻のない予定も載せる。 */
  includeNone: boolean;
  /** 看護師・患者ごとに改ページ。 */
  pageBreak: boolean;
}

/** 検索語の下限 (文字)。BE も 2 文字未満は無視するが、無駄な再取得を避けるため送らない。 */
const SEARCH_MIN_LEN = 2;

/** 絞り込み部分（from / to / patient_id / office_id / staff_id / state / q）。 */
function filterParams(f: VisitHistoryFilters): URLSearchParams {
  const qs = new URLSearchParams();
  qs.set('from', f.from);
  qs.set('to', f.to);
  if (f.patientId) qs.set('patient_id', f.patientId);
  if (f.officeId) qs.set('office_id', f.officeId);
  if (f.staffId) qs.set('staff_id', f.staffId);
  if (f.state) qs.set('state', f.state);
  const q = f.q?.trim() ?? '';
  if (q.length >= SEARCH_MIN_LEN) qs.set('q', q);
  return qs;
}

/** 一覧のクエリ文字列（純関数・テストから直接縛る）。 */
export function buildHistoryListQuery(params: UseVisitHistoryParams): string {
  const qs = filterParams(params);
  qs.set('sort', params.sort ?? 'date');
  qs.set('limit', String(params.limit ?? 50));
  qs.set('offset', String(params.offset ?? 0));
  return qs.toString();
}

/** Excel 出力のクエリ文字列（絞り込み＋並び。ページングは付けない）。 */
export function buildHistoryExportQuery(filters: VisitHistoryFilters): string {
  const qs = filterParams(filters);
  qs.set('sort', filters.sort ?? 'date');
  return qs.toString();
}

/** A4 出力のクエリ文字列（絞り込み＋ group / include_none / page_break）。 */
export function buildHistoryReportQuery(
  filters: VisitHistoryFilters,
  options: VisitHistoryReportOptions,
): string {
  const qs = filterParams(filters);
  qs.set('group', options.group);
  qs.set('include_none', String(options.includeNone));
  qs.set('page_break', String(options.pageBreak));
  return qs.toString();
}

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') return '';
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

/** GET /visit-history — 期間と絞り込みで引いた 1 ページ分と、全体の集計。 */
export function useVisitHistory(
  params: UseVisitHistoryParams,
): UseQueryResult<VisitHistoryList, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { enabled = true, ...rest } = params;

  return useQuery<VisitHistoryList, Error>({
    queryKey: ['visit-history', 'list', rest],
    enabled: status === 'authenticated' && enabled,
    // ページ送り・絞り込みで条件が変わっても、次の結果が届くまで前の結果を出し続ける
    // （集計帯・件数・ページ送りが一瞬消えて画面が跳ねるのを防ぐ）。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${VISIT_HISTORY_PATH}?${buildHistoryListQuery(rest)}`, {
        accessToken,
        refreshToken,
      });
      return parseVisitHistory(raw);
    },
  });
}

/** `Content-Disposition` からファイル名を取り出す（RFC 5987 の `filename*` を優先）。 */
export function parseDispositionFilename(header: string | null): string | null {
  if (!header) return null;
  const star = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      /* ASCII 形へ落とす */
    }
  }
  const ascii = header.match(/filename="?([^";]+)"?/i);
  return ascii?.[1]?.trim() || null;
}

export interface VisitHistoryExportFile {
  blob: Blob;
  /** 保存するファイル名（BE の指定が無ければ `visit-history_{from}_{to}.xlsx`）。 */
  filename: string;
}

/**
 * GET /visit-history/export — 絞り込みどおりの .xlsx を取得する。
 *
 * `fetcher` は本文をテキストで読むのでバイナリには使えない。既存の Excel 出力
 * （`lib/api/patientsExcel.ts`）と同じく Bearer 付きの `fetch` で blob を受ける。
 */
export function useVisitHistoryExport(): UseMutationResult<
  VisitHistoryExportFile,
  Error,
  VisitHistoryFilters
> {
  const { data: session } = useSession();
  const { accessToken } = authPair(session);

  return useMutation<VisitHistoryExportFile, Error, VisitHistoryFilters>({
    mutationFn: async (filters) => {
      const path = `${VISIT_HISTORY_PATH}/export?${buildHistoryExportQuery(filters)}`;
      const headers: Record<string, string> = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      const res = await fetch(`${resolveBaseUrl()}${path}`, {
        method: 'GET',
        headers,
        cache: 'no-store',
      });
      if (!res.ok) {
        // `apiErrorMessage` が `detail` を拾えるよう、JSON なら JSON のまま載せる。
        const text = await res.text();
        let body: unknown = text;
        try {
          body = JSON.parse(text);
        } catch {
          /* テキストのまま */
        }
        throw new ApiError(`API ${res.status} ${res.statusText} (${path})`, res.status, body);
      }
      const filename =
        parseDispositionFilename(res.headers.get('Content-Disposition')) ??
        `visit-history_${filters.from}_${filters.to}.xlsx`;
      return { blob: await res.blob(), filename };
    },
  });
}

export interface VisitHistoryReportVariables {
  filters: VisitHistoryFilters;
  options: VisitHistoryReportOptions;
}

/**
 * GET /visit-history/report — A4 縦の自己完結 HTML を文字列で受ける。
 *
 * 契約は「HTML を返す」。`fetcher` は JSON にできない本文を文字列のまま返すので
 * それを使う（401 の再発行・Cloudflare Access 切れの案内を `fetcher` に任せるため）。
 * BE が既存レポートと同じ `{html}` の JSON で返してきても読めるようにしてある。
 */
export function useVisitHistoryReport(): UseMutationResult<
  string,
  Error,
  VisitHistoryReportVariables
> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<string, Error, VisitHistoryReportVariables>({
    mutationFn: async ({ filters, options }) => {
      const raw = await fetcher<unknown>(
        `${VISIT_HISTORY_PATH}/report?${buildHistoryReportQuery(filters, options)}`,
        { accessToken, refreshToken },
      );
      const html =
        typeof raw === 'string'
          ? raw
          : raw && typeof raw === 'object' && typeof (raw as { html?: unknown }).html === 'string'
            ? (raw as { html: string }).html
            : '';
      if (html.trim() === '') throw new Error('レポートの本文が空でした');
      return html;
    },
  });
}

/** 調整の出どころ。サーバはこのヘッダで `source`（mobile / pc）を決める。 */
const CLIENT_SURFACE_HEADER = { 'X-Client-Surface': 'pc' } as const;

/**
 * 実績が変わると見え方が変わる画面のクエリ（設計 2026-09-30 §8-3）。
 * 打刻履歴・訪問モニター・訪問（スケジュール / 詳細）・スマホの自分の訪問。
 */
const ACTUAL_TIME_DEPENDENT_KEYS = [['visit-history'], ['monitor'], ['visits'], ['me']] as const;

function useInvalidateActualTime(): () => void {
  const qc = useQueryClient();
  return () => {
    for (const queryKey of ACTUAL_TIME_DEPENDENT_KEYS) {
      void qc.invalidateQueries({ queryKey });
    }
  };
}

export interface AdjustActualTimeVariables {
  visitId: string;
  kind: ActualTimeKind;
  /** JST の `HH:MM`。日付はサーバが `visit_date` と組み合わせる。 */
  time: string;
}

/**
 * PUT /api/v1/visits/{visit_id}/actual-time — 実績の時刻を合わせる（PC）。
 *
 * 予定（`start_time` / `end_time`）は動かない。範囲外は 422、期間外などは 403、
 * 読み取りが無い・削除済みは 409 で、どれも `detail` がそのまま画面に出せる日本語
 * （`apiErrorMessage` で取り出す）。応答の `VisitRead` は使わず、一覧を取り直す。
 * 理由（`reason_code` / `reason_text`）は API では任意のまま受けるが、送らない
 * （PO 決定 2026-10-01・設計 §12）。
 */
export function useAdjustVisitActualTime(): UseMutationResult<
  unknown,
  Error,
  AdjustActualTimeVariables
> {
  const invalidate = useInvalidateActualTime();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, AdjustActualTimeVariables>({
    mutationFn: ({ visitId, kind, time }) =>
      fetcher<unknown>(`/api/v1/visits/${encodeURIComponent(visitId)}/actual-time`, {
        method: 'PUT',
        headers: CLIENT_SURFACE_HEADER,
        body: JSON.stringify({ kind, time }),
        accessToken,
        refreshToken,
      }),
    onSuccess: invalidate,
  });
}

export interface ResetActualTimeVariables {
  visitId: string;
  kind: ActualTimeKind;
}

/** DELETE /api/v1/visits/{visit_id}/actual-time?kind= — 読取時刻に戻す（PC）。 */
export function useResetVisitActualTime(): UseMutationResult<
  unknown,
  Error,
  ResetActualTimeVariables
> {
  const invalidate = useInvalidateActualTime();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<unknown, Error, ResetActualTimeVariables>({
    mutationFn: ({ visitId, kind }) =>
      fetcher<unknown>(
        `/api/v1/visits/${encodeURIComponent(visitId)}/actual-time?kind=${encodeURIComponent(kind)}`,
        { method: 'DELETE', headers: CLIENT_SURFACE_HEADER, accessToken, refreshToken },
      ),
    onSuccess: invalidate,
  });
}
