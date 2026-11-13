"use client";

import { Database } from "lucide-react";

import { useDatasets } from "@/hooks/use-datasets";
import { formatFileSize } from "@/lib/format";

/** Real storage figure: the summed file size of the uploaded datasets.
 * The backend has no storage quota, so there's no "of N GB" or percentage. */
export function StorageUsage() {
  const { data } = useDatasets();
  if (!data) return null;

  const totalBytes = data.datasets.reduce((sum, d) => sum + d.metadata.file_size, 0);
  const count = data.datasets.length;

  return (
    <div className="m-3 rounded-xl border border-sidebar-border bg-sidebar-accent/30 p-3.5">
      <div className="flex items-center gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
          <Database className="size-4" />
        </div>
        <div className="min-w-0">
          <p className="text-xs font-medium">Storage used</p>
          <p className="truncate text-xs text-muted-foreground">
            {formatFileSize(totalBytes)} · {count} {count === 1 ? "dataset" : "datasets"}
          </p>
        </div>
      </div>
    </div>
  );
}
