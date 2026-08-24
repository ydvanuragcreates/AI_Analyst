import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatNumber } from "@/lib/format";
import type { DatasetProfile } from "@/types/dataset";

const KIND_LABEL: Record<string, string> = {
  numeric: "Numeric",
  categorical: "Categorical",
  date: "Date",
  boolean: "Boolean",
  other: "Other",
};

export function DatasetSchemaTable({ profile }: { profile: DatasetProfile }) {
  return (
    <div className="overflow-x-auto rounded-xl border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Column</TableHead>
            <TableHead>Type</TableHead>
            <TableHead className="text-right">Missing</TableHead>
            <TableHead className="text-right">Unique</TableHead>
            <TableHead>Flags</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {profile.columns.map((column) => (
            <TableRow key={column.name}>
              <TableCell className="font-mono text-sm font-medium">{column.name}</TableCell>
              <TableCell>
                <Badge variant="outline">{KIND_LABEL[column.kind] ?? column.kind}</Badge>
                <span className="ml-2 text-xs text-muted-foreground">{column.dtype}</span>
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatNumber(column.missing_count)}
                {column.missing_percentage > 0 && <span className="ml-1 text-xs text-muted-foreground">({column.missing_percentage.toFixed(1)}%)</span>}
              </TableCell>
              <TableCell className="text-right tabular-nums">{formatNumber(column.distinct_count)}</TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {column.is_likely_identifier && (
                    <Badge variant="secondary" className="text-xs">
                      Identifier
                    </Badge>
                  )}
                  {column.is_high_cardinality && (
                    <Badge variant="secondary" className="text-xs">
                      High cardinality
                    </Badge>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
