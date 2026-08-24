import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { VisualizationSpec } from "@/types/deep-analysis";

/** Fallback renderer for the "table" visualization type, and for any
 * type this frontend doesn't have a dedicated chart for -- a table is
 * always a safe, readable way to show structured rows rather than
 * failing to render anything. */
export function TableView({ spec }: { spec: VisualizationSpec }) {
  if (spec.data.length === 0) {
    return <p className="text-sm text-muted-foreground">No data to display.</p>;
  }

  const columns = Object.keys(spec.data[0]);

  return (
    <ScrollArea className="max-h-96 rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead key={column} className="whitespace-nowrap capitalize">
                {column.replace(/_/g, " ")}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {spec.data.map((row, index) => (
            <TableRow key={index}>
              {columns.map((column) => (
                <TableCell key={column} className="whitespace-nowrap">
                  {formatCell(row[column])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </ScrollArea>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
