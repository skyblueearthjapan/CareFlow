/**
 * TanStack Query hooks for /api/v1/scheduling-settings — Phase G-88 Step4.
 *
 * 自動最適化ルール (訪問間バッファー / 移動速度 / 昼休み / 営業時間 / コース定員) の
 * 事業所共通設定を読み書きする。権限は admin/manager (BE が require_role で担保)。
 *
 *   GET /api/v1/scheduling-settings → { values, is_default }
 *   PUT /api/v1/scheduling-settings → 部分更新 (omit=不変, null=既定リセット), GET と同形を返す
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  schedulingSettingsResponseSchema,
  type SchedulingSettingsResponse,
  type SchedulingSettingsUpdate,
} from '@/lib/schemas/schedulingSettings';

const SCHEDULING_SETTINGS_KEY = ['scheduling-settings'] as const;
const SCHEDULING_SETTINGS_PATH = '/api/v1/scheduling-settings';

/** GET /api/v1/scheduling-settings — 現在の有効値 + 既定フラグ。 */
export function useSchedulingSettings() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<SchedulingSettingsResponse>({
    queryKey: SCHEDULING_SETTINGS_KEY,
    queryFn: async () => {
      const raw = await fetcher<unknown>(SCHEDULING_SETTINGS_PATH, {
        accessToken,
        refreshToken,
      });
      return schedulingSettingsResponseSchema.parse(raw);
    },
    enabled: status === 'authenticated',
    // 編集中フォームの draft をウィンドウ再フォーカスの背景リフェッチで
    // 上書きしないよう、focus 再取得を無効化する (page.tsx の draft 同期と併用)。
    refetchOnWindowFocus: false,
  });
}

/**
 * PUT /api/v1/scheduling-settings — 部分更新。
 *   - 省略フィールド = 不変
 *   - 明示 null      = 既定値リセット
 * 成功後は scheduling-settings クエリを invalidate して再取得する。
 */
export function useUpdateSchedulingSettings() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<SchedulingSettingsResponse, Error, SchedulingSettingsUpdate>({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>(SCHEDULING_SETTINGS_PATH, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return schedulingSettingsResponseSchema.parse(raw);
    },
    onSuccess: (data) => {
      // レスポンス自体が新しい有効値なので、再 fetch を待たず即時反映する。
      qc.setQueryData(SCHEDULING_SETTINGS_KEY, data);
      void qc.invalidateQueries({ queryKey: SCHEDULING_SETTINGS_KEY });
    },
  });
}
