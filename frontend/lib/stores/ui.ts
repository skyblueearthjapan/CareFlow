import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type UIState = {
  sidebarCollapsed: boolean;
  density: 'compact' | 'comfortable';
  /**
   * スケジュール画面の上部 (ページ見出し + 週セレクタ Card + ツールバー Row1/Row2) を
   * 畳んで 1 行のコンパクト行にまとめるか (PO 要望 2026-08-23: 盤面を広く見せたい)。
   * 全タブ共通 (曜日 / 週 / 職員スケジュール)。既定は false = 展開。
   */
  scheduleHeaderCollapsed: boolean;
  /**
   * 狭い画面 (1400px 未満) の初回表示でサイドバーを自動で畳んだか
   * (mac-ui-crossplatform-design.md §2-B2)。一度適用したら以後は利用者の開閉を尊重する。
   */
  sidebarAutoCollapsedApplied: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  applySidebarAutoCollapse: () => void;
  setDensity: (v: 'compact' | 'comfortable') => void;
  setScheduleHeaderCollapsed: (v: boolean) => void;
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      density: 'comfortable',
      scheduleHeaderCollapsed: false,
      sidebarAutoCollapsedApplied: false,
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      applySidebarAutoCollapse: () =>
        set((s) =>
          s.sidebarAutoCollapsedApplied
            ? s
            : { sidebarAutoCollapsedApplied: true, sidebarCollapsed: true },
        ),
      setDensity: (v) => set({ density: v }),
      setScheduleHeaderCollapsed: (v) => set({ scheduleHeaderCollapsed: v }),
    }),
    {
      name: 'carelink-ui',
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        density: s.density,
        scheduleHeaderCollapsed: s.scheduleHeaderCollapsed,
        sidebarAutoCollapsedApplied: s.sidebarAutoCollapsedApplied,
      }),
    },
  ),
);
