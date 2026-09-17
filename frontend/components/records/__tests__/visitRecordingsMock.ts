/**
 * `@/lib/queries/visit-recordings` の共有モックファクトリ（テスト専用ヘルパ）。
 *
 * 背景（2026-09-18・Phase 3 レビュー H-1）:
 *   訪問記録まわりの画面は `RecordDetailDialog` を内側に抱えるため、ダイアログが
 *   新しいフックを 1 本足すだけで、そのモジュールを**明示ファクトリで**モックしていた
 *   テストが一斉に「No "xxx" export is defined on the mock」で落ちていた
 *   （`useRecordReport` 追加時に 4 ファイル・20 件）。
 *
 * そこで **実モジュールを土台にする**（`importOriginal`）。純関数・zod スキーマ・
 * 定数は本物のまま使い、ネットワークを踏むフックだけを黙って成功する既定スタブに
 * 差し替える。以後 export が増えても本物が入るので、モックの取りこぼしは起きない。
 *
 *   vi.mock('@/lib/queries/visit-recordings', async (importOriginal) => {
 *     const { visitRecordingsMock } = await import(
 *       '@/components/records/__tests__/visitRecordingsMock'
 *     );
 *     return visitRecordingsMock(importOriginal, {
 *       useVisitRecordings: (...a: unknown[]) => mockUseVisitRecordings(...a),
 *     });
 *   });
 *
 * 第 2 引数の上書きが最優先なので、テストごとの差し込みはこれまでどおり書ける。
 * ※ `vi.mock` は巻き上げられるため、ファクトリの中で `await import(...)` すること
 *   （トップレベル import の束縛は参照できない）。
 */
import { vi } from 'vitest';

/** ネットワークを踏むフックの既定スタブ（黙って成功し、何も返さない）。 */
function hookStubs(): Record<string, unknown> {
  return {
    useVisitRecordings: () => ({
      data: { items: [], total: 0 },
      isLoading: false,
      isPending: false,
      isError: false,
      error: null,
    }),
    useVisitRecording: () => ({ data: null, isLoading: false, isPending: false, isError: false }),
    useUploadRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useUpdateRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRetryRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteRecording: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useRecordReport: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useVoiceUsage: () => ({ data: undefined, isPending: false, isError: false, error: null }),
  };
}

/**
 * 実モジュール → 既定スタブ → テスト固有の上書き、の順に重ねたモックを返す。
 *
 * @param importOriginal `vi.mock` のファクトリが受け取る実モジュールの読み込み関数
 * @param overrides      このテストだけの差し込み（最優先）
 */
export async function visitRecordingsMock(
  importOriginal: () => Promise<unknown>,
  overrides: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, ...hookStubs(), ...overrides };
}
