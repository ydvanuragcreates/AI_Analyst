"use client";

import { BarChart3, Clock, Database, Zap, type LucideIcon } from "lucide-react";

import { Sparkline } from "@/components/dashboard/sparkline";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalyses } from "@/hooks/use-analyses";
import { useDatasets } from "@/hooks/use-datasets";
import { formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const WEEKS = 12;

/** Index 0..WEEKS-1 = oldest..current week, or -1 if outside the window. */
function weekBucket(iso: string, now: number): number {
  const weeksAgo = Math.floor((now - new Date(iso).getTime()) / WEEK_MS);
  return weeksAgo >= 0 && weeksAgo < WEEKS ? WEEKS - 1 - weeksAgo : -1;
}

/** Running total at the end of each of the last WEEKS weeks. */
function cumulativeSeries(dates: string[], total: number, now: number): number[] {
  const perWeek = Array(WEEKS).fill(0);
  for (const d of dates) {
    const b = weekBucket(d, now);
    if (b >= 0) perWeek[b] += 1;
  }
  // `total` may exceed `dates.length` (the API pages at 100), so start
  // from whatever predates the window.
  let running = total - perWeek.reduce((a, b) => a + b, 0);
  return perWeek.map((n) => (running += n));
}

function formatDuration(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${(seconds / 60).toFixed(1)} min`;
}

function activityNote(dates: string[], now: number, verb: string): string {
  const recent = dates.filter((d) => now - new Date(d).getTime() < WEEK_MS).length;
  if (recent > 0) return `+${recent} this week`;
  if (dates.length === 0) return `None ${verb} yet`;
  const latest = dates.reduce((a, b) => (new Date(a) > new Date(b) ? a : b));
  return `Last ${verb} ${formatRelativeTime(latest)}`;
}

const TONES = {
  violet: { tile: "bg-violet-500/15 text-violet-500 dark:text-violet-400", stroke: "#a78bfa" },
  blue: { tile: "bg-blue-500/15 text-blue-500 dark:text-blue-400", stroke: "#60a5fa" },
  amber: { tile: "bg-amber-500/15 text-amber-500 dark:text-amber-400", stroke: "#fbbf24" },
  emerald: { tile: "bg-emerald-500/15 text-emerald-500 dark:text-emerald-400", stroke: "#34d399" },
} as const;

interface StatCardProps {
  label: string;
  value: string;
  note: string;
  icon: LucideIcon;
  tone: keyof typeof TONES;
  series: number[];
}

function StatCard({ label, value, note, icon: Icon, tone, series }: StatCardProps) {
  const colors = TONES[tone];
  return (
    <div className="relative flex gap-4 overflow-hidden rounded-2xl border bg-card p-5">
      <div className={cn("flex size-11 shrink-0 items-center justify-center rounded-xl", colors.tile)}>
        <Icon className="size-5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-muted-foreground">{label}</p>
        <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
        <p className="mt-1 truncate text-xs text-muted-foreground" title={note}>
          {note}
        </p>
      </div>
      {/* Sits beside the big number, clear of the label above and note below. */}
      <Sparkline values={series} color={colors.stroke} className="absolute right-4 top-11 h-9 w-20" />
    </div>
  );
}

export function StatsRow() {
  const datasets = useDatasets();
  // The API caps page_size at 100, so trends cover the latest 100 runs.
  const analyses = useAnalyses({ page: 1, page_size: 100 });
  const reports = useAnalyses({ page: 1, page_size: 100, mode: "deep", status: "completed" });

  if (!datasets.data || !analyses.data || !reports.data) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-26 rounded-2xl" />
        ))}
      </div>
    );
  }

  // "Now" = when the data was fetched (keeps render pure, and the
  // queries refetch often enough that this is effectively current).
  const now = Math.max(datasets.dataUpdatedAt, analyses.dataUpdatedAt, reports.dataUpdatedAt);

  const datasetDates = datasets.data.datasets.map((d) => d.metadata.uploaded_at);
  const analysisDates = analyses.data.analyses.map((a) => a.created_at);
  const reportDates = reports.data.analyses.map((a) => a.completed_at ?? a.created_at);

  // Average wall-clock time of completed runs, overall and per week
  // (weeks without runs carry the previous value so the line stays readable).
  const completed = analyses.data.analyses.filter((a) => a.status === "completed" && a.completed_at);
  const durationOf = (a: (typeof completed)[number]) => new Date(a.completed_at as string).getTime() - new Date(a.created_at).getTime();
  const avgMs = completed.length ? completed.reduce((sum, a) => sum + durationOf(a), 0) / completed.length : 0;
  const weeklyAvg: number[] = [];
  for (let week = 0; week < WEEKS; week++) {
    const runs = completed.filter((a) => weekBucket(a.created_at, now) === week);
    weeklyAvg.push(runs.length ? runs.reduce((s, a) => s + durationOf(a), 0) / runs.length : (weeklyAvg[week - 1] ?? avgMs));
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard
        label="Total Datasets"
        value={String(datasets.data.datasets.length)}
        note={activityNote(datasetDates, now, "added")}
        icon={Database}
        tone="violet"
        series={cumulativeSeries(datasetDates, datasets.data.datasets.length, now)}
      />
      <StatCard
        label="Total Analyses"
        value={String(analyses.data.total)}
        note={activityNote(analysisDates, now, "run")}
        icon={BarChart3}
        tone="blue"
        series={cumulativeSeries(analysisDates, analyses.data.total, now)}
      />
      <StatCard
        label="Reports Generated"
        value={String(reports.data.total)}
        note={activityNote(reportDates, now, "generated")}
        icon={Zap}
        tone="amber"
        series={cumulativeSeries(reportDates, reports.data.total, now)}
      />
      <StatCard
        label="Avg. Analysis Time"
        value={completed.length ? formatDuration(avgMs) : "—"}
        note={completed.length ? `${completed.length} completed ${completed.length === 1 ? "run" : "runs"}` : "No completed runs yet"}
        icon={Clock}
        tone="emerald"
        series={weeklyAvg}
      />
    </div>
  );
}
