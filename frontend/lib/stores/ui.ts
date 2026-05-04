import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type UIState = {
  sidebarCollapsed: boolean;
  density: 'compact' | 'comfortable';
  aiInputOpen: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  setDensity: (v: 'compact' | 'comfortable') => void;
  setAiInputOpen: (v: boolean) => void;
  toggleAiInput: () => void;
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      density: 'comfortable',
      aiInputOpen: false,
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      setDensity: (v) => set({ density: v }),
      setAiInputOpen: (v) => set({ aiInputOpen: v }),
      toggleAiInput: () => set((s) => ({ aiInputOpen: !s.aiInputOpen })),
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
