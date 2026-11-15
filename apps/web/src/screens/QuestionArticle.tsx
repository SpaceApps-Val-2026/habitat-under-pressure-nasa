import { useEffect, useState, type CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, getQuestionArticle, resolvePlace, type QuestionArticle as Article } from "../api/client";
import { fetchPlaceImage, PlaceImage, type PlaceImageInfo } from "../blocks/PlaceImage";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "../layout/page";
import { buttonClass } from "../ui/controls";
import { AskProgress, ClarifyBox, useAsk, type Create, type Resolve, type Start } from "./ask";
import { EditorialSection } from "./Editorial";

import "./report-flow.css";
import "./run-stage.css";

type Load = (id: string) => Promise<Article>;
type ImageInfo = (bbox: number[]) => Promise<PlaceImageInfo | null>;
type Zone = { name: string; bbox: number[]; geometry?: unknown };

// The article's place as a zone, when it already is one (with a bbox); otherwise null and its name is resolved.
function zoneObject(zone: Article["zone"]): Zone | null {
  return zone && typeof zone === "object" && Array.isArray(zone.bbox) && zone.bbox.length === 4 ? zone : null;
}

function zoneName(zone: Article["zone"]): string {
  return typeof zone === "string" ? zone : (zone?.name ?? "");
}

const EXAMPLES: MessageKey[] = ["article.examples.warming", "article.examples.overall", "article.examples.rain"];

// The article Delfos writes when a question has no computable path, inside the app: the person's question as
// kicker, the headline and lead in the interface language, what remains unknown, the sources, and ways on:
// another question, this one rephrased, a place to choose when Delfos asks which, or a question it can answer.
export function QuestionArticle({
  load = getQuestionArticle,
  create,
  start,
  resolve = resolvePlace,
  imageInfo = fetchPlaceImage,
}: {
  load?: Load;
  create?: Create;
  start?: Start;
  resolve?: Resolve;
  imageInfo?: ImageInfo;
}) {
  const { id = "" } = useParams();
  const { t, language } = useI18n();
  const [article, setArticle] = useState<Article | null>(null);
  const [missing, setMissing] = useState<false | "gone" | "failed">(false);
  const asking = useAsk({ create, start, resolve });
  const { setClarification } = asking;
  const [place, setPlace] = useState<{ zone: Zone; info: PlaceImageInfo } | null>(null);

  useEffect(() => {
    let live = true;
    setArticle(null);
    setMissing(false);
    load(id)
      .then((value) => {
        if (!live) return;
        setArticle(value);
        // A question about the place is answered here, in the same box as on the cover.
        if (value.clarify || value.candidates.length > 0) {
          setClarification({
            kind: "clarification",
            missing: "place",
            question: { en: "", es: "" },
            candidates: value.candidates,
          });
        }
      })
      .catch((error) => {
        if (live) setMissing(error instanceof ApiError && error.status === 404 ? "gone" : "failed");
      });
    return () => {
      live = false;
    };
  }, [id, load, setClarification]);

  // The satellite picture of the place, without holding the text: the zone the article carries, or one
  // gazetteer call for its name (cached on the server). Ambiguous, not found or any failure: no figure.
  useEffect(() => {
    setPlace(null);
    if (!article) return;
    let live = true;
    const direct = zoneObject(article.zone);
    const name = zoneName(article.zone);
    const found: Promise<Zone | null> = direct
      ? Promise.resolve(direct)
      : name
        ? resolve(name, language).then((answer) => (answer.status === "found" ? zoneObject(answer.zone as Zone) : null))
        : Promise.resolve(null);
    found
      .then(async (zone) => {
        if (!live || !zone?.bbox || zone.bbox.length !== 4) return;
        const info = await imageInfo(zone.bbox);
        if (live && info) setPlace({ zone, info });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
    // The language only names the place for the gazetteer; a change of language keeps the same picture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [article, resolve, imageInfo]);

  const text = (value: Record<string, string>) => value[language] ?? value.en ?? "";

  if (missing) {
    return (
      <main className={`${PAGE} py-40`}>
        <section className="mx-auto flex max-w-[68ch] flex-col gap-16">
          <h1 className="title-1">{t(missing === "gone" ? "article.missing" : "home.askFailed")}</h1>
          <Link to="/" className={`${buttonClass("secondary")} w-fit`}>
            {t("article.home")}
          </Link>
        </section>
      </main>
    );
  }

  if (!article) {
    return (
      <main className={`${PAGE} py-40`}>
        <p role="status" className="body mx-auto max-w-[68ch] text-muted">
          {t("article.loading")}
        </p>
      </main>
    );
  }

  const sources = article.sources.filter((source) => source.name);
  const placeName = zoneName(article.zone);
  const examples = placeName ? EXAMPLES.map((key) => t(key).replace("{zone}", placeName)) : [];

  return (
    <main className={`${PAGE} fade-in py-40`}>
      <article
        className="mx-auto flex max-w-[68ch] flex-col gap-28 text-ink"
        style={{ counterReset: "figure", "--figure-label": JSON.stringify(t("report.figure")) } as CSSProperties}
      >
        <header className="flex flex-col gap-12 border-b border-line pb-28">
          <p className="label text-muted">{article.question}</p>
          <h1 className="display text-balance max-sm:text-[34px]!">{text(article.headline)}</h1>
          <p className="text-xl leading-snug text-muted">{text(article.lead)}</p>
          {placeName && (
            <p className="flex flex-wrap items-center gap-6">
              <span className="label text-muted">{t("article.place")}</span>
              <span className="label rounded-xs border border-line-strong px-6 py-2 text-ink">{placeName}</span>
            </p>
          )}
        </header>

        {place && (
          <div className="flex justify-center [&_.report-figure]:my-0">
            <PlaceImage zone={place.zone} info={place.info} />
          </div>
        )}

        <div className="flex flex-col gap-12">
          <AskProgress ask={asking} />
          <ClarifyBox ask={asking} question={article.question} />
          {asking.failureText && !asking.sending && (
            <p role="alert" className="fade-in small rounded-md bg-alert-soft p-12 text-alert">
              {asking.failureText}
            </p>
          )}
        </div>

        <section aria-labelledby="article-remains" className="flex flex-col gap-8 border-l-4 border-line-strong pl-16">
          <h2 id="article-remains" className="label text-muted">
            {t("article.remains")}
          </h2>
          <p className="body text-lg leading-relaxed">{text(article.cannot_say)}</p>
        </section>

        <EditorialSection heading={t("article.canTitle")}>
          <p className="body text-lg leading-relaxed">{t("article.can")}</p>
          {examples.length > 0 && (
            <div className="flex flex-col gap-6">
              <span className="label text-muted">{t("article.tryTitle")}</span>
              <ul className="flex flex-wrap gap-6">
                {examples.map((example) => (
                  <li key={example}>
                    <button
                      type="button"
                      disabled={asking.sending}
                      onClick={() => asking.send(example)}
                      className="small min-h-11 rounded-pill border border-line-strong bg-paper px-12 py-6 text-left text-ink transition-colors hover:border-signal hover:text-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:opacity-50"
                    >
                      {example}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </EditorialSection>

        {sources.length > 0 && (
          <EditorialSection heading={t("article.sources")}>
            <ol className="flex list-decimal flex-col gap-4 pl-22">
              {sources.map((source) => (
                <li key={source.name} className="body">
                  {source.url?.startsWith("https://") ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline decoration-line-strong underline-offset-4 hover:text-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                    >
                      {source.name}
                    </a>
                  ) : (
                    source.name
                  )}
                </li>
              ))}
            </ol>
          </EditorialSection>
        )}

        <div className="flex flex-wrap gap-10">
          <Link to="/" className={buttonClass("primary")}>
            {t("article.askAnother")}
          </Link>
          <Link to="/" state={{ question: article.question }} className={buttonClass("secondary")}>
            {t("article.rephrase")}
          </Link>
        </div>
      </article>
    </main>
  );
}
