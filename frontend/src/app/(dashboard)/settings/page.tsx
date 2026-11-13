"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { PageHeader } from "@/components/common/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useHasMounted } from "@/hooks/use-has-mounted";
import { cn } from "@/lib/utils";

const THEME_OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const mounted = useHasMounted();

  return (
    <div>
      <PageHeader title="Settings" description="Manage your preferences." />
      <div className="max-w-2xl space-y-6 px-6 pb-10 sm:px-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Appearance</CardTitle>
            <CardDescription>Choose how AI Data Analyst looks on this device.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex gap-2">
              {THEME_OPTIONS.map((option) => {
                const Icon = option.icon;
                const isActive = mounted && theme === option.value;
                return (
                  <button
                    key={option.value}
                    onClick={() => setTheme(option.value)}
                    className={cn(
                      "flex flex-1 flex-col items-center gap-2 rounded-lg border p-4 text-sm transition-colors",
                      isActive ? "border-primary bg-primary/5 ring-1 ring-primary" : "hover:bg-accent"
                    )}
                    aria-pressed={isActive}
                  >
                    <Icon className="size-5" />
                    {option.label}
                  </button>
                );
              })}
            </div>
          </CardContent>
        </Card>

      </div>
    </div>
  );
}
