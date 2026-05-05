/**
 * TanStack Query hooks for the per-user notification inbox (Wave 4-D).
 *
 * Endpoints:
 *   GET   /api/v1/notifications?unread_only&limit&offset → Paginated<NotificationRead>
 *   PATCH /api/v1/notifications/{id}/read                → NotificationRead
 *   POST  /api/v1/notifications/mark-all-read            → { updated: number }
 *
 * The unread-count poll runs at 60s. Heavier polling would chew battery
 * on the mobile bundle for marginal UX gain; W6 will move this to a
 * server-sent stream.
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
import type {
  MarkAllReadResponse,
  NotificationList,
  NotificationRead,
} from '@/lib/schemas/notification';

const NOTIFICATIONS_KEY = ['notifications'] as const;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface UseNotificationsParams {
  unreadOnly?: boolean;
  limit?: number;
  offset?: number;
  /** Override polling interval in ms; default 60_000 (60s). */
  pollMs?: number;
}

/** GET /api/v1/notifications — paginated list, polls every 60s by default. */
export function useNotifications(
  params: UseNotificationsParams = {},
): UseQueryResult<NotificationList, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const unreadOnly = params.unreadOnly ?? false;
  const limit = params.limit ?? 50;
  const offset = params.offset ?? 0;
  const pollMs = params.pollMs ?? 60_000;

  return useQuery<NotificationList, Error>({
    queryKey: [...NOTIFICATIONS_KEY, { unreadOnly, limit, offset }],
    enabled: status === 'authenticated',
    refetchInterval: pollMs,
    refetchIntervalInBackground: false,
    queryFn: () => {
      const qs = new URLSearchParams({
        unread_only: String(unreadOnly),
        limit: String(limit),
        offset: String(offset),
      });
      return fetcher<NotificationList>(
        `/api/v1/notifications?${qs.toString()}`,
        { accessToken, refreshToken },
      );
    },
  });
}

/** PATCH /api/v1/notifications/{id}/read */
export function useMarkRead(): UseMutationResult<
  NotificationRead,
  Error,
  string
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<NotificationRead, Error, string>({
    mutationFn: (notificationId) =>
      fetcher<NotificationRead>(
        `/api/v1/notifications/${notificationId}/read`,
        { method: 'PATCH', accessToken, refreshToken },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
    },
  });
}

/** POST /api/v1/notifications/mark-all-read */
export function useMarkAllRead(): UseMutationResult<
  MarkAllReadResponse,
  Error,
  void
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<MarkAllReadResponse, Error, void>({
    mutationFn: () =>
      fetcher<MarkAllReadResponse>('/api/v1/notifications/mark-all-read', {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
    },
  });
}

/** Convenience: count of unread notifications in the most recent page. */
export function useUnreadNotificationCount(): number {
  const { data } = useNotifications({ unreadOnly: true, limit: 1 });
  return data?.total ?? 0;
}
