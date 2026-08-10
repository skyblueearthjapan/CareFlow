/**
 * NG スタッフ (患者 × スタッフ割当禁止) zod schemas.
 *
 * 設計書 `docs/plans/patient-ng-staff-design.md` §4 の API 契約に 1:1 対応。
 * Backend `backend/app/api/v1/patients` の ng-staff エンドポイントと一致させる。
 *
 * 概念 (§2):
 *   - 患者ごとに「このスタッフは割り当てない」恒久ルールを持つ (複数名可・理由メモ任意)。
 *   - 強度は性別制限 (`Patient.sex_restriction`) と同格。エンジンはハード制約、
 *     人手操作はブロックせず確認ダイアログで通す (§7)。
 */
import { z } from 'zod';

// ---------------------------------------------------------------------------
// GET /api/v1/patients/{patient_id}/ng-staff — response item (§4-1)
// ---------------------------------------------------------------------------

export const patientNgStaffReadSchema = z.object({
  staff_id: z.string().uuid(),
  /** BE は解決済みの氏名を載せる。欠落・null は「(氏名未登録)」表示に落とす。 */
  staff_name: z.string().nullable().optional(),
  /** 退職 (soft delete) 済みスタッフでも行は残る → 表示側で「(退職)」注記。 */
  staff_is_deleted: z.boolean().default(false),
  note: z.string().nullable().optional(),
  decided_by_user_id: z.string().uuid().nullable().optional(),
  created_at: z.string().nullable().optional(),
});
export type PatientNgStaffRead = z.infer<typeof patientNgStaffReadSchema>;

export const patientNgStaffListSchema = z.array(patientNgStaffReadSchema);

/**
 * 閲覧系 (§8-2) の 1 行表示用ラベル。0 件は「なし」。
 * 退職済みスタッフは「(退職)」を添える。
 */
export function formatNgStaffNames(rows: readonly PatientNgStaffRead[]): string {
  if (rows.length === 0) return 'なし';
  return rows
    .map((r) => `${r.staff_name ?? '(氏名未登録)'}${r.staff_is_deleted ? '(退職)' : ''}`)
    .join('・');
}

// ---------------------------------------------------------------------------
// PUT /api/v1/patients/{patient_id}/ng-staff/{staff_id} — request (§4-1)
// ---------------------------------------------------------------------------

export const patientNgStaffUpsertSchema = z.object({
  note: z.string().nullable(),
});
export type PatientNgStaffUpsert = z.infer<typeof patientNgStaffUpsertSchema>;

// ---------------------------------------------------------------------------
// §7-2: 手動経路 (PATCH /courses/{id}) の 422 acknowledge プロトコル
//   { detail: { code: 'constraint_confirmation_required', warnings: [...] } }
// ---------------------------------------------------------------------------

export const CONSTRAINT_WARNING_KINDS = ['gender', 'ng_staff'] as const;
export type ConstraintWarningKind = (typeof CONSTRAINT_WARNING_KINDS)[number];

export const constraintWarningSchema = z.object({
  kind: z.enum(CONSTRAINT_WARNING_KINDS),
  patient_id: z.string(),
  patient_name: z.string().nullable().optional(),
  staff_id: z.string(),
  staff_name: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type ConstraintWarning = z.infer<typeof constraintWarningSchema>;

export const CONSTRAINT_CONFIRMATION_CODE = 'constraint_confirmation_required';

export const constraintConfirmationDetailSchema = z.object({
  code: z.literal(CONSTRAINT_CONFIRMATION_CODE),
  warnings: z.array(constraintWarningSchema),
});
export type ConstraintConfirmationDetail = z.infer<typeof constraintConfirmationDetailSchema>;

/**
 * ApiError.body (422) から確認要求の詳細を安全に取り出す。BE は
 * `{ detail: { code: 'constraint_confirmation_required', warnings: [...] } }` で返す。
 * 形が違えば null (= 通常のエラー表示にフォールバック)。
 *
 * 手本: `parseOverlapDetail` (`lib/schemas/trainee_accompaniment.ts`)。
 */
export function parseConstraintConfirmationDetail(
  body: unknown,
): ConstraintConfirmationDetail | null {
  if (typeof body !== 'object' || body === null) return null;
  const detail = (body as { detail?: unknown }).detail;
  const parsed = constraintConfirmationDetailSchema.safeParse(detail);
  return parsed.success ? parsed.data : null;
}
