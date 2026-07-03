/**
 * opLog — Wave U-3 スキーマ寛容パース + queryKey テスト。
 *
 * ネットワーク / QueryClient は不要。Zod .catch() による寛容パースの
 * 正確な挙動を検証する。
 */
import { describe, it, expect } from 'vitest';
import { opLogStateSchema } from '../../schemas/v2/opLog';
import { OP_LOG_STATE_KEY } from '../opLog';

describe('opLogStateSchema — 寛容パース', () => {
  it('正常なレスポンスをパースする', () => {
    const result = opLogStateSchema.parse({
      can_undo: true,
      can_redo: false,
      undo_label: '田中様を移動',
      redo_label: null,
    });
    expect(result.can_undo).toBe(true);
    expect(result.can_redo).toBe(false);
    expect(result.undo_label).toBe('田中様を移動');
    expect(result.redo_label).toBeNull();
  });

  it('全フィールド欠落 → 安全なデフォルト値 (can_undo=false, can_redo=false)', () => {
    const result = opLogStateSchema.parse({});
    expect(result.can_undo).toBe(false);
    expect(result.can_redo).toBe(false);
    expect(result.undo_label).toBeNull();
    expect(result.redo_label).toBeNull();
  });

  it('can_undo 欠落 → false にフォールバック', () => {
    const result = opLogStateSchema.parse({ can_redo: true, redo_label: '進む' });
    expect(result.can_undo).toBe(false);
    expect(result.can_redo).toBe(true);
    expect(result.redo_label).toBe('進む');
  });

  it('can_undo が非 boolean → false にフォールバック', () => {
    const result = opLogStateSchema.parse({ can_undo: 'yes', can_redo: 1 });
    expect(result.can_undo).toBe(false);
    expect(result.can_redo).toBe(false);
  });

  it('レスポンス全体が null → デフォルト値に .catch() フォールバック', () => {
    const result = opLogStateSchema.parse(null);
    expect(result.can_undo).toBe(false);
    expect(result.can_redo).toBe(false);
    expect(result.undo_label).toBeNull();
    expect(result.redo_label).toBeNull();
  });

  it('undo_label が null → null として保持', () => {
    const result = opLogStateSchema.parse({
      can_undo: true,
      can_redo: true,
      undo_label: null,
      redo_label: '進む先',
    });
    expect(result.undo_label).toBeNull();
    expect(result.redo_label).toBe('進む先');
  });

  it('BE 追加フィールドは無視される (zod strip)', () => {
    const result = opLogStateSchema.parse({
      can_undo: false,
      can_redo: false,
      undo_label: null,
      redo_label: null,
      extra_field: 'ignored',
    });
    expect(result).toEqual({
      can_undo: false,
      can_redo: false,
      undo_label: null,
      redo_label: null,
    });
  });
});

describe('OP_LOG_STATE_KEY', () => {
  it('定数値が "op-log-state" である', () => {
    expect(OP_LOG_STATE_KEY).toBe('op-log-state');
  });
});
