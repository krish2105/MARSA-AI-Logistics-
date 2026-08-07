"use client";

import { useSyncExternalStore } from "react";

// Module-level constants: `subscribe` must be referentially stable or
// useSyncExternalStore re-subscribes on every render.
const subscribe = () => () => {};
const getClientSnapshot = () => true;
const getServerSnapshot = () => false;

/**
 * `true` once the client has hydrated, `false` during SSR and the first
 * client render.
 *
 * Theme-aware UI needs this because the real theme is unknowable on the
 * server. The usual `useState(false)` + `useEffect(() => setMounted(true))`
 * does the same job but trips React 19's `set-state-in-effect` rule and costs
 * a cascading render; `useSyncExternalStore` is the sanctioned way to read a
 * value that legitimately differs between server and client.
 */
export function useIsHydrated(): boolean {
  return useSyncExternalStore(subscribe, getClientSnapshot, getServerSnapshot);
}
