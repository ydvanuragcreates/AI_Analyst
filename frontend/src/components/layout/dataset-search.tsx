"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

/** Filters the real, already-loaded dataset list by name -- there is no
 * backend full-text search endpoint, so this deliberately scopes itself
 * to what /datasets can actually filter client-side rather than faking a
 * broader search experience. Press "/" anywhere to focus it. */
export function DatasetSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
      event.preventDefault();
      inputRef.current?.focus();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <form
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        router.push(value.trim() ? `/datasets?q=${encodeURIComponent(value.trim())}` : "/datasets");
      }}
      className="relative hidden w-full max-w-lg sm:block"
    >
      <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <input
        ref={inputRef}
        type="search"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Search datasets..."
        aria-label="Search datasets"
        className="h-10 w-full rounded-xl border bg-card/60 pl-11 pr-12 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/50 focus:ring-2 focus:ring-primary/20 [&::-webkit-search-cancel-button]:hidden"
      />
      <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded-md border bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
        /
      </kbd>
    </form>
  );
}
