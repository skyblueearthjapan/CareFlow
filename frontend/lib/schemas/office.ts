/**
 * Office (拠点) zod schemas — v2 re-export shim (W1-FE3).
 *
 * 設計仕様書 v0.9 §4.3 に基づき、拠点マスタは Wave 0-C で `lib/schemas/v2/office.ts`
 * に再定義済み。本ファイルは Wave 1 の移行期間中、既存 import パスを壊さないための
 * 互換 re-export 層として残置する。
 *
 * 旧 schema が公開していた以下のシンボルを v2 schema にマップする:
 *   - `OfficeReadSchema`     → `officeV2ReadSchema`
 *   - `OfficeCreateSchema`   → `officeV2CreateSchema`
 *   - `OfficeUpdateSchema`   → `officeV2UpdateSchema`
 *   - `Office`, `OfficeCreate`, `OfficeUpdate` 型も同様
 *
 * Wave 1 が完了したら本ファイルを削除し、各 import を `@/lib/schemas/v2/office`
 * に直接張り直す予定（実装手順書 v0.2 §0-C）。
 */
export {
  officeV2ReadSchema as OfficeReadSchema,
  officeV2CreateSchema as OfficeCreateSchema,
  officeV2UpdateSchema as OfficeUpdateSchema,
  // W12-FE: resolve endpoint schemas
  RESOLVE_CONFIDENCE,
  officeResolveResponseSchema,
  officeResolveRequestSchema,
} from '@/lib/schemas/v2/office';
export type {
  OfficeV2Read as Office,
  OfficeV2Create as OfficeCreate,
  OfficeV2Update as OfficeUpdate,
  // W12-FE: resolve endpoint types
  ResolveConfidence,
  OfficeResolveResponse,
} from '@/lib/schemas/v2/office';
