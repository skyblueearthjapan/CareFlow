'use client';
/**
 * 予実比較レポート (月) のフック — カイポケの予定 × 実績。
 *
 * GET /api/v1/integrations/plan-actual-report?month=YYYY-MM&format=html
 *   → A4 の自己完結 HTML (text/html)。FE は Blob URL にして新しいタブへ開く
 *     (ReconcileReportButton / SyncReportButton と同じ方式)。
 *
 * 保存済みスナップショットを読むだけ (RPA は回らない・即応答)。
 * 実績がまだ無い月は 404 + 日本語 detail が返るので、`message` に載せ替えて投げ直す
 * (status は残すので呼び出し側で 404 だけ別文言にできる)。
 */
import { useMutation } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';

const REPORT_PATH = '/api/v1/integrations/plan-actual-report';

function reportUrl(month: string, format: 'html'): string {
  return `${REPORT_PATH}?${new URLSearchParams({ month, format }).toString()}`;
}

/** ApiError の body から BE の日本語 detail を取り出す (無ければ null)。 */
export function planActualErrorDetail(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  const body: unknown = e.body;
  if (typeof body === 'string' && body.trim()) return body;
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
  }
  return null;
}

/** BE の detail を `message` に載せ替えた ApiError を作る (status / body は保つ)。 */
function withServerDetail(e: unknown): unknown {
  const detail = planActualErrorDetail(e);
  return detail && e instanceof ApiError ? new ApiError(detail, e.status, e.body) : e;
}

export interface PlanActualReportHtml {
  /** BE が組み立てた A4 の自己完結 HTML。 */
  html: string;
  /** 新しいタブへ渡すための Blob (UTF-8 指定・文字化け防止)。 */
  blob: Blob;
}

/** 印刷用 HTML を取得する。 */
export function usePlanActualReport() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken;
  const refreshToken = session?.refreshToken;

  return useMutation<PlanActualReportHtml, Error, { month: string }>({
    mutationFn: async ({ month }) => {
      let raw: unknown;
      try {
        // text/html なので fetcher は JSON にできず素の文字列を返す (safeJsonParse)。
        raw = await fetcher<unknown>(reportUrl(month, 'html'), { accessToken, refreshToken });
      } catch (e) {
        throw withServerDetail(e);
      }
      // 空応答で真っ白なタブを開かない (JSON が返ってきた等の想定外も含む)。
      if (typeof raw !== 'string' || raw.trim() === '') {
        throw new Error('レポートを取得できませんでした');
      }
      return { html: raw, blob: new Blob([raw], { type: 'text/html;charset=utf-8' }) };
    },
  });
}
