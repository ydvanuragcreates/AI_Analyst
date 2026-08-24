"use client";

import { Sparkles } from "lucide-react";

import { AnomalyCard } from "@/components/analysis/anomaly-card";
import { CorrelationCard } from "@/components/analysis/correlation-card";
import { InsightCard } from "@/components/analysis/insight-card";
import { RecommendationCard } from "@/components/analysis/recommendation-card";
import { TrendCard } from "@/components/analysis/trend-card";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalysisInsights, useAnalysisReport } from "@/hooks/use-analyses";

export function InsightsTab({ analysisId }: { analysisId: string }) {
  const insightsQuery = useAnalysisInsights(analysisId, true);
  const reportQuery = useAnalysisReport(analysisId, true);

  if (insightsQuery.isLoading || reportQuery.isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (insightsQuery.isError) {
    return (
      <div className="p-6">
        <ErrorState error={insightsQuery.error} onRetry={() => insightsQuery.refetch()} />
      </div>
    );
  }

  const insights = insightsQuery.data ?? [];
  const trends = reportQuery.data?.trends ?? [];
  const anomalies = reportQuery.data?.anomalies ?? [];
  const correlations = reportQuery.data?.correlations ?? [];
  const recommendations = reportQuery.data?.recommendations ?? [];

  if (insights.length === 0 && trends.length === 0 && anomalies.length === 0 && correlations.length === 0) {
    return (
      <div className="p-6">
        <EmptyState icon={Sparkles} title="No insights yet" description="This analysis didn't produce any reviewed insights from the available data." />
      </div>
    );
  }

  return (
    <div className="space-y-8 p-4 sm:p-6">
      {insights.length > 0 && (
        <Section title="Key insights">
          <div className="grid gap-4 md:grid-cols-2">
            {insights.map((insight) => (
              <InsightCard key={insight.title} insight={insight} />
            ))}
          </div>
        </Section>
      )}

      {trends.length > 0 && (
        <Section title="Trends">
          <div className="grid gap-4 md:grid-cols-2">
            {trends.map((trend) => (
              <TrendCard key={`${trend.metric_column}-${trend.date_column}`} trend={trend} />
            ))}
          </div>
        </Section>
      )}

      {anomalies.length > 0 && (
        <Section title="Anomalies">
          <div className="grid gap-4 md:grid-cols-2">
            {anomalies.map((anomaly, index) => (
              <AnomalyCard key={`${anomaly.metric_column}-${index}`} anomaly={anomaly} />
            ))}
          </div>
        </Section>
      )}

      {correlations.length > 0 && (
        <Section title="Correlations">
          <div className="grid gap-4 md:grid-cols-2">
            {correlations.map((correlation) => (
              <CorrelationCard key={`${correlation.column_a}-${correlation.column_b}`} correlation={correlation} />
            ))}
          </div>
        </Section>
      )}

      {recommendations.length > 0 && (
        <Section title="Recommendations">
          <div className="grid gap-4 md:grid-cols-2">
            {recommendations.map((recommendation, index) => (
              <RecommendationCard key={index} recommendation={recommendation} />
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}
