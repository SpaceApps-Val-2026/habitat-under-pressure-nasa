import { useI18n } from "../i18n";
import { useUnits } from "../units";
import {
  Absence,
  DataClassTag,
  evidenceOf,
  fillText,
  formatMeasure,
  localized,
  referenced,
  Section,
  localizedUnit,
  VARIABLES,
  periodText,
  resolutionText,
  sourceDisplayName,
  type BlockProps,
  type StepResult,
} from "./shared";
import { useCatalog, useVariableName } from "./catalog";
import { DataClassMark } from "./DataClassMark";

// People inside the flooded area: a big figure with its unit, the data class and every limit the
// result carries. An absence replaces the figure; it never becomes a zero.
export function ExposureCounter({ block, results, findings, language, notes }: BlockProps) {
  const { t } = useI18n();
  const variableLabel = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const { units } = useUnits();
  const targets = referenced(block, results).filter((item) => item.type === "value");
  const finding = findings.find((candidate) => candidate.id === block.finding);
  const sentence =
    fillText(block.text, results, language, units) ?? fillText(finding?.text, results, language, units);

  const caption = (item: StepResult) =>
    [
      item.period ? `${t("blocks.period")}: ${periodText(item.period, language)}` : "",
      item.support?.resolution_m !== undefined
        ? `${t("blocks.resolution")}: ${resolutionText(item.support.resolution_m, language)}`
        : "",
      ...[...new Set(item.sources.filter((source) => source.source !== "none").map((source) => sourceDisplayName(source, results, catalog?.sources, language)))].map((source) => `${t("blocks.source")}: ${source}`),
    ]
      .filter(Boolean)
      .join(" · ");

  const first = targets[0];
  const title =
    first ? variableLabel(first.variable) : t("design.exposure");

  return (
    <Section title={title}>
      {targets.length === 0 && <p className="body text-muted">{t("blocks.noValue")}</p>}
      <div className={`grid gap-12 ${targets.length > 1 ? "md:grid-cols-2" : ""}`}>
        {targets.map((item) => {
          const measured =
            item.value === undefined
              ? null
              : formatMeasure(item.value, item.unit, language, units, item.method?.operation, false);
          const unit = localizedUnit(item.unit, units, language);
          const interval = item.interval;
          const low = interval ? formatMeasure(interval.low, item.unit, language, units, item.method?.operation, false) : null;
          const high = interval ? formatMeasure(interval.high, item.unit, language, units, item.method?.operation, false) : null;
          const evidence = evidenceOf(item);
          return (
            <figure key={item.id} className="flex flex-col gap-6 rounded-lg border border-line bg-paper p-12">
              <figcaption className="label text-muted">
                {variableLabel(item.variable)}
              </figcaption>
              {item.absence ? (
                <Absence item={item} />
              ) : (
                <>
                  <p className="figure">
                    {measured ? measured.text : t("blocks.noValue")}{" "}
                    <span className="whitespace-nowrap">
                      {unit && <span className="small text-muted">{unit}</span>}
                      <DataClassMark value={item.data_class} />
                    </span>
                  </p>
                  {low && high && (
                    <p className="data text-muted">
                      {t("blocks.interval")}{" "}
                      {new Intl.NumberFormat(language, { style: "percent" }).format(interval!.level)}: {low.text} –{" "}
                      {high.text} {high.unit}
                    </p>
                  )}
                  <DataClassTag value={item.data_class} />
                  {evidence && (
                    <div className="flex flex-col gap-2">
                      <span className="label text-muted">{t(evidence.name)}</span>
                      <span className="small text-muted">{t(evidence.note)}</span>
                    </div>
                  )}
                  <p className="caption text-muted">
                    {caption(item)}
                    {notes}
                  </p>
                  {item.limits && item.limits.length > 0 && (
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
      {sentence && <p className="body-lg">{sentence}</p>}
    </Section>
  );
}
