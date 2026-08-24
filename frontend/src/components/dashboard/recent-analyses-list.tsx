"use client";

import { MessagesSquare } from "lucide-react";
import Link from "next/link";

import { AnalysisListItem } from "@/components/analysis/analysis-list-item";
import { AnalysisListSkeleton } from "@/components/analysis/analysis-list-skeleton";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { useAnalyses } from "@/hooks/use-analyses";
import { useDatasets } from "@/hooks/use-datasets";

export function RecentAnalysesList() {
  const { data, isLoading, isError, error, refetch } = useAnalyses({ page: 1, page_size: 5 });
  const { data: datasetsResponse } = useDatasets();

  const datasetNameById = new Map(datasetsResponse?.datasets.map((d) => [d.dataset_id, d.metadata.filename]) ?? []);

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Recent analyses</h2>
        <Button variant="ghost" size="sm" asChild>
          <Link href="/analyses">View all</Link>
        </Button>
      </div>

      {isLoading && <AnalysisListSkeleton />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && !isError && data && data.analyses.length === 0 && (
        <EmptyState
          icon={MessagesSquare}
          title="Ask your first question about your data"
          description="Upload a dataset, then start an analysis to see results here."
        />
      )}
      {!isLoading && !isError && data && data.analyses.length > 0 && (
        <div className="space-y-3">
          {data.analyses.map((analysis) => (
            <AnalysisListItem key={analysis.analysis_id} analysis={analysis} datasetName={datasetNameById.get(analysis.dataset_id)} />
          ))}
        </div>
      )}
    </section>
  );
}
