"use client";

import { Loader2, ShieldQuestion } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useApproveAnalysis } from "@/hooks/use-analyses";
import { ApiError } from "@/types/api";

/** Phase 8's human-in-the-loop gate (Analysis.status === "waiting_for_approval")
 * -- a low-confidence, expensive, or repeatedly-disputed finding pauses
 * here until a human explicitly approves or rejects it (with feedback
 * fed back into the next attempt). See backend/app/agent/reviewer.py. */
export function ApprovalPanel({ analysisId }: { analysisId: string }) {
  const [feedback, setFeedback] = useState("");
  const [showRejectForm, setShowRejectForm] = useState(false);
  const approve = useApproveAnalysis(analysisId);

  const submit = (approved: boolean) => {
    approve.mutate(
      { approved, feedback: approved ? undefined : feedback.trim() || undefined },
      {
        onSuccess: () => toast.success(approved ? "Approved — resuming analysis." : "Rejected — the analyst will try again."),
        onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not record your decision."),
      }
    );
  };

  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5">
      <div className="flex items-start gap-3">
        <ShieldQuestion className="mt-0.5 size-5 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="flex-1">
          <h3 className="text-sm font-semibold">This analysis needs your approval</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            The reviewer flagged this result as low-confidence, costly, or repeatedly disputed. Approve to continue, or reject with feedback to have it
            reconsidered.
          </p>

          {showRejectForm && (
            <Textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder="What should be reconsidered?"
              className="mt-3"
              rows={2}
            />
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => submit(true)} disabled={approve.isPending}>
              {approve.isPending && <Loader2 className="size-4 animate-spin" />}
              Approve
            </Button>
            {showRejectForm ? (
              <Button size="sm" variant="outline" onClick={() => submit(false)} disabled={approve.isPending}>
                Submit rejection
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => setShowRejectForm(true)} disabled={approve.isPending}>
                Reject
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
