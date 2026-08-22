/**
 * KaipokeReconcilePanel — カイポケ突合ビュー (週空間 C1・weekly-space-design.md §7-3)。
 *
 * **2026-08-22 (週空間 Phase E・運転席) をもって UI は撤去**。
 * 取得/適用ロジックは `cockpit/useKaipokeReconcile.ts` へ、画面は
 * `cockpit/SyncBar.tsx` (同期バー) へ移った。テストもそれぞれへ移植済み。
 *
 * このファイルに残すのは、盤面ゴーストの**型の置き場**だけ。
 * `cockpit/reconcileMarkers.ts` の `CockpitMarker` がこの型の上位互換
 * (kind:'visit'|'event' を足したもの) として定義されているため、
 * 型を移すと import が循環する。名前と場所は据え置く。
 */

/** 盤面セルに描くゴーストマーカー (`${staffId}:${weekday}` で引く)。 */
export interface ReconcileMarker {
  /** add=🟣カイポケのみ / update=🟡変更あり / delete=🔵らく助のみ(カイポケ側に無し) */
  action: 'add' | 'update' | 'delete';
  externalId: string;
  title: string;
  start: string;
  end: string;
  beforeStart?: string | null;
  beforeEnd?: string | null;
}

export type ReconcileMarkersByCell = Map<string, ReconcileMarker[]>;
