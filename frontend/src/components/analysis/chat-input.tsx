"use client";

import { ArrowUp, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function ChatInput({ onSubmit, isSubmitting, placeholder = "Ask a follow-up..." }: { onSubmit: (question: string) => void; isSubmitting: boolean; placeholder?: string }) {
  const [value, setValue] = useState("");

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || isSubmitting) return;
    onSubmit(trimmed);
    setValue("");
  };

  return (
    <div className="flex items-end gap-2 rounded-xl border bg-background p-2 shadow-sm">
      <Textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder}
        rows={1}
        className="max-h-32 min-h-9 resize-none border-0 shadow-none focus-visible:ring-0"
        aria-label="Follow-up question"
      />
      <Button size="icon" className="shrink-0 rounded-full" onClick={submit} disabled={isSubmitting || !value.trim()} aria-label="Send">
        {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
      </Button>
    </div>
  );
}
