import * as Plot from "@observablehq/plot";
import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "../i18n";
import { useUnits } from "../units";
import {
  localizedUnit,
  axisLabel,
  Absence,
  convertValue,
  DATA_CLASS,
  isMarked,
  fillText,
  formatMeasure,
  referenced,
  Section,
  VARIABLES,
  periodText,
  sourceDisplayName,
  type BlockProps,
} from "./shared";
import { useCatalog, useVariableName } from "./catalog";
import { DataClassMark } from "./DataClassMark";

// A list cut into n nearly equal runs, in order.
function chunks<T>(items: T[], n: number): T[][] {
  const size = Math.ceil(items.length / n);
  return Array.from({ length: n }, (_, index) => items.slice(index * size, (index + 1) * size)).filter((run) => run.length > 0);
}

export function TrendChart({ block, results, findings, language, zoneLabels, catalogSources, notes, expanded = false }: BlockProps) {
  const { t } = useI18n();
  const variableLabel = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const { units } = useUnits();
  const target = referenced(block, results).find((item) => item.type === "series");
  const zone = target?.zone ? zoneLabels?.[target.zone] : undefined;
  const name = target ? variableLabel(target.variable) : undefined;
  // The catalogue names each source; the id is only the fallback.
  const source = target?.sources[0];
  const sourceLabel = source && source.source !== "none" ? sourceDisplayName(source, results, catalogSources ?? catalog?.sources, language) : undefined;
  const title = [name, zone, sourceLabel].filter(Boolean).join(" · ") || t("blocks.series");
  const points = useMemo(
    () =>
      (target?.series ?? [])
        .filter((point) => point.v !== undefined && point.v !== null)
        .map((point) => ({
          date: new Date(point.t),
          v: convertValue(point.v as number, target?.unit, units, target?.method?.operation),
          low: point.low !== undefined ? convertValue(point.low, target?.unit, units, target?.method?.operation) : undefined,
          high: point.high !== undefined ? convertValue(point.high, target?.unit, units, target?.method?.operation) : undefined,
        }))
        .filter((point) => !Number.isNaN(point.date.getTime())),
    [target, units],
  );
  const band = points.some((point) => point.low !== undefined && point.high !== undefined);
  const withIntervals = band;
  // A yearly series reads years on the axis; a monthly one reads month and year. Both in the screen's language.
  const annual = points.length > 0 && points.every((point) => point.date.getMonth() === 0 && point.date.getDate() === 1);
  const axisNumber = useMemo(() => new Intl.NumberFormat(language, { maximumFractionDigits: 1 }), [language]);
  // The y axis says what it measures in words and unit, never an arrow that reads like a "1".
  const axisTitle = axisLabel(name, localizedUnit(target?.unit, units, language));
  // Several years read in years, one tick per ~90 px; a short series keeps months.
  const spanYears =
    points.length > 1 ? (points[points.length - 1].date.getTime() - points[0].date.getTime()) / (365.25 * 864e5) : 0;
  const container = useRef<HTMLDivElement>(null);
  const [chartFailed, setChartFailed] = useState(false);

  useEffect(() => {
    const node = container.current;
    if (!node || !target || points.length === 0) return;
    setChartFailed(false);
    const marks = [
      Plot.lineY(points, { x: "date", y: "v", stroke: "var(--signal)" }),
      Plot.dot(points, { x: "date", y: "v", fill: "var(--signal)" }),
    ];
    if (band) {
      marks.unshift(
        Plot.areaY(points, { x: "date", y1: "low", y2: "high", fill: "var(--topic, var(--signal))", fillOpacity: 0.3 }),
      );
    }
    let chart: ReturnType<typeof Plot.plot> | null = null;
    try {
      // The figure fills the article column: a fixed width leaves the chart small in a wide page.
      const plotWidth = Math.max(320, node.clientWidth || 640);
      chart = Plot.plot({
        width: plotWidth,
        height: 240,
        marginLeft: 56,
        marginBottom: 32,
        x: {
          label: null,
          type: "time",
          ticks: Math.max(2, Math.min(Math.floor(plotWidth / 90), annual || spanYears > 2 ? Math.ceil(spanYears) + 1 : 12)),
          // Years for a yearly series, month and year for a monthly one: the language of the screen.
          tickFormat: (value: Date) =>
            annual || spanYears > 2
              ? new Intl.DateTimeFormat(language, { year: "numeric" }).format(value)
              : new Intl.DateTimeFormat(language, { month: "short", year: "numeric" }).format(value),
        },
        y: { label: axisTitle, labelArrow: "none", grid: true, tickFormat: (value: number) => axisNumber.format(value) },
        style: { background: "transparent" },
        marks,
      });
      node.append(chart);
      // Plot labels its marks with aria-label on <g>, which is prohibited without a role.
      // The container already carries the chart's accessible name.
      chart.querySelectorAll("g[aria-label]").forEach((group) => group.removeAttribute("aria-label"));
    } catch {
      setChartFailed(true);
    }
    return () => {
      chart?.remove();
    };
  }, [points, band, target, language, annual, axisNumber, axisTitle, spanYears]);

  const dataClass =
    target && isMarked(target.data_class)
      ? undefined
      : target && DATA_CLASS[target.data_class]
        ? t(DATA_CLASS[target.data_class])
        : target?.data_class;
  const finding = findings.find((item) => item.id === block.finding);
  const sentence =
    fillText(block.text, results, language, units) ?? fillText(finding?.text, results, language, units);

  return (
    <Section title={title}>
      {!target && <p className="body text-muted">{t("blocks.noSeries")}</p>}
      {target?.absence && <Absence item={target} />}
      {target && !target.absence && points.length === 0 && <p className="body text-muted">{t("blocks.noPoints")}</p>}
      {target && !target.absence && points.length > 0 && (
        <>
          <div
            ref={container}
            role="img"
            aria-label={t("blocks.seriesAlt")}
            className="w-full overflow-x-auto"
          />
          {chartFailed && <p className="small text-amber-ink">{t("blocks.chartFailed")}</p>}
          <p className="caption text-muted">
            {[sourceLabel, dataClass, target.period ? periodText(target.period, language) : ""]
              .filter(Boolean)
              .join(" · ")}
            <DataClassMark value={target?.data_class} />
            {notes}
          </p>
          <details className="small" open={expanded}>
            <summary className="caption w-fit cursor-pointer text-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus">
              {t("blocks.dataBehind")}
            </summary>
            {/* Read in full (scientific), a long series flows in columns; an interval missing on every row is said once. */}
            <div className={`mt-6 w-full overflow-x-auto${expanded && points.length > 15 ? " data-behind-columns" : ""}`}>
              {(expanded && points.length > 15 ? chunks(points, 3) : [points]).map((rows, index) => (
                <table key={index} className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-line">
                      <th scope="col" className="label py-4 text-muted">{t("blocks.period")}</th>
                      <th scope="col" className="label py-4 text-muted">{t("blocks.value")}</th>
                      {withIntervals && <th scope="col" className="label py-4 text-muted">{t("blocks.interval")}</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((point) => {
                      const value = formatMeasure(point.v, target.unit, language, units, target.method?.operation);
                      const low = point.low !== undefined ? formatMeasure(point.low, target.unit, language, units, target.method?.operation, false) : null;
                      const high = point.high !== undefined ? formatMeasure(point.high, target.unit, language, units, target.method?.operation, false) : null;
                      return (
                        <tr key={point.date.toISOString()} className="border-b border-line">
                          <td className="data py-4 whitespace-nowrap">
                            {new Intl.DateTimeFormat(language, { year: "numeric", month: "short", timeZone: "UTC" }).format(point.date)}
                          </td>
                          <td className="data py-4 whitespace-nowrap">
                            {value.text} {value.unit}
                          </td>
                          {withIntervals && (
                            <td className="data py-4 whitespace-nowrap">
                              {low && high ? `${low.text} – ${high.text} ${low.unit}` : t("blocks.noValue")}
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ))}
            </div>
            {!withIntervals && <p className="caption mt-4 text-muted">{t("blocks.noIntervalPerValue")}</p>}
          </details>
        </>
      )}
      {sentence && <p className="body-lg">{sentence}</p>}
    </Section>
  );
}
