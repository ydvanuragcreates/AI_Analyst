"use client";

import { Upload } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { NewAnalysisDialog } from "@/components/analysis/new-analysis-dialog";
import { useCurrentUser } from "@/hooks/use-auth";
import { useHasMounted } from "@/hooks/use-has-mounted";

function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export function WelcomeHeader() {
  const { data: user } = useCurrentUser();
  // The greeting depends on the viewer's local hour, which can differ
  // between the server-rendered pass and the client -- gate it behind
  // useHasMounted() (no effect/setState involved) rather than risk a
  // hydration text mismatch.
  const mounted = useHasMounted();
  const greetingText = mounted ? greeting(new Date().getHours()) : "Welcome back";

  return (
    <div className="rounded-xl border bg-gradient-to-br from-card to-muted/40 p-6 sm:p-8">
      <h1 className="text-2xl font-semibold tracking-tight">
        {greetingText}
        {user?.email ? `, ${user.email.split("@")[0]}` : ""}
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">Analyze your data with natural language.</p>
      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="outline" asChild>
          <Link href="/datasets">
            <Upload className="size-4" /> Upload dataset
          </Link>
        </Button>
        <NewAnalysisDialog />
      </div>
    </div>
  );
}
