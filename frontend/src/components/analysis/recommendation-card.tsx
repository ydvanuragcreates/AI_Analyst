import { Lightbulb } from "lucide-react";

import type { Recommendation } from "@/types/deep-analysis";

export function RecommendationCard({ recommendation }: { recommendation: Recommendation }) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2">
        <Lightbulb className="size-4 text-amber-500" />
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Recommendation</span>
      </div>

      <dl className="mt-3 space-y-3">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Fact</dt>
          <dd className="mt-0.5 text-sm">{recommendation.fact}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Interpretation</dt>
          <dd className="mt-0.5 text-sm text-muted-foreground">{recommendation.interpretation}</dd>
        </div>
        <div className="rounded-lg bg-primary/5 p-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-primary">Recommendation</dt>
          <dd className="mt-0.5 text-sm font-medium">{recommendation.recommendation}</dd>
        </div>
      </dl>
    </div>
  );
}
