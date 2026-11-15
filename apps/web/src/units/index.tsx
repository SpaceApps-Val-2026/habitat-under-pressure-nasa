import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type Units = "metric" | "imperial";

// Only an explicit choice is remembered. The former key ("hup-units") was written on first visit from
// the browser locale, so an en-US browser kept Fahrenheit in a Spanish interface; it is ignored.
const STORAGE_KEY = "hup-units-choice";

function storedUnits(): Units | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "metric" || value === "imperial" ? value : null;
  } catch {
    return null;
  }
}

// Metric for everyone, in every language, until the person picks imperial in the options menu.
function initialUnits(): Units {
  return storedUnits() ?? "metric";
}

type UnitsContextValue = { units: Units; setUnits: (units: Units) => void };

const UnitsContext = createContext<UnitsContextValue | null>(null);

export function UnitsProvider({ children }: { children: ReactNode }) {
  const [units, setState] = useState<Units>(initialUnits);

  const setUnits = useCallback((next: Units) => {
    setState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Remembering the preference is optional; rendering is not.
    }
  }, []);

  const value = useMemo(() => ({ units, setUnits }), [units, setUnits]);
  return <UnitsContext.Provider value={value}>{children}</UnitsContext.Provider>;
}

export function useUnits(): UnitsContextValue {
  const value = useContext(UnitsContext);
  if (!value) throw new Error("useUnits must be used inside UnitsProvider");
  return value;
}
