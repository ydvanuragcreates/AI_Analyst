"use client";

import { AlertCircle } from "lucide-react";

import { AnomalyCard } from "@/components/analysis/anomaly-card";
import { CorrelationCard } from "@/components/analysis/correlation-card";
import { RecommendationCard } from "@/components/analysis/recommendation-card";
import { SegmentCard } from "@/components/analysis/segment-card";
import { TrendCard } from "@/components/analysis/trend-card";
import { ErrorState } from "@/components/common/error-state";
import { ExecutiveSummary } from "@/components/reports/executive-summary";
import { FindingsSection } from "@/components/reports/findings-section";
import { DataQualityPanel } from "@/components/datasets/data-quality-panel";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalysisReport } from "@/hooks/use-analyses";

export function ReportView({ analysisId }: { analysisId: string }) {
  const { data: report, isLoading, isError, error, refetch } = useAnalysisReport(analysisId, true);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (isError || !report) {
    return <ErrorState error={error} title="We couldn't load this report" onRetry={() => refetch()} />;
  }

  return (
    <div className="space-y-10">
      <ExecutiveSummary summary={report.executive_summary} overview={report.dataset_overview} />

      <ReportSection title="Data quality">
        <DataQualityPanel issues={report.data_quality} totalRowCount={report.dataset_overview.row_count} />
      </ReportSection>

      <ReportSection title="Key findings">
        <FindingsSection insights={report.key_findings} />
      </ReportSection>

      {report.trends.length > 0 && (
        <ReportSection title="Trends">
          <div className="grid gap-4 md:grid-cols-2">
            {report.trends.map((trend) => (
              <TrendCard key={`${trend.metric_column}-${trend.date_column}`} trend={trend} />
            ))}
          </div>
        </ReportSection>
      )}

      {report.anomalies.length > 0 && (
        <ReportSection title="Anomalies">
          <div className="grid gap-4 md:grid-cols-2">
            {report.anomalies.map((anomaly, index) => (
              <AnomalyCard key={`${anomaly.metric_column}-${index}`} anomaly={anomaly} />
            ))}
          </div>
        </ReportSection>
      )}

      {report.segment_analysis.length > 0 && (
        <ReportSection title="Segment analysis">
          <div className="grid gap-4 md:grid-cols-2">
            {report.segment_analysis.map((segment) => (
              <SegmentCard key={`${segment.dimension_column}-${segment.metric_column}`} segment={segment} />
            ))}
          </div>
        </ReportSection>
      )}

      {report.correlations.length > 0 && (
        <ReportSection title="Correlations">
          <div className="grid gap-4 md:grid-cols-2">
            {report.correlations.map((correlation) => (
              <CorrelationCard key={`${correlation.column_a}-${correlation.column_b}`} correlation={correlation} />
            ))}
          </div>
        </ReportSection>
      )}

      {report.recommendations.length > 0 && (
        <ReportSection title="Recommendations">
          <div className="grid gap-4 md:grid-cols-2">
            {report.recommendations.map((recommendation, index) => (
              <RecommendationCard key={index} recommendation={recommendation} />
            ))}
          </div>
        </ReportSection>
      )}

      <ReportSection title="Limitations">
        <ul className="space-y-2">
          {report.limitations.map((limitation, index) => (
            <li key={index} className="flex items-start gap-2 text-sm text-muted-foreground">
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
              {limitation}
            </li>
          ))}
        </ul>
      </ReportSection>
    </div>
  );
}

function ReportSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <Separator className="mb-6" />
      <h2 className="mb-4 text-lg font-semibold tracking-tight">{title}</h2>
      {children}
    </section>
  );
}
