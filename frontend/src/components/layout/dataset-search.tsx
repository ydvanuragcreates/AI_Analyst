"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Input } from "@/components/ui/input";

/** Filters the real, already-loaded dataset list by name -- there is no
 * backend full-text search endpoint, so this deliberately scopes itself
 * to what /datasets can actually filter client-side rather than faking a
 * broader search experience. */
export function DatasetSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");

  return (
    <form
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        router.push(value.trim() ? `/datasets?q=${encodeURIComponent(value.trim())}` : "/datasets");
      }}
      className="relative hidden w-full max-w-xs sm:block"
    >
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Search datasets..."
        className="pl-8"
        aria-label="Search datasets"
      />
    </form>
  );
}
