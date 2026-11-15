import { Link } from "react-router-dom";

import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";

export const STYLES = ["journalistic", "institutional", "scientific"] as const;
export type Style = (typeof STYLES)[number];

const TAB_KEY: Record<Style | "panel", MessageKey> = {
  journalistic: "report.style.journalistic",
  institutional: "report.style.institutional",
  scientific: "report.style.scientific",
  panel: "report.data",
};

export function analysisPath(id: string, tab: Style | "panel"): string {
  const base = `/a/${encodeURIComponent(id)}`;
  if (tab === "journalistic") return base;
  return tab === "panel" ? `${base}/panel` : `${base}/report/${tab}`;
}

// One family of views of the same analysis: the three report formats and the data panel. The story is
// the default view; the data panel is a tab like the others.
export function AnalysisTabs({ id, current }: { id: string; current: string }) {
  const { t } = useI18n();
  return (
    <nav aria-label={t("report.format")} className="print:hidden">
      <ul className="flex flex-wrap gap-x-16 gap-y-4">
        {[...STYLES, "panel" as const].map((tab) => (
          <li key={tab}>
            <Link
              to={analysisPath(id, tab)}
              aria-current={tab === current ? "page" : undefined}
              preventScrollReset
              className={`control inline-flex min-h-8 items-center border-b-2 py-4 no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus ${
                tab === current ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {t(TAB_KEY[tab])}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
