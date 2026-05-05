/**
 * Local fallback store for visit check-in / check-out (Wave 2-C).
 *
 * The backend `/api/v1/visits/{id}/checkin` route is not implemented yet
 * (Phase 5 backlog). Until it ships we persist a small record per
 * (staff, visit) pair in `localStorage` so a tap survives navigation. Once
 * the server lands, callers clear the entry on success and the visit's
 * server-side `status` becomes the source of truth.
 *
 * Privacy / Security (W2-C-codex final review):
 *   - Keys are namespaced by staff id (`checkin:{staffId}:{visitId}`) so a
 *     user-switch on a shared device cannot read the previous account's
 *     records.
 *   - Lat/lng are rounded to 4 decimal places (~11 m) before persisting to
 *     reduce PII exposure of an offline-only fallback.
 *   - Records auto-expire after `TTL_MS` (24h) on read; stale entries are
 *     swept and removed.
 *   - `clearAllCheckins()` purges every `checkin:*` key — used at sign-out
 *     and on auth events to avoid leaving PHI-adjacent data behind.
 */

const PREFIX = 'checkin:';
const TTL_MS = 24 * 60 * 60 * 1000;

export interface CheckinPayload {
  status: 'checked_in' | 'checked_out';
  /** ISO 8601 timestamp captured client-side. */
  at: string;
  /** Latitude rounded to 4 decimal places (≈11 m). */
  lat?: number;
  /** Longitude rounded to 4 decimal places (≈11 m). */
  lng?: number;
  /** Optional photo URI (data: URL or remote ref) — currently unused. */
  photo_uri?: string;
}

interface StoredEntry extends CheckinPayload {
  /** Epoch ms — used for TTL eviction on read. */
  saved_at: number;
}

function userKey(staffId: string, visitId: string): string {
  return `${PREFIX}${staffId}:${visitId}`;
}

/** Round to 4 decimal places. Returns `undefined` for non-finite inputs. */
function roundCoord(v: number | undefined): number | undefined {
  if (typeof v !== 'number' || !Number.isFinite(v)) return undefined;
  return Math.round(v * 10000) / 10000;
}

function isStoredEntry(value: unknown): value is StoredEntry {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  if (v.status !== 'checked_in' && v.status !== 'checked_out') return false;
  if (typeof v.at !== 'string') return false;
  if (typeof v.saved_at !== 'number') return false;
  return true;
}

export function saveCheckin(
  staffId: string,
  visitId: string,
  payload: CheckinPayload,
): void {
  if (typeof window === 'undefined') return;
  if (!staffId || !visitId) return;
  const entry: StoredEntry = {
    status: payload.status,
    at: payload.at,
    saved_at: Date.now(),
    ...(payload.lat !== undefined ? { lat: roundCoord(payload.lat) } : {}),
    ...(payload.lng !== undefined ? { lng: roundCoord(payload.lng) } : {}),
    ...(payload.photo_uri !== undefined ? { photo_uri: payload.photo_uri } : {}),
  };
  try {
    window.localStorage.setItem(userKey(staffId, visitId), JSON.stringify(entry));
  } catch {
    /* quota / private mode — ignore */
  }
}

export function loadCheckin(
  staffId: string,
  visitId: string,
): CheckinPayload | null {
  if (typeof window === 'undefined') return null;
  if (!staffId || !visitId) return null;
  const key = userKey(staffId, visitId);
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isStoredEntry(parsed)) {
      window.localStorage.removeItem(key);
      return null;
    }
    if (Date.now() - parsed.saved_at > TTL_MS) {
      window.localStorage.removeItem(key);
      return null;
    }
    const result: CheckinPayload = {
      status: parsed.status,
      at: parsed.at,
      ...(parsed.lat !== undefined ? { lat: parsed.lat } : {}),
      ...(parsed.lng !== undefined ? { lng: parsed.lng } : {}),
      ...(parsed.photo_uri !== undefined ? { photo_uri: parsed.photo_uri } : {}),
    };
    return result;
  } catch {
    return null;
  }
}

export function clearCheckin(staffId: string, visitId: string): void {
  if (typeof window === 'undefined') return;
  if (!staffId || !visitId) return;
  try {
    window.localStorage.removeItem(userKey(staffId, visitId));
  } catch {
    /* ignore */
  }
}

/**
 * Purge every `checkin:*` key from localStorage. Called at sign-out and on
 * auth-refresh failure so PHI-adjacent records do not outlive the session.
 */
export function clearAllCheckins(): void {
  if (typeof window === 'undefined') return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(PREFIX)) keys.push(k);
    }
    for (const k of keys) {
      window.localStorage.removeItem(k);
    }
  } catch {
    /* ignore */
  }
}
