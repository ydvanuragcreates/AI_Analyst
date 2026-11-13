"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SidebarLogo, SidebarNav } from "@/components/layout/sidebar-nav";
import { StorageUsage } from "@/components/layout/storage-usage";
import { useUIStore } from "@/stores/ui-store";
import { cn } from "@/lib/utils";

/** Desktop sidebar only -- see components/layout/mobile-sidebar.tsx for
 * the small-screen Sheet-based drawer variant. */
export function Sidebar() {
  const collapsed = useUIStore((state) => state.sidebarCollapsed);
  const toggleCollapsed = useUIStore((state) => state.toggleSidebarCollapsed);

  return (
    <aside
      className={cn(
        "hidden shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 md:flex",
        collapsed ? "w-[72px]" : "w-64"
      )}
    >
      <SidebarLogo collapsed={collapsed} />
      <div className="flex-1 overflow-y-auto">
        <SidebarNav collapsed={collapsed} />
      </div>
      {!collapsed && <StorageUsage />}
      <div className="border-t border-sidebar-border p-2">
        <Button
          variant="ghost"
          size="icon"
          className="w-full justify-center text-muted-foreground"
          onClick={toggleCollapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </Button>
      </div>
    </aside>
  );
}
