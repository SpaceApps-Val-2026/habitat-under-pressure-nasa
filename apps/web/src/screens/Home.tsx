import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";

import {
  createAnalysis,
  createPlan,
  getAnalysis,
  getCatalog,
  listAnalyses,
  resolvePlace,
  type AnalysisSummary,
} from "../api/client";
import type { plan } from "../contracts";
import { DESIGN } from "../design";
import type { GlobePlace } from "../globe/Globe";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "../layout/page";
import { Button } from "../ui/controls";
import { Tooltip } from "../ui/Tooltip";

import { cardTitle, readAsked, shortPlace, type Asked } from "./analysis-card";
import { AskProgress, ClarifyBox, useAsk, type Create, type Resolve, type Start } from "./ask";
import "./run-stage.css";

// The globe is the heaviest part of the cover; the text and the reports paint first and the
// canvas arrives in its own chunk.
const Globe = lazy(() => import("../globe/Globe").then((module) => ({ default: module.Globe })));

type Load = typeof listAnalyses;
type Read = (id: string) => Promise<{ plan?: plan.AnalysisPlan | null }>;
type Names = () => Promise<{ variables: { id: string; name: { [language: string]: string | undefined } }[] }>;

const THEME_TEXT: Record<GlobePlace["theme"], string> = {
  change: "text-theme-climate",
  pressure: "text-theme-pressure",
  water: "text-theme-water",
};

const THEME_KEY: Record<GlobePlace["theme"], MessageKey> = {
  change: "home.themes.change",
  pressure: "home.themes.pressure",
  water: "home.themes.water",
};

const HEAT_BG = ["bg-heat-1", "bg-heat-2", "bg-heat-3", "bg-heat-4", "bg-heat-5"];

function ThemeMark({ theme }: { theme: GlobePlace["theme"] }) {
  const shape =
    theme === "change"
      ? "rounded-full"
      : theme === "water"
        ? "[clip-path:polygon(50%_0,100%_100%,0_100%)]"
        : "rounded-xs";
  return <i aria-hidden="true" className={`inline-block size-8 shrink-0 bg-current ${THEME_TEXT[theme]} ${shape}`} />;
}

function stripeColor(anomaly: number): string {
  const clipped = Math.max(-2.5, Math.min(2.5, anomaly));
  const index = Math.round(((clipped + 2.5) / 5) * 4);
  return HEAT_BG[Math.min(HEAT_BG.length - 1, Math.max(0, index))];
}

// The card is a fixed illustration: metric in both languages, whatever the units option says, with the
// figures rounded to what an annual reanalysis mean can carry.
function number(language: string, options: Intl.NumberFormatOptions): (value: number) => string {
  const format = new Intl.NumberFormat(language, options);
  return (value) => format.format(value).replace("-", "−");
}

function ReadingCard({ place }: { place: GlobePlace }) {
  const { t, language } = useI18n();
  const trendOf = number(language, { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });
  const oneDecimal = number(language, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const smallTrend = number(language, { maximumSignificantDigits: 2, signDisplay: "exceptZero" });
  const whole = number(language, { maximumFractionDigits: 0 });
  const temperature = place.t2m[place.t2m.length - 1];
  const perDecade = t("home.perDecade");

  return (
    <aside
      aria-label={t("home.cardAria")}
      className="hidden flex-col gap-12 rounded-xl border border-line bg-paper p-16 text-ink @min-[600px]:flex"
    >
      <div className="flex flex-wrap items-center gap-6">
        <span className="label inline-flex items-center gap-4 text-muted">
          <ThemeMark theme={place.theme} />
          {t(THEME_KEY[place.theme])}
        </span>
        <span className="label rounded-xs border border-line-strong px-6 py-2 text-muted">
          {t("dataClass.reanalysis")}
        </span>
      </div>

      <h2 className="title-1 text-balance">{place.name}</h2>

      <div className="flex flex-col gap-2">
        <p className="figure-xl">
          {trendOf(place.sen_dec)} <span className="small text-muted">°C</span>
        </p>
        <p className="small text-muted">
          {perDecade} · {t(place.sig ? "home.clearTrend" : "home.noClearTrend")}
        </p>
      </div>

      <figure className="flex flex-col gap-4">
        <div aria-hidden="true" className="flex h-10 gap-1">
          {place.t2m.map((value, index) => (
            <i key={index} className={`min-w-0 flex-1 ${stripeColor(value - place.base)}`} />
          ))}
        </div>
        <figcaption className="caption text-muted">
          {t("home.miniCaption")} · {t("home.miniYears")}
        </figcaption>
      </figure>

      <dl className="flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <dt className="label text-muted">{t("home.variables.temperature")}</dt>
          <dd className="data">
            {oneDecimal(temperature)} <span className="small text-muted">°C</span>
          </dd>
        </div>
        <div className="flex flex-col gap-2">
          <dt className="label text-muted">{t("home.variables.precipitation")}</dt>
          <dd className="data">
            {oneDecimal(place.prec.mean)} <span className="small text-muted">{t("home.mmDay")}</span>
            <span className="small text-muted">
              {" "}
              ·{" "}
              {place.prec.sig
                ? `${smallTrend(place.prec.sen_dec)} ${t("home.mmDay")} ${perDecade}`
                : t("home.noClearTrend")}
            </span>
          </dd>
        </div>
        <div className="flex flex-col gap-2">
          <dt className="label text-muted">{t("home.variables.soil")}</dt>
          <dd className="data">
            {whole(place.soil.mean * 100)} <span className="small text-muted">{t("home.saturation")}</span>
            <span className="small text-muted">
              {" "}
              ·{" "}
              {place.soil.sig
                ? `${smallTrend(place.soil.sen_dec * 100)} ${t("home.points")} ${perDecade}`
                : t("home.noClearTrend")}
            </span>
          </dd>
        </div>
      </dl>

      <p className="caption text-muted">
        <span className="label text-muted">{t("dataClass.illustrative")}</span> {t("home.globeNote")}
      </p>
      <p className="caption text-muted">{t("home.coastline")}</p>
      <p className="caption text-muted">{t("home.fine")}</p>
    </aside>
  );
}

export function Home({
  create = createPlan,
  load = listAnalyses,
  start = createAnalysis,
  read = getAnalysis,
  names = getCatalog,
  resolve = resolvePlace,
}: {
  create?: Create;
  load?: Load;
  start?: Start;
  read?: Read;
  names?: Names;
  resolve?: Resolve;
}) {
  const { t, language } = useI18n();
  // Back from a run the person stopped waiting for: the question stays in the box, with a note.
  // `cancelled`: the server stopped it; `leftRun`: the cancel request failed, so it may still be computing.
  const arrived = useLocation().state as { question?: string; leftRun?: boolean; cancelled?: boolean } | null;
  const [question, setQuestion] = useState(arrived?.question ?? "");
  const [leftRun, setLeftRun] = useState<"cancelled" | "waiting" | null>(
    arrived?.cancelled ? "cancelled" : arrived?.leftRun ? "waiting" : null,
  );
  const asking = useAsk({ create, start, resolve });
  const { sending, notPossible, failureText } = asking;
  const [reports, setReports] = useState<AnalysisSummary[] | null>(null);
  const [reportsFailed, setReportsFailed] = useState(false);
  const [asked, setAsked] = useState<Record<string, Asked>>({});
  const [templates, setTemplates] = useState<string[]>([]);
  const [place, setPlace] = useState<GlobePlace | null>(null);
  const answerBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    let stop = () => {};
    load({ limit: 6 })
      .then(({ items }) => {
        if (!live) return;
        setReports(items);
        // The examples and the readable titles come from the questions people really asked.
        stop = readAsked(items, read, (id, found) => setAsked((current) => ({ ...current, [id]: found })));
      })
      .catch(() => {
        if (live) setReportsFailed(true);
      });
    return () => {
      live = false;
      stop();
    };
  }, [load, read]);

  // Real questions make the examples; short ones first, and none so long it cannot be read on a chip.
  const questions = [...new Set(Object.values(asked).map((entry) => entry.text).filter(Boolean))]
    .filter((text) => text.length <= 140)
    .sort((a, b) => Number(a.length > 60) - Number(b.length > 60))
    .slice(0, 3);
  const needTemplates = Object.keys(asked).length > 0 && questions.length < 2;

  // Only when too few real questions exist: «{variable} in {place}: what has changed?», with the catalogue's
  // name of a variable and the place of an analysis that really ran.
  useEffect(() => {
    if (!needTemplates) return;
    let live = true;
    names()
      .then((catalog) => {
        if (!live) return;
        const seen = new Set<string>();
        const built: string[] = [];
        for (const entry of Object.values(asked)) {
          if (entry.text || !entry.variable || !entry.place) continue;
          const variable = catalog.variables.find((item) => item.id === entry.variable);
          const name = variable ? (variable.name[language] ?? variable.name.en) : null;
          if (!name) continue;
          const text = t("home.exampleTemplate")
            .replace("{variable}", name)
            .replace("{place}", entry.place);
          if (!seen.has(text)) {
            seen.add(text);
            built.push(text);
          }
        }
        setTemplates(built.slice(0, 3 - questions.length));
      })
      .catch(() => {});
    return () => {
      live = false;
    };
    // questions.length is derived from asked; asked, language and needTemplates are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asked, language, needTemplates, names]);

  const examples = [...questions, ...(needTemplates ? templates : [])];

  useEffect(() => {
    if (!notPossible) return;
    // On a phone the globe sits above the box: bring the answer into view.
    answerBox.current?.scrollIntoView?.({ block: "nearest" });
  }, [notPossible]);

  // Enter launches the question; the shared flow (screens/ask.tsx) takes it to the run, the report or the
  // article, and asks here which place when it has to.
  function send(text: string) {
    setLeftRun(null);
    void asking.send(text);
  }

  function ask(event: FormEvent) {
    event.preventDefault();
    send(question.trim());
  }

  function askExample(text: string) {
    setQuestion(text);
    send(text);
  }

  const date = (value: string) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? ""
      : new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(parsed);
  };
  const meta = (item: AnalysisSummary) =>
    [shortPlace(item.zone?.name), DESIGN[item.design] ? t(DESIGN[item.design]) : "", date(item.created)]
      .filter(Boolean)
      .join(" · ");
  // The person's own question reads better than a title assembled from variable names.
  const title = (item: AnalysisSummary) =>
    cardTitle(item, asked[item.id], language, DESIGN[item.design] ? t(DESIGN[item.design]) : "", t("library.untitled"));
  const trendText = place
    ? new Intl.NumberFormat(language, { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" })
        .format(place.sen_dec)
        .replace("-", "−")
    : "";

  return (
    <main className="@container flex flex-col bg-fog">
      <section
        aria-label={t("home.askSection")}
        className="relative overflow-hidden bg-orbit text-orbit-ink @min-[900px]:min-h-[700px]"
      >
        <div className="relative h-[230px] w-full overflow-hidden @min-[600px]:h-[380px] @min-[900px]:absolute @min-[900px]:inset-0 @min-[900px]:h-full">
          <Suspense fallback={null}>
            <Globe
              label={t("home.globeAlt")}
              className="block size-full"
              initialId="sagunt"
              onPlaceChange={setPlace}
            />
          </Suspense>
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-orbit to-transparent"
          />
          {place && (
            <div
              aria-live="polite"
              className="absolute inset-x-16 bottom-4 flex flex-col gap-2 @min-[600px]:hidden"
            >
              <span className="flex min-w-0 items-center gap-6">
                <ThemeMark theme={place.theme} />
                <span className="title-2 truncate text-orbit-ink">{place.name}</span>
              </span>
              <span className="data text-orbit-muted">
                <b className="text-orbit-ink">{trendText} °C</b> {t("home.perDecade")} ·{" "}
                {t(place.sig ? "home.clearTrend" : "home.noClearTrend")}
              </span>
              <span className="flex flex-wrap items-center gap-4">
                <span className="label rounded-xs border border-orbit-line px-6 py-2 text-orbit-muted">
                  {t("dataClass.reanalysis")}
                </span>
                <span className="label rounded-xs border border-orbit-line px-6 py-2 text-orbit-focus">
                  {t("dataClass.illustrative")}
                </span>
              </span>
            </div>
          )}
        </div>

        <p className="caption px-16 pt-6 text-orbit-muted @min-[600px]:hidden">
          {t("home.fineShort")} · {t("home.globeNote")}
        </p>

        <div className={`${PAGE} relative z-10 flex flex-col gap-16 pb-22 pt-16 @min-[600px]:gap-16 @min-[600px]:pb-28 @min-[600px]:pt-22 @min-[900px]:grid @min-[900px]:grid-cols-[minmax(0,1fr)_380px] @min-[900px]:items-start @min-[900px]:gap-28 @min-[900px]:pt-40`}>
          <div className="flex flex-col gap-16">
            <p className="label text-orbit-focus">{t("home.kicker")}</p>
            <h1 className="display text-balance max-[600px]:text-4xl!">
              <span className="block font-normal">{t("home.headlineLead")}</span> {t("home.headline")}
            </h1>
            <p className="body hidden max-w-[44ch] text-orbit-muted @min-[600px]:block">{t("home.lead")}</p>

            <form
              onSubmit={ask}
              role="search"
              className="flex max-w-[560px] gap-8 rounded-xl border border-line-strong bg-paper p-6 shadow-lg transition-colors focus-within:border-focus"
            >
              <label className="sr-only" htmlFor="home-q">
                {t("home.askLabel")}
              </label>
              <input
                id="home-q"
                type="search"
                className="min-w-0 flex-1 bg-transparent px-8 py-6 text-base text-ink outline-none! focus-visible:outline-none!"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={t("home.askPlaceholder")}
              />
              <Button type="submit" disabled={sending || question.trim() === ""} className="shrink-0">
                {t("home.ask")}
              </Button>
            </form>

            <div ref={answerBox} className="flex max-w-[560px] flex-col gap-12 empty:hidden">
              <AskProgress ask={asking} />
              <ClarifyBox ask={asking} question={question.trim()} />
              {notPossible && !sending && (
                <div role="status" className="fade-in flex flex-col gap-8 rounded-xl border border-line bg-paper p-12 text-ink">
                  <p className="body font-semibold">{t("home.notPossible")}</p>
                  {notPossible.limits.length > 0 && (
                    <ul className="grid list-disc gap-4 pl-16">
                      {notPossible.limits.map((limit) => (
                        <li key={limit} className="small text-muted">
                          {limit}
                        </li>
                      ))}
                    </ul>
                  )}
                  {notPossible.alternative && (
                    <div className="flex flex-col gap-4">
                      <span className="label text-muted">{t("home.alternative")}</span>
                      <Button
                        type="button"
                        variant="secondary"
                        className="small w-fit text-left"
                        onClick={() => askExample(notPossible.alternative)}
                      >
                        {notPossible.alternative}
                      </Button>
                    </div>
                  )}
                </div>
              )}
              {failureText && !sending && (
                <p role="alert" className="fade-in small rounded-md bg-orbit-2 p-12 text-orbit-ink">
                  {failureText}
                </p>
              )}
              {leftRun && !sending && (
                <p role="status" className="fade-in small rounded-md border border-orbit-line bg-orbit-2 p-12 text-orbit-ink">
                  {t(leftRun === "cancelled" ? "home.cancelledRun" : "home.leftRun")}
                </p>
              )}
            </div>

            {examples.length > 0 && (
              <div className="flex max-w-[560px] flex-col gap-6">
                <span className="label text-orbit-muted">{t("home.examples")}</span>
                <ul className="flex flex-wrap gap-6">
                  {examples.map((example) => (
                    <li key={example} className="min-w-0 max-w-[min(100%,44ch)]">
                      {/* One line: a long question is cut with «…» and read whole in its tooltip. */}
                      <Tooltip text={example}>
                        <button
                          type="button"
                          data-truncate
                          disabled={sending}
                          onClick={() => askExample(example)}
                          className="small block max-w-full truncate rounded-pill border border-orbit-line px-10 py-4 text-left text-orbit-ink hover:border-orbit-focus focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orbit-focus disabled:opacity-50"
                        >
                          {example}
                        </button>
                      </Tooltip>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p className="caption text-orbit-muted">{t("home.check")}</p>

            <ul
              aria-label={t("home.themes.label")}
              className="hidden flex-wrap gap-x-16 gap-y-6 text-orbit-muted @min-[600px]:flex"
            >
              {(["change", "pressure", "water"] as const).map((theme) => (
                <li key={theme} className="small flex items-center gap-6">
                  <ThemeMark theme={theme} />
                  {t(THEME_KEY[theme])}
                </li>
              ))}
            </ul>
          </div>

          {place && <ReadingCard place={place} />}
        </div>
      </section>

      <section className={`${PAGE} flex flex-col gap-12 py-22`} aria-labelledby="home-reports">
        <div className="flex flex-wrap items-baseline justify-between gap-10">
          <h2 id="home-reports" className="title-1 text-xl!">
            {t("home.reports")}
          </h2>
          <Link
            to="/library"
            className="small inline-flex min-h-8 items-center font-semibold text-signal underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          >
            {t("home.allReports")}
          </Link>
        </div>

        {reportsFailed && <p className="body text-muted">{t("library.failed")}</p>}
        {reports && reports.length === 0 && <p className="body text-muted">{t("library.empty")}</p>}
        {reports && reports.length > 0 && (
          <ul className="fade-in grid grid-cols-1 gap-12 @min-[600px]:grid-cols-2 @min-[1000px]:grid-cols-3">
            {reports.map((item) => (
              <li key={item.id} className="flex min-w-0">
                <Link
                  to={`/a/${item.id}`}
                  className="flex h-full w-full min-w-0 flex-col gap-8 rounded-xl border border-line bg-paper p-16 hover:border-line-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                >
                  <span className="flex flex-wrap items-center gap-6">
                    <span className="small text-muted">{meta(item)}</span>
                    {item.featured && (
                      <span className="label inline-flex items-center rounded-xs border border-line-strong px-6 py-2 text-muted">
                        {t("library.featuredTag")}
                      </span>
                    )}
                    {item.status === "cancelled" && (
                      <span className="label inline-flex items-center rounded-xs border border-line-strong bg-soft px-6 py-2 text-muted">
                        {t("library.status.cancelled")}
                      </span>
                    )}
                  </span>
                  <Tooltip text={title(item)} block>
                    <h3 data-truncate className="title-2 min-w-0 truncate text-ink">
                      {title(item)}
                    </h3>
                  </Tooltip>
                  {item.summary && <p className="body text-muted">{item.summary[language] ?? item.summary.en}</p>}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
