import { InsightCard } from "@/components/analysis/insight-card";
import type { Insight } from "@/types/deep-analysis";

export function FindingsSection({ insights }: { insights: Insight[] }) {
  if (insights.length === 0) {
    return <p className="text-sm text-muted-foreground">No key findings were produced from this analysis.</p>;
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {insights.map((insight) => (
        <InsightCard key={insight.title} insight={insight} />
      ))}
    </div>
  );
}
