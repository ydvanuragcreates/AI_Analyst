// Single source of truth for the backend API base URL. Every other
// module reads this instead of touching process.env directly, and
// nothing outside this file is allowed to hardcode "localhost" (see
// lib/api/client.ts, hooks/use-analysis-stream.ts).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
