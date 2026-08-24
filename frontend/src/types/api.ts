// Mirrors backend/app/core/errors.py's v1 error envelope:
//   {"error": {"code", "message", "request_id"}}
// The legacy (non-v1) Phase 1-8 routes instead return {"detail": "..."}
// (see main.py's dataset_service_error_handler) -- ApiError normalizes
// both shapes so callers never need to know which route family answered.

export interface V1ErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string | null;
  };
}

export interface LegacyErrorBody {
  detail: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(status: number, message: string, code: string | null = null, requestId: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}
