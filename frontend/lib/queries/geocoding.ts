/**
 * Geocoding query hooks — CareFlow Wave 4-C.
 *
 * Wraps `POST /api/v1/geocode` so address fields can auto-fill lat/lng
 * via the cache-first relay. The `useGeocodeOnAddressChange` helper
 * implements debounced fetching and exposes the loading/error state
 * needed by <AddressGeocodeField />.
 */
'use client';

import { useEffect, useRef, useState } from 'react';

import { useMutation } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  GeocodeResponseSchema,
  type GeocodeRequest,
  type GeocodeResponse,
} from '@/lib/schemas/geocoding';

/** Mutation hook — POST /api/v1/geocode (manual / forced refresh path). */
export function useGeocode() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<GeocodeResponse, Error, GeocodeRequest>({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>('/api/v1/geocode', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return GeocodeResponseSchema.parse(raw);
    },
  });
}

export interface UseGeocodeOnAddressChangeState {
  data: GeocodeResponse | null;
  error: Error | null;
  isLoading: boolean;
  /** Trigger a force-refresh fetch for the current address. */
  refresh: () => void;
}

/**
 * Debounced auto-geocoder. Fires `POST /api/v1/geocode` after the user
 * stops typing for `debounceMs` (default 800ms). Keeps the latest result
 * in component state so the parent form can apply lat/lng on change.
 *
 * The hook intentionally does NOT mutate any form fields itself — that
 * orchestration belongs in the consuming component
 * (<AddressGeocodeField /> takes care of focus detection + value writes).
 */
export function useGeocodeOnAddressChange(
  address: string,
  debounceMs = 800,
  options?: { enabled?: boolean },
): UseGeocodeOnAddressChangeState {
  const enabled = options?.enabled ?? true;
  const mutation = useGeocode();

  const [data, setData] = useState<GeocodeResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const lastAddressRef = useRef<string>('');

  useEffect(() => {
    if (!enabled) return;
    const trimmed = address.trim();
    // Heuristic: skip very short inputs (avoids hammering on every keystroke).
    if (trimmed.length < 5) {
      setData(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    if (trimmed === lastAddressRef.current) {
      return;
    }

    setIsLoading(true);
    setError(null);
    const timer = setTimeout(() => {
      lastAddressRef.current = trimmed;
      mutation.mutate(
        { address: trimmed },
        {
          onSuccess: (res) => {
            setData(res);
            setIsLoading(false);
          },
          onError: (err) => {
            setError(err);
            setIsLoading(false);
          },
        },
      );
    }, debounceMs);

    return () => clearTimeout(timer);
    // mutation reference is stable for the hook's lifetime; we deliberately
    // exclude it from deps to avoid re-firing on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [address, debounceMs, enabled]);

  const refresh = () => {
    const trimmed = address.trim();
    if (trimmed.length < 5) return;
    setIsLoading(true);
    setError(null);
    mutation.mutate(
      { address: trimmed, force_refresh: true },
      {
        onSuccess: (res) => {
          setData(res);
          setIsLoading(false);
          lastAddressRef.current = trimmed;
        },
        onError: (err) => {
          setError(err);
          setIsLoading(false);
        },
      },
    );
  };

  return { data, error, isLoading, refresh };
}
