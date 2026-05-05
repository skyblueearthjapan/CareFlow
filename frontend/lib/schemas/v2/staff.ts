/**
 * StaffV2 zod schemas (Wave 0-C).
 *
 * 設計仕様書 v0.9 §4.2 に基づき、v2 で残す **9 項目** のみを保持する。
 * 削除する 6 項目 (can_double_team / 自宅住所 + lat/lng / 得意エリア /
 * 1日最大訪問数 / スキル / 割付ボリューム) は v2 schema には含めない。
 *
 * メンターフィールド (`mentor_id`) は型としては **残す**(UI 上は基本情報ではなく
 * 「詳細セクション」扱いだが、データ層には残置)。
 *
 * Backend `backend/app/schemas/v2/staff.py` の Pydantic schema と
 * **完全一致** させる。
 */
import { z } from 'zod';

// ─────────────────────────────────────────────────────────────────────────
// Constants & primitive enums
// ─────────────────────────────────────────────────────────────────────────

/**
 * 役割 (§4.2):
 *   admin = 全権 / manager (= M1) = 管理者枠 (割当対象外) / staff = 通常スタッフ
 */
export const ROLE_V2_VALUES = ['admin', 'manager', 'staff'] as const;
export const roleV2Enum = z.enum(ROLE_V2_VALUES);
export type RoleV2 = z.infer<typeof roleV2Enum>;

/**
 * 状態 (§4.2 確定: 在籍 / 休職 / 退職 の 3 値):
 *   active = 在籍 / on_leave = 休職 / retired = 退職
 *   保存値は英語、UI 表示は ja-JP に変換する。
 */
export const STAFF_STATUS_V2_VALUES = ['active', 'on_leave', 'retired'] as const;
export const staffStatusV2Enum = z.enum(STAFF_STATUS_V2_VALUES);
export type StaffStatusV2 = z.infer<typeof staffStatusV2Enum>;

/** 性別 (§4.2). */
export const STAFF_SEX_V2_VALUES = ['male', 'female', 'unknown'] as const;
export const staffSexV2Enum = z.enum(STAFF_SEX_V2_VALUES);
export type StaffSexV2 = z.infer<typeof staffSexV2Enum>;

// ─────────────────────────────────────────────────────────────────────────
// StaffV2 base / Create / Update / Read
// ─────────────────────────────────────────────────────────────────────────

/**
 * スタッフマスタ v2 (§4.2 残す 9 項目).
 *
 * 削除済み (v2 schema に含めない): can_double_team, home_address,
 * home_lat, home_lng, areas, max_per_day, skill_level, assignment_volume。
 */
export const staffV2BaseSchema = z.object({
  // 基本情報
  code: z.string().max(64).nullable().optional(),
  name: z.string().min(1, '氏名は必須です').max(120),
  kana: z.string().max(120).nullable().optional(),
  sex: staffSexV2Enum.nullable().optional(),
  /** manager (= M1) は割当対象外 (§3.6.5) */
  role: roleV2Enum.default('staff'),
  /** 在籍 (active) 以外 (on_leave / retired) は割当除外 */
  status: staffStatusV2Enum.default('active'),
  /**
   * 主拠点 (§4.3). プルダウン選択 (稲毛 / 都賀)。
   * 距離計算の起点はスタッフが選択した主拠点の住所。
   */
  primary_office_id: z.string().uuid().nullable().optional(),

  note: z.string().nullable().optional(),

  // 詳細セクション (§4.2)
  /**
   * メンター。新人スタッフのみ設定。
   * UI 上は基本情報ではなく「詳細」セクション扱いだが、データ層には残置 (§4.2)。
   */
  mentor_id: z.string().uuid().nullable().optional(),
});
export type StaffV2Base = z.infer<typeof staffV2BaseSchema>;

/** POST /api/v1/staff リクエスト (W1-BE2). */
export const staffV2CreateSchema = staffV2BaseSchema;
export type StaffV2Create = z.infer<typeof staffV2CreateSchema>;

/** PATCH /api/v1/staff/{id} リクエスト (W1-BE2). 全フィールド optional. */
export const staffV2UpdateSchema = z.object({
  code: z.string().max(64).nullable().optional(),
  name: z.string().min(1).max(120).optional(),
  kana: z.string().max(120).nullable().optional(),
  sex: staffSexV2Enum.nullable().optional(),
  role: roleV2Enum.optional(),
  status: staffStatusV2Enum.optional(),
  primary_office_id: z.string().uuid().nullable().optional(),
  note: z.string().nullable().optional(),
  mentor_id: z.string().uuid().nullable().optional(),
});
export type StaffV2Update = z.infer<typeof staffV2UpdateSchema>;

/** GET /api/v1/staff/{id} レスポンス (W1-BE2). */
export const staffV2ReadSchema = staffV2BaseSchema.extend({
  id: z.string().uuid(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted_at: z.string().nullable().optional(),
});
export type StaffV2Read = z.infer<typeof staffV2ReadSchema>;
