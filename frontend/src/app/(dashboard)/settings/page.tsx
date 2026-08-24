"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { useCurrentUser, useLogout } from "@/hooks/use-auth";
import { useHasMounted } from "@/hooks/use-has-mounted";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";

const THEME_OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

export default function SettingsPage() {
  const { data: user, isLoading } = useCurrentUser();
  const { theme, setTheme } = useTheme();
  const logout = useLogout();
  const mounted = useHasMounted();

  return (
    <div>
      <PageHeader title="Settings" description="Manage your profile and preferences." />
      <div className="max-w-2xl space-y-6 px-6 pb-10 sm:px-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Profile</CardTitle>
            <CardDescription>Your account details.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label className="text-xs text-muted-foreground">Email</Label>
              <p className="mt-1 text-sm font-medium">{isLoading ? "Loading…" : user?.email}</p>
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">Member since</Label>
              <p className="mt-1 text-sm font-medium">{user?.created_at ? formatDate(user.created_at) : "—"}</p>
            </div>
          </CardContent>
        </Card>

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

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Session</CardTitle>
          </CardHeader>
          <CardContent>
            <Separator className="mb-4" />
            <Button variant="destructive" onClick={logout}>
              Log out
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
