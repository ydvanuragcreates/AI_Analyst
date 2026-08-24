"use client";

import { useSyncExternalStore } from "react";

const emptySubscribe = () => () => {};

/** True only after the client has mounted -- the React-recommended way
 * to guard hydration-sensitive rendering (e.g. "what theme icon to show")
 * without the "setState synchronously in an effect" anti-pattern a plain
 * `useEffect(() => setMounted(true), [])` triggers. */
export function useHasMounted(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  );
}
