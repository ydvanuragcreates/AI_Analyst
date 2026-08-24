"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { analysesApi } from "@/lib/api/analyses";
import { queryKeys } from "@/lib/query-keys";
import type { AnalysisHistoryParams, ApproveV1Request, CreateAnalysisRequest } from "@/types/analysis";

export function useAnalyses(params?: AnalysisHistoryParams) {
  return useQuery({
    queryKey: queryKeys.analyses(params),
    queryFn: () => analysesApi.list(params),
    staleTime: 15 * 1000,
  });
}

/** Polls while an analysis is still in flight (SSE is the primary
 * real-time channel -- see use-analysis-stream.ts -- but this keeps the
 * detail record itself in sync, e.g. after a page reload mid-run or if
 * the SSE connection drops and the terminal event was missed). */
export function useAnalysis(analysisId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.analysis(analysisId ?? ""),
    queryFn: () => analysesApi.get(analysisId as string),
    enabled: Boolean(analysisId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "running" || status === "queued" ? 4000 : false;
    },
  });
}

export function useCreateAnalysis() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateAnalysisRequest) => analysesApi.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["analyses"] });
    },
  });
}

export function useCancelAnalysis(analysisId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => analysesApi.cancel(analysisId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.analysis(analysisId) });
    },
  });
}

export function useApproveAnalysis(analysisId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ApproveV1Request) => analysesApi.approve(analysisId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.analysis(analysisId) });
    },
  });
}

export function useAnalysisInsights(analysisId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.analysisInsights(analysisId ?? ""),
    queryFn: () => analysesApi.insights(analysisId as string),
    enabled: Boolean(analysisId) && enabled,
  });
}

export function useAnalysisReport(analysisId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.analysisReport(analysisId ?? ""),
    queryFn: () => analysesApi.report(analysisId as string),
    enabled: Boolean(analysisId) && enabled,
  });
}

export function useAnalysisVisualizations(analysisId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.analysisVisualizations(analysisId ?? ""),
    queryFn: () => analysesApi.visualizations(analysisId as string),
    enabled: Boolean(analysisId) && enabled,
  });
}
