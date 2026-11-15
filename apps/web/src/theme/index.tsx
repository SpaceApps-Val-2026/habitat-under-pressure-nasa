import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";

export type Theme = "light" | "dark";
// What the person chose: a theme, or to follow the system's.
export type ThemeMode = Theme | "system";

const STORAGE_KEY = "hup-theme";

function storedMode(): ThemeMode | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : null;
  } catch {
    return null; // localStorage can be blocked; the page still renders.
  }
}

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

type ThemeContextValue = {
  /** The theme on screen. */
  theme: Theme;
  /** What the person chose. */
  mode: ThemeMode;
  setTheme: (mode: ThemeMode) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Without a stored choice the page follows the system, as before, but only a choice is written.
  const [mode, setMode] = useState<ThemeMode>(() => storedMode() ?? "system");
  const [system, setSystem] = useState<Theme>(systemTheme);
  const theme = mode === "system" ? system : mode;

  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!query) return;
    const onChange = () => setSystem(query.matches ? "dark" : "light");
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const value = useMemo(
    () => ({
      theme,
      mode,
      setTheme: (next: ThemeMode) => {
        setMode(next);
        try {
          localStorage.setItem(STORAGE_KEY, next);
        } catch {
          // Remembering the theme is optional; rendering is not.
        }
      },
    }),
    [theme, mode],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
