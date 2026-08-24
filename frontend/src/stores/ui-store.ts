"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Pure client/UI state only -- never server data. Server state (datasets,
 * analyses, insights, ...) always lives in TanStack Query, never here.
 */
interface UIState {
  sidebarCollapsed: boolean;
  mobileSidebarOpen: boolean;
  toggleSidebarCollapsed: () => void;
  setMobileSidebarOpen: (open: boolean) => void;

  /** Active tab in the analysis workspace -- kept here (not in the URL)
   * so switching tabs never triggers a navigation/re-fetch. */
  activeAnalysisTab: string;
  setActiveAnalysisTab: (tab: string) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      mobileSidebarOpen: false,
      toggleSidebarCollapsed: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setMobileSidebarOpen: (open) => set({ mobileSidebarOpen: open }),

      activeAnalysisTab: "chat",
      setActiveAnalysisTab: (tab) => set({ activeAnalysisTab: tab }),
    }),
    {
      name: "ai-analyst-ui",
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed }),
    }
  )
);
