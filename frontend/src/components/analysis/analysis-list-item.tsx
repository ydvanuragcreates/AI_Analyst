import { ArrowUpRight, MessagesSquare, Sparkles } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/common/status-badge";
import { formatRelativeTime } from "@/lib/format";
import type { AnalysisDetailResponse } from "@/types/analysis";

export function AnalysisListItem({ analysis, datasetName }: { analysis: AnalysisDetailResponse; datasetName?: string }) {
  const href = analysis.mode === "deep" && analysis.status === "completed" ? `/reports/${analysis.analysis_id}` : `/analysis/${analysis.analysis_id}`;
  const ModeIcon = analysis.mode === "deep" ? Sparkles : MessagesSquare;

  return (
    <Link
      href={href}
      className="group flex items-start justify-between gap-4 rounded-lg border bg-card p-4 transition-colors hover:border-foreground/20 hover:bg-accent/40"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <ModeIcon className="size-3.5" aria-hidden="true" />
          <span>{analysis.mode === "deep" ? "Autonomous investigation" : "Question"}</span>
          {datasetName && (
            <>
              <span aria-hidden="true">·</span>
              <span className="truncate">{datasetName}</span>
            </>
          )}
        </div>
        <p className="mt-1 truncate text-sm font-medium">
          {analysis.question || (analysis.mode === "deep" ? "Full dataset investigation" : "Untitled question")}
        </p>
        {analysis.answer && <p className="mt-1 line-clamp-1 text-sm text-muted-foreground">{analysis.answer}</p>}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <StatusBadge status={analysis.status} />
        <span className="text-xs text-muted-foreground">{formatRelativeTime(analysis.created_at)}</span>
      </div>
      <ArrowUpRight className="mt-1 size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" />
    </Link>
  );
}
