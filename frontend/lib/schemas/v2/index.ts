/**
 * v2 共有 zod schemas (Wave 0-C) — re-export entry point.
 *
 * 設計仕様書 `docs/plans/v2-allocation-redesign.md` v0.9 に対応する型定義。
 * Backend `backend/app/schemas/v2/` の Pydantic schema と **完全一致** させる。
 *
 * 実装手順書 §1 0-C で要求される 11 種の必須型:
 *   PatientV2 (Base/Create/Update/Read)         - 設計書 §4.1
 *   WeeklyPatternV2                              - 設計書 §3.3 / §4.1
 *   StaffV2 (Base/Create/Update/Read)            - 設計書 §4.2
 *   OfficeV2 (Base/Create/Update/Read)           - 設計書 §4.3
 *   CourseV2 (Base/Create/Update/Read)           - 設計書 §4.5
 *   VisitV2 (Base/Create/Update/Read)            - 設計書 §3.3 / §4.5
 *   PendingRequestV2 (Base/Create/Update/Read)   - 設計書 §3.5 / §4.4
 *   CourseStatus enum                             - 設計書 §4.5
 *   RequestType enum                              - 設計書 §4.4
 *   RequestStatus enum                            - 設計書 §3.5.4
 *   AiContextType enum                            - 設計書 §3.5.2
 *
 * 既存 schema との関係 (実装手順書 §1 0-C):
 *   既存 `frontend/lib/schemas/{patient,staff,office}.ts` は Wave 1 完了時に
 *   v2 schema へ統合する (本 index は移行用 re-export のみ提供)。
 */

export * from './enums';
export * from './patient';
export * from './staff';
export * from './office';
export * from './course';
export * from './visit';
export * from './pending_request';
