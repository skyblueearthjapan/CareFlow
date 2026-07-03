/**
 * op-log schemas — Wave U-3 戻る/進む (undo/redo).
 *
 * GET /api/v1/schedule/v2/op-log/state → OpLogState
 * POST .../undo / .../redo → OpLogState (新しい state を含む)
 *
 * BE 並行実装中のため欠落フィールドは .catch() で寛容にデフォルト値へフォールバック。
 * レスポンス全体が欠落 / 不正でも { can_undo: false, can_redo: false, ... } を返す。
 */
import { z } from 'zod';

/**
 * GET /api/v1/schedule/v2/op-log/state および
 * POST .../undo / .../redo レスポンスの共通スキーマ。
 *
 * 寛容パース: フィールド欠落は安全なデフォルト (undo/redo 不可) にフォールバック。
 */
export const opLogStateSchema = z
  .object({
    can_undo: z.boolean().catch(false),
    can_redo: z.boolean().catch(false),
    undo_label: z.string().nullable().catch(null),
    redo_label: z.string().nullable().catch(null),
  })
  .catch({
    can_undo: false,
    can_redo: false,
    undo_label: null,
    redo_label: null,
  });

export type OpLogState = z.infer<typeof opLogStateSchema>;
