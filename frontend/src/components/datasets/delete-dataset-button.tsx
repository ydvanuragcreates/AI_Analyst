"use client";

import { Loader2, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useDeleteDataset } from "@/hooks/use-datasets";
import { ApiError } from "@/types/api";

export function DeleteDatasetButton({ datasetId, datasetName, redirectAfter }: { datasetId: string; datasetName: string; redirectAfter?: boolean }) {
  const deleteDataset = useDeleteDataset();
  const router = useRouter();

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-destructive" aria-label={`Delete ${datasetName}`}>
          <Trash2 className="size-4" />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete &ldquo;{datasetName}&rdquo;?</AlertDialogTitle>
          <AlertDialogDescription>
            This can&apos;t be undone. Datasets with existing analyses can&apos;t be deleted — those analyses must remain reproducible.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-white hover:bg-destructive/90"
            disabled={deleteDataset.isPending}
            onClick={(event) => {
              event.preventDefault();
              deleteDataset.mutate(datasetId, {
                onSuccess: () => {
                  toast.success(`${datasetName} deleted.`);
                  if (redirectAfter) router.push("/datasets");
                },
                onError: (error) => {
                  toast.error(error instanceof ApiError ? error.message : "Could not delete this dataset.");
                },
              });
            }}
          >
            {deleteDataset.isPending && <Loader2 className="size-4 animate-spin" />}
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
