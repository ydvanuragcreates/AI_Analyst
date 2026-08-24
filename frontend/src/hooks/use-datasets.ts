"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { datasetsApi } from "@/lib/api/datasets";
import { queryKeys } from "@/lib/query-keys";

export function useDatasets() {
  return useQuery({
    queryKey: queryKeys.datasets(),
    queryFn: () => datasetsApi.list(),
    staleTime: 30 * 1000,
  });
}

export function useDataset(datasetId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.dataset(datasetId ?? ""),
    queryFn: () => datasetsApi.get(datasetId as string),
    enabled: Boolean(datasetId),
  });
}

export function useDatasetProfile(datasetId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.datasetProfile(datasetId ?? ""),
    queryFn: () => datasetsApi.profile(datasetId as string),
    enabled: Boolean(datasetId),
    staleTime: 5 * 60 * 1000, // the backend itself caches this; no need to refetch aggressively
  });
}

export function useDatasetQuality(datasetId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.datasetQuality(datasetId ?? ""),
    queryFn: () => datasetsApi.quality(datasetId as string),
    enabled: Boolean(datasetId),
    staleTime: 5 * 60 * 1000,
  });
}

export function useUploadDataset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (file: File) => datasetsApi.upload(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.datasets() });
    },
  });
}

export function useDeleteDataset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (datasetId: string) => datasetsApi.delete(datasetId),
    onSuccess: (_data, datasetId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.datasets() });
      queryClient.removeQueries({ queryKey: queryKeys.dataset(datasetId) });
    },
  });
}
