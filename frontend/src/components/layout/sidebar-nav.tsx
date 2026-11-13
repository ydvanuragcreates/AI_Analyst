"use client";

import { BarChart3, Database, FileText, Home, Settings, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: Home },
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/analyses", label: "Analyses", icon: BarChart3 },
  { href: "/reports", label: "Reports", icon: FileText },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

export function SidebarNav({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-1 px-3 py-2">
      {NAV_ITEMS.map((item) => {
        const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors",
              isActive
                ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-[inset_0_0_0_1px] shadow-primary/30"
                : "text-sidebar-foreground/75 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
              collapsed && "justify-center px-2"
            )}
            aria-current={isActive ? "page" : undefined}
            title={collapsed ? item.label : undefined}
          >
            <Icon className="size-[18px] shrink-0" aria-hidden="true" />
            {!collapsed && <span>{item.label}</span>}
          </Link>
        );
      })}
    </nav>
  );
}

export function SidebarLogo({ collapsed }: { collapsed?: boolean }) {
  return (
    <Link href="/dashboard" className={cn("flex h-16 items-center gap-2.5 px-5", collapsed && "justify-center px-2")}>
      <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-600 text-white shadow-lg shadow-violet-600/30">
        <Sparkles className="size-4" />
      </div>
      {!collapsed && <span className="text-[15px] font-semibold tracking-tight">AI Data Analyst</span>}
    </Link>
  );
}
