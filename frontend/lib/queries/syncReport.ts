'use client';
/**
 * 連携結果レポート (らく助⇄カイポケ) のフック。
 *
 * GET /api/v1/integrations/kaipoke/jobs/{jobId}/report?format=json
 * 応答には印刷用の自己完結 HTML (`html`) が同梱される。FE はそれを新しいタブに書き出す
 * (ReconcileReportButton / FeasibilityCheckButton と同じ方式)。
 * 保存済みの job / job_items を読むだけなので RPA は回らず即応答する。
 *
 * BE 側は今も章立てを追加中のため、スキーマは `.passthrough()` で余剰キーを許容し、
 * FE が実際に使う `html` だけを必須にする。
 */
import { useMutation } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

export const SyncReportSchema = z
  .object({
    job: z.record(z.unknown()).nullish(),
    summary: z.record(z.unknown()).nullish(),
    // BE 側で値が増える可能性があるため enum で閉じない ('full' | 'summary_only' が現行)。
    detailLevel: z.string().nullish(),
    generatedAt: z.string().nullish(),
    html: z.string(),
  })
  .passthrough();

export type SyncReport = z.infer<typeof SyncReportSchema>;

export function useSyncReport() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken;
  const refreshToken = session?.refreshToken;

  return useMutation<SyncReport, Error, { jobId: string }>({
    mutationFn: async ({ jobId }) => {
      const raw = await fetcher<unknown>(
        `/api/v1/integrations/kaipoke/jobs/${encodeURIComponent(jobId)}/report?format=json`,
        { accessToken, refreshToken },
      );
      return SyncReportSchema.parse(raw);
    },
  });
}
