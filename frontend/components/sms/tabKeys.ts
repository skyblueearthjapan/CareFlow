/**
 * タブの並び (role="tablist") を矢印のキーで動かす (WAI-ARIA のタブの決まり)。
 * ←/→ で隣へ (端では反対の端へ)、Home/End で最初/最後へ。動かしたらそのタブに合わせる。
 * 呼ぶ側は、選んでいるタブだけ tabIndex=0、ほかは -1 にする。
 */
import type { KeyboardEvent } from 'react';

export function handleTabListKeyDown<K extends string>(
  e: KeyboardEvent<HTMLElement>,
  keys: readonly K[],
  current: K,
  select: (next: K) => void,
  tabId: (key: K) => string,
): void {
  const i = keys.indexOf(current);
  if (i < 0 || keys.length === 0) return;
  let next: number;
  switch (e.key) {
    case 'ArrowRight':
      next = (i + 1) % keys.length;
      break;
    case 'ArrowLeft':
      next = (i - 1 + keys.length) % keys.length;
      break;
    case 'Home':
      next = 0;
      break;
    case 'End':
      next = keys.length - 1;
      break;
    default:
      return;
  }
  e.preventDefault();
  const k = keys[next];
  if (k === undefined) return;
  select(k);
  document.getElementById(tabId(k))?.focus();
}
