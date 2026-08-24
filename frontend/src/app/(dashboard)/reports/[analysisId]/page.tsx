"use client";

import { use } from "react";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { ErrorState } from "@/components/common/error-state";
import { ReportView } from "@/components/reports/report-view";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalysis } from "@/hooks/use-analyses";

export default function ReportDetailPage({ params }: { params: Promise<{ analysisId: string }> }) {
  const { analysisId } = use(params);
  const { data: analysis, isLoading, isError, error, refetch } = useAnalysis(analysisId);

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 sm:px-8">
      <Link href="/reports" className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ChevronLeft className="size-4" /> Back to reports
      </Link>

      {isLoading && <Skeleton className="h-96 w-full" />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}

      {!isLoading && !isError && analysis && analysis.mode !== "deep" && (
        <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          This analysis was a direct question, not an autonomous investigation, so it has no report.
        </p>
      )}
      {!isLoading && !isError && analysis && analysis.mode === "deep" && analysis.status !== "completed" && (
        <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          This report isn&apos;t ready yet — the investigation is still {analysis.status}.
        </p>
      )}
      {!isLoading && !isError && analysis && analysis.mode === "deep" && analysis.status === "completed" && <ReportView analysisId={analysisId} />}
    </div>
  );
}
