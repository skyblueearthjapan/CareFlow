/**
 * TanStack Query hooks for /api/v1/checkin-settings — QR 訪問チェックイン Phase 4。
 *
 * チェックイン判定しきい値 (距離 / 精度 / 時間 / 退出忘れ) の全社一律設定。
 *
 *   GET /api/v1/checkin-settings        → { values, is_default }            (admin/manager)
 *   PUT /api/v1/checkin-settings        → 部分更新 (omit=不変, null=既定リセット) (admin/manager)
 *   GET /api/v1/checkin-settings/public → { match_m, review_m, accuracy_m } (全ログイン可)
 *
 * public はモバイル到着プレビューの概算しきい値同期に使う (staff も読める)。
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  checkinSettingsPublicSchema,
  checkinSettingsResponseSchema,
  type CheckinSettingsPublic,
  type CheckinSettingsResponse,
  type CheckinSettingsUpdate,
} from '@/lib/schemas/checkinSettings';

const CHECKIN_SETTINGS_KEY = ['checkin-settings'] as const;
const CHECKIN_SETTINGS_PUBLIC_KEY = ['checkin-settings', 'public'] as const;
const CHECKIN_SETTINGS_PATH = '/api/v1/checkin-settings';
const CHECKIN_SETTINGS_PUBLIC_PATH = '/api/v1/checkin-settings/public';

/** GET /api/v1/checkin-settings — 現在の有効値 + 既定フラグ (admin/manager)。 */
export function useCheckinSettings() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<CheckinSettingsResponse>({
    queryKey: CHECKIN_SETTINGS_KEY,
    queryFn: async () => {
      const raw = await fetcher<unknown>(CHECKIN_SETTINGS_PATH, { accessToken, refreshToken });
      return checkinSettingsResponseSchema.parse(raw);
    },
    enabled: status === 'authenticated',
    // 編集中フォームの draft をウィンドウ再フォーカスの背景リフェッチで上書きしない。
    refetchOnWindowFocus: false,
  });
}

/**
 * PUT /api/v1/checkin-settings — 部分更新。
 *   - 省略フィールド = 不変
 *   - 明示 null      = 既定値リセット
 * 成功後は checkin-settings / monitor を invalidate して再取得する。
 */
export function useUpdateCheckinSettings() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<CheckinSettingsResponse, Error, CheckinSettingsUpdate>({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>(CHECKIN_SETTINGS_PATH, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return checkinSettingsResponseSchema.parse(raw);
    },
    onSuccess: (data) => {
      // レスポンス自体が新しい有効値なので即時反映する。
      qc.setQueryData(CHECKIN_SETTINGS_KEY, data);
      void qc.invalidateQueries({ queryKey: CHECKIN_SETTINGS_KEY });
      // 退出忘れ等の判定はモニターが設定値で都度評価するため再フェッチを促す。
      void qc.invalidateQueries({ queryKey: ['monitor'] });
    },
  });
}

/**
 * GET /api/v1/checkin-settings/public — 距離系しきい値 (match/review/accuracy)。
 * staff を含む全ログインユーザが読める。モバイル到着プレビューの概算同期に使う。
 */
export function useCheckinSettingsPublic() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<CheckinSettingsPublic>({
    queryKey: CHECKIN_SETTINGS_PUBLIC_KEY,
    queryFn: async () => {
      const raw = await fetcher<unknown>(CHECKIN_SETTINGS_PUBLIC_PATH, {
        accessToken,
        refreshToken,
      });
      return checkinSettingsPublicSchema.parse(raw);
    },
    enabled: status === 'authenticated',
    // しきい値は頻繁に変わらない。プレビュー用なので軽くキャッシュする。
    staleTime: 5 * 60_000,
  });
}
