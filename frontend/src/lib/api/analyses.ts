import { apiClient } from "@/lib/api/client";
import type {
  AnalysisDetailResponse,
  AnalysisHistoryParams,
  AnalysisHistoryResponse,
  AnalysisJobResponse,
  AnalysisStatusV1Response,
  ApproveV1Request,
  CreateAnalysisRequest,
} from "@/types/analysis";
import type { Insight, Report, VisualizationSpec } from "@/types/deep-analysis";

// backend/app/api/routes/analysis_jobs.py
export const analysesApi = {
  create: (payload: CreateAnalysisRequest) => apiClient.post<AnalysisJobResponse>("/api/v1/analysis", payload),
  get: (analysisId: string) => apiClient.get<AnalysisDetailResponse>(`/api/v1/analysis/${analysisId}`),
  // Cast: AnalysisHistoryParams has no index signature (its fields are
  // typed string-literal unions like AnalysisMode, not plain strings),
  // so it isn't structurally assignable to RequestOptions.params's
  // Record<string, unknown> -- safe here since buildUrl only ever reads
  // known primitive values off it at runtime.
  list: (params?: AnalysisHistoryParams) => apiClient.get<AnalysisHistoryResponse>("/api/v1/analysis", { params: params as Record<string, unknown> | undefined }),
  cancel: (analysisId: string) => apiClient.post<AnalysisStatusV1Response>(`/api/v1/analysis/${analysisId}/cancel`),
  approve: (analysisId: string, payload: ApproveV1Request) =>
    apiClient.post<AnalysisJobResponse>(`/api/v1/analysis/${analysisId}/approve`, payload),

  // Phase 10: only populated once a mode="deep" analysis has completed.
  insights: (analysisId: string) => apiClient.get<Insight[]>(`/api/v1/analysis/${analysisId}/insights`),
  report: (analysisId: string) => apiClient.get<Report>(`/api/v1/analysis/${analysisId}/report`),
  visualizations: (analysisId: string) => apiClient.get<VisualizationSpec[]>(`/api/v1/analysis/${analysisId}/visualizations`),
};
