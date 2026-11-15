import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

import { useI18n } from "../i18n";
import { useTheme, type ThemeMode } from "../theme";
import { Segmented } from "../ui/Segmented";
import { useUnits, type Units } from "../units";

import { usePresence } from "../ui/presence";

import "./options.css";

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className="size-16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

const SUN = (
  <Icon>
    <circle cx="10" cy="10" r="3.5" />
    <path d="M10 2v2M10 16v2M2 10h2M16 10h2M4.3 4.3l1.4 1.4M14.3 14.3l1.4 1.4M4.3 15.7l1.4-1.4M14.3 5.7l1.4-1.4" />
  </Icon>
);
const MOON = (
  <Icon>
    <path d="M16 12.5A6.5 6.5 0 0 1 7.5 4a6.5 6.5 0 1 0 8.5 8.5z" />
  </Icon>
);
const SCREEN = (
  <Icon>
    <rect x="2.5" y="3.5" width="15" height="10" rx="1.5" />
    <path d="M7 17h6M10 13.5V17" />
  </Icon>
);
const THERMOMETER = (
  <Icon>
    <path d="M8 11.5V4a2 2 0 1 1 4 0v7.5a3.5 3.5 0 1 1-4 0z" />
    <path d="M10 8v5" />
  </Icon>
);

// One setting: a mono label, what it does in one line, and its control.
function Setting({ id, label, note, children }: { id: string; label: string; note: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-8 border-t border-line pt-16">
      <h3 id={id} className="label text-muted">
        {label}
      </h3>
      {children}
      <p className="small text-muted">{note}</p>
    </section>
  );
}

// The options drawer: language, theme and units. A dialog over a dimmed page, focus kept inside it,
// Escape or the close button or a click on the page closes it; the bar puts focus back on its button.
export function Options({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, language, setLanguage } = useI18n();
  const { mode, setTheme } = useTheme();
  const { units, setUnits } = useUnits();
  const panel = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const { mounted, closing } = usePresence(open);

  useEffect(() => {
    if (!open) return;
    close.current?.focus();
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Tab and Shift+Tab go round inside the drawer.
  function trap(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "Tab" || !panel.current) return;
    const focusable = [...panel.current.querySelectorAll<HTMLElement>("button, [href], input, [tabindex]:not([tabindex='-1'])")].filter(
      (element) => !element.hasAttribute("disabled"),
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  if (!mounted) return null;

  return (
    <div className={`fixed inset-0 z-50 flex justify-end print:hidden${closing ? " fade-out" : ""}`}>
      <div aria-hidden="true" className="options-scrim absolute inset-0 bg-orbit/60" onClick={onClose} />
      <section
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="options-title"
        aria-describedby="options-intro"
        onKeyDown={trap}
        className="options-drawer scroll-thin relative z-10 flex h-full w-full max-w-[380px] flex-col overflow-y-auto border-l border-line bg-paper text-ink shadow-overlay"
      >
        <header className="flex items-start justify-between gap-12 border-b border-line px-22 pt-22 pb-16">
          <div className="flex flex-col gap-4">
            <h2 id="options-title" className="title-2">
              {t("options.title")}
            </h2>
            <p id="options-intro" className="small text-muted">
              {t("options.intro")}
            </p>
          </div>
          <button
            ref={close}
            type="button"
            aria-label={t("options.closeLabel")}
            onClick={onClose}
            className="inline-flex size-[36px] shrink-0 items-center justify-center rounded-md border border-line text-muted transition-colors hover:border-line-strong hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          >
            <Icon>
              <path d="M5 5l10 10M15 5L5 15" />
            </Icon>
          </button>
        </header>

        <div className="flex flex-col gap-22 px-22 pt-6 pb-22">
          <Setting id="options-language" label={t("options.language")} note={t("options.languageNote")}>
            <Segmented
              labelledBy="options-language"
              value={language}
              onChange={setLanguage}
              options={[
                { value: "es", label: t("language.es") },
                { value: "en", label: t("language.en") },
              ]}
            />
          </Setting>

          <Setting id="options-theme" label={t("options.theme")} note={t("options.themeNote")}>
            <Segmented<ThemeMode>
              labelledBy="options-theme"
              value={mode}
              onChange={setTheme}
              options={[
                { value: "light", label: t("theme.light"), icon: SUN },
                { value: "dark", label: t("theme.dark"), icon: MOON },
                { value: "system", label: t("options.system"), icon: SCREEN },
              ]}
            />
          </Setting>

          <Setting id="options-units" label={t("options.units")} note={t("options.unitsNote")}>
            <Segmented<Units>
              labelledBy="options-units"
              value={units}
              onChange={setUnits}
              options={[
                { value: "metric", label: t("options.metric"), icon: THERMOMETER },
                { value: "imperial", label: t("options.imperial"), icon: THERMOMETER },
              ]}
            />
          </Setting>
        </div>
      </section>
    </div>
  );
}
