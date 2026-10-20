/**
 * TanStack Query hooks for /api/v1/google-drive — Google ドライブの接続 (管理者のみ)。
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import {
  googleDriveConnectStartSchema,
  googleDrivePickerTokenSchema,
  googleDriveStatusSchema,
  type GoogleDrivePickerToken,
  type GoogleDriveStatus,
} from '@/lib/schemas/googleDrive';

export const GOOGLE_DRIVE_STATUS_KEY = ['google-drive', 'status'] as const;
const BASE = '/api/v1/google-drive';

/** ApiError の body から BE の日本語 detail を取り出す (無ければ既定の文)。 */
export function googleDriveErrorMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.body && typeof e.body === 'object' && 'detail' in e.body) {
    const detail = (e.body as { detail: unknown }).detail;
    if (typeof detail === 'string' && detail) return detail;
  }
  return fallback;
}

function useTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    ready: status === 'authenticated',
  };
}

/** GET /status — 接続の状態。 */
export function useGoogleDriveStatus(enabled = true) {
  const { accessToken, refreshToken, ready } = useTokens();
  return useQuery<GoogleDriveStatus>({
    queryKey: GOOGLE_DRIVE_STATUS_KEY,
    queryFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/status`, { accessToken, refreshToken });
      return googleDriveStatusSchema.parse(raw);
    },
    enabled: ready && enabled,
  });
}

/** POST /connect — 許可の画面の URL を受け取る (呼び出し側がその URL へ移る)。 */
export function useStartGoogleDriveConnect() {
  const { accessToken, refreshToken } = useTokens();
  return useMutation<string, Error, void>({
    mutationFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/connect`, {
        method: 'POST',
        accessToken,
        refreshToken,
        // 合言葉の cookie を受け取る (同じオリジン)。
        credentials: 'same-origin',
      });
      return googleDriveConnectStartSchema.parse(raw).authorization_url;
    },
  });
}

/** POST /picker-token — Picker を開くための短い寿命のトークン。 */
export function useGoogleDrivePickerToken() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<GoogleDrivePickerToken, Error, void>({
    mutationFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/picker-token`, {
        method: 'POST',
        accessToken,
        refreshToken,
      });
      return googleDrivePickerTokenSchema.parse(raw);
    },
    // 接続し直しが要ると分かったら状態を取り直す。
    onError: () => void qc.invalidateQueries({ queryKey: GOOGLE_DRIVE_STATUS_KEY }),
  });
}

/** PUT /folder — Picker で選んだフォルダを保存先にする。 */
export function useSaveGoogleDriveFolder() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<GoogleDriveStatus, Error, string>({
    mutationFn: async (folderId) => {
      const raw = await fetcher<unknown>(`${BASE}/folder`, {
        method: 'PUT',
        body: JSON.stringify({ folder_id: folderId }),
        accessToken,
        refreshToken,
      });
      return googleDriveStatusSchema.parse(raw);
    },
    onSuccess: (data) => qc.setQueryData(GOOGLE_DRIVE_STATUS_KEY, data),
    onError: () => void qc.invalidateQueries({ queryKey: GOOGLE_DRIVE_STATUS_KEY }),
  });
}

/** DELETE /connection — 切断。 */
export function useDisconnectGoogleDrive() {
  const { accessToken, refreshToken } = useTokens();
  const qc = useQueryClient();
  return useMutation<GoogleDriveStatus, Error, void>({
    mutationFn: async () => {
      const raw = await fetcher<unknown>(`${BASE}/connection`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
      return googleDriveStatusSchema.parse(raw);
    },
    onSuccess: (data) => qc.setQueryData(GOOGLE_DRIVE_STATUS_KEY, data),
  });
}
