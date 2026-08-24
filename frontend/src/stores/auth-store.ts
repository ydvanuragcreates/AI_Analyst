"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type { UserResponse } from "@/types/auth";

/**
 * Session storage for the backend's JWT bearer token.
 *
 * KNOWN TRADEOFF: the backend (backend/app/api/deps.py) issues a plain
 * bearer token with no refresh/cookie mechanism -- it never sets an
 * httpOnly cookie. Given that existing contract (which this frontend
 * phase is instructed not to redesign), persisting the token in
 * localStorage (via zustand's `persist` middleware) is the only way for
 * a session to survive a page reload without re-implementing a
 * cookie-issuing layer on the backend. This is a real, documented
 * XSS-surface tradeoff, not an oversight -- see the project write-up's
 * "known limitations" section. The token is never logged, never sent
 * anywhere except this app's own API client (lib/api/client.ts), and is
 * cleared immediately on logout or a 401 response.
 */
interface AuthState {
  token: string | null;
  user: UserResponse | null;
  hasHydrated: boolean;
  setSession: (token: string, user: UserResponse) => void;
  setUser: (user: UserResponse) => void;
  clearSession: () => void;
  setHasHydrated: (value: boolean) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      hasHydrated: false,
      setSession: (token, user) => set({ token, user }),
      setUser: (user) => set({ user }),
      clearSession: () => set({ token: null, user: null }),
      setHasHydrated: (value) => set({ hasHydrated: value }),
    }),
    {
      name: "ai-analyst-auth",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ token: state.token, user: state.user }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);

/** Non-hook accessor for the current token -- used by the plain (non-React)
 * API client so every request can attach `Authorization` without needing
 * to be called from inside a component. */
export function getAuthToken(): string | null {
  return useAuthStore.getState().token;
}

export function clearAuthSession(): void {
  useAuthStore.getState().clearSession();
}
