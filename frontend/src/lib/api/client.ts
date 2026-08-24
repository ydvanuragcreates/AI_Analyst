import { API_URL } from "@/lib/env";
import { clearAuthSession, getAuthToken } from "@/stores/auth-store";
import { ApiError, type LegacyErrorBody, type V1ErrorBody } from "@/types/api";

// Intentionally `unknown`-valued (not a strict string/number/boolean
// union): callers pass typed request-param interfaces (e.g.
// AnalysisHistoryParams) whose fields are string literal unions, and
// requiring every such interface to carry an index signature just to
// satisfy this function's param type isn't worth the friction -- this
// function's own runtime check (skip undefined/null, else String(value))
// is what actually matters, not the static type of each value.
function buildUrl(path: string, params?: Record<string, unknown>): string {
  const url = new URL(path.startsWith("http") ? path : `${API_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function parseErrorBody(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body (e.g. a proxy error page) -- fall through to the generic message below.
  }

  if (body && typeof body === "object" && "error" in body) {
    const v1 = body as V1ErrorBody;
    return new ApiError(response.status, v1.error.message, v1.error.code, v1.error.request_id);
  }
  if (body && typeof body === "object" && "detail" in body) {
    const legacy = body as LegacyErrorBody;
    return new ApiError(response.status, legacy.detail);
  }

  return new ApiError(response.status, mapStatusToMessage(response.status));
}

/** Fallback, human-readable messages by HTTP status -- used only when the
 * backend didn't include its own message (e.g. a network/proxy failure). */
function mapStatusToMessage(status: number): string {
  switch (status) {
    case 401:
      return "Your session has expired. Please sign in again.";
    case 403:
      return "You don't have permission to do that.";
    case 404:
      return "We couldn't find what you were looking for.";
    case 408:
      return "The request took too long and timed out.";
    case 409:
      return "This conflicts with an existing request.";
    case 422:
      return "Some of the information provided wasn't valid.";
    case 429:
      return "Too many requests -- please slow down and try again shortly.";
    default:
      return status >= 500 ? "Something went wrong on our end. Please try again." : "The request could not be completed.";
  }
}

export interface RequestOptions {
  params?: Record<string, unknown>;
  signal?: AbortSignal;
  /** Skip attaching the Authorization header (login/register only). */
  skipAuth?: boolean;
}

async function request<T>(method: string, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const isFormData = body instanceof FormData;
  if (body !== undefined && !isFormData) {
    headers["Content-Type"] = "application/json";
  }

  const token = options.skipAuth ? null : getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(buildUrl(path, options.params), {
    method,
    headers,
    body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    const error = await parseErrorBody(response);
    if (response.status === 401 && !options.skipAuth) {
      // The session is no longer valid -- clear it so the app's route
      // guard (components/auth/AuthGuard.tsx) redirects to /login rather
      // than repeatedly retrying with a dead token.
      clearAuthSession();
    }
    throw error;
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T>(path: string, options?: RequestOptions) => request<T>("GET", path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>("POST", path, body, options),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>("PATCH", path, body, options),
  delete: <T>(path: string, options?: RequestOptions) => request<T>("DELETE", path, undefined, options),
};

export { buildUrl };
