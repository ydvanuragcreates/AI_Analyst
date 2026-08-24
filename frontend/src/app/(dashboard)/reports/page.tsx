"use client";

import { FileText } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { StatusBadge } from "@/components/common/status-badge";
import { NewAnalysisDialog } from "@/components/analysis/new-analysis-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalyses } from "@/hooks/use-analyses";
import { useDatasets } from "@/hooks/use-datasets";
import { formatRelativeTime } from "@/lib/format";

export default function ReportsPage() {
  const { data, isLoading, isError, error, refetch } = useAnalyses({ mode: "deep", page: 1, page_size: 50 });
  const { data: datasetsResponse } = useDatasets();
  const datasetNameById = new Map(datasetsResponse?.datasets.map((d) => [d.dataset_id, d.metadata.filename]) ?? []);

  const reports = data?.analyses.filter((analysis) => analysis.status === "completed") ?? [];

  return (
    <div>
      <PageHeader title="Reports" description="Autonomous investigations with a full analytical report." actions={<NewAnalysisDialog />} />
      <div className="px-6 pb-10 sm:px-8">
        {isLoading && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        )}
        {isError && <ErrorState error={error} onRetry={() => refetch()} />}
        {!isLoading && !isError && reports.length === 0 && (
          <EmptyState
            icon={FileText}
            title="No reports yet"
            description="Run an autonomous investigation on a dataset to generate a full analytical report."
            action={<NewAnalysisDialog />}
          />
        )}
        {!isLoading && !isError && reports.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {reports.map((analysis) => (
              <Link
                key={analysis.analysis_id}
                href={`/reports/${analysis.analysis_id}`}
                className="group rounded-xl border bg-card p-5 transition-colors hover:border-foreground/20 hover:bg-accent/40"
              >
                <div className="flex items-center justify-between">
                  <FileText className="size-5 text-primary" />
                  <StatusBadge status={analysis.status} />
                </div>
                <p className="mt-3 truncate text-sm font-medium">{datasetNameById.get(analysis.dataset_id) ?? "Dataset"}</p>
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{analysis.answer || analysis.question || "Full dataset investigation"}</p>
                <p className="mt-3 text-xs text-muted-foreground">{formatRelativeTime(analysis.created_at)}</p>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
