/**
 * 未送信チェックインの保留キュー (QR チェックイン Phase 2 / クロスレビュー指摘 #3).
 *
 * `checkin-storage.ts` は「画面遷移をまたいで直近の打刻状態を覚える」ための
 * 24h で自動失効する保険であって、サーバへ届かなかった記録の再送はしない。
 * 本キューはそれとは別物で、**真のネットワーク障害 / サーバ一時障害 (5xx) で
 * サーバに届かなかった打刻を保持し、電波復帰時にベストエフォートで再送する**。
 *
 * 設計上の割り切り:
 *   - **黙って消えない**: TTL での自動失効はしない (24h で記録が無言で消える事故を
 *     防ぐ)。送信成功時のみキューから取り除く。
 *   - **再送はベストエフォート**: POST のレスポンスが消失 (送れたがレスポンスを
 *     受け取れなかった) ケースでは二重記録が起こり得る。サーバ側 `visit_checkins`
 *     は append-only / 最新採用 (latest-wins) のため、二重記録は監査ログに 2 行
 *     残るだけで実害が無い前提で許容する (サーバ idempotency key は本 Phase 凍結)。
 *   - **4xx は破棄して通知**: 再送しても成功しない確定エラー (404=無効な QR /
 *     409=対象外の患者 等) は、`post` コールバックが {@link DropPendingError} を
 *     throw することでキューから取り除く。黙って捨てず、破棄した entry と理由を
 *     {@link flushPending} の戻り値 (`dropped`) で呼び出し元へ返し、トースト等で
 *     利用者へ通知できるようにする。ネット障害 / 5xx は従来どおり保持して再試行。
 *   - staff id で名前空間を分け、共有端末でのユーザ切替時に他人の記録を読まない。
 */

const PREFIX = 'checkin-pending:';

/**
 * 保留打刻の種別。
 *
 * `adhoc_arrival` は「予定外訪問の到着」(設計 §4-3 `POST /visits/adhoc-checkin`)。
 * 圏外で候補一覧を取れないまま担当外の患者宅で読み取った場合、visit がまだ
 * 存在しない = `visit_id` を持たない打刻になるため、他 3 種と違い
 * **`visit_id` は空文字**・**`payload.qr_token` 必須** (トークンが患者特定の唯一の鍵)。
 */
export type PendingKind = 'arrival' | 'departure' | 'no_show' | 'adhoc_arrival';

/** 再送時にそのまま POST body にする打刻ペイロード。 */
export interface PendingPayload {
  qr_token?: string;
  lat?: number;
  lng?: number;
  accuracy?: number;
  reason?: string;
  is_override?: boolean;
  /** 端末時刻 (ISO 8601)。サーバは `device_time` として読む。 */
  at: string;
}

export interface PendingEntry {
  /** ローカル一意 id (重複再送の取り除きに使う)。 */
  id: string;
  /** 対象 visit。`adhoc_arrival` は visit 未生成のため空文字。 */
  visit_id: string;
  kind: PendingKind;
  payload: PendingPayload;
  /** Epoch ms — キュー投入時刻 (表示・順序用。失効には使わない)。 */
  queued_at: number;
}

/**
 * `post` コールバックが「再送しても成功しない確定エラー (4xx)」を表すために
 * throw する番兵エラー。{@link flushPending} はこれを受けると entry をキューから
 * 取り除き、`reason` 付きで `dropped` に記録する。これ以外の reject (ネット障害 /
 * 5xx) は「まだ届かない」として保持し次回再試行する。
 */
export class DropPendingError extends Error {
  readonly reason: string;
  constructor(reason: string) {
    super(reason);
    this.name = 'DropPendingError';
    this.reason = reason;
  }
}

/** 4xx で破棄した保留 entry と、その理由。 */
export interface DroppedPending {
  entry: PendingEntry;
  reason: string;
}

/** {@link flushPending} の結果: 残件数 + 破棄した entry 一覧。 */
export interface FlushResult {
  /** 再送できず残った保留件数。 */
  remaining: number;
  /** 4xx で破棄した entry (理由付き)。呼び出し元が通知に使う。 */
  dropped: DroppedPending[];
}

function userKey(staffId: string): string {
  return `${PREFIX}${staffId}`;
}

function isPendingEntry(value: unknown): value is PendingEntry {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  if (typeof v.id !== 'string' || typeof v.visit_id !== 'string') return false;
  if (
    v.kind !== 'arrival' &&
    v.kind !== 'departure' &&
    v.kind !== 'no_show' &&
    v.kind !== 'adhoc_arrival'
  ) {
    return false;
  }
  if (!v.payload || typeof v.payload !== 'object') return false;
  const payload = v.payload as Record<string, unknown>;
  if (typeof payload.at !== 'string') return false;
  // 予定外の到着は qr_token が患者特定の唯一の鍵 — 欠けた entry は再送しても
  // 必ず失敗するので、読み込み時点で捨てる (キューに居座らせない)。
  if (v.kind === 'adhoc_arrival' && typeof payload.qr_token !== 'string') return false;
  return true;
}

function readAll(staffId: string): PendingEntry[] {
  if (typeof window === 'undefined' || !staffId) return [];
  try {
    const raw = window.localStorage.getItem(userKey(staffId));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isPendingEntry);
  } catch {
    return [];
  }
}

function writeAll(staffId: string, entries: PendingEntry[]): void {
  if (typeof window === 'undefined' || !staffId) return;
  try {
    if (entries.length === 0) {
      window.localStorage.removeItem(userKey(staffId));
    } else {
      window.localStorage.setItem(userKey(staffId), JSON.stringify(entries));
    }
  } catch {
    /* quota / private mode — ignore */
  }
}

function genId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch {
    /* fall through */
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** 未送信の打刻をキューに積む。生成した entry を返す。 */
export function enqueuePending(
  staffId: string,
  entry: { visit_id: string; kind: PendingKind; payload: PendingPayload },
): PendingEntry | null {
  if (typeof window === 'undefined' || !staffId) return null;
  const full: PendingEntry = {
    id: genId(),
    visit_id: entry.visit_id,
    kind: entry.kind,
    payload: entry.payload,
    queued_at: Date.now(),
  };
  const all = readAll(staffId);
  all.push(full);
  writeAll(staffId, all);
  return full;
}

export function listPending(staffId: string): PendingEntry[] {
  return readAll(staffId);
}

export function countPending(staffId: string): number {
  return readAll(staffId).length;
}

export function removePending(staffId: string, id: string): void {
  const all = readAll(staffId);
  writeAll(
    staffId,
    all.filter((e) => e.id !== id),
  );
}

/**
 * 保留分をベストエフォートで再送する。
 *   - `post` が解決 (成功) → 送信できたとみなしキューから取り除く。
 *   - `post` が {@link DropPendingError} を throw → 4xx 確定エラー。取り除いて
 *     `dropped` に理由付きで記録する (黙って捨てない)。
 *   - `post` がそれ以外で reject (ネット障害 / 5xx) → まだ届かないので残す。
 * 残件数と破棄一覧を返す。
 */
export async function flushPending(
  staffId: string,
  post: (entry: PendingEntry) => Promise<unknown>,
): Promise<FlushResult> {
  if (typeof window === 'undefined' || !staffId) return { remaining: 0, dropped: [] };
  const all = readAll(staffId);
  const dropped: DroppedPending[] = [];
  for (const entry of all) {
    try {
      await post(entry);
      removePending(staffId, entry.id);
    } catch (err) {
      if (err instanceof DropPendingError) {
        // 再送不可の確定エラー — 取り除き、理由付きで呼び出し元へ返す。
        removePending(staffId, entry.id);
        dropped.push({ entry, reason: err.reason });
      }
      // それ以外 (ネット障害 / 5xx): まだ届かない — 残して次回 (online / mount) に再試行。
    }
  }
  return { remaining: countPending(staffId), dropped };
}
