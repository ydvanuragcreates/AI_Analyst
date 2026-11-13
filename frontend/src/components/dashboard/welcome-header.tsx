"use client";

import { Plus, Sparkles, Upload } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { NewAnalysisDialog } from "@/components/analysis/new-analysis-dialog";
import { useHasMounted } from "@/hooks/use-has-mounted";

function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Decorative 3D bar trio: each bar is a gradient front face plus a
 * lighter skewed top face. */
function HeroBars() {
  const heights = ["h-16", "h-24", "h-36"];
  return (
    <div className="pointer-events-none absolute bottom-6 right-8 hidden items-end gap-5 md:flex" aria-hidden="true">
      {heights.map((height) => (
        <div key={height} className="relative w-11">
          <div className="absolute -top-2.5 left-0 h-2.5 w-11 origin-bottom-left -skew-x-[40deg] translate-x-1.5 rounded-t-sm bg-violet-300/90" />
          <div className={`${height} relative w-11 rounded-b-sm bg-gradient-to-b from-violet-400 via-violet-600 to-violet-800 shadow-[0_0_40px_-4px] shadow-violet-500/60`}>
            <div className="absolute inset-y-0 right-0 w-2.5 bg-black/20" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function WelcomeHeader() {
  // The greeting depends on the viewer's local hour, which can differ
  // between the server-rendered pass and the client -- gate it behind
  // useHasMounted() (no effect/setState involved) rather than risk a
  // hydration text mismatch.
  const mounted = useHasMounted();
  const greetingText = mounted ? greeting(new Date().getHours()) : "Welcome back";

  return (
    <div className="relative overflow-hidden rounded-2xl border border-primary/20 bg-gradient-to-br from-hero-from via-hero-from to-hero-to p-7 sm:p-9">
      {/* Glow + swoosh behind the bars */}
      <div className="pointer-events-none absolute -right-20 -top-24 size-96 rounded-full bg-violet-600/25 blur-3xl" aria-hidden="true" />
      <svg className="pointer-events-none absolute inset-y-0 right-0 hidden h-full w-2/3 md:block" viewBox="0 0 600 220" preserveAspectRatio="none" aria-hidden="true">
        <path d="M0 200 C 180 190, 300 120, 420 90 S 560 40, 600 20 L 600 220 L 0 220 Z" className="fill-violet-500/10" />
        <path d="M0 200 C 180 190, 300 120, 420 90 S 560 40, 600 20" className="stroke-violet-400/30" strokeWidth="1.5" fill="none" />
      </svg>
      <HeroBars />

      <div className="relative max-w-xl">
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Sparkles className="size-4 text-violet-400" /> AI Data Analyst
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-[2rem]">
          {greetingText} <span aria-hidden="true">👋</span>
        </h1>
        <p className="mt-2 text-muted-foreground">Turn your data into insights with AI-powered analysis.</p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button asChild className="h-10 rounded-lg px-4 shadow-lg shadow-primary/30">
            <Link href="/datasets">
              <Upload className="size-4" /> Upload dataset
            </Link>
          </Button>
          <NewAnalysisDialog
            trigger={
              <Button variant="outline" className="h-10 rounded-lg border-foreground/15 bg-background/40 px-4 backdrop-blur hover:bg-background/70">
                <Plus className="size-4" /> New analysis
              </Button>
            }
          />
        </div>
      </div>
    </div>
  );
}
