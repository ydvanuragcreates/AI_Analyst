"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileSpreadsheet, Loader2, UploadCloud, XCircle } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { datasetsApi, type UploadPhase } from "@/lib/api/datasets";
import { queryKeys } from "@/lib/query-keys";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ApiError } from "@/types/api";

const ACCEPTED_EXTENSIONS = [".csv", ".xlsx", ".json"];
const ACCEPTED_MIME = ".csv,.xlsx,.json,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

type UploadState = { status: "idle" } | { status: UploadPhase; file: File; percent: number } | { status: "ready"; file: File } | { status: "error"; file: File; message: string };

export function DatasetUploader({ onUploaded }: { onUploaded?: (datasetId: string) => void }) {
  const [state, setState] = useState<UploadState>({ status: "idle" });
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const startUpload = useCallback(
    (file: File) => {
      const extension = `.${file.name.split(".").pop()?.toLowerCase()}`;
      if (!ACCEPTED_EXTENSIONS.includes(extension)) {
        setState({ status: "error", file, message: "Unsupported file type. Use CSV, XLSX, or JSON." });
        return;
      }

      setState({ status: "uploading", file, percent: 0 });
      datasetsApi
        .uploadWithProgress(file, (phase, percent) => setState({ status: phase, file, percent }))
        .then((response) => {
          setState({ status: "ready", file });
          queryClient.invalidateQueries({ queryKey: queryKeys.datasets() });
          onUploaded?.(response.dataset_id);
          toast.success(`${file.name} uploaded successfully.`);
        })
        .catch((error: unknown) => {
          const message = error instanceof ApiError ? error.message : "Failed to upload the dataset.";
          setState({ status: "error", file, message });
        });
    },
    [onUploaded, queryClient]
  );

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      const file = event.dataTransfer.files[0];
      if (file) startUpload(file);
    },
    [startUpload]
  );

  if (state.status === "uploading" || state.status === "processing") {
    return (
      <div className="rounded-xl border border-dashed p-8 text-center">
        <Loader2 className="mx-auto mb-3 size-8 animate-spin text-muted-foreground" />
        <p className="text-sm font-medium">{state.status === "uploading" ? `Uploading ${state.file.name}...` : "Processing your dataset..."}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {state.status === "uploading" ? "Sending file to the server" : "Validating, parsing, and profiling — this can take a moment for larger files"}
        </p>
        <Progress value={state.status === "uploading" ? state.percent : 100} className="mx-auto mt-4 max-w-xs" />
      </div>
    );
  }

  if (state.status === "ready") {
    return (
      <div className="flex items-center justify-between rounded-xl border p-6">
        <div className="flex items-center gap-3">
          <CheckCircle2 className="size-6 text-emerald-500" />
          <div>
            <p className="text-sm font-medium">{state.file.name}</p>
            <p className="text-xs text-muted-foreground">{formatFileSize(state.file.size)} · ready to analyze</p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={() => setState({ status: "idle" })}>
          Upload another
        </Button>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-6 text-center">
        <XCircle className="mx-auto mb-2 size-8 text-destructive" />
        <p className="text-sm font-medium">Upload failed</p>
        <p className="mt-1 text-xs text-muted-foreground">{state.message}</p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => setState({ status: "idle" })}>
          Try again
        </Button>
      </div>
    );
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-10 text-center transition-colors",
        isDragging ? "border-primary bg-primary/5" : "border-border"
      )}
    >
      <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-muted">
        {isDragging ? <FileSpreadsheet className="size-6 text-muted-foreground" /> : <UploadCloud className="size-6 text-muted-foreground" />}
      </div>
      <p className="text-sm font-medium">Upload your dataset</p>
      <p className="mt-1 text-sm text-muted-foreground">Drag &amp; drop a file here, or</p>
      <Button variant="outline" size="sm" className="mt-4" onClick={() => inputRef.current?.click()}>
        Choose file
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_MIME}
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) startUpload(file);
          event.target.value = "";
        }}
      />
      <p className="mt-4 text-xs text-muted-foreground">CSV · XLSX · JSON</p>
    </div>
  );
}
