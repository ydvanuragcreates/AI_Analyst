import { BarChart3, User } from "lucide-react";

import { cn } from "@/lib/utils";

export function ChatMessage({ role, children }: { role: "user" | "assistant"; children: React.ReactNode }) {
  const isUser = role === "user";
  return (
    <div className={cn("flex items-start gap-3", isUser && "flex-row-reverse")}>
      <div
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-full",
          isUser ? "bg-secondary text-secondary-foreground" : "bg-primary text-primary-foreground"
        )}
      >
        {isUser ? <User className="size-4" /> : <BarChart3 className="size-4" />}
      </div>
      <div className={cn("max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed", isUser ? "bg-secondary text-secondary-foreground" : "bg-muted")}>
        {children}
      </div>
    </div>
  );
}
