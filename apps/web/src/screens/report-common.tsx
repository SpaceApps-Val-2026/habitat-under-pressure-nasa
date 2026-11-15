// What the three report formats share: numbered sources and the notes that point at them, the toolbar,
// and the printed page on light paper. Each format lays out its own body.
import { useEffect } from "react";

import type { PlaceImageInfo } from "../blocks/PlaceImage";
import { periodLabel } from "../blocks/PlaceImage";
import { distinctSources, formatNumber, localized, sourceDisplayName, type StepResult } from "../blocks/shared";
import { sourceCitation, sourceLicense, type CatalogSource } from "../catalog/sources";
import type { analysis } from "../contracts";
import { useI18n } from "../i18n";
import { AnalysisTabs } from "./AnalysisTabs";

type SavedAnalysis = analysis.SavedAnalysis;
type SourceRef = StepResult["sources"][number];

const sourceKey = (source: { source: string; url: string }) => `${source.source}|${source.url}`;

// A superscript note to the numbered sources: every figure the text states says where it comes from.
export function Notes({ numbers }: { numbers: number[] }) {
  const { t } = useI18n();
  return (
    <>
      {numbers.map((number) => (
        <a key={number} className="fn" href={`#source-${number}`} aria-label={`${t("blocks.source")} ${number}`}>
          {number}
        </a>
      ))}
    </>
  );
}

// Sources are numbered once per report; the satellite view of the place is one more, after the data's own.
export function numberSources(results: StepResult[], placeImage: PlaceImageInfo | null) {
  const sources = distinctSources(results);
  const index = new Map(sources.map((entry, position) => [sourceKey(entry.source), position + 1]));
  const placeNumber = placeImage ? sources.length + 1 : 0;
  const numbersFor = (ids: string[]) => {
    const numbers = new Set<number>();
    for (const item of results) {
      if (!ids.includes(item.id)) continue;
      for (const source of item.sources) {
        const number = index.get(sourceKey(source));
        if (number) numbers.add(number);
      }
    }
    return [...numbers].sort((a, b) => a - b);
  };
  const notesFor = (ids: string[]) => <Notes numbers={numbersFor(ids)} />;
  return { sources, placeNumber, numbersFor, notesFor };
}

export function ReportToolbar({ id, style, count }: { id: string; style: string; count: number }) {
  const { t } = useI18n();
  return (
    <div className="report-wide report-toolbar print:hidden">
      <AnalysisTabs id={id} current={style} />
      <span className="report-spacer" />
      <a href="#sources" className="report-ghost">
        {t("blocks.sources")} ({count})
      </a>
      <button type="button" onClick={() => window.print()} className="report-ghost">
        {t("report.print")}
      </button>
    </div>
  );
}

// The printed report is always on light paper, also when the app is in the dark theme.
export function useLightPrint() {
  useEffect(() => {
    let previous: string | null = null;
    const before = () => {
      previous = document.documentElement.dataset.theme ?? null;
      document.documentElement.dataset.theme = "light";
    };
    const after = () => {
      if (previous) document.documentElement.dataset.theme = previous;
      else delete document.documentElement.dataset.theme;
      previous = null;
    };
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
}

export function SourcesSection({
  saved,
  results,
  sources,
  catalogSources,
  placeImage,
  placeNumber,
  date,
  title,
}: {
  saved: SavedAnalysis;
  results: StepResult[];
  sources: { source: SourceRef; period: StepResult["period"] }[];
  catalogSources: CatalogSource[];
  placeImage: PlaceImageInfo | null;
  placeNumber: number;
  date: string;
  title?: string;
}) {
  const { t, language } = useI18n();
  if (sources.length === 0 && !placeImage) return null;
  const versions = Object.entries(saved.fingerprint.data);
  const placeCatalog = placeImage?.source?.id ? catalogSources.find((entry) => entry.id === placeImage.source?.id) : undefined;
  const placeLicense = placeCatalog
    ? sourceLicense({ source: placeCatalog.id }, catalogSources, language, undefined, date)
    : localized(placeImage?.license, language);
  return (
    <section id="sources" aria-labelledby="report-sources" className="report-col report-sources">
      <h2 id="report-sources">{title ?? t("blocks.sources")}</h2>
      <ol>
        {sources.map(({ source, period }, index) => {
          const name = sourceDisplayName(source, results, catalogSources, language);
          const citation = sourceCitation(source, catalogSources, language, period, date);
          const license = sourceLicense(source, catalogSources, language, period, date);
          return (
            <li key={sourceKey(source)} id={`source-${index + 1}`}>
              <a href={source.url} target="_blank" rel="noreferrer noopener">
                {name}
              </a>
              {[source.product, source.version].filter(Boolean).length > 0 &&
                ` · ${[source.product, source.version].filter(Boolean).join(" · ")}`}
              {source.n_items !== undefined && ` · ${formatNumber(source.n_items, language)} ${t("blocks.items")}`}
              {citation && <span className="cite">{citation}</span>}
              {license && <span className="license">{license}</span>}
            </li>
          );
        })}
        {placeImage && (
          <li id={`source-${placeNumber}`}>
            {placeCatalog
              ? sourceDisplayName({ source: placeCatalog.id, url: "" }, results, catalogSources, language)
              : localized(placeImage.source?.name, language)}
            {periodLabel(placeImage.period) && ` · ${periodLabel(placeImage.period)}`}
            {placeImage.credit && <span className="cite">{localized(placeImage.credit, language)}</span>}
            {placeLicense && <span className="license">{placeLicense}</span>}
          </li>
        )}
      </ol>
      <p className="foot">
        {t("report.analysis")}: {saved.id} · {t("report.catalog")}: {saved.plan.catalog_version} · {t("report.fingerprint")}:{" "}
        {saved.fingerprint.sha256}
        {versions.length > 0 && (
          <>
            <br />
            {t("report.dataVersions")}:{" "}
            {versions
              .map(([source, version]) => `${sourceDisplayName({ source, url: "" }, results, catalogSources, language)} ${version}`)
              .join(" · ")}
          </>
        )}
      </p>
    </section>
  );
}
