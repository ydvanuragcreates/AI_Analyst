import Link from "next/link";

import { StatusBadge } from "@/components/common/status-badge";
import { formatNumber } from "@/lib/format";
import type { AnalysisDetailResponse } from "@/types/analysis";
import type { DatasetProfile } from "@/types/dataset";

export function AnalysisContextPanel({ analysis, datasetName, profile }: { analysis: AnalysisDetailResponse; datasetName?: string; profile?: DatasetProfile }) {
  return (
    <aside className="hidden w-72 shrink-0 space-y-6 overflow-y-auto border-l p-5 lg:block">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Current analysis</p>
        <dl className="mt-3 space-y-3 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">Dataset</dt>
            <dd className="mt-0.5">
              <Link href={`/datasets/${analysis.dataset_id}`} className="font-medium hover:underline">
                {datasetName ?? "Unknown dataset"}
              </Link>
            </dd>
          </div>
          {profile && (
            <>
              <div>
                <dt className="text-xs text-muted-foreground">Rows</dt>
                <dd className="mt-0.5 font-medium tabular-nums">{formatNumber(profile.row_count)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Columns</dt>
                <dd className="mt-0.5 font-medium tabular-nums">{profile.column_count}</dd>
              </div>
            </>
          )}
          <div>
            <dt className="text-xs text-muted-foreground">Question</dt>
            <dd className="mt-0.5 font-medium">{analysis.question || "Full dataset investigation"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Status</dt>
            <dd className="mt-1">
              <StatusBadge status={analysis.status} />
            </dd>
          </div>
        </dl>
      </div>

      {profile && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Dataset profile</p>
          <div className="mt-3 space-y-2 text-sm">
            <ColumnGroup label="Numeric" columns={profile.numeric_columns} />
            <ColumnGroup label="Categorical" columns={profile.categorical_columns} />
            <ColumnGroup label="Date" columns={profile.date_columns} />
          </div>
        </div>
      )}
    </aside>
  );
}

function ColumnGroup({ label, columns }: { label: string; columns: string[] }) {
  if (columns.length === 0) return null;
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {columns.slice(0, 6).map((column) => (
          <span key={column} className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs">
            {column}
          </span>
        ))}
        {columns.length > 6 && <span className="text-xs text-muted-foreground">+{columns.length - 6} more</span>}
      </div>
    </div>
  );
}
