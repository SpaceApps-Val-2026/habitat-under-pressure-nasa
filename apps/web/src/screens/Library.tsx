import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  getAnalysis,
  getPlaceImageCredit,
  listAnalyses,
  placeImageUrl,
  type AnalysisFilters,
  type AnalysisSummary,
  type PlaceImageCredit,
} from "../api/client";
import { DESIGN } from "../design";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "../layout/page";
import { Button, Field, SearchField, Toggle } from "../ui/controls";
import { Select } from "../ui/Select";
import { Tooltip } from "../ui/Tooltip";
import { cardTitle, placeLine, readAsked, type Asked, type ReadPlan } from "./analysis-card";

const STATUS: Record<string, MessageKey> = {
  planned: "library.status.planned",
  finished: "library.status.finished",
  running: "library.status.running",
  failed: "library.status.failed",
  cancelled: "library.status.cancelled",
};

const TONE: Record<string, string> = {
  planned: "border-line-strong text-muted",
  finished: "border-signal bg-signal-soft text-signal",
  running: "border-amber bg-amber-soft text-amber-ink",
  failed: "border-alert bg-alert-soft text-alert",
  cancelled: "border-line-strong bg-soft text-muted",
};

type Load = (filters: AnalysisFilters) => Promise<{ items: AnalysisSummary[] }>;
type Credit = (bbox: number[]) => Promise<PlaceImageCredit | null>;

// Twice the box, for sharp pixels on dense screens; the route caches every width it serves.
const THUMB_PX = 160;

// The satellite picture of the zone in a small square; a neutral mark while it loads and when there is none.
function Thumb({ bbox }: { bbox: number[] | undefined }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const usable = Array.isArray(bbox) && bbox.length === 4;
  return (
    <div className="relative size-[72px] shrink-0 overflow-hidden rounded-md border border-line bg-soft max-sm:size-[56px]">
      {state !== "ready" && (
        <svg
          viewBox="0 0 24 24"
          className="absolute inset-0 m-auto size-22 text-line-strong"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M12 21s6-5.3 6-10a6 6 0 1 0-12 0c0 4.7 6 10 6 10z" />
          <circle cx="12" cy="11" r="2" />
        </svg>
      )}
      {usable && state !== "failed" && (
        <img
          src={placeImageUrl(bbox, THUMB_PX)}
          alt=""
          loading="lazy"
          decoding="async"
          onLoad={() => setState("ready")}
          onError={() => setState("failed")}
          className={`relative size-full object-cover transition-opacity ${state === "ready" ? "opacity-100" : "opacity-0"}`}
        />
      )}
    </div>
  );
}

export function Library({
  load = listAnalyses,
  read = getAnalysis,
  credit = getPlaceImageCredit,
}: {
  load?: Load;
  read?: ReadPlan;
  credit?: Credit;
}) {
  const { t, language } = useI18n();
  const [q, setQ] = useState("");
  const [design, setDesign] = useState("");
  const [featured, setFeatured] = useState(false);
  const [items, setItems] = useState<AnalysisSummary[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [checking, setChecking] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [asked, setAsked] = useState<Record<string, Asked>>({});
  const [imageCredit, setImageCredit] = useState<PlaceImageCredit | null>(null);
  const latest = useRef(0);
  const creditAsked = useRef(false);

  useEffect(() => {
    const token = ++latest.current;
    setChecking(true);
    load({ q: q.trim() || undefined, design: design || undefined, featured: featured || undefined })
      .then((result) => {
        if (token !== latest.current) return;
        setItems(result.items);
        setFailed(false);
      })
      .catch(() => {
        if (token !== latest.current) return;
        setItems(null);
        setFailed(true);
      })
      .finally(() => {
        if (token === latest.current) setChecking(false);
      });
  }, [q, design, featured, attempt, load]);

  // The list paints with the saved titles; each becomes the person's question as its plan arrives.
  useEffect(() => {
    if (!items) return;
    const missing = items.filter((item) => !(item.id in asked));
    if (missing.length === 0) return;
    return readAsked(missing, read, (id, found) => setAsked((current) => ({ ...current, [id]: found })));
    // `asked` only filters what is already read; listening to it would restart the reads on every arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, read]);

  // One credit line for the pictures, as the provider asks, from the first place that has one.
  useEffect(() => {
    if (creditAsked.current || !items) return;
    const first = items.find((item) => item.zone?.bbox?.length === 4);
    if (!first?.zone) return;
    creditAsked.current = true;
    credit(first.zone.bbox)
      .then((info) => setImageCredit(info))
      .catch(() => {});
  }, [items, credit]);

  const date = (value: string) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? ""
      : new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(parsed);
  };
  const designLabel = (id: string) => (DESIGN[id] ? t(DESIGN[id]) : t("library.designUnknown"));
  const statusLabel = (status: string) => (STATUS[status] ? t(STATUS[status]) : t("library.status.unknown"));
  const hasFilters = q.trim() !== "" || design !== "" || featured;
  const creditText = imageCredit?.credit ? (imageCredit.credit[language] ?? imageCredit.credit.en ?? "") : "";

  return (
    <main className={`${PAGE} py-22`}>
      <section className="flex flex-col gap-16 rounded-xl border border-line bg-paper p-16">
        <h1 className="title-1">{t("library.title")}</h1>

        <div className="flex flex-col gap-12 md:grid md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] md:items-end md:gap-16">
          <Field id="library-q" label={t("library.search")}>
            <SearchField id="library-q" value={q} onChange={setQ} placeholder={t("library.searchPlaceholder")} />
          </Field>
          <Select
            id="library-design"
            label={t("library.design")}
            value={design}
            onChange={setDesign}
            options={[
              { value: "", label: t("library.allDesigns") },
              ...Object.keys(DESIGN).map((id) => ({ value: id, label: t(DESIGN[id]) })),
            ]}
          />
          <Toggle id="library-featured" checked={featured} onChange={() => setFeatured(!featured)}>
            {t("library.featuredOnly")}
          </Toggle>
        </div>

        <div aria-live="polite" aria-busy={checking} className="flex flex-col gap-12">
          {failed && (
            <div className="flex flex-wrap items-center gap-10">
              <p className="body text-alert">{t("library.failed")}</p>
              <Button type="button" variant="secondary" onClick={() => setAttempt((value) => value + 1)}>
                {t("library.retry")}
              </Button>
            </div>
          )}

          {!failed && checking && !items && <p className="body text-muted">{t("library.loading")}</p>}

          {!failed && items && items.length === 0 && (
            <p className="body text-muted">{hasFilters ? t("library.noMatches") : t("library.empty")}</p>
          )}

          {!failed && items && items.length > 0 && (
            <ul className="fade-in flex flex-col gap-8">
              {items.map((item) => {
                const title = cardTitle(item, asked[item.id], language, designLabel(item.design), t("library.untitled"));
                const meta = [placeLine(item.zone?.name) || t("library.noZone"), date(item.created) || t("library.noDate")].join(
                  " · ",
                );
                return (
                  <li
                    key={item.id}
                    className="relative flex items-center gap-12 rounded-md border border-line bg-paper p-12 transition-colors hover:border-line-strong hover:bg-soft"
                  >
                    <Thumb bbox={item.zone?.bbox} />
                    <div className="flex min-w-0 flex-1 flex-col gap-4">
                      <Tooltip text={title} block inCard>
                        <h2 data-truncate className="title-2 min-w-0 truncate">
                          {/* The whole card opens the report: the title link covers it. */}
                          <Link
                            to={`/a/${item.id}`}
                            className="text-ink no-underline after:absolute after:inset-0 after:rounded-md after:content-[''] focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-focus"
                          >
                            {title}
                          </Link>
                        </h2>
                      </Tooltip>
                      <p className="small min-w-0 truncate text-muted">{meta}</p>
                      <div className="flex flex-wrap items-center gap-6">
                        <span className="label inline-flex items-center rounded-xs border border-line-strong px-6 py-2 text-muted">
                          {designLabel(item.design)}
                        </span>
                        {item.status !== "finished" && (
                          <span
                            className={`label inline-flex items-center rounded-xs border px-6 py-2 ${
                              TONE[item.status] ?? "border-line-strong text-muted"
                            }`}
                          >
                            {statusLabel(item.status)}
                          </span>
                        )}
                        {item.featured && (
                          <span className="label inline-flex items-center rounded-xs border border-signal bg-signal-soft px-6 py-2 text-signal">
                            {t("library.featuredTag")}
                          </span>
                        )}
                        <Link
                          to={item.status === "running" ? `/run/${item.id}` : `/run/${item.id}?replay=1`}
                          className="small relative z-10 ml-auto inline-flex min-h-8 items-center text-muted underline hover:text-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                        >
                          {t("library.seeRun")}
                        </Link>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          {!failed && items && items.length > 0 && creditText && (
            <p className="caption text-muted">{t("library.imageCredit").replace("{credit}", creditText)}</p>
          )}
        </div>
      </section>
    </main>
  );
}
