import type { DatasetOverview } from "@/types/deep-analysis";
import { formatNumber } from "@/lib/format";

export function ExecutiveSummary({ summary, overview }: { summary: string; overview: DatasetOverview }) {
  return (
    <section>
      <h2 className="text-lg font-semibold tracking-tight">Executive summary</h2>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">{summary}</p>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Rows" value={formatNumber(overview.row_count)} />
        <Stat label="Columns" value={String(overview.column_count)} />
        <Stat label="Numeric" value={String(overview.numeric_column_count)} />
        <Stat label="Categorical" value={String(overview.categorical_column_count)} />
        <Stat label="Date" value={String(overview.date_column_count)} />
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}
