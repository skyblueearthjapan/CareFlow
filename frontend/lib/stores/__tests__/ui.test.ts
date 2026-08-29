/**
 * useUIStore — サイドバーの自動折りたたみ (mac-ui-crossplatform-design.md §2-B2)。
 *
 *   1. applySidebarAutoCollapse は初回だけ畳み、フラグを立てる
 *   2. 利用者が開き直した後にもう一度呼んでも畳まない (開閉を尊重)
 *   3. フラグは永続化 (partialize) に含まれる
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { useUIStore } from '@/lib/stores/ui';

describe('useUIStore — sidebar auto collapse', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({ sidebarCollapsed: false, sidebarAutoCollapsedApplied: false });
  });

  it('1. 初回は畳んでフラグを立てる', () => {
    useUIStore.getState().applySidebarAutoCollapse();
    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
    expect(useUIStore.getState().sidebarAutoCollapsedApplied).toBe(true);
  });

  it('2. 利用者が開き直した後は二度と自動で畳まない', () => {
    useUIStore.getState().applySidebarAutoCollapse();
    useUIStore.getState().setSidebarCollapsed(false);
    useUIStore.getState().applySidebarAutoCollapse();
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it('3. フラグは localStorage (carelink-ui) に永続される', () => {
    useUIStore.getState().applySidebarAutoCollapse();
    const raw = window.localStorage.getItem('carelink-ui');
    expect(raw).not.toBeNull();
    const persisted = JSON.parse(raw!) as { state: Record<string, unknown> };
    expect(persisted.state.sidebarAutoCollapsedApplied).toBe(true);
    expect(persisted.state.sidebarCollapsed).toBe(true);
  });
});
