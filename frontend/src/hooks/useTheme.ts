import { useCallback, useEffect, useState } from "react";

import { useMediaQuery } from "./useMediaQuery";

export type Theme = "light" | "dark";
const STORAGE_KEY = "scholaria-theme"; // also read by the boot script in index.html

function readStored(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** Follows the OS preference until the user picks a theme explicitly. */
export function useTheme() {
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const [chosen, setChosen] = useState<Theme | null>(readStored);
  const theme: Theme = chosen ?? (prefersDark ? "dark" : "light");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setChosen(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // storage unavailable (private mode): the choice lasts for this session only
    }
  }, [theme]);

  return { theme, toggle };
}
