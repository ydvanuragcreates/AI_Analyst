"use client";

import { BarChart3 } from "lucide-react";

import { VisualizationRenderer } from "@/components/charts/visualization-renderer";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalysisVisualizations } from "@/hooks/use-analyses";

export function ChartsTab({ analysisId }: { analysisId: string }) {
  const { data, isLoading, isError, error, refetch } = useAnalysisVisualizations(analysisId, true);

  if (isLoading) {
    return (
      <div className="grid gap-4 p-6 md:grid-cols-2">
        <Skeleton className="h-80 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }
  if (isError) {
    return (
      <div className="p-6">
        <ErrorState error={error} onRetry={() => refetch()} />
      </div>
    );
  }
  if (!data || data.length === 0) {
    return (
      <div className="p-6">
        <EmptyState icon={BarChart3} title="No charts available" description="This analysis didn't produce any visualizations." />
      </div>
    );
  }

  return (
    <div className="grid gap-4 p-4 sm:p-6 md:grid-cols-2">
      {data.map((spec, index) => (
        <VisualizationRenderer key={`${spec.type}-${spec.title}-${index}`} spec={spec} />
      ))}
    </div>
  );
}
