"use client";

import { Database } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { DatasetTable } from "@/components/datasets/dataset-table";
import { UploadDatasetDialog } from "@/components/datasets/upload-dataset-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useDatasets } from "@/hooks/use-datasets";

function DatasetsPageContent() {
  const { data, isLoading, isError, error, refetch } = useDatasets();
  const searchParams = useSearchParams();
  const query = searchParams.get("q")?.toLowerCase().trim() ?? "";

  const datasets = data?.datasets.filter((dataset) => !query || dataset.metadata.filename.toLowerCase().includes(query)) ?? [];

  return (
    <div>
      <PageHeader title="Datasets" description="Upload, inspect, and analyze your data." actions={<UploadDatasetDialog />} />
      <div className="px-6 pb-8 sm:px-8">
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        )}
        {isError && <ErrorState error={error} onRetry={() => refetch()} />}
        {!isLoading && !isError && data && data.datasets.length === 0 && (
          <EmptyState icon={Database} title="No datasets yet" description="Upload your first dataset to start analyzing it." action={<UploadDatasetDialog />} />
        )}
        {!isLoading && !isError && data && data.datasets.length > 0 && datasets.length === 0 && (
          <EmptyState icon={Database} title="No matching datasets" description={`Nothing matches "${query}".`} />
        )}
        {!isLoading && !isError && datasets.length > 0 && <DatasetTable datasets={datasets} />}
      </div>
    </div>
  );
}

export default function DatasetsPage() {
  return (
    <Suspense fallback={null}>
      <DatasetsPageContent />
    </Suspense>
  );
}
