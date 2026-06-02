/**
 * API wrappers for patient master Excel export / import.
 *
 * Export/template endpoints return xlsx blobs; import returns a typed JSON
 * preview/apply response validated with Zod.
 */
import { ApiError } from '@/lib/api-client';
import {
  patientExcelImportResponseSchema,
  patientExcelReplaceAllResponseSchema,
  type PatientExcelImportResponse,
  type PatientExcelReplaceAllResponse,
} from '@/lib/schemas/patientExcel';

function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') {
    return '';
  }
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

interface AuthOpts {
  accessToken: string;
  refreshToken: string | null;
}

/** Fetch a blob (xlsx) from an authenticated endpoint. */
async function fetchBlob(path: string, opts: AuthOpts): Promise<Blob> {
  const url = `${resolveBaseUrl()}${path}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${opts.accessToken}`,
  };
  const res = await fetch(url, { method: 'GET', headers, cache: 'no-store' });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(`API ${res.status} ${res.statusText} (${path})`, res.status, text);
  }
  return res.blob();
}

/** Parse the download filename out of a Content-Disposition header.
 *
 * Prefers the RFC 5987 ``filename*=UTF-8''...`` form (日本語ファイル名対応) and
 * falls back to the plain ASCII ``filename="..."`` form. Returns null when the
 * header is missing or unparseable so the caller can supply its own default.
 */
function parseContentDispositionFilename(header: string | null): string | null {
  if (!header) return null;
  // RFC 5987: filename*=UTF-8''<percent-encoded>
  const star = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      // fall through to ASCII fallback
    }
  }
  // ASCII: filename="..."
  const ascii = header.match(/filename="?([^";]+)"?/i);
  if (ascii?.[1]) return ascii[1].trim();
  return null;
}

/** Fetch a blob (xlsx) plus its server-supplied download filename. */
async function fetchBlobWithFilename(
  path: string,
  opts: AuthOpts,
): Promise<{ blob: Blob; filename: string | null }> {
  const url = `${resolveBaseUrl()}${path}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${opts.accessToken}`,
  };
  const res = await fetch(url, { method: 'GET', headers, cache: 'no-store' });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(`API ${res.status} ${res.statusText} (${path})`, res.status, text);
  }
  const filename = parseContentDispositionFilename(res.headers.get('Content-Disposition'));
  const blob = await res.blob();
  return { blob, filename };
}

/**
 * Download the current patient master as an xlsx blob.
 * GET /api/v1/patients/import-export/export
 */
export async function downloadPatientsExcel(opts: AuthOpts): Promise<Blob> {
  return fetchBlob('/api/v1/patients/import-export/export', opts);
}

/**
 * Download an empty xlsx template (column headers only).
 * GET /api/v1/patients/import-export/template
 */
export async function downloadPatientsExcelTemplate(opts: AuthOpts): Promise<Blob> {
  return fetchBlob('/api/v1/patients/import-export/template', opts);
}

/**
 * Upload an xlsx file for dry-run preview or live commit.
 * POST /api/v1/patients/import-export/import?dry_run=...
 *
 * NOTE: ``dry_run`` is a *query parameter* on the backend (FastAPI ``Form()``
 * 注釈なし), so it MUST be sent in the URL, not as a multipart field.
 * Putting it in the FormData makes the backend silently fall back to its
 * default of ``True`` and the import will never commit.
 *
 * 手動回帰確認手順:
 *   1. 患者マスタ画面で Excel インポート → プレビュー → 反映ボタン
 *   2. 反映後にトーストが「患者マスタを更新しました」になることを確認
 *   3. DB / 一覧画面で変更が反映されていること (transaction_applied=true)
 *
 * @param dryRun  true → preview only (default); false → commit
 */
export async function importPatientsExcel(
  opts: AuthOpts & { file: File; dryRun: boolean },
): Promise<PatientExcelImportResponse> {
  const url =
    `${resolveBaseUrl()}/api/v1/patients/import-export/import` +
    `?dry_run=${opts.dryRun ? 'true' : 'false'}`;
  const formData = new FormData();
  formData.append('file', opts.file);

  const res = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${opts.accessToken}` },
    body: formData,
    cache: 'no-store',
  });

  const text = await res.text();
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    raw = text;
  }

  if (!res.ok) {
    throw new ApiError(
      `API ${res.status} ${res.statusText} (/api/v1/patients/import-export/import)`,
      res.status,
      raw,
    );
  }

  return patientExcelImportResponseSchema.parse(raw);
}

/**
 * Upload an xlsx file for the *complete replacement* (backup restore) flow.
 * POST /api/v1/patients/import-export/replace-all?dry_run=...
 *
 * admin のみ. 通常 import との違い:
 *   - Excel に無い既存患者は soft delete される
 *   - 空セルは NULL で上書きされる
 *   - 既存 PFV は全件物理削除 → Excel から再投入
 *   - atomic: error 1 件で全 rollback (422 を返す)
 */
export async function replaceAllPatientsExcel(
  opts: AuthOpts & { file: File; dryRun: boolean },
): Promise<PatientExcelReplaceAllResponse> {
  const url =
    `${resolveBaseUrl()}/api/v1/patients/import-export/replace-all` +
    `?dry_run=${opts.dryRun ? 'true' : 'false'}`;
  const formData = new FormData();
  formData.append('file', opts.file);

  const res = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${opts.accessToken}` },
    body: formData,
    cache: 'no-store',
  });

  const text = await res.text();
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    raw = text;
  }

  if (!res.ok) {
    throw new ApiError(
      `API ${res.status} ${res.statusText} (/api/v1/patients/import-export/replace-all)`,
      res.status,
      raw,
    );
  }

  return patientExcelReplaceAllResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 患者個別カルテ (A4・1 患者 1 ファイル)
//   * GET  /karte-template            — 空白カルテ (新規ヒアリング記入用)
//   * GET  /karte-export?patient_id=  — 1 患者のカルテ
//   * POST /karte-import?dry_run=     — 単一カルテ取込 (プレビュー / 反映)
// 取込レスポンスは一括 import と同一スキーマなので ImportPreviewModal を流用できる.
// ---------------------------------------------------------------------------

/**
 * Download an empty patient karte (A4 ヒアリングシート) as an xlsx blob.
 * GET /api/v1/patients/import-export/karte-template
 */
export async function downloadKarteTemplate(opts: AuthOpts): Promise<Blob> {
  return fetchBlob('/api/v1/patients/import-export/karte-template', opts);
}

/**
 * Download a single patient's karte as an xlsx blob.
 * GET /api/v1/patients/import-export/karte-export?patient_id=<uuid>
 *
 * Returns both the blob and the server's Content-Disposition filename
 * (患者コード_氏名.xlsx) so the caller can preserve it on download.
 */
export async function downloadPatientKarte(
  opts: AuthOpts & { patientId: string },
): Promise<{ blob: Blob; filename: string | null }> {
  const path =
    `/api/v1/patients/import-export/karte-export` +
    `?patient_id=${encodeURIComponent(opts.patientId)}`;
  return fetchBlobWithFilename(path, opts);
}

/**
 * Upload a single patient karte for dry-run preview or live commit.
 * POST /api/v1/patients/import-export/karte-import?dry_run=...
 *
 * ``dry_run`` は backend の query parameter なので URL に乗せる
 * (importPatientsExcel と同じ理由 — multipart field に入れると常に default の
 * True に倒れて反映されない). 応答は一括 import と同一スキーマ.
 *
 * @param dryRun  true → preview only; false → commit
 */
export async function importPatientKarte(
  opts: AuthOpts & { file: File; dryRun: boolean },
): Promise<PatientExcelImportResponse> {
  const url =
    `${resolveBaseUrl()}/api/v1/patients/import-export/karte-import` +
    `?dry_run=${opts.dryRun ? 'true' : 'false'}`;
  const formData = new FormData();
  formData.append('file', opts.file);

  const res = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${opts.accessToken}` },
    body: formData,
    cache: 'no-store',
  });

  const text = await res.text();
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    raw = text;
  }

  if (!res.ok) {
    throw new ApiError(
      `API ${res.status} ${res.statusText} (/api/v1/patients/import-export/karte-import)`,
      res.status,
      raw,
    );
  }

  return patientExcelImportResponseSchema.parse(raw);
}

/** Trigger a browser file download from a Blob. */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
