import Link from "next/link";
import { FileText } from "lucide-react";

import { ReportView } from "@/components/reports/report-view";
import { Button } from "@/components/ui/button";

export function ReportTab({ analysisId }: { analysisId: string }) {
  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6 flex justify-end">
        <Button variant="outline" size="sm" asChild>
          <Link href={`/reports/${analysisId}`}>
            <FileText className="size-4" /> Open full report
          </Link>
        </Button>
      </div>
      <ReportView analysisId={analysisId} />
    </div>
  );
}
