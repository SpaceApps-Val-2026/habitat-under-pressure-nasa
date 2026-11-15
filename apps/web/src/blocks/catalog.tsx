import { createContext, useCallback, useContext, type ReactNode } from "react";

import type { catalog } from "../contracts";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";

type Catalog = catalog.CatalogSourcesVariablesAndOperations;
type Named = { id: string; name: string | Record<string, string | undefined> };

// The catalogue as the report and the panel fetched it, so every block names variables and
// sources the same way without threading it through each prop. Absent, blocks fall back to the
// copy they carry and, last, to the id.
const CatalogContext = createContext<Partial<Pick<Catalog, "sources" | "variables">> | null>(null);

export function CatalogProvider({
  value,
  children,
}: {
  value: Partial<Pick<Catalog, "sources" | "variables">> | null;
  children: ReactNode;
}) {
  return <CatalogContext.Provider value={value}>{children}</CatalogContext.Provider>;
}

export function useCatalog() {
  return useContext(CatalogContext);
}

function nameOf(entry: Named | undefined, language: string): string | undefined {
  if (!entry) return undefined;
  if (typeof entry.name === "string") return entry.name;
  return entry.name[language] ?? entry.name.en ?? undefined;
}

// A variable's human name: the catalogue first, then the copy in the locales, then the id.
export function variableName(
  id: string | undefined,
  language: string,
  t: (key: MessageKey) => string,
  variables: Named[] | undefined,
  fallback: Record<string, MessageKey>,
): string {
  if (!id) return "";
  const named = nameOf(variables?.find((item) => item.id === id), language);
  if (named) return named;
  return fallback[id] ? t(fallback[id]) : id;
}

export function useVariableName(fallback: Record<string, MessageKey>) {
  const { t, language } = useI18n();
  const catalogValue = useCatalog();
  const variables = catalogValue?.variables as Named[] | undefined;
  return useCallback(
    (id: string | undefined) => variableName(id, language, t, variables, fallback),
    [language, t, variables, fallback],
  );
}
