"use client";

import { MessagesSquare } from "lucide-react";
import { Suspense, useState } from "react";

import { AnalysisListItem } from "@/components/analysis/analysis-list-item";
import { AnalysisListSkeleton } from "@/components/analysis/analysis-list-skeleton";
import { NewAnalysisDialog } from "@/components/analysis/new-analysis-dialog";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { useAnalyses } from "@/hooks/use-analyses";
import { useDatasets } from "@/hooks/use-datasets";
import type { AnalysisMode } from "@/types/analysis";

const MODE_FILTERS: { label: string; value: AnalysisMode | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Questions", value: "qa" },
  { label: "Investigations", value: "deep" },
];

function AnalysesPageContent() {
  const [mode, setMode] = useState<AnalysisMode | "all">("all");
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, error, refetch } = useAnalyses({ mode: mode === "all" ? undefined : mode, page, page_size: 20 });
  const { data: datasetsResponse } = useDatasets();

  const datasetNameById = new Map(datasetsResponse?.datasets.map((d) => [d.dataset_id, d.metadata.filename]) ?? []);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div>
      <PageHeader title="Analyses" description="Every question and investigation you've run." actions={<NewAnalysisDialog />} />
      <div className="px-6 pb-10 sm:px-8">
        <div className="mb-4 flex gap-1.5">
          {MODE_FILTERS.map((filter) => (
            <Button
              key={filter.value}
              size="sm"
              variant={mode === filter.value ? "secondary" : "ghost"}
              onClick={() => {
                setMode(filter.value);
                setPage(1);
              }}
            >
              {filter.label}
            </Button>
          ))}
        </div>

        {isLoading && <AnalysisListSkeleton count={5} />}
        {isError && <ErrorState error={error} onRetry={() => refetch()} />}
        {!isLoading && !isError && data && data.analyses.length === 0 && (
          <EmptyState icon={MessagesSquare} title="Ask your first question about your data" description="Start an analysis from a dataset to see it here." />
        )}
        {!isLoading && !isError && data && data.analyses.length > 0 && (
          <>
            <div className="space-y-3">
              {data.analyses.map((analysis) => (
                <AnalysisListItem key={analysis.analysis_id} analysis={analysis} datasetName={datasetNameById.get(analysis.dataset_id)} />
              ))}
            </div>
            {totalPages > 1 && (
              <div className="mt-6 flex items-center justify-center gap-3 text-sm">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <span className="text-muted-foreground">
                  Page {page} of {totalPages}
                </span>
                <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function AnalysesPage() {
  return (
    <Suspense fallback={null}>
      <AnalysesPageContent />
    </Suspense>
  );
}
