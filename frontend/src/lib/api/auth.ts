import { apiClient } from "@/lib/api/client";
import type { LoginRequest, RegisterRequest, TokenResponse, UserResponse } from "@/types/auth";

// backend/app/api/routes/auth.py
export const authApi = {
  register: (payload: RegisterRequest) => apiClient.post<TokenResponse>("/api/v1/auth/register", payload, { skipAuth: true }),
  login: (payload: LoginRequest) => apiClient.post<TokenResponse>("/api/v1/auth/login", payload, { skipAuth: true }),
  me: () => apiClient.get<UserResponse>("/api/v1/auth/me"),
};
