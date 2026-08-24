import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/types/api";

/** A useful, non-technical error surface -- never a stack trace. Backend
 * error messages (ApiError.message) are already written to be safe/
 * user-facing (see backend/app/core/errors.py), so they're shown as-is;
 * anything else falls back to a generic message. */
export function ErrorState({ error, title = "Something went wrong", onRetry }: { error?: unknown; title?: string; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : error instanceof Error ? "An unexpected error occurred." : undefined;

  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-16 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="size-6 text-destructive" />
      </div>
      <h3 className="text-sm font-semibold">{title}</h3>
      {message && <p className="mt-1 max-w-sm text-sm text-muted-foreground">{message}</p>}
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-5" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
