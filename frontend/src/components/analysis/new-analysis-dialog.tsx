"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useCreateAnalysis } from "@/hooks/use-analyses";
import { useDatasets } from "@/hooks/use-datasets";
import { ApiError } from "@/types/api";
import type { AnalysisMode } from "@/types/analysis";

const formSchema = z
  .object({
    dataset_id: z.string().min(1, "Choose a dataset"),
    mode: z.custom<AnalysisMode>((value) => value === "qa" || value === "deep"),
    question: z.string().max(5000, "Keep it under 5000 characters"),
  })
  .refine((data) => data.mode === "deep" || data.question.trim().length > 0, {
    message: "Enter a question, or switch to autonomous investigation",
    path: ["question"],
  });

type FormValues = z.infer<typeof formSchema>;

export function NewAnalysisDialog({ datasetId, trigger }: { datasetId?: string; trigger?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { data: datasetsResponse } = useDatasets();
  const createAnalysis = useCreateAnalysis();

  const {
    control,
    register,
    handleSubmit,
    watch,
    formState: { errors },
    reset,
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { dataset_id: datasetId ?? "", mode: "qa", question: "" },
  });

  const mode = watch("mode");
  const datasets = datasetsResponse?.datasets ?? [];

  const onSubmit = (values: FormValues) => {
    createAnalysis.mutate(
      { dataset_id: values.dataset_id, mode: values.mode, question: values.question.trim() || undefined },
      {
        onSuccess: (job) => {
          setOpen(false);
          reset();
          router.push(`/analysis/${job.analysis_id}`);
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Could not start the analysis.");
        },
      }
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        {trigger ?? (
          <Button>
            <Sparkles className="size-4" /> New analysis
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <DialogHeader>
            <DialogTitle>New analysis</DialogTitle>
            <DialogDescription>Ask a question, or let the agent autonomously investigate the whole dataset.</DialogDescription>
          </DialogHeader>

          <div className="space-y-5 py-4">
            <div className="space-y-2">
              <Label htmlFor="dataset_id">Dataset</Label>
              <Controller
                control={control}
                name="dataset_id"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} disabled={Boolean(datasetId)}>
                    <SelectTrigger id="dataset_id" className="w-full">
                      <SelectValue placeholder="Choose a dataset" />
                    </SelectTrigger>
                    <SelectContent>
                      {datasets.map((dataset) => (
                        <SelectItem key={dataset.dataset_id} value={dataset.dataset_id}>
                          {dataset.metadata.filename}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.dataset_id && <p className="text-sm text-destructive">{errors.dataset_id.message}</p>}
            </div>

            <div className="space-y-2">
              <Label>Analysis type</Label>
              <Controller
                control={control}
                name="mode"
                render={({ field }) => (
                  <div className="grid grid-cols-2 gap-2">
                    <ModeOption
                      selected={field.value === "qa"}
                      title="Ask a question"
                      description="Get a direct, grounded answer."
                      onClick={() => field.onChange("qa")}
                    />
                    <ModeOption
                      selected={field.value === "deep"}
                      title="Autonomous investigation"
                      description="Profile, find insights, build a report."
                      onClick={() => field.onChange("deep")}
                    />
                  </div>
                )}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="question">{mode === "deep" ? "Focus (optional)" : "Your question"}</Label>
              <Textarea
                id="question"
                rows={3}
                placeholder={mode === "deep" ? "e.g. Focus on regional performance" : "e.g. Why did revenue decline in July?"}
                {...register("question")}
              />
              {errors.question && <p className="text-sm text-destructive">{errors.question.message}</p>}
            </div>
          </div>

          <DialogFooter>
            <Button type="submit" disabled={createAnalysis.isPending}>
              {createAnalysis.isPending && <Loader2 className="size-4 animate-spin" />}
              Start analysis
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ModeOption({ selected, title, description, onClick }: { selected: boolean; title: string; description: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`rounded-lg border p-3 text-left text-sm transition-colors ${
        selected ? "border-primary bg-primary/5 ring-1 ring-primary" : "hover:bg-accent"
      }`}
    >
      <div className="font-medium">{title}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{description}</div>
    </button>
  );
}
