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
  setSidebarCollapsed: (v: boolean) => void;
  setDensity: (v: 'compact' | 'comfortable') => void;
  setScheduleHeaderCollapsed: (v: boolean) => void;
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      density: 'comfortable',
      scheduleHeaderCollapsed: false,
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      setDensity: (v) => set({ density: v }),
      setScheduleHeaderCollapsed: (v) => set({ scheduleHeaderCollapsed: v }),
    }),
    {
      name: 'carelink-ui',
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        density: s.density,
        scheduleHeaderCollapsed: s.scheduleHeaderCollapsed,
      }),
    },
  ),
);
