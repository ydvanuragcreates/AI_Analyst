"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { authApi } from "@/lib/api/auth";
import { queryKeys } from "@/lib/query-keys";
import { useAuthStore } from "@/stores/auth-store";
import type { LoginRequest, RegisterRequest } from "@/types/auth";

export function useCurrentUser() {
  const token = useAuthStore((state) => state.token);
  const hasHydrated = useAuthStore((state) => state.hasHydrated);

  return useQuery({
    queryKey: queryKeys.me(),
    queryFn: () => authApi.me(),
    enabled: hasHydrated && Boolean(token),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useLogin() {
  const router = useRouter();
  const setSession = useAuthStore((state) => state.setSession);
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: LoginRequest) => authApi.login(payload),
    onSuccess: async (token) => {
      // The token is needed before /me can be called with it attached.
      setSession(token.access_token, { id: "", email: "", created_at: "" });
      const user = await authApi.me();
      setSession(token.access_token, user);
      queryClient.setQueryData(queryKeys.me(), user);
      router.push("/dashboard");
    },
  });
}

export function useRegister() {
  const router = useRouter();
  const setSession = useAuthStore((state) => state.setSession);
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: RegisterRequest) => authApi.register(payload),
    onSuccess: async (token) => {
      setSession(token.access_token, { id: "", email: "", created_at: "" });
      const user = await authApi.me();
      setSession(token.access_token, user);
      queryClient.setQueryData(queryKeys.me(), user);
      router.push("/dashboard");
    },
  });
}

export function useLogout() {
  const router = useRouter();
  const clearSession = useAuthStore((state) => state.clearSession);
  const queryClient = useQueryClient();

  return () => {
    clearSession();
    queryClient.clear();
    router.push("/login");
  };
}
