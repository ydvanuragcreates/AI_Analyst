import type { AnalysisHistoryParams } from "@/types/analysis";

/** Centralized TanStack Query key factory -- every hook in hooks/ builds
 * its keys from here so invalidation (e.g. after upload/create/delete)
 * never has to guess another hook's key shape. */
export const queryKeys = {

  datasets: () => ["datasets"] as const,
  dataset: (datasetId: string) => ["datasets", datasetId] as const,
  datasetProfile: (datasetId: string) => ["datasets", datasetId, "profile"] as const,
  datasetQuality: (datasetId: string) => ["datasets", datasetId, "quality"] as const,

  analyses: (params?: AnalysisHistoryParams) => ["analyses", params ?? {}] as const,
  analysis: (analysisId: string) => ["analyses", analysisId] as const,
  analysisInsights: (analysisId: string) => ["analyses", analysisId, "insights"] as const,
  analysisReport: (analysisId: string) => ["analyses", analysisId, "report"] as const,
  analysisVisualizations: (analysisId: string) => ["analyses", analysisId, "visualizations"] as const,
};
