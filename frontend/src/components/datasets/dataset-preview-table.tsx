import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { DatasetMetadata } from "@/types/dataset";

/**
 * Renders the bounded preview the backend already returns with dataset
 * metadata (DatasetMetadata.preview, capped server-side at
 * Settings.dataset_preview_rows -- see services/dataset_service.py).
 * There is no authenticated, owned endpoint for paginated raw rows
 * beyond this preview (the legacy /api/analysis/{id}/sample route
 * predates Phase 9 auth and has no ownership check, so it's
 * deliberately not used here) -- this preview is what "don't load the
 * entire dataset into the browser" resolves to today.
 */
export function DatasetPreviewTable({ metadata }: { metadata: DatasetMetadata }) {
  if (metadata.preview.length === 0) {
    return <p className="text-sm text-muted-foreground">No preview rows available.</p>;
  }

  const columns = metadata.columns.map((column) => column.name);

  return (
    <div>
      <div className="overflow-x-auto rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column} className="font-mono text-xs">
                  {column}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {metadata.preview.map((row, index) => (
              <TableRow key={index}>
                {columns.map((column) => (
                  <TableCell key={column} className="whitespace-nowrap text-sm">
                    {formatCell(row[column])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Showing the first {metadata.preview.length} of {metadata.row_count.toLocaleString("en-US")} rows.
      </p>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
