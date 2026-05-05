/**
 * TanStack Query hooks for /api/v1/ai/* — D4 Phase E / Wave 4-B.
 *
 * Mirrors backend `app/api/v1/ai.py`.
 *   - `useInterpret()`        POST /ai/interpret  (admin/manager/staff)
 *   - `useAiLogs(params)`     GET  /ai/logs       (admin/manager only)
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type { Paginated } from '@/lib/schemas/integration';
import {
  type AiLogRead,
  AiLogReadSchema,
  type InterpretRequest,
  type InterpretResponse,
  InterpretResponseSchema,
} from '@/lib/schemas/ai';

export function useInterpret() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<InterpretResponse, Error, InterpretRequest>({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>('/api/v1/ai/interpret', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      // Validate the response shape so downstream UI never crashes on
      // unexpected payloads from a Gemini SDK upgrade.
      return InterpretResponseSchema.parse(raw);
    },
  });
}

// --- /ai/logs (admin/manager) -------------------------------------------

export interface UseAiLogsParams {
  since?: string;
  until?: string;
  model?: string;
  limit?: number;
  offset?: number;
}

export function useAiLogs(params: UseAiLogsParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const { since, until, model, limit = 50, offset = 0 } = params;

  return useQuery<Paginated<AiLogRead>>({
    queryKey: [
      'ai',
      'logs',
      since ?? null,
      until ?? null,
      model ?? null,
      limit,
      offset,
    ],
    queryFn: async () => {
      const usp = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (since) usp.set('since', since);
      if (until) usp.set('until', until);
      if (model) usp.set('model', model);
      const raw = await fetcher<{ items: unknown[]; total: number; limit: number; offset: number }>(
        `/api/v1/ai/logs?${usp.toString()}`,
        { accessToken, refreshToken },
      );
      return {
        ...raw,
        items: raw.items.map((i) => AiLogReadSchema.parse(i)),
      } as Paginated<AiLogRead>;
    },
    enabled: status === 'authenticated',
  });
}

export function useInvalidateAiLogs() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ['ai', 'logs'] });
}
