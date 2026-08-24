import { afterEach, describe, expect, it } from "vitest";

import { clearAuthSession, getAuthToken, useAuthStore } from "@/stores/auth-store";

describe("auth-store", () => {
  afterEach(() => {
    useAuthStore.getState().clearSession();
  });

  it("starts with no session", () => {
    expect(getAuthToken()).toBeNull();
  });

  it("setSession stores the token and user, readable via getAuthToken (non-hook accessor)", () => {
    useAuthStore.getState().setSession("abc123", { id: "u1", email: "a@b.com", created_at: "2026-01-01" });
    expect(getAuthToken()).toBe("abc123");
    expect(useAuthStore.getState().user?.email).toBe("a@b.com");
  });

  it("clearAuthSession removes both token and user", () => {
    useAuthStore.getState().setSession("abc123", { id: "u1", email: "a@b.com", created_at: "2026-01-01" });
    clearAuthSession();
    expect(getAuthToken()).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
  });
});
