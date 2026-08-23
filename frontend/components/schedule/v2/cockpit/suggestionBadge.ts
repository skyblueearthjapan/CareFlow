/**
 * 「担当なし」コース帯の提案バッジ (Phase 2-B) — 文言の**単一ソース**。
 *
 * リスト盤面 (`StaffWeekBoard`) と タイムライン (`StaffTimelineView`) の
 * 両方から使う。モック `docs/mockups/unassigned-suggestions-mock.html` の
 * `.badge` / `.badge.zero` / `.badge.calc` と同じ 3 状態。
 */

/** 1 コース分の状態。`'calc'` = 問い合わせ中 / 未計算は `undefined`。 */
export type SuggestionBadgeState = { ok: number } | 'calc';

/** `${templateId}:${weekday}` → 状態。 */
export type SuggestionBadgeMap = Map<string, SuggestionBadgeState>;

export interface SuggestionBadgeView {
  label: string;
  /** 'idle' = 未計算/計算中 / 'ok' = 引受可あり / 'zero' = 丸ごと可 0 名。 */
  tone: 'idle' | 'ok' | 'zero';
  /** 計算中は押しても意味がないので無効化する。 */
  busy: boolean;
}

/** 状態 → バッジの見た目・文言。 */
export function suggestionBadgeView(state: SuggestionBadgeState | undefined): SuggestionBadgeView {
  if (state === 'calc') return { label: '確認中…', tone: 'idle', busy: true };
  if (state == null) return { label: '提案を見る', tone: 'idle', busy: false };
  if (state.ok > 0) return { label: `◎ ${state.ok}名 引受可`, tone: 'ok', busy: false };
  // 0 名でも「1件ずつ」の入口として押させる (押すと分割導線が出る)。
  return { label: '◎ 0名（1件ずつ）', tone: 'zero', busy: false };
}

/** バッジの追加クラス (トークンは既存の success/warning 系に合わせる)。 */
export function suggestionBadgeClass(tone: SuggestionBadgeView['tone']): string {
  const base =
    'inline-flex shrink-0 items-center rounded-full border px-1.5 py-px text-[10px] font-bold leading-none disabled:opacity-50';
  if (tone === 'ok') return `${base} border-success bg-success-bg text-success`;
  if (tone === 'zero') return `${base} border-border-warning bg-warning-bg text-warning-strong`;
  return `${base} border-border-default bg-bg-base text-text-muted hover:border-brand-primary hover:text-brand-primary`;
}
