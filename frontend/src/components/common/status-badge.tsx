import { CheckCircle2, CircleDashed, Clock, Loader2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AnalysisStatus } from "@/types/analysis";

const STATUS_CONFIG: Record<AnalysisStatus, { label: string; icon: typeof CheckCircle2; className: string }> = {
  queued: { label: "Queued", icon: CircleDashed, className: "bg-muted text-muted-foreground" },
  running: { label: "Running", icon: Loader2, className: "bg-blue-500/10 text-blue-600 dark:text-blue-400" },
  waiting_for_approval: { label: "Needs approval", icon: Clock, className: "bg-amber-500/10 text-amber-600 dark:text-amber-400" },
  completed: { label: "Completed", icon: CheckCircle2, className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" },
  failed: { label: "Failed", icon: XCircle, className: "bg-destructive/10 text-destructive" },
  cancelled: { label: "Cancelled", icon: XCircle, className: "bg-muted text-muted-foreground" },
};

export function StatusBadge({ status, className }: { status: AnalysisStatus; className?: string }) {
  const config = STATUS_CONFIG[status];
  const Icon = config.icon;
  return (
    <Badge variant="secondary" className={cn("gap-1 font-medium", config.className, className)}>
      <Icon className={cn("size-3", status === "running" && "animate-spin")} aria-hidden="true" />
      {config.label}
    </Badge>
  );
}
