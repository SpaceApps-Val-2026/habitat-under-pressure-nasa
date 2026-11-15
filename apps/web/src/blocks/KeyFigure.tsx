import { useI18n } from "../i18n";
import { useUnits } from "../units";
import { useCatalog, useVariableName } from "./catalog";
import {
  Absence,
  DataClassTag,
  distinctSources,
  evidenceOf,
  figureName,
  fillText,
  formatMeasure,
  formatNumber,
  INDEX_UNITS,
  localized,
  localizedUnit,
  periodText,
  referenced,
  resolutionText,
  resourceRow,
  sourceDisplayName,
  trendPresentation,
  VARIABLES,
  type BlockProps,
} from "./shared";
import { DataClassMark } from "./DataClassMark";

export function KeyFigure({ block, results, findings, language, catalogSources, compact = false }: BlockProps) {
  const { t } = useI18n();
  const { units } = useUnits();
  const name = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const sourcesCatalog = catalogSources ?? catalog?.sources;
  const targets = referenced(block, results);
  const finding = findings.find((item) => item.id === block.finding);
  const sentence =
    fillText(block.text, targets, language, units) ?? fillText(finding?.text, targets, language, units);

  return (
    <section className="flex flex-col gap-12">
      <div className={`grid gap-12 ${targets.length > 1 ? "md:grid-cols-2" : ""}`}>
        {targets.map((item) => {
          const trend = trendPresentation(item, units, language);
          // A level is not signed; a change is. The sign is what tells the reader which one it is.
          const operation = item.method?.operation;
          const signed = operation === "trend" || operation === "baci_contrast";
          const measured =
            item.value !== undefined
              ? formatMeasure(item.value, item.unit, language, units, operation, signed)
              : null;
          const value = trend ? { text: trend.value, unit: trend.unit } : measured;
          const low = trend?.low
            ? { text: trend.low, unit: trend.unit }
            : item.interval
              ? formatMeasure(item.interval.low, item.unit, language, units, item.method?.operation, false)
              : null;
          const high = trend?.high
            ? { text: trend.high, unit: trend.unit }
            : item.interval
              ? formatMeasure(item.interval.high, item.unit, language, units, item.method?.operation, false)
              : null;
          // The unit is a word in some results ("people"); a small trend moves to the decade, and that
          // unit is the one the sentence uses. Both must read the same.
          const unit = trend?.unit ?? (item.value !== undefined ? localizedUnit(item.unit, units, language) : (INDEX_UNITS[item.variable] ?? ""));
          // An estimate carries its range and the size it assumes in its table, not an interval; it is
          // not a measurement, and its evidence reads as an estimate from published coefficients.
          const resource = resourceRow(item);
          const evidence = resource
            ? { name: "blocks.estimate" as const, note: "blocks.estimateNote" as const }
            : evidenceOf(item);
          const details = [
            item.period ? `${t("blocks.period")}: ${periodText(item.period, language)}` : "",
            item.coverage?.n_dates !== undefined
              ? `${t("blocks.dates")}: ${formatNumber(item.coverage.n_dates, language)}`
              : "",
            item.support?.resolution_m !== undefined
              ? `${t("blocks.resolution")}: ${resolutionText(item.support.resolution_m, language)}`
              : "",
          ].filter(Boolean);
          const sources = distinctSources([item]);
          return (
            <figure key={item.id} className="flex flex-col gap-6 rounded-lg border border-line bg-paper p-12">
              <figcaption className="label text-muted">
                {figureName(item, language, name)}
              </figcaption>
              {item.absence ? (
                <Absence item={item} />
              ) : (
                <>
                  <p className="figure">
                    {value ? value.text : t("blocks.noValue")}{" "}
                    <span className="whitespace-nowrap">
                      <span className="small text-muted">{unit}</span>
                      <DataClassMark value={item.data_class} />
                    </span>
                  </p>
                  {low && high && (
                    <p className="data text-muted">
                      {t("blocks.interval")} {new Intl.NumberFormat(language, { style: "percent" }).format(item.interval!.level)}:{" "}
                      {low.text} – {high.text} {trend?.unit ?? localizedUnit(item.unit, units, language)}
                    </p>
                  )}
                  {item.interval && item.direction_assertable === false && (
                    <p className="small text-amber-ink">{t("blocks.noCentralFigure")}</p>
                  )}
                  {resource && (
                    <p className="data text-muted">
                      {t("blocks.range")}: {formatMeasure(resource.low, item.unit, language, units, undefined, false).text} –{" "}
                      {formatMeasure(resource.high, item.unit, language, units, undefined, false).text}{" "}
                      {localizedUnit(item.unit, units, language)}
                    </p>
                  )}
                  {!item.interval && !resource && item.value !== undefined && (
                    <p className="small text-amber-ink">{t("blocks.noInterval")}</p>
                  )}
                  <DataClassTag value={item.data_class} />
                  {evidence && (
                    <div className="flex flex-col gap-2">
                      <span className="label text-muted">{t(evidence.name)}</span>
                      <span className="small text-muted">{t(evidence.note)}</span>
                    </div>
                  )}
                  {/* In the report the sources and the limits have their own sections; each is written once. */}
                  {!compact && sources.length > 0 && (
                    <div className="flex flex-wrap items-center gap-6">
                      {sources.map(({ source }, index) => (
                        <a
                          key={`${source.source}-${index}`}
                          href={source.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="small text-signal underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                        >
                          {t("blocks.source")}: {sourceDisplayName(source, results, sourcesCatalog, language)}
                        </a>
                      ))}
                    </div>
                  )}
                  {details.length > 0 && <p className="caption text-muted">{details.join(" · ")}</p>}
                  {!compact && item.limits && item.limits.length > 0 && (
                    <div className="flex flex-col gap-2">
                      <span className="label text-muted">{t("blocks.limits")}</span>
                      <ul className="small flex list-disc flex-col gap-2 pl-16 text-muted">
                        {[...new Set(item.limits.map((limit) => localized(limit, language)))].map((limit) => (
                          <li key={limit}>{limit}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </figure>
          );
        })}
      </div>
      {/* A sentence the web cannot complete is left out; the figure above still reads on its own. */}
      {sentence && <p className="body-lg">{sentence}</p>}
    </section>
  );
}
