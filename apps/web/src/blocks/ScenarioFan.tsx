import * as Plot from "@observablehq/plot";
import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "../i18n";
import { useUnits } from "../units";
import {
  localizedUnit,
  axisLabel,
  Absence,
  convertUnit,
  convertValue,
  DATA_CLASS,
  isMarked,
  fillText,
  referenced,
  Section,
  VARIABLES,
  sourceDisplayName,
  type BlockProps,
} from "./shared";
import { useCatalog, useVariableName } from "./catalog";
import { DataClassMark } from "./DataClassMark";

// A scenario fan: one median line per scenario and the spread across models at each horizon.
// The band is model spread, never a confidence interval, and the legend says so in words as
// well as in colour.
const SCENARIO_COLOURS = ["var(--heat-1)", "var(--heat-2)", "var(--heat-4)", "var(--heat-5)"];

type FanPoint = { horizon: string; scenario: string; v: number; low: number; high: number };

export function ScenarioFan({ block, results, findings, language, catalogSources, notes }: BlockProps) {
  const { t } = useI18n();
  const variableLabel = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const { units } = useUnits();
  const target = referenced(block, results).find(
    (item) => item.type === "series" && (item.series ?? []).some((point) => point.scenario),
  );
  const unit = convertUnit(target?.unit, units);
  const name = target ? variableLabel(target.variable) : undefined;
  // The y axis in words and unit, never an arrow that reads like a "1".
  const axisTitle = axisLabel(name, localizedUnit(target?.unit, units, language));
  const title = [name, t("blocks.fan")].filter(Boolean).join(" · ");
  const operation = target?.method?.operation;
  const points = useMemo<FanPoint[]>(() => {
    const made: FanPoint[] = [];
    for (const point of target?.series ?? []) {
      if (
        typeof point.t !== "string" ||
        typeof point.scenario !== "string" ||
        typeof point.v !== "number" ||
        typeof point.low !== "number" ||
        typeof point.high !== "number" ||
        !Number.isFinite(point.v) ||
        !Number.isFinite(point.low) ||
        !Number.isFinite(point.high) ||
        point.low > point.v ||
        point.v > point.high
      ) {
        return [];
      }
      made.push({
        horizon: point.t,
        scenario: point.scenario,
        v: convertValue(point.v, target?.unit, units, operation),
        low: convertValue(point.low, target?.unit, units, operation),
        high: convertValue(point.high, target?.unit, units, operation),
      });
    }
    return made;
  }, [target, units, operation]);
  const scenarios = useMemo(
    () => [...new Set(points.map((point) => point.scenario))].sort(),
    [points],
  );
  const horizons = useMemo(
    () => [...new Set(points.map((point) => point.horizon))].sort((left, right) => left.localeCompare(right, "en", { numeric: true })),
    [points],
  );
  const ranges = useMemo(
    () => scenarios.map((_, index) => SCENARIO_COLOURS[index % SCENARIO_COLOURS.length]),
    [scenarios],
  );
  const colour = (scenario: string) => ranges[scenarios.indexOf(scenario)];
  const container = useRef<HTMLDivElement>(null);
  const [chartFailed, setChartFailed] = useState(false);

  useEffect(() => {
    const node = container.current;
    if (!node || points.length === 0) return;
    setChartFailed(false);
    let chart: ReturnType<typeof Plot.plot> | null = null;
    try {
      const plotWidth = Math.max(320, node.clientWidth || 640);
      chart = Plot.plot({
        width: plotWidth,
        height: 240,
        marginLeft: 56,
        marginBottom: 32,
        x: { label: null, type: "point", domain: horizons },
        y: { label: axisTitle, labelArrow: "none", grid: true },
        color: { domain: scenarios, range: ranges },
        style: { background: "transparent" },
        marks: [
          Plot.areaY(points, { x: "horizon", y1: "low", y2: "high", fill: "scenario", fillOpacity: 0.14 }),
          Plot.lineY(points, { x: "horizon", y: "v", stroke: "scenario" }),
          Plot.dot(points, { x: "horizon", y: "v", fill: "scenario" }),
        ],
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
  }, [points, horizons, scenarios, ranges, unit, axisTitle]);

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
      {target && !target.absence && points.length === 0 && <p className="body text-muted">{t("blocks.fanNoPoints")}</p>}
      {points.length > 0 && (
        <>
          <div ref={container} role="img" aria-label={t("blocks.fanAlt")} className="w-full overflow-x-auto" />
          {chartFailed && <p className="small text-amber-ink">{t("blocks.chartFailed")}</p>}
          <ul className="flex flex-wrap gap-x-12 gap-y-4">
            {scenarios.map((scenario) => (
              <li key={scenario} className="flex items-center gap-6">
                <span
                  aria-hidden="true"
                  className="inline-block size-8 rounded-xs"
                  style={{ background: colour(scenario) }}
                />
                <span className="data text-muted">{scenario}</span>
              </li>
            ))}
          </ul>
          <p className="caption text-muted">
            {[
              target?.sources[0] && target.sources[0].source !== "none"
                ? sourceDisplayName(target.sources[0], results, catalogSources ?? catalog?.sources, language)
                : undefined,
              dataClass,
              t("blocks.fanSpread"),
            ]
              .filter(Boolean)
              .join(" · ")}
            <DataClassMark value={target?.data_class} />
            {notes}
          </p>
        </>
      )}
      {sentence && <p className="body-lg">{sentence}</p>}
    </Section>
  );
}
