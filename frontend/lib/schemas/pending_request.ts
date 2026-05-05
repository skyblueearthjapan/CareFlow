/**
 * Pending request zod schemas — re-export of v2 definitions (Wave 3 W3-FE6).
 *
 * 設計仕様書 v0.9 §3.5 / §4.4 / API 契約 v0.1 §7 に対応する v2 schemas は
 * `frontend/lib/schemas/v2/pending_request.ts` に集約されている。
 *
 * 本ファイルは:
 *   1. 既存のフラットな `lib/schemas/{patient,staff,...}` 命名規約と整合性を
 *      取るための **re-export** ファイル (W3-FE6 所有)
 *   2. v2 移行期に `lib/schemas/v2/...` → `lib/schemas/...` の移行ポイントを
 *      集約する（実装手順書 §1 0-C 参照）
 *
 * 直接 v2 ディレクトリを import せず、本モジュール経由で参照すること。
 */

export {
  pendingRequestApproveSchema,
  pendingRequestRejectSchema,
  pendingRequestV2BaseSchema,
  pendingRequestV2CreateSchema,
  pendingRequestV2ReadSchema,
  pendingRequestV2UpdateSchema,
  type PendingRequestApprove,
  type PendingRequestReject,
  type PendingRequestV2Base,
  type PendingRequestV2Create,
  type PendingRequestV2Read,
  type PendingRequestV2Update,
} from './v2/pending_request';

export {
  REQUEST_SCOPE_VALUES,
  REQUEST_STATUS_VALUES,
  REQUEST_TYPE_VALUES,
  requestScopeEnum,
  requestStatusEnum,
  requestTypeEnum,
  type RequestScope,
  type RequestStatus,
  type RequestType,
} from './v2/enums';
