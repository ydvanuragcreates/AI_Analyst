"use client";

import { ChevronRight, Database } from "lucide-react";
import Link from "next/link";

import { DashboardCard } from "@/components/dashboard/dashboard-card";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDatasets } from "@/hooks/use-datasets";
import { formatFileSize, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const FILE_TONES: Record<string, string> = {
  csv: "from-emerald-500 to-emerald-700",
  xlsx: "from-violet-500 to-violet-700",
  xls: "from-violet-500 to-violet-700",
  json: "from-blue-500 to-blue-700",
  parquet: "from-pink-500 to-pink-700",
};

/** Document-shaped file badge with a folded corner and the type label. */
function FileIcon({ type }: { type: string }) {
  const key = type.toLowerCase().replace(/^\./, "");
  const label = key === "parquet" ? "PARQ" : key.toUpperCase().slice(0, 4);
  return (
    <div
      className={cn(
        "relative flex h-11 w-9 shrink-0 items-end justify-center rounded-md bg-gradient-to-b pb-1.5 text-[9px] font-bold tracking-wide text-white shadow-md",
        FILE_TONES[key] ?? "from-slate-500 to-slate-700"
      )}
      style={{ clipPath: "polygon(0 0, 68% 0, 100% 26%, 100% 100%, 0 100%)" }}
      aria-hidden="true"
    >
      <span className="absolute right-0 top-0 size-3 rounded-bl-sm bg-white/35" />
      <span className="absolute left-2 top-2.5 h-0.5 w-3 rounded bg-white/70" />
      {label}
    </div>
  );
}

export function DatasetsSummary() {
  const { data, isLoading, isError, error, refetch } = useDatasets();

  return (
    <DashboardCard icon={Database} title="Datasets" subtitle="Your uploaded datasets" href="/datasets">
      {isLoading && (
        <div className="space-y-2 p-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full rounded-xl" />
          ))}
        </div>
      )}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && data.datasets.length === 0 && (
        <EmptyState
          icon={Database}
          title="No datasets yet"
          description="Upload your first dataset to start analyzing it."
          action={
            <Button asChild size="sm">
              <Link href="/datasets">Upload dataset</Link>
            </Button>
          }
        />
      )}
      {data && data.datasets.length > 0 && (
        <div className="divide-y divide-border">
          {data.datasets.slice(0, 5).map((dataset) => (
            <Link
              key={dataset.dataset_id}
              href={`/datasets/${dataset.dataset_id}`}
              className="group flex items-center gap-3.5 rounded-xl px-3 py-3 transition-colors hover:bg-accent/50"
            >
              <FileIcon type={dataset.metadata.file_type} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{dataset.metadata.filename}</p>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {formatFileSize(dataset.metadata.file_size)} · Updated {formatRelativeTime(dataset.metadata.uploaded_at)}
                </p>
              </div>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
            </Link>
          ))}
        </div>
      )}
    </DashboardCard>
  );
}
