/**
 * Staff event (研修日 / イベント) zod schemas — mirrors the F5-Backend
 * contract for `/api/v1/staff/{staff_id}/events`:
 *
 *   GET    .../events?from=YYYY-MM-DD&to=YYYY-MM-DD
 *     → list[EventRead]
 *   POST   .../events                    Body=EventCreate  → EventRead
 *   PATCH  .../events/{event_id}         Body=EventUpdate  → EventRead
 *   DELETE .../events/{event_id}         → 204
 *
 * NOTE: Distinct from `staffEventSchema` in `./staff.ts` (the legacy
 * starts_at/ends_at shape used by Wave 1 read-only mocks). The Wave 2
 * REST contract uses `date` + `start_time` + `end_time` instead.
 */
import { z } from 'zod';

export const eventTypeSchema = z.enum(['研修', 'イベント']);
export type EventType = z.infer<typeof eventTypeSchema>;

const dateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, '日付は YYYY-MM-DD 形式で入力してください');
const timeSchema = z.string().regex(/^\d{2}:\d{2}$/, '時刻は HH:MM 形式で入力してください');

const titleSchema = z
  .string()
  .min(1, 'タイトルを入力してください')
  .max(100, 'タイトルは100文字以内で入力してください');

export const eventCreateSchema = z
  .object({
    date: dateSchema,
    title: titleSchema,
    start_time: timeSchema,
    end_time: timeSchema,
    type: eventTypeSchema,
    note: z.string().max(500).optional().nullable(),
    /**
     * 🔒絶対に潰せないイベント。ひな形 (event_templates.blocking) から作った
     * イベントに 🔒 を引き継ぐため作成時に送る
     * (staff-event-history-design.md §2 Phase 2)。
     * 省略時 undefined = 送らない → BE 既定 false で従来どおり。
     */
    blocking: z.boolean().optional(),
  })
  .refine((v) => v.start_time < v.end_time, {
    message: '開始時刻は終了時刻より前にしてください',
    path: ['end_time'],
  });

export const eventUpdateSchema = z
  .object({
    date: dateSchema.optional(),
    title: titleSchema.optional(),
    start_time: timeSchema.optional(),
    end_time: timeSchema.optional(),
    type: eventTypeSchema.optional(),
    note: z.string().max(500).optional().nullable(),
    /**
     * Wave 39: D&D で event を別 staff に付け替えるための任意フィールド.
     * BE 側 ``EventUpdate.new_staff_id`` (UUID) と一致.
     * URL の staff_id は「現在の所有者」, body の new_staff_id で移動先を指す.
     */
    new_staff_id: z.string().uuid().optional(),
    /**
     * イベント考慮2段階提案 (2026-07-27): 🔒絶対に潰せないイベント。
     * ON のイベントは提案エンジンのフォールバックでも占有として扱われ、
     * 衝突提案が出ない。既定 OFF。カイポケ再取り込みでも保持される。
     */
    blocking: z.boolean().optional(),
  })
  .refine(
    (v) => {
      if (v.start_time && v.end_time) return v.start_time < v.end_time;
      return true;
    },
    {
      message: '開始時刻は終了時刻より前にしてください',
      path: ['end_time'],
    },
  );

export const eventReadSchema = z.object({
  id: z.string().uuid(),
  /**
   * Wave 39: 担当スタッフ ID. D&D 移動時の URL 構築 + クライアント側衝突
   * チェックに使う. 旧来の API クライアントが staff_id 無し (古い BE) を
   * 受け取る互換のため optional として扱う.
   */
  staff_id: z.string().uuid().optional(),
  date: dateSchema,
  title: z.string(),
  start_time: timeSchema,
  end_time: timeSchema,
  type: eventTypeSchema,
  note: z.string().nullable().optional(),
  // 🔒絶対に潰せないイベント (旧BE未送出 → false の寛容パース).
  blocking: z.boolean().catch(false),
  /**
   * 出所 ('manual' | 'kaipoke' | 'fixed')。スタッフ詳細のイベント一覧が
   * 「カイポケ / 固定」バッジと出所チップの絞り込みに使う
   * (staff-event-history-design.md §2 Phase 1)。旧 BE 互換の既定は 'manual'。
   */
  source: z.string().default('manual'),
  /** 「今週だけ外す」の取消印。非 null の行は打消線で描く。 */
  cancelled_at: z.string().nullable().optional(),
  /** カイポケ個別業務 / 固定イベント展開の冪等キー。未同期は null。 */
  external_id: z.string().nullable().optional(),
});

export type EventCreate = z.infer<typeof eventCreateSchema>;
export type EventUpdate = z.infer<typeof eventUpdateSchema>;
export type EventRead = z.infer<typeof eventReadSchema>;
