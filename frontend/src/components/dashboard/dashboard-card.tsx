import { ArrowRight, type LucideIcon } from "lucide-react";
import Link from "next/link";

/** Shared shell for the dashboard's list panels: icon tile, title,
 * subtitle and a "View all" link. */
export function DashboardCard({
  icon: Icon,
  title,
  subtitle,
  href,
  children,
}: {
  icon: LucideIcon;
  title: string;
  subtitle: string;
  href: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border bg-card">
      <div className="flex items-start justify-between gap-4 p-5 pb-3">
        <div className="flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl bg-primary/15 text-primary">
            <Icon className="size-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
            <p className="text-sm text-muted-foreground">{subtitle}</p>
          </div>
        </div>
        <Link href={href} className="flex shrink-0 items-center gap-1 pt-1 text-sm font-medium text-primary hover:underline">
          View all <ArrowRight className="size-4" />
        </Link>
      </div>
      <div className="px-3 pb-3">{children}</div>
    </section>
  );
}
