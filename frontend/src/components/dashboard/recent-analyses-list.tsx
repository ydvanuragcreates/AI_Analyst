"use client";

import { Activity, BarChart3, ChevronRight, MessagesSquare, Sparkles } from "lucide-react";
import Link from "next/link";

import { DashboardCard } from "@/components/dashboard/dashboard-card";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { StatusBadge } from "@/components/common/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalyses } from "@/hooks/use-analyses";
import { useDatasets } from "@/hooks/use-datasets";
import { formatRelativeTime } from "@/lib/format";
import type { AnalysisDetailResponse } from "@/types/analysis";

function AnalysisRow({ analysis, datasetName }: { analysis: AnalysisDetailResponse; datasetName?: string }) {
  const href = analysis.mode === "deep" && analysis.status === "completed" ? `/reports/${analysis.analysis_id}` : `/analysis/${analysis.analysis_id}`;
  const Icon = analysis.mode === "deep" ? Sparkles : BarChart3;
  const title = analysis.question || (analysis.mode === "deep" ? "Full dataset investigation" : "Untitled question");
  const subtitle = analysis.answer || analysis.error || (datasetName ? `On ${datasetName}` : analysis.mode === "deep" ? "Autonomous investigation" : "Question");

  return (
    <Link href={href} className="group flex items-center gap-4 rounded-xl px-3 py-3 transition-colors hover:bg-accent/50">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground group-hover:text-foreground">
        <Icon className="size-[18px]" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{title}</p>
        <p className="mt-0.5 truncate text-sm text-muted-foreground">{subtitle}</p>
      </div>
      <StatusBadge status={analysis.status} className="hidden sm:inline-flex" />
      <span className="hidden w-24 shrink-0 text-right text-xs text-muted-foreground md:block">{formatRelativeTime(analysis.created_at)}</span>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
    </Link>
  );
}

export function RecentAnalysesList() {
  const { data, isLoading, isError, error, refetch } = useAnalyses({ page: 1, page_size: 5 });
  const { data: datasetsResponse } = useDatasets();
  const datasetNameById = new Map(datasetsResponse?.datasets.map((d) => [d.dataset_id, d.metadata.filename]) ?? []);

  return (
    <DashboardCard icon={Activity} title="Recent analyses" subtitle="Your latest data analyses and insights" href="/analyses">
      {isLoading && (
        <div className="space-y-2 p-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full rounded-xl" />
          ))}
        </div>
      )}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && data.analyses.length === 0 && (
        <EmptyState
          icon={MessagesSquare}
          title="Ask your first question about your data"
          description="Upload a dataset, then start an analysis to see results here."
        />
      )}
      {data && data.analyses.length > 0 && (
        <div className="divide-y divide-border">
          {data.analyses.map((analysis) => (
            <AnalysisRow key={analysis.analysis_id} analysis={analysis} datasetName={datasetNameById.get(analysis.dataset_id)} />
          ))}
        </div>
      )}
    </DashboardCard>
  );
}
