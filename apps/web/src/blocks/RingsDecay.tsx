import * as Plot from "@observablehq/plot";
import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "../i18n";
import { useUnits, type Units } from "../units";
import {
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
  periodText,
  sourceDisplayName,
  type BlockProps,
  type StepResult,
} from "./shared";
import { useCatalog, useVariableName } from "./catalog";
import { DataClassMark } from "./DataClassMark";

type RingPoint = { distance: number; label: string; v: number; low?: number; high?: number };

// A ring arrives either as a series whose x is a distance ("1", "1–2 km") or as one value per
// ring, with its edges in the method parameters. A date or a year is never a distance.
export function parseDistance(text: string): number | null {
  const token = text.trim();
  if (/^\d{4}(?:-\d{2})?/.test(token)) return null;
  const numbers = token.replace(",", ".").match(/\d+(?:\.\d+)?/g);
  if (!numbers || numbers.length > 2) return null;
  const from = Number(numbers[0]);
  const to = numbers[1] === undefined ? from : Number(numbers[1]);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from || to > 500) return null;
  return (from + to) / 2;
}

function numberParam(params: Record<string, unknown>, key: string): number | null {
  const value = params[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function ringEdges(params: Record<string, unknown> | undefined): [number, number] | null {
  if (!params) return null;
  const ring = params.ring_km;
  if (Array.isArray(ring) && ring.length === 2 && ring.every((edge) => typeof edge === "number")) {
    return [ring[0] as number, ring[1] as number];
  }
  const from = numberParam(params, "ring_from_km") ?? numberParam(params, "distance_km");
  const to = numberParam(params, "ring_to_km") ?? numberParam(params, "distance_km");
  return from !== null && to !== null && to >= from ? [from, to] : null;
}

function shift(item: StepResult, value: number, units: Units): number {
  return convertValue(value, item.unit, units, item.method?.operation);
}

// The change against distance, one bar per ring, with its interval when the result carries one.
export function RingsDecay({ block, results, findings, language, notes }: BlockProps) {
  const { t } = useI18n();
  const variableLabel = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const { units } = useUnits();
  const targets = referenced(block, results);
  const { points, item } = useMemo(() => {
    const made: RingPoint[] = [];
    let source: StepResult | undefined;
    for (const candidate of targets) {
      if (candidate.absence) continue;
      if (candidate.type === "series") {
        for (const point of candidate.series ?? []) {
          if (typeof point.t !== "string" || typeof point.v !== "number" || !Number.isFinite(point.v)) continue;
          const distance = parseDistance(point.t);
          if (distance === null) continue;
          const low = typeof point.low === "number" && Number.isFinite(point.low) ? point.low : undefined;
          const high = typeof point.high === "number" && Number.isFinite(point.high) ? point.high : undefined;
          if (low !== undefined && high !== undefined && !(low <= point.v && point.v <= high)) continue;
          source ??= candidate;
          made.push({
            distance,
            label: point.t.trim(),
            v: shift(candidate, point.v, units),
            low: low === undefined ? undefined : shift(candidate, low, units),
            high: high === undefined ? undefined : shift(candidate, high, units),
          });
        }
      }
      if (candidate.type === "value" && typeof candidate.value === "number" && Number.isFinite(candidate.value)) {
        const edges = ringEdges(candidate.method?.params as Record<string, unknown> | undefined);
        if (!edges) continue;
        source ??= candidate;
        const interval = candidate.interval;
        made.push({
          distance: (edges[0] + edges[1]) / 2,
          label: `${edges[0]}–${edges[1]}`,
          v: shift(candidate, candidate.value, units),
          low: interval ? shift(candidate, interval.low, units) : undefined,
          high: interval ? shift(candidate, interval.high, units) : undefined,
        });
      }
    }
    const unique = new Map<number, RingPoint>();
    for (const point of made) if (!unique.has(point.distance)) unique.set(point.distance, point);
    return { points: [...unique.values()].sort((left, right) => left.distance - right.distance), item: source };
  }, [targets, units]);

  const band = points.some((point) => point.low !== undefined && point.high !== undefined);
  const container = useRef<HTMLDivElement>(null);
  const [chartFailed, setChartFailed] = useState(false);
  // The points array is rebuilt on every render; the chart only needs to be when its numbers move.
  const signature = points.map((point) => `${point.label}|${point.v}|${point.low ?? ""}|${point.high ?? ""}`).join(";");
  const unitLabel = convertUnit(item?.unit, units) ?? "";

  useEffect(() => {
    const node = container.current;
    if (!node || points.length === 0) return;
    setChartFailed(false);
    const labels = points.map((point) => point.label);
    const marks = [
      Plot.barY(points, { x: "label", y: "v", fill: "var(--signal)", fillOpacity: 0.35 }),
      Plot.ruleY([0], { stroke: "var(--line-strong)" }),
    ];
    if (band) {
      marks.push(
        Plot.ruleX(points, { x: "label", y1: "low", y2: "high", stroke: "var(--ink)", strokeWidth: 1.5 }),
      );
    }
    marks.push(Plot.dot(points, { x: "label", y: "v", fill: "var(--ink)" }));
    let chart: ReturnType<typeof Plot.plot> | null = null;
    try {
      chart = Plot.plot({
        // The figure's width, like the other charts, so the drawing never sits in one corner of it.
        width: Math.max(320, node.clientWidth || 640),
        height: 240,
        marginLeft: 56,
        marginBottom: 32,
        x: { label: "km", type: "band", domain: labels },
        y: { label: axisLabel(name, unitLabel), labelArrow: "none", grid: true },
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the signature holds every number drawn
  }, [signature, unitLabel, band]);

  const name = item ? variableLabel(item.variable) : undefined;
  const dataClass =
    item && isMarked(item.data_class)
      ? undefined
      : item && DATA_CLASS[item.data_class]
        ? t(DATA_CLASS[item.data_class])
        : item?.data_class;
  const absence = targets.find((candidate) => candidate.absence);
  const finding = findings.find((candidate) => candidate.id === block.finding);
  const sentence =
    fillText(block.text, results, language, units) ?? fillText(finding?.text, results, language, units);

  return (
    <Section title={name ?? t("blocks.series")}>
      {absence && points.length === 0 && <Absence item={absence} />}
      {!absence && points.length === 0 && <p className="body text-muted">{t("blocks.noPoints")}</p>}
      {points.length > 0 && (
        <>
          <div
            ref={container}
            role="img"
            aria-label={`${t("blocks.series")}${name ? `: ${name}` : ""}`}
            className="w-full overflow-x-auto"
          />
          {chartFailed && <p className="small text-amber-ink">{t("blocks.chartFailed")}</p>}
          <p className="caption text-muted">
            {[item?.sources[0] ? sourceDisplayName(item.sources[0], results, catalog?.sources, language) : undefined, dataClass, item?.period ? periodText(item.period, language) : ""]
              .filter(Boolean)
              .join(" · ")}
            <DataClassMark value={item?.data_class} />
            {notes}
          </p>
        </>
      )}
      {sentence && <p className="body-lg">{sentence}</p>}
    </Section>
  );
}
