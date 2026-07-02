/**
 * PatientFixedVisitV2 zod schemas (W9-FE1 / W22 / W37 Phase 3-A).
 *
 * 設計仕様書 `docs/plans/v2-allocation-redesign.md` §3.6.8 (固定枠ライフサイクル)
 * および `docs/plans/v2-api-contracts.md` §8 / §9 に基づく型定義。
 *
 * Backend `backend/app/schemas/v2/patient_fixed_visit.py` と完全一致させる。
 *
 * W22: course_template_id (UUID | null) を追加。
 *   BE Wave 22 (feat/v2-w22-pfv-course) で migration 0025 として追加された列。
 *   並列実装のため型契約を先行ミラー。BE 完成後に再同期する。
 *
 * W37 Phase 1 / Phase 3-A:
 *   `slot_index` (0 or 1) を追加。複数スタッフ対応患者は同曜日・同時刻に
 *   2 コース固定するため slot_index 0/1 の 2 行を持つ。1 名体制では default 0.
 *   UNIQUE 制約は (patient_id, mode, weekday, slot_index)。FE bulk PUT も
 *   (weekday, slot_index) ペアで重複検出する。
 */
import { z } from 'zod';

export const PATIENT_FIXED_VISIT_MODES = ['normal', 'special'] as const;
export type PatientFixedVisitMode = (typeof PATIENT_FIXED_VISIT_MODES)[number];

/**
 * W37 Phase 1: スロット番号 (0 or 1).
 * 1 名体制 (既存) では 0 のみ。
 * 複数スタッフ対応では同 weekday に 0/1 の 2 行を持つ。
 */
export const SLOT_INDEX_VALUES = [0, 1] as const;
export type SlotIndex = (typeof SLOT_INDEX_VALUES)[number];

export const patientFixedVisitV2BaseSchema = z.object({
  weekday: z.number().int().min(0).max(6),
  start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/, 'HH:MM 形式'),
  duration_min: z.number().int().min(1).max(480).default(30),
  /** W22: コーステンプレート ID (UUID | null). null = 未指定 (Layer 1 フォールバック). */
  course_template_id: z.string().uuid().nullable().optional(),
  /**
   * W37 Phase 1: 同 weekday × 同 mode 内のスロット番号 (0 or 1).
   * 1 名体制では 0 のみ送信し、BE 側 default も 0。
   * 複数スタッフ対応 (requires_multiple_staff=true) では 0/1 の 2 行を送る。
   * 後方互換: 省略時は BE 側で default=0 が付く。
   */
  slot_index: z
    .number()
    .int()
    .min(0)
    .max(1)
    .default(0)
    .describe(
      '同 patient × 同 mode × 同 weekday の中のスロット番号 (0 or 1). 1 名体制では 0 のみ.',
    ),
  /**
   * Phase E-5 (項目 ⑥B): サブ拠点 ID.
   * 主担当拠点 (patient.primary_office_id) とは別に、フォロー時の配置先を指定する.
   * null/undefined = 未指定 (従来挙動). 指定時は course_template_id も sub_office の
   * template を指す必要がある (BE 側で 422 検証).
   */
  sub_office_id: z.string().uuid().nullable().optional(),
  /**
   * Phase G-21: 「完全固定」フラグ.
   * true のとき、自動最適化 (Layer 2) はこの PFV から生成された visit を移動・削除しない.
   * Read 用途では BE が値を返す. Write 用途 (bulk PUT) では省略可 (BE は既存値維持 or false default).
   */
  is_pinned: z.boolean().optional(),
});

export const patientFixedVisitV2ReadSchema = patientFixedVisitV2BaseSchema.extend({
  id: z.string().uuid(),
  patient_id: z.string().uuid(),
  mode: z.enum(PATIENT_FIXED_VISIT_MODES),
  created_at: z.string(),
  updated_at: z.string(),
});

/**
 * W37 Phase 1: bulk PUT は (weekday, slot_index) のペアで重複を検出する。
 * 1 名体制では従来どおり weekday ごとに 1 件のみだが、
 * 複数スタッフ対応では同じ weekday で slot_index 0/1 の 2 件を許容する。
 */
export const patientFixedVisitsBulkPutSchema = z.object({
  mode: z.enum(PATIENT_FIXED_VISIT_MODES),
  items: z
    .array(patientFixedVisitV2BaseSchema)
    // 7 曜日 × slot 0/1 = 最大 14 件
    .max(14)
    .superRefine((items, ctx) => {
      const seen = new Map<string, number>();
      items.forEach((it, i) => {
        // slot_index が省略された場合は default 0 として扱う
        const slot = it.slot_index ?? 0;
        const key = `${it.weekday}-${slot}`;
        if (seen.has(key)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: [i, 'weekday'],
            message: '同じ曜日・スロットが重複しています',
          });
        }
        seen.set(key, i);
      });
    }),
});

export type PatientFixedVisitV2Base = z.infer<typeof patientFixedVisitV2BaseSchema>;
export type PatientFixedVisitV2Read = z.infer<typeof patientFixedVisitV2ReadSchema>;
export type PatientFixedVisitsBulkPut = z.infer<typeof patientFixedVisitsBulkPutSchema>;

/**
 * P0-2 Commit 1 (BE `731884d`): PUT /patients/{id}/fixed-visits のレスポンスが
 * `{items, warnings}` エンベロープに変更された。warnings 要素は再検証カーネル
 * (`pfv_validator.py`) の `PfvValidationWarningOut` = {code, message, weekday, severity}。
 *
 * 寛容パース方針: code / severity は未知値を壊さないよう `z.string()` (enum 化しない)。
 * weekday は nullable (曜日非依存の警告は null)。将来 BE が新フィールドを足しても
 * FE 採用フローを止めないよう `.passthrough()` で余剰キーを許容する。
 */
export const patientFixedVisitWarningSchema = z
  .object({
    // 未知 code を壊さない寛容パース (enum 化しない).
    code: z.string(),
    message: z.string(),
    // 曜日非依存の警告は null / 省略あり.
    weekday: z.number().int().min(0).max(6).nullable().optional(),
    // 'warning' | 'error' 等. 未知 severity も string で受ける.
    severity: z.string(),
  })
  .passthrough();
export type PatientFixedVisitWarning = z.infer<typeof patientFixedVisitWarningSchema>;

/**
 * PUT /patients/{id}/fixed-visits のエンベロープレスポンス。
 * - items: 既存 Read スキーマで検証。要素 drift 時は `.catch([])` で items のみ空に落とし、
 *   warnings 表示 (本 Commit の主目的) を巻き込まない。
 * - warnings: 上記の寛容な warning スキーマ配列。
 *
 * 呼出側 (hook) は `.safeParse` でさらに包み、object でない等の致命ケースでも
 * `{items:[], warnings:[]}` にフォールバックして採用フローを止めない。
 */
export const patientFixedVisitsBulkPutResponseSchema = z.object({
  items: z.array(patientFixedVisitV2ReadSchema).catch([]),
  warnings: z.array(patientFixedVisitWarningSchema).catch([]),
});
export type PatientFixedVisitsBulkPutResponse = z.infer<
  typeof patientFixedVisitsBulkPutResponseSchema
>;

/**
 * PUT fixed-visits レスポンスの寛容パース。parse 失敗 (object でない / null 等) でも
 * throw せず `{items:[], warnings:[]}` を返し、採用・保存フローを止めない。
 */
export function parsePatientFixedVisitsBulkPutResponse(
  raw: unknown,
): PatientFixedVisitsBulkPutResponse {
  const parsed = patientFixedVisitsBulkPutResponseSchema.safeParse(raw);
  return parsed.success ? parsed.data : { items: [], warnings: [] };
}
