"use client";

import { Menu } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DatasetSearch } from "@/components/layout/dataset-search";
import { ThemeSwitch } from "@/components/layout/theme-switch";
import { useUIStore } from "@/stores/ui-store";

export function TopNav() {
  const setMobileSidebarOpen = useUIStore((state) => state.setMobileSidebarOpen);

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur sm:px-6">
      <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileSidebarOpen(true)} aria-label="Open navigation">
        <Menu className="size-5" />
      </Button>

      <div className="flex flex-1 items-center justify-between gap-4">
        <DatasetSearch />
        <div className="flex items-center gap-1">
          <ThemeSwitch />
        </div>
      </div>
    </header>
  );
}
