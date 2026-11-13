"use client";

import { DatasetsSummary } from "@/components/dashboard/datasets-summary";
import { RecentAnalysesList } from "@/components/dashboard/recent-analyses-list";
import { StatsRow } from "@/components/dashboard/stats-row";
import { WelcomeHeader } from "@/components/dashboard/welcome-header";

export default function DashboardPage() {
  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <WelcomeHeader />
      <StatsRow />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <div className="min-w-0 lg:col-span-3">
          <RecentAnalysesList />
        </div>
        <div className="min-w-0 lg:col-span-2">
          <DatasetsSummary />
        </div>
      </div>
    </div>
  );
}
