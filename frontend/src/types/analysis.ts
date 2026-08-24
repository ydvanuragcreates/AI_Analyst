// Mirrors backend/app/schemas/analysis_v1.py exactly -- the Phase 9
// job-based analysis API that both the Q&A ("qa") and Phase 10
// autonomous ("deep") modes go through.

export type AnalysisStatus = "queued" | "running" | "waiting_for_approval" | "completed" | "failed" | "cancelled";
export type AnalysisMode = "qa" | "deep";

export interface CreateAnalysisRequest {
  dataset_id: string;
  question?: string;
  mode?: AnalysisMode;
}

export interface AnalysisJobResponse {
  analysis_id: string;
  status: AnalysisStatus;
}

export interface AnalysisStatusV1Response {
  analysis_id: string;
  status: AnalysisStatus;
  progress: number;
  error: string | null;
}

export interface AnalysisDetailResponse {
  analysis_id: string;
  dataset_id: string;
  question: string;
  mode: AnalysisMode;
  status: AnalysisStatus;
  progress: number;
  answer: string | null;
  confidence: number | null;
  error: string | null;
  token_usage: Record<string, number> | null;
  created_at: string;
  completed_at: string | null;
}

export interface AnalysisHistoryResponse {
  analyses: AnalysisDetailResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApproveV1Request {
  approved: boolean;
  feedback?: string;
}

export interface AnalysisHistoryParams {
  dataset_id?: string;
  status?: AnalysisStatus;
  mode?: AnalysisMode;
  page?: number;
  page_size?: number;
}
