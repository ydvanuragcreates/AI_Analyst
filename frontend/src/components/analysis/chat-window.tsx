"use client";

import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { AnalysisProgress } from "@/components/analysis/analysis-progress";
import { ApprovalPanel } from "@/components/analysis/approval-panel";
import { ChatInput } from "@/components/analysis/chat-input";
import { ChatMessage } from "@/components/analysis/chat-message";
import { SuggestedQuestions } from "@/components/analysis/suggested-questions";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { useCreateAnalysis } from "@/hooks/use-analyses";
import type { StreamEvent } from "@/hooks/use-analysis-stream";
import { ApiError } from "@/types/api";
import type { AnalysisDetailResponse } from "@/types/analysis";
import type { DatasetProfile } from "@/types/dataset";

export function ChatWindow({
  analysis,
  events,
  datasetProfile,
}: {
  analysis: AnalysisDetailResponse;
  events: StreamEvent[];
  datasetProfile?: DatasetProfile;
}) {
  const router = useRouter();
  const createAnalysis = useCreateAnalysis();

  const askFollowUp = (question: string) => {
    // No conversational thread continuity exists in the v1 job-based API
    // (see the project write-up's "known limitations") -- a follow-up
    // starts a fresh, independent analysis against the same dataset.
    createAnalysis.mutate(
      { dataset_id: analysis.dataset_id, mode: "qa", question },
      {
        onSuccess: (job) => router.push(`/analysis/${job.analysis_id}`),
        onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not start the analysis."),
      }
    );
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
        <ChatMessage role="user">{analysis.question || "Autonomously investigate this dataset."}</ChatMessage>

        {(analysis.status === "queued" || analysis.status === "running") && (
          <AnalysisProgress mode={analysis.mode} events={events} isTerminalFailure={false} isTerminalSuccess={false} />
        )}

        {analysis.status === "waiting_for_approval" && <ApprovalPanel analysisId={analysis.analysis_id} />}

        {analysis.status === "failed" && <ErrorState title="Analysis failed" error={analysis.error ? new ApiError(500, analysis.error) : undefined} />}

        {analysis.status === "cancelled" && (
          <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">This analysis was cancelled.</div>
        )}

        {analysis.status === "completed" && (
          <>
            <ChatMessage role="assistant">
              <p>{analysis.answer}</p>
              {analysis.confidence !== null && (
                <Badge variant="secondary" className="mt-2">
                  Confidence {Math.round(analysis.confidence * 100)}%
                </Badge>
              )}
            </ChatMessage>
            {datasetProfile && <SuggestedQuestions profile={datasetProfile} onSelect={askFollowUp} />}
          </>
        )}
      </div>

      {(analysis.status === "completed" || analysis.status === "failed") && (
        <div className="border-t p-4">
          <ChatInput onSubmit={askFollowUp} isSubmitting={createAnalysis.isPending} />
        </div>
      )}
    </div>
  );
}
