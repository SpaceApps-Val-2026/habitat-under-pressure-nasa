import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import logo from "../assets/logo.svg";
import { useI18n } from "../i18n";
import { Options } from "./Options";
import { PAGE } from "./page";

// The sections of the app, in the bar on wide screens and in the menu on narrow ones.
const SECTIONS = [
  ["/", "nav.home"],
  ["/library", "nav.library"],
  ["/about", "nav.about"],
  ["/manifesto", "nav.manifesto"],
] as const;

function sectionClass(isActive: boolean, wide: boolean): string {
  return `control inline-flex min-h-10 items-center px-16 py-10 no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus ${
    wide ? "" : "w-full"
  } ${
    isActive
      ? "bg-signal-soft font-semibold text-signal"
      : "text-muted hover:bg-soft hover:text-ink"
  }`;
}

export function AppBar() {
  const { language, setLanguage, t } = useI18n();
  const [options, setOptions] = useState(false);
  const languages = useRef<HTMLDetailsElement>(null);
  const optionsButton = useRef<HTMLButtonElement>(null);
  const [menu, setMenu] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const { pathname } = useLocation();

  // A chosen section closes the menu; Escape closes it and puts focus back on its button.
  useEffect(() => setMenu(false), [pathname]);
  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenu(false);
      menuButton.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  function choose(code: "en" | "es") {
    setLanguage(code);
    const details = languages.current;
    details?.removeAttribute("open");
    details?.querySelector("summary")?.focus();
  }

  function closeOptions() {
    setOptions(false);
    optionsButton.current?.focus();
  }

  return (
    <header className="border-b border-line bg-paper text-ink print:hidden">
      <div className={`${PAGE} flex flex-wrap items-center gap-10 py-10 sm:gap-16`}>
      <Link
        to="/"
        className="flex items-center gap-10 text-ink no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      >
        <img src={logo} alt="" className="size-40" />
        {/* On a phone the brand keeps its logo and its name for screen readers; the sections need the room. */}
        <span className="title-2 flex flex-col leading-none! max-[479px]:sr-only">
          <span className="font-normal">{t("brand.habitat")}</span>
          <span>{t("brand.underPressure")}</span>
        </span>
      </Link>

      <nav aria-label={t("nav.sections")} className="ml-8 hidden items-center gap-4 border-l border-line pl-[24px] lg:flex">
        {SECTIONS.map(([to, key]) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => sectionClass(isActive, true)}>
            {t(key)}
          </NavLink>
        ))}
      </nav>

      {/* Four sections do not fit beside the brand on a phone or a tablet: they go in a menu. */}
      <button
        ref={menuButton}
        type="button"
        aria-expanded={menu}
        aria-controls="app-menu"
        onClick={() => setMenu((open) => !open)}
        className="control inline-flex min-h-10 items-center gap-6 rounded-sm border border-line-strong px-10 py-4 text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus lg:hidden"
      >
        <svg viewBox="0 0 20 20" className="size-16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          {menu ? <path d="M5 5l10 10M15 5L5 15" /> : <path d="M3 5.5h14M3 10h14M3 14.5h14" />}
        </svg>
        {t("nav.menu")}
      </button>

      <div className="ml-auto flex flex-wrap items-center gap-10">
        <details ref={languages} className="relative">
          <summary
            aria-label={`${t("language.label")} ${language.toUpperCase()}`}
            className="flex cursor-pointer list-none items-center gap-6 rounded-sm border border-line-strong px-10 py-4 text-ink marker:content-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          >
            <svg
              viewBox="0 0 24 24"
              className="size-16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18" />
            </svg>
            <span className="control">{language.toUpperCase()}</span>
          </summary>
          <div className="fade-in absolute right-0 z-20 mt-4 flex min-w-max flex-col gap-2 rounded-md border border-line bg-paper p-4">
            {(["en", "es"] as const).map((code) => (
              <button
                key={code}
                type="button"
                onClick={() => choose(code)}
                className="control flex items-center gap-6 rounded-xs px-10 py-4 text-left text-ink hover:bg-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                <span className="data text-muted">{t(code === "en" ? "language.short.en" : "language.short.es")}</span>
                <span>{t(code === "en" ? "language.en" : "language.es")}</span>
              </button>
            ))}
          </div>
        </details>

        <button
          ref={optionsButton}
          type="button"
          aria-label={t("nav.options")}
          onClick={() => setOptions(true)}
          className="flex items-center rounded-sm border border-line-strong px-10 py-4 text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          <svg
            viewBox="0 0 24 24"
            className="size-16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true"
          >
            <path d="M4 7h10M18 7h2M4 12h4M12 12h8M4 17h8M16 17h4" />
            <circle cx="16" cy="7" r="2" />
            <circle cx="10" cy="12" r="2" />
            <circle cx="14" cy="17" r="2" />
          </svg>
        </button>
        </div>

        {menu && (
          <nav id="app-menu" aria-label={t("nav.sections")} className="fade-in basis-full border-t border-line pt-8 lg:hidden">
            <ul className="flex flex-col gap-2">
              {SECTIONS.map(([to, key]) => (
                <li key={to}>
                  <NavLink to={to} end={to === "/"} className={({ isActive }) => sectionClass(isActive, false)}>
                    {t(key)}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        )}

        <Options open={options} onClose={closeOptions} />
      </div>
    </header>
  );
}
