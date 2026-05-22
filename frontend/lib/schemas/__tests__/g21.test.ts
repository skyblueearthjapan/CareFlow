/**
 * Phase G-21 T4 reviewer fixes — schema unit tests.
 *
 * カバーするシナリオ:
 *   C1: officeFeatureFlagSchema は `enabled` 省略でも parse 成功 / `enabled_at`
 *       optional 維持. BE は `enabled` を返さない.
 *   L1: PAIR_MODES 順序は preferred → required → blocked.
 *   M3: patientSameAddressLinkCreateSchema.note は 500 文字を超えると 422.
 *   L2: pfvPinBulkRequestSchema は payload 内重複 pfv_id を検出して error.
 */
import { describe, it, expect } from 'vitest';

import {
  PAIR_MODES,
  officeFeatureFlagSchema,
  patientSameAddressLinkCreateSchema,
  pfvPinBulkRequestSchema,
} from '../g21';

const UUID_A = '00000000-0000-0000-0000-00000000000a';
const UUID_B = '00000000-0000-0000-0000-00000000000b';
const UUID_C = '00000000-0000-0000-0000-00000000000c';

describe('Phase G-21 reviewer fixes — zod schemas', () => {
  // ─── C1: officeFeatureFlagSchema enabled は optional ──────────────────
  describe('C1: officeFeatureFlagSchema', () => {
    it('enabled 省略 + enabled_at = null でも parse 成功 (= 未有効レコード)', () => {
      const result = officeFeatureFlagSchema.safeParse({
        office_id: UUID_A,
        feature_key: 'g21_new_algorithm',
        enabled_at: null,
        enabled_by_user_id: null,
        note: null,
      });
      expect(result.success).toBe(true);
    });

    it('enabled 省略 + enabled_at = ISO 日時 → parse 成功 (= 有効レコード)', () => {
      const result = officeFeatureFlagSchema.safeParse({
        office_id: UUID_A,
        feature_key: 'g21_new_algorithm',
        enabled_at: '2026-05-22T10:00:00Z',
        enabled_by_user_id: UUID_B,
        note: '稼働開始',
      });
      expect(result.success).toBe(true);
    });

    it('enabled も明示的に true で渡しても parse 成功 (互換のため受け入れる)', () => {
      const result = officeFeatureFlagSchema.safeParse({
        office_id: UUID_A,
        feature_key: 'g21_new_algorithm',
        enabled: true,
        enabled_at: '2026-05-22T10:00:00Z',
      });
      expect(result.success).toBe(true);
    });
  });

  // ─── L1: PAIR_MODES 順序 ──────────────────────────────────────────────
  describe('L1: PAIR_MODES 列挙順', () => {
    it('preferred → required → blocked の順序であること', () => {
      expect(PAIR_MODES).toEqual(['preferred', 'required', 'blocked']);
    });
  });

  // ─── M3: note maxLength = 500 ─────────────────────────────────────────
  describe('M3: patientSameAddressLinkCreateSchema note maxLength', () => {
    it('note 500 文字までは parse 成功', () => {
      const note = 'あ'.repeat(500);
      const result = patientSameAddressLinkCreateSchema.safeParse({
        patient_a_id: UUID_A,
        patient_b_id: UUID_B,
        pair_mode: 'blocked',
        note,
      });
      expect(result.success).toBe(true);
    });

    it('note 501 文字は 422 (zod error)', () => {
      const note = 'あ'.repeat(501);
      const result = patientSameAddressLinkCreateSchema.safeParse({
        patient_a_id: UUID_A,
        patient_b_id: UUID_B,
        pair_mode: 'blocked',
        note,
      });
      expect(result.success).toBe(false);
      if (!result.success) {
        const noteIssue = result.error.issues.find((i) => i.path[0] === 'note');
        expect(noteIssue).toBeDefined();
        expect(noteIssue?.message).toContain('500 文字以内');
      }
    });

    it('note 省略は parse 成功 (任意フィールド)', () => {
      const result = patientSameAddressLinkCreateSchema.safeParse({
        patient_a_id: UUID_A,
        patient_b_id: UUID_B,
        pair_mode: 'required',
      });
      expect(result.success).toBe(true);
    });
  });

  // ─── L2: pfvPinBulkRequestSchema 重複 pfv_id 検出 ─────────────────────
  describe('L2: pfvPinBulkRequestSchema 重複 pfv_id ガード', () => {
    it('全て uniq な pfv_id なら parse 成功', () => {
      const result = pfvPinBulkRequestSchema.safeParse([
        { pfv_id: UUID_A, is_pinned: true },
        { pfv_id: UUID_B, is_pinned: false },
        { pfv_id: UUID_C, is_pinned: true },
      ]);
      expect(result.success).toBe(true);
    });

    it('同一 pfv_id を 2 件含むと 422 (zod error)', () => {
      const result = pfvPinBulkRequestSchema.safeParse([
        { pfv_id: UUID_A, is_pinned: true },
        { pfv_id: UUID_B, is_pinned: false },
        { pfv_id: UUID_A, is_pinned: false }, // 重複
      ]);
      expect(result.success).toBe(false);
      if (!result.success) {
        const dupIssue = result.error.issues.find((i) => i.message.includes('複数回'));
        expect(dupIssue).toBeDefined();
        // path は items の index = 2 を指す.
        expect(dupIssue?.path).toEqual([2, 'pfv_id']);
      }
    });

    it('空配列は parse 成功 (= no-op bulk request)', () => {
      const result = pfvPinBulkRequestSchema.safeParse([]);
      expect(result.success).toBe(true);
    });
  });
});
