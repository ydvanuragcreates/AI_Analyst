"use client";

import { Database } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDatasets } from "@/hooks/use-datasets";
import { formatNumber } from "@/lib/format";

export function DatasetsSummary() {
  const { data, isLoading, isError, error, refetch } = useDatasets();

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Datasets</h2>
        <Button variant="ghost" size="sm" asChild>
          <Link href="/datasets">View all</Link>
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      )}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && !isError && data && data.datasets.length === 0 && (
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
      {!isLoading && !isError && data && data.datasets.length > 0 && (
        <div className="space-y-2">
          {data.datasets.slice(0, 4).map((dataset) => (
            <Link
              key={dataset.dataset_id}
              href={`/datasets/${dataset.dataset_id}`}
              className="flex items-center justify-between rounded-lg border bg-card px-4 py-3 text-sm transition-colors hover:border-foreground/20 hover:bg-accent/40"
            >
              <span className="truncate font-medium">{dataset.metadata.filename}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {formatNumber(dataset.metadata.row_count)} rows · v{dataset.version}
              </span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
