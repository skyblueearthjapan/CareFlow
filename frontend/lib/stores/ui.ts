import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type UIState = {
  sidebarCollapsed: boolean;
  density: 'compact' | 'comfortable';
  setSidebarCollapsed: (v: boolean) => void;
  setDensity: (v: 'compact' | 'comfortable') => void;
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      density: 'comfortable',
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      setDensity: (v) => set({ density: v }),
    }),
    {
      name: 'carelink-ui',
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        density: s.density,
      }),
    },
  ),
);
