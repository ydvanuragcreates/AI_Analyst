"use client";

import { DatasetsSummary } from "@/components/dashboard/datasets-summary";
import { RecentAnalysesList } from "@/components/dashboard/recent-analyses-list";
import { WelcomeHeader } from "@/components/dashboard/welcome-header";

export default function DashboardPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-8 sm:px-8">
      <WelcomeHeader />
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <RecentAnalysesList />
        </div>
        <div>
          <DatasetsSummary />
        </div>
      </div>
    </div>
  );
}
