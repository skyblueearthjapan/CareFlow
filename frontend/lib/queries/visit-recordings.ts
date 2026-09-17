/**
 * 訪問の音声記録 — 型・zod スキーマ・TanStack Query hooks（設計 §10-3）。
 *
 * エンドポイント:
 *   POST   /api/v1/visit-recordings                 multipart → 202 VisitRecordingRead
 *   GET    /api/v1/visit-recordings?…               → {items, total}
 *   GET    /api/v1/visit-recordings/{id}            → VisitRecordingRead
 *   GET    /api/v1/visit-recordings/{id}/audio      → 音声本体（Bearer 必須・410 で削除済み）
 *   PATCH  /api/v1/visit-recordings/{id}            → VisitRecordingRead
 *
 * 応答は**行単位で検証する**（`parseVisitRecordings`）。BE の項目欠落 1 行で
 * 訪問詳細が丸ごと落ちるのは割に合わないので、読めない行だけ warn して捨てる
 * （`lib/queries/me.ts` の `parseMyOverrides` と同じ流儀）。
 *
 * アップロードは `fetcher` を通さない — JSON の Content-Type を必ず付けるため
 * multipart の boundary が壊れる（`lib/queries/visit-photos.ts` と同じ理由）。
 * 進捗が要る箇所があるので送信は XMLHttpRequest（fetch は upload 進捗を取れない）。
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
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';
import { audioFileName, VISIT_RECORDINGS_PATH } from '@/lib/voice/queue';

/** 状態（設計 §10-2）。`unlinked` は患者未紐付けの表示用。 */
export type VisitRecordingStatus =
  | 'uploaded'
  | 'transcribing'
  | 'summarized'
  | 'failed'
  | 'unlinked';

/**
 * 要約 JSON。キーは看護記録テンプレの見出し（主訴・様子／バイタル／処置・ケア／
 * 申し送り／次回／free）だが、テンプレは `prompt_version` で変わりうるので
 * **形は決め打ちしない**（描画側が配列 / 連想 / 文字列を見て出し分ける）。
 */
export const visitRecordingSummarySchema = z.record(z.unknown());
export type VisitRecordingSummary = Record<string, unknown>;

export const visitRecordingReadSchema = z.object({
  id: z.string(),
  visit_id: z.string().nullable().optional(),
  patient_id: z.string().nullable().optional(),
  patient_name: z.string().nullable().optional(),
  staff_id: z.string().nullable().optional(),
  staff_name: z.string().nullable().optional(),
  office_id: z.string().nullable().optional(),
  recorded_at: z.string(),
  ended_at: z.string().nullable().optional(),
  duration_sec: z.number().nullable().optional(),
  // 未知の状態でも行を捨てない（表示側が既定の見た目に落とす）。
  status: z.string(),
  has_audio: z.boolean().nullable().optional(),
  audio_mime: z.string().nullable().optional(),
  audio_bytes: z.number().nullable().optional(),
  transcript: z.string().nullable().optional(),
  transcript_json: z.unknown().nullable().optional(),
  summary: visitRecordingSummarySchema.nullable().optional(),
  summary_text: z.string().nullable().optional(),
  provider: z.string().nullable().optional(),
  model: z.string().nullable().optional(),
  prompt_version: z.string().nullable().optional(),
  tokens_in: z.number().nullable().optional(),
  tokens_out: z.number().nullable().optional(),
  cost_usd: z.union([z.number(), z.string()]).nullable().optional(),
  error_message: z.string().nullable().optional(),
  consent_confirmed: z.boolean().nullable().optional(),
  reviewed_by: z.string().nullable().optional(),
  reviewed_at: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});

export type VisitRecordingRead = z.infer<typeof visitRecordingReadSchema>;

export interface VisitRecordingList {
  items: VisitRecordingRead[];
  total: number;
}

/** `GET /visit-recordings/{id}/audio`（Bearer 必須・`AuthedAudio` が使う）。 */
export function recordingAudioUrl(id: string): string {
  return `${VISIT_RECORDINGS_PATH}/${id}/audio`;
}

/** 一覧応答の寛容パース。`{items,total}` でも素の配列でも読む。 */
export function parseVisitRecordings(raw: unknown): VisitRecordingList {
  const rows: unknown[] = Array.isArray(raw)
    ? raw
    : raw && typeof raw === 'object' && Array.isArray((raw as { items?: unknown }).items)
      ? ((raw as { items: unknown[] }).items ?? [])
      : [];
  const total =
    raw && typeof raw === 'object' && typeof (raw as { total?: unknown }).total === 'number'
      ? (raw as { total: number }).total
      : rows.length;
  const items: VisitRecordingRead[] = [];
  for (const row of rows) {
    const parsed = visitRecordingReadSchema.safeParse(row);
    if (parsed.success) {
      items.push(parsed.data);
    } else {
      console.warn('[visit-recordings] 読めない行を無視しました', { issues: parsed.error.issues });
    }
  }
  return { items, total };
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

export interface UseVisitRecordingsParams {
  visitId?: string | null;
  patientId?: string | null;
  staffId?: string | null;
  /** `YYYY-MM-DD`（inclusive）。 */
  from?: string | null;
  to?: string | null;
  status?: string | null;
  limit?: number;
  offset?: number;
}

function buildListQuery(params: UseVisitRecordingsParams): string {
  const qs = new URLSearchParams();
  if (params.visitId) qs.set('visit_id', params.visitId);
  if (params.patientId) qs.set('patient_id', params.patientId);
  if (params.staffId) qs.set('staff_id', params.staffId);
  if (params.from) qs.set('from', params.from);
  if (params.to) qs.set('to', params.to);
  if (params.status) qs.set('status', params.status);
  qs.set('limit', String(params.limit ?? 50));
  qs.set('offset', String(params.offset ?? 0));
  return qs.toString();
}

/** GET /visit-recordings — 訪問 / 患者 / スタッフ / 期間で絞った一覧。 */
export function useVisitRecordings(
  params: UseVisitRecordingsParams = {},
): UseQueryResult<VisitRecordingList, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  // 絞り込みが 1 つも無い問い合わせは投げない（全件取得の事故防止）。
  const hasScope = !!(params.visitId || params.patientId || params.staffId);

  return useQuery<VisitRecordingList, Error>({
    queryKey: ['visit-recordings', 'list', params],
    enabled: status === 'authenticated' && hasScope,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${VISIT_RECORDINGS_PATH}?${buildListQuery(params)}`, {
        accessToken,
        refreshToken,
      });
      return parseVisitRecordings(raw);
    },
  });
}

/** GET /visit-recordings/{id} — `transcript` / `summary` を含む 1 件。 */
export function useVisitRecording(
  id: string | null | undefined,
): UseQueryResult<VisitRecordingRead, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useQuery<VisitRecordingRead, Error>({
    queryKey: ['visit-recordings', 'detail', id ?? null],
    enabled: status === 'authenticated' && !!id,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${VISIT_RECORDINGS_PATH}/${id}`, {
        accessToken,
        refreshToken,
      });
      return visitRecordingReadSchema.parse(raw);
    },
  });
}

export interface UploadRecordingVariables {
  /** 録音した音声（`File` でも `Blob` でも可）。 */
  audio: Blob;
  visitId?: string | null;
  patientId?: string | null;
  /** 端末時刻（ISO 8601）。 */
  recordedAt: string;
  durationSec: number;
  consent: boolean;
  deviceMime?: string | null;
  /**
   * 端末側の一意キー（レビュー M-B）。キュー投入（`enqueueVoice` の id）と
   * **同じ値**を渡す。同じ音声がキュー経由と直接送信の両方で届いても、BE が
   * `client_id` で 1 件に畳める（409）。作れない端末では null で送らない。
   */
  clientId?: string | null;
  /** 0〜1 の送信進捗。 */
  onProgress?: (ratio: number) => void;
}

/**
 * POST /visit-recordings（multipart・202 で `VisitRecordingRead`）。
 *
 * 圏外を跨ぐ録音は `lib/voice/queue.ts` のキュー経由で送る。このフックは
 * 「その場で送って結果を受け取る」直接経路（ボイスメモ取り込みの即時送信など）。
 */
export function useUploadRecording(): UseMutationResult<
  VisitRecordingRead,
  Error,
  UploadRecordingVariables
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken } = authPair(session);

  return useMutation<VisitRecordingRead, Error, UploadRecordingVariables>({
    mutationFn: (vars) => uploadRecording(vars, accessToken),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visit-recordings'] });
    },
  });
}

/** XHR 送信の実体（進捗コールバックのため fetch ではなく XMLHttpRequest）。 */
function uploadRecording(
  vars: UploadRecordingVariables,
  accessToken: string | null,
): Promise<VisitRecordingRead> {
  const mime = vars.deviceMime ?? vars.audio.type ?? '';
  const form = new FormData();
  form.append('audio', vars.audio, audioFileName(mime));
  if (vars.visitId) form.append('visit_id', vars.visitId);
  if (vars.patientId) form.append('patient_id', vars.patientId);
  form.append('recorded_at', vars.recordedAt);
  form.append('duration_sec', String(Math.max(0, Math.round(vars.durationSec))));
  form.append('consent', vars.consent ? 'true' : 'false');
  if (mime) form.append('device_mime', mime);
  if (vars.clientId) form.append('client_id', vars.clientId);

  return new Promise<VisitRecordingRead>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${resolveBaseUrl()}${VISIT_RECORDINGS_PATH}`);
    if (accessToken) xhr.setRequestHeader('Authorization', `Bearer ${accessToken}`);
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable && ev.total > 0) vars.onProgress?.(ev.loaded / ev.total);
    };
    xhr.onerror = () => reject(new Error('音声を送信できませんでした'));
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(`音声の送信に失敗しました（${xhr.status}）`));
        return;
      }
      vars.onProgress?.(1);
      try {
        resolve(visitRecordingReadSchema.parse(JSON.parse(xhr.responseText)));
      } catch (err) {
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    };
    xhr.send(form);
  });
}

/** PATCH のボディ（設計 §10-3）。 */
export interface UpdateRecordingPayload {
  patient_id?: string;
  visit_id?: string;
  reviewed?: boolean;
  note_append?: string;
}

/** PATCH /visit-recordings/{id} — 確認済み / 紐付け変更 / 追記。 */
export function useUpdateRecording(
  id: string,
): UseMutationResult<VisitRecordingRead, Error, UpdateRecordingPayload> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<VisitRecordingRead, Error, UpdateRecordingPayload>({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>(`${VISIT_RECORDINGS_PATH}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return visitRecordingReadSchema.parse(raw);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visit-recordings'] });
    },
  });
}
