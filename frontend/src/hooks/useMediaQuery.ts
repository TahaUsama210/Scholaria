import { useCallback, useSyncExternalStore } from "react";

const supported = (): boolean =>
  typeof window !== "undefined" && typeof window.matchMedia === "function";

/** Subscribes to a CSS media query. Returns false where matchMedia is unavailable. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (!supported()) return () => undefined;
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => supported() && window.matchMedia(query).matches,
    () => false,
  );
}

/** Tailwind breakpoints used for layout decisions in JS. */
export const BREAKPOINTS = {
  md: "(min-width: 768px)",
  xl: "(min-width: 1280px)",
} as const;
