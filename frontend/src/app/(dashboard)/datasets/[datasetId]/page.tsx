"use client";

import { use } from "react";
import { Sparkles } from "lucide-react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { NewAnalysisDialog } from "@/components/analysis/new-analysis-dialog";
import { DataQualityPanel } from "@/components/datasets/data-quality-panel";
import { DatasetPreviewTable } from "@/components/datasets/dataset-preview-table";
import { DatasetSchemaTable } from "@/components/datasets/dataset-schema-table";
import { DeleteDatasetButton } from "@/components/datasets/delete-dataset-button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useDataset, useDatasetProfile, useDatasetQuality } from "@/hooks/use-datasets";
import { formatDate, formatFileSize, formatNumber } from "@/lib/format";

export default function DatasetDetailPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = use(params);
  const { data: dataset, isLoading, isError, error, refetch } = useDataset(datasetId);
  const profileQuery = useDatasetProfile(datasetId);
  const qualityQuery = useDatasetQuality(datasetId);

  if (isLoading) {
    return (
      <div className="space-y-4 px-6 py-8 sm:px-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (isError || !dataset) {
    return (
      <div className="px-6 py-8 sm:px-8">
        <ErrorState error={error} title="We couldn't load this dataset" onRetry={() => refetch()} />
      </div>
    );
  }

  const { metadata } = dataset;

  return (
    <div>
      <PageHeader
        title={metadata.filename}
        description={`Version ${dataset.version} · Uploaded ${formatDate(metadata.uploaded_at)}`}
        actions={
          <>
            <NewAnalysisDialog
              datasetId={datasetId}
              trigger={
                <button className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-xs transition-colors hover:bg-primary/90">
                  <Sparkles className="size-4" /> Analyze dataset
                </button>
              }
            />
            <DeleteDatasetButton datasetId={datasetId} datasetName={metadata.filename} redirectAfter />
          </>
        }
      />

      <div className="px-6 pb-10 sm:px-8">
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Rows" value={formatNumber(metadata.row_count)} />
          <StatCard label="Columns" value={String(metadata.column_count)} />
          <StatCard label="File size" value={formatFileSize(metadata.file_size)} />
          <StatCard label="Type" value={metadata.file_type.toUpperCase()} />
        </div>

        <Tabs defaultValue="quality">
          <TabsList>
            <TabsTrigger value="quality">Data quality</TabsTrigger>
            <TabsTrigger value="schema">Schema</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
          </TabsList>

          <TabsContent value="quality" className="mt-4">
            {qualityQuery.isLoading && <Skeleton className="h-48 w-full" />}
            {qualityQuery.isError && <ErrorState error={qualityQuery.error} onRetry={() => qualityQuery.refetch()} />}
            {qualityQuery.data && (
              <DataQualityPanel issues={qualityQuery.data.issues} totalRowCount={qualityQuery.data.total_row_count} duplicateRowCount={qualityQuery.data.duplicate_row_count} />
            )}
          </TabsContent>

          <TabsContent value="schema" className="mt-4">
            {profileQuery.isLoading && <Skeleton className="h-48 w-full" />}
            {profileQuery.isError && <ErrorState error={profileQuery.error} onRetry={() => profileQuery.refetch()} />}
            {profileQuery.data && <DatasetSchemaTable profile={profileQuery.data} />}
          </TabsContent>

          <TabsContent value="preview" className="mt-4">
            <DatasetPreviewTable metadata={metadata} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="px-4 py-3">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      </CardContent>
    </Card>
  );
}
