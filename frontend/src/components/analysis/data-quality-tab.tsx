"use client";

import { DataQualityPanel } from "@/components/datasets/data-quality-panel";
import { ErrorState } from "@/components/common/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalysisReport } from "@/hooks/use-analyses";

export function DataQualityTab({ analysisId }: { analysisId: string }) {
  const { data, isLoading, isError, error, refetch } = useAnalysisReport(analysisId, true);

  if (isLoading) return <Skeleton className="m-6 h-64" />;
  if (isError) {
    return (
      <div className="p-6">
        <ErrorState error={error} onRetry={() => refetch()} />
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6">
      <DataQualityPanel
        issues={data?.data_quality ?? []}
        totalRowCount={data?.dataset_overview.row_count}
      />
    </div>
  );
}
