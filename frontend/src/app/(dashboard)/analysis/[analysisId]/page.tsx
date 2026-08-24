"use client";

import { use } from "react";
import Link from "next/link";
import { ChevronLeft, Wifi, WifiOff } from "lucide-react";

import { AnalysisContextPanel } from "@/components/analysis/analysis-context-panel";
import { ChartsTab } from "@/components/analysis/charts-tab";
import { ChatWindow } from "@/components/analysis/chat-window";
import { DataQualityTab } from "@/components/analysis/data-quality-tab";
import { InsightsTab } from "@/components/analysis/insights-tab";
import { ReportTab } from "@/components/analysis/report-tab";
import { ErrorState } from "@/components/common/error-state";
import { StatusBadge } from "@/components/common/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAnalysis } from "@/hooks/use-analyses";
import { useAnalysisStream } from "@/hooks/use-analysis-stream";
import { useDataset, useDatasetProfile } from "@/hooks/use-datasets";

export default function AnalysisWorkspacePage({ params }: { params: Promise<{ analysisId: string }> }) {
  const { analysisId } = use(params);
  const { data: analysis, isLoading, isError, error, refetch } = useAnalysis(analysisId);
  const dataset = useDataset(analysis?.dataset_id);
  const profile = useDatasetProfile(analysis?.dataset_id);

  const streamEnabled = analysis?.status === "queued" || analysis?.status === "running";
  const stream = useAnalysisStream(analysisId, streamEnabled);

  if (isLoading) {
    return (
      <div className="space-y-4 px-6 py-8">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (isError || !analysis) {
    return (
      <div className="px-6 py-8">
        <ErrorState error={error} title="We couldn't load this analysis" onRetry={() => refetch()} />
      </div>
    );
  }

  const showDeepTabs = analysis.mode === "deep" && analysis.status === "completed";

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/analyses" className="text-muted-foreground hover:text-foreground" aria-label="Back to analyses">
            <ChevronLeft className="size-4" />
          </Link>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">Dataset: {dataset.data?.metadata.filename ?? "…"}</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {streamEnabled && (
            <span title={stream.connectionStatus === "open" ? "Connected" : "Connecting"} className="text-muted-foreground">
              {stream.connectionStatus === "open" ? <Wifi className="size-4" /> : <WifiOff className="size-4" />}
            </span>
          )}
          <StatusBadge status={analysis.status} />
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex flex-1 flex-col overflow-hidden">
          <Tabs defaultValue="chat" className="flex flex-1 flex-col overflow-hidden gap-0">
            <TabsList className="mx-4 mt-3 w-fit sm:mx-6">
              <TabsTrigger value="chat">Chat</TabsTrigger>
              {showDeepTabs && (
                <>
                  <TabsTrigger value="insights">Insights</TabsTrigger>
                  <TabsTrigger value="charts">Charts</TabsTrigger>
                  <TabsTrigger value="quality">Data Quality</TabsTrigger>
                  <TabsTrigger value="report">Report</TabsTrigger>
                </>
              )}
            </TabsList>

            <TabsContent value="chat" className="flex-1 overflow-hidden">
              <ChatWindow analysis={analysis} events={stream.events} datasetProfile={profile.data} />
            </TabsContent>

            {showDeepTabs && (
              <>
                <TabsContent value="insights" className="flex-1 overflow-y-auto">
                  <InsightsTab analysisId={analysisId} />
                </TabsContent>
                <TabsContent value="charts" className="flex-1 overflow-y-auto">
                  <ChartsTab analysisId={analysisId} />
                </TabsContent>
                <TabsContent value="quality" className="flex-1 overflow-y-auto">
                  <DataQualityTab analysisId={analysisId} />
                </TabsContent>
                <TabsContent value="report" className="flex-1 overflow-y-auto">
                  <ReportTab analysisId={analysisId} />
                </TabsContent>
              </>
            )}
          </Tabs>
        </div>

        <AnalysisContextPanel analysis={analysis} datasetName={dataset.data?.metadata.filename} profile={profile.data} />
      </div>
    </div>
  );
}
