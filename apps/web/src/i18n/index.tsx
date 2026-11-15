import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { messages, type Language, type MessageKey } from "./messages";

const STORAGE_KEY = "hup-lang";

function storedLanguage(): Language | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "en" || value === "es" ? value : null;
  } catch {
    return null; // localStorage can be blocked; the page still renders.
  }
}

function initialLanguage(): Language {
  if (storedLanguage()) return storedLanguage() as Language;
  return navigator.language.toLowerCase().startsWith("es") ? "es" : "en";
}

function text(source: unknown, key: string): string {
  const value = key
    .split(".")
    .reduce<unknown>((node, part) => (typeof node === "object" && node !== null ? (node as Record<string, unknown>)[part] : undefined), source);
  if (typeof value !== "string") throw new Error(`Missing message: ${key}`);
  return value;
}

type I18n = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: MessageKey) => string;
};

const I18nContext = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(initialLanguage);

  useEffect(() => {
    document.documentElement.lang = language;
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // Remembering the language is optional; rendering is not.
    }
  }, [language]);

  const t = useCallback((key: MessageKey) => text(messages[language], key), [language]);
  const value = useMemo(() => ({ language, setLanguage, t }), [language, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}
