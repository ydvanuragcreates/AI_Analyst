"use client";

import { CheckCircle2, Sparkles } from "lucide-react";
import Link from "next/link";

import { NewAnalysisDialog } from "@/components/analysis/new-analysis-dialog";
import { DeleteDatasetButton } from "@/components/datasets/delete-dataset-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatFileSize, formatNumber } from "@/lib/format";
import type { DatasetV1Response } from "@/types/dataset";

export function DatasetTable({ datasets }: { datasets: DatasetV1Response[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Type</TableHead>
            <TableHead className="text-right">Rows</TableHead>
            <TableHead className="text-right">Columns</TableHead>
            <TableHead>Version</TableHead>
            <TableHead>Uploaded</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {datasets.map((dataset) => (
            <TableRow key={dataset.dataset_id}>
              <TableCell className="font-medium">
                <Link href={`/datasets/${dataset.dataset_id}`} className="hover:underline">
                  {dataset.metadata.filename}
                </Link>
                <div className="text-xs text-muted-foreground">{formatFileSize(dataset.metadata.file_size)}</div>
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="uppercase">
                  {dataset.metadata.file_type}
                </Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">{formatNumber(dataset.metadata.row_count)}</TableCell>
              <TableCell className="text-right tabular-nums">{dataset.metadata.column_count}</TableCell>
              <TableCell>v{dataset.version}</TableCell>
              <TableCell className="text-muted-foreground">{formatDate(dataset.metadata.uploaded_at)}</TableCell>
              <TableCell>
                <Badge variant="secondary" className="gap-1 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="size-3" /> Ready
                </Badge>
              </TableCell>
              <TableCell className="text-right">
                <div className="flex items-center justify-end gap-1">
                  <NewAnalysisDialog
                    datasetId={dataset.dataset_id}
                    trigger={
                      <Button variant="ghost" size="sm">
                        <Sparkles className="size-4" /> Analyze
                      </Button>
                    }
                  />
                  <DeleteDatasetButton datasetId={dataset.dataset_id} datasetName={dataset.metadata.filename} />
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
