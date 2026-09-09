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
  /**
   * スケジュール画面のトグル「非稼働を表示」(患者ステータス連動 Phase 3・
   * design 2026-09-09 §3-4)。既定は false = 連動取消 (status_cancel) を隠す。
   * ON にすると残骸点検用に打ち消し線つきで出す。非稼働患者の**残っている予定**は
   * このトグルに関係なく常にバッジ付きで出る (隠さない = 原則⑥)。
   */
  showInactiveVisits: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  applySidebarAutoCollapse: () => void;
  setDensity: (v: 'compact' | 'comfortable') => void;
  setScheduleHeaderCollapsed: (v: boolean) => void;
  setShowInactiveVisits: (v: boolean) => void;
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      density: 'comfortable',
      scheduleHeaderCollapsed: false,
      sidebarAutoCollapsedApplied: false,
      showInactiveVisits: false,
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      applySidebarAutoCollapse: () =>
        set((s) =>
          s.sidebarAutoCollapsedApplied
            ? s
            : { sidebarAutoCollapsedApplied: true, sidebarCollapsed: true },
        ),
      setDensity: (v) => set({ density: v }),
      setScheduleHeaderCollapsed: (v) => set({ scheduleHeaderCollapsed: v }),
      setShowInactiveVisits: (v) => set({ showInactiveVisits: v }),
    }),
    {
      name: 'carelink-ui',
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        density: s.density,
        scheduleHeaderCollapsed: s.scheduleHeaderCollapsed,
        sidebarAutoCollapsedApplied: s.sidebarAutoCollapsedApplied,
        showInactiveVisits: s.showInactiveVisits,
      }),
    },
  ),
);
