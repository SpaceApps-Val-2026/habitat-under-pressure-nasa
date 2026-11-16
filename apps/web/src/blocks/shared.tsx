import type { ReactNode } from "react";

import type { analysis, result } from "../contracts";
import { sourceName, type CatalogSource } from "../catalog/sources";
import { useI18n } from "../i18n";
import { messages } from "../i18n/messages";
import type { MessageKey } from "../i18n/messages";
import type { Units } from "../units";
import { useVariableName } from "./catalog";

export type StoryBlock = NonNullable<analysis.SavedAnalysis["story"]>[string][number];
export type Finding = analysis.SavedAnalysis["findings"][number];
export type StepResult = result.StepResult;

export type BlockProps = {
  block: StoryBlock;
  results: StepResult[];
  findings: Finding[];
  style: string;
  language: string;
  // Human names for the result zones, when the screen knows them (site and reference area).
  zoneLabels?: Record<string, string>;
  // The catalogue's sources, when the screen has fetched them: license and attribution line.
  catalogSources?: CatalogSource[];
  // The page writes the limits and the sources once, in their own sections: the block leaves them out.
  compact?: boolean;
  // The day the analysis was created, as the citations' `[date]` (access date) reads it.
  accessed?: string;
  // Citation markers into the page's numbered sources, written at the end of the block's source line.
  notes?: ReactNode;
  // Folded content (the data behind a chart, the law) shown open: the formats that read it in full.
  expanded?: boolean;
};

export const DATA_CLASS: Record<string, MessageKey> = {
  measured: "dataClass.measured",
  reanalysis: "dataClass.reanalysis",
  extrapolated: "dataClass.extrapolated",
  approximated: "dataClass.approximated",
  illustrative: "dataClass.illustrative",
};

export const EVIDENCE: Record<string, { name: MessageKey; note: MessageKey }> = {
  E0: { name: "evidence.E0.name", note: "evidence.E0.note" },
  E1: { name: "evidence.E1.name", note: "evidence.E1.note" },
  E2: { name: "evidence.E2.name", note: "evidence.E2.note" },
  E3: { name: "evidence.E3.name", note: "evidence.E3.note" },
  E4: { name: "evidence.E4.name", note: "evidence.E4.note" },
  E5: { name: "evidence.E5.name", note: "evidence.E5.note" },
};

// The wording of a result's evidence level. One level reads differently by design: a contrast against a
// control that the server caps at E1 (zone and control already diverged before the milestone) did use a
// comparison area, so it is not "a change without a comparison area".
const CONTROL_OPERATIONS = new Set(["baci_contrast"]);

export function evidenceOf(
  item: StepResult | undefined,
  level: string | undefined = item?.evidence_level,
): { name: MessageKey; note: MessageKey } | undefined {
  if (!level) return undefined;
  const controlled =
    item !== undefined &&
    (CONTROL_OPERATIONS.has(item.method?.operation ?? "") || item.diagnostics?.pre_trend_control === true);
  if (level === "E1" && controlled) return { name: "evidence.E1control.name", note: "evidence.E1control.note" };
  // A series read from its source at E0 was not "only described": its values come from a source. The note says
  // what they are (an approximation when the class is approximated) and that no trend or cause is established.
  if (level === "E0" && item?.method?.operation === "read_series" && !item.absence && (item.series?.length ?? 0) > 0) {
    return item.data_class === "approximated"
      ? { name: "evidence.E0series.name", note: "evidence.E0approximated.note" }
      : { name: "evidence.E0series.name", note: "evidence.E0series.note" };
  }
  return EVIDENCE[level];
}

// The catalogue names its variables by id; without a catalogue fetch, these are the ones the
// example analysis uses. An unknown id still shows as is, never as a made-up name.
export const VARIABLES: Record<string, MessageKey> = {
  ndvi: "variables.ndvi",
  lst: "variables.lst",
  soil_moisture: "variables.soil_moisture",
  water_demand: "variables.water_demand",
  air_temperature: "variables.air_temperature",
  air_temperature_max: "variables.air_temperature_max",
  precipitation: "variables.precipitation",
  tas_mean: "variables.tas_mean",
  tasmax_mean: "variables.tasmax_mean",
  tasmax_max: "variables.tasmax_max",
  days_above_35c: "variables.days_above_35c",
  pr_total: "variables.pr_total",
  pr_max_1day: "variables.pr_max_1day",
  population_in_flood_area: "variables.population_in_flood_area",
  built_up_surface: "variables.built_up_surface",
  built_up_fraction: "variables.built_up_fraction",
};

// An index is dimensionless; its own name is the only unit a reader has. Never invented: only
// the indices listed here show a name.
export const INDEX_UNITS: Record<string, string> = { ndvi: "NDVI" };

export const ABSENCE: Record<string, MessageKey> = {
  no_coverage: "absence.no_coverage",
  insufficient_qa: "absence.insufficient_qa",
  source_unreachable: "absence.source_unreachable",
  needs_package: "absence.needs_package",
  not_computed: "absence.not_computed",
};

export function localized(value: Record<string, string> | undefined, language: string): string {
  return value ? (value[language] ?? value.en ?? "") : "";
}

export function referenced(block: StoryBlock, results: StepResult[]): StepResult[] {
  const ids = block.results ?? [];
  return ids.length > 0 ? results.filter((item) => ids.includes(item.id)) : results;
}

// Counts and other plain numbers; measurements go through formatMeasure. Only sentences that
// name a change ask for the sign.
// Thousands always grouped, also four-digit numbers in Spanish ("1.550 personas"): a reader scans
// a count by its groups. The option is typed as boolean in the ES2022 lib.
export const GROUPING = { useGrouping: "always" as unknown as boolean };

export function formatNumber(value: number, language: string, signed = false): string {
  return new Intl.NumberFormat(language, {
    ...GROUPING,
    maximumFractionDigits: 4,
    signDisplay: signed ? "exceptZero" : "auto",
  })
    .format(value)
    .replace("-", "−");
}

// Operations whose number is a difference, not a level. A difference converts by scale only:
// adding 32 would turn 0.03 °C/year into 32 °F/year. New operations that return differences
// must be added here until the contract carries an explicit quantity flag.
const DIFFERENCE_OPERATIONS = new Set(["trend", "baci_contrast"]);

export function convertValue(
  value: number,
  unit: string | undefined,
  units: Units,
  operation?: string,
): number {
  if (units !== "imperial" || !unit?.includes("°C")) return value;
  const scaleOnly = unit.includes("/") || (operation ? DIFFERENCE_OPERATIONS.has(operation) : false);
  return scaleOnly ? (value * 9) / 5 : (value * 9) / 5 + 32;
}

export function convertUnit(unit: string | undefined, units: Units): string | undefined {
  return units === "imperial" && unit?.includes("°C") ? unit.replace("°C", "°F") : unit;
}

// A small trend reads per decade, the way the composer writes it: the value, the unit and both
// interval bounds move together, so no sentence mixes a per-year figure with a per-decade one.
const DECADE = 10;
const UNIT_WORDS: Record<string, [string, string]> = {
  "/year": ["per year", "por año"],
  "/decade": ["per decade", "por década"],
  "/month": ["per month", "al mes"],
};
// Units that are a word, not a symbol.
const UNIT_NAMES: Record<string, [string, string]> = {
  people: ["people", "personas"],
  percentile: ["percentile", "percentil"],
  years: ["years", "años"],
};
const TERMS: Record<string, [string, string]> = {
  "Sen slope": ["Sen slope", "pendiente de Sen"],
  "Sen slope, Gilbert interval": ["Sen slope, Gilbert interval", "pendiente de Sen, intervalo de Gilbert"],
};

function localizedTerm(name: string, language: string): string {
  const words = TERMS[name];
  return words ? words[language === "es" ? 1 : 0] : name;
}

// Powers written as a reader writes them: m3 is m³, km2 is km².
export function prettyUnit(unit: string): string {
  return unit.replace(/\b(m|km|cm|mm|ha)([23])\b/g, (_, base: string, power: string) => `${base}${power === "2" ? "²" : "³"}`);
}

export function localizedUnit(unit: string | undefined, units: Units, language: string): string {
  const raw = convertUnit(unit, units);
  const converted = raw ? prettyUnit(raw) : raw;
  if (!converted || converted === "1") return "";
  const named = UNIT_NAMES[converted];
  if (named) return named[language === "es" ? 1 : 0];
  const slash = converted.indexOf("/");
  if (slash === -1) return converted;
  const words = UNIT_WORDS[converted.slice(slash)];
  // A dimensionless rate ("1/year") reads as the rate alone: "per year".
  const base = converted.slice(0, slash) === "1" ? "" : `${converted.slice(0, slash)} `;
  return words ? `${base}${words[language === "es" ? 1 : 0]}` : converted;
}

// Python's precision(): as many decimals as the interval width supports, never more than two for
// values above a hundredth (and a little more for the smallest ones).
function decadeDecimals(item: StepResult): number {
  const interval = item.interval;
  if (!interval || !(interval.high > interval.low)) return 2;
  const magnitude = Math.max(Math.abs(interval.low), Math.abs(interval.high)) * DECADE;
  const cap = magnitude < 0.01 ? Math.max(2, 2 - Math.floor(Math.log10(magnitude))) : 2;
  const width = -Math.floor(Math.log10(((interval.high - interval.low) * DECADE) / 4));
  const decimals = Math.min(cap, width);
  return Number.isFinite(decimals) ? Math.max(0, decimals) : 2;
}

export function trendPresentation(
  item: StepResult,
  units: Units,
  language: string,
): { value: string; unit: string; low: string | null; high: string | null } | null {
  const value = item.value;
  if (item.method?.operation !== "trend" || !item.unit?.endsWith("/year")) return null;
  if (typeof value !== "number" || value === 0 || Math.abs(value) >= 0.1) return null;
  const decimals = decadeDecimals(item);
  const format = (number: number) =>
    new Intl.NumberFormat(language,{ minimumFractionDigits: decimals, maximumFractionDigits: decimals })
      .format(convertValue(number * DECADE, item.unit, units, "trend"))
      .replace("-", "−");
  return {
    value: format(value),
    unit: localizedUnit(item.unit.replace("/year", "/decade"), units, language),
    low: item.interval ? format(item.interval.low) : null,
    high: item.interval ? format(item.interval.high) : null,
  };
}

// A measurement with its unit. Four significant digits keep the real precision without float noise.
export function formatMeasure(
  value: number,
  unit: string | undefined,
  language: string,
  units: Units,
  operation?: string,
  signed = true,
  digits = 4,
): { text: string; unit: string | undefined } {
  const converted = convertUnit(unit, units);
  return {
    text: new Intl.NumberFormat(language, {
      ...GROUPING,
      maximumSignificantDigits: digits,
      signDisplay: signed ? "exceptZero" : "auto",
    })
      .format(convertValue(value, unit, units, operation))
      .replace("-", "−"),
    unit: converted === "1" || !converted ? undefined : prettyUnit(converted),
  };
}

export function formatPercent(fraction: number, language: string): string {
  return new Intl.NumberFormat(language, { style: "percent", maximumFractionDigits: 20 }).format(fraction);
}

type FillCopy = (typeof messages)["en"]["blocks"]["fill"];

function fillCopy(language: string): FillCopy {
  return (messages[language as "en" | "es"] ?? messages.en).blocks.fill;
}

// Three significant digits, the same as the composed sentences, and the language separators.
export function fillNumber(value: number, language: string): string {
  return new Intl.NumberFormat(language, { ...GROUPING, maximumSignificantDigits: 3 }).format(value).replace("-", "−");
}

function monthName(month: number, language: string): string {
  return new Intl.DateTimeFormat(language, { month: "long", timeZone: "UTC" }).format(
    new Date(Date.UTC(2000, month - 1, 1)),
  );
}

function monthDate(value: string, language: string): string {
  const match = /^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(value);
  if (!match) return value;
  const year = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12) return value;
  return `${monthName(month, language)}${language === "es" ? " de " : " "}${year}`;
}

function stateText(item: StepResult, language: string, units: Units): string | null {
  const rawUnit = item.unit ?? "";
  const monthly = rawUnit.endsWith("/month");
  const baseUnit = monthly ? rawUnit.slice(0, -"/month".length) : rawUnit;
  const unit = convertUnit(baseUnit, units) ?? "";
  const raw = item.series ?? [];
  if (raw.some((point) => point.model || point.scenario)) return null;
  const seen = new Set<string>();
  const points: { t: string; v: number }[] = [];
  for (const point of raw) {
    if (seen.has(point.t)) return null;
    seen.add(point.t);
    if (typeof point.v === "number" && Number.isFinite(point.v)) points.push({ t: point.t, v: point.v });
  }
  if (points.length === 0) return null;
  points.sort((a, b) => a.t.localeCompare(b.t));
  const values = points.map((point) => convertValue(point.v, baseUnit, units, "read_series"));
  const aggregate = monthly
    ? values.reduce((sum, value) => sum + value, 0)
    : values.reduce((sum, value) => sum + value, 0) / values.length;
  const peakRaw = Math.max(...points.map((point) => point.v));
  const peak = convertValue(peakRaw, baseUnit, units, "read_series");
  // A series of one value a year (every t on the 1st of January) says the year, not the date.
  const yearly = !monthly && points.every((point) => /^\d{4}-01-01$/.test(point.t));
  const dates = points
    .filter((point) => point.v === peakRaw)
    .map((point) => (yearly ? point.t.slice(0, 4) : monthly ? monthDate(point.t, language) : point.t));
  const copy = fillCopy(language);
  const withUnit = (value: number) => `${fillNumber(value, language)}${unit ? ` ${unit}` : ""}`;
  return `${monthly ? copy.total : copy.mean} ${withUnit(aggregate)}, ${copy.maximum} ${withUnit(peak)} (${dates.join(", ")})`;
}

function scenarioText(item: StepResult, language: string, units: Units): string | null {
  const points = item.series ?? [];
  if (points.length === 0) return null;
  const unit = convertUnit(item.unit, units) ?? "";
  const join = language === "es" ? " a " : " to ";
  const parts: string[] = [];
  for (const point of points) {
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
      return null;
    }
    const value = convertValue(point.v, item.unit, units, "climate_delta");
    const low = convertValue(point.low, item.unit, units, "climate_delta");
    const high = convertValue(point.high, item.unit, units, "climate_delta");
    parts.push(
      `${point.t} (${point.scenario}): ${fillNumber(value, language)} ${unit} (${fillNumber(low, language)}${join}${fillNumber(high, language)} ${unit})`,
    );
  }
  return parts.join("; ");
}

function windowText(bounds: unknown, language: string): string | null {
  if (!Array.isArray(bounds) || bounds.length !== 2) return null;
  const start = String(bounds[0]);
  const end = String(bounds[1]);
  const startYear = /^(\d{4})-01-01$/.exec(start)?.[1];
  const endYear = /^(\d{4})-12-31$/.exec(end)?.[1];
  if (startYear && endYear) {
    if (startYear === endYear) return startYear;
    return language === "es" ? `de ${startYear} a ${endYear}` : `${startYear} to ${endYear}`;
  }
  if (startYear) {
    const until = monthDate(end, language);
    return language === "es" ? `${startYear} a ${until}` : `${startYear} to ${until}`;
  }
  if (start === end) return monthDate(start, language);
  if (/^\d{4}$/.test(start) && /^\d{4}$/.test(end)) {
    return language === "es" ? `de ${start} a ${end}` : `${start} to ${end}`;
  }
  const from = monthDate(start, language);
  const to = monthDate(end, language);
  return language === "es" ? `${from} a ${to}` : `${from} to ${to}`;
}

function methodText(item: StepResult, language: string): string | null {
  const method = item.method as { operation?: string; params?: Record<string, unknown> } | undefined;
  if (!method) return null;
  const params = method.params ?? {};
  const before = windowText(params.before, language);
  const after = windowText(params.after, language);
  if (before && after) return `${before} / ${after}`;
  if (
    (method.operation === "rain_extreme" || method.operation === "same_date_anomaly") &&
    Array.isArray(params.months) &&
    params.year !== undefined
  ) {
    const year = Number(params.year);
    const months = [...new Set(params.months.map((month) => Number(month)))]
      .filter((month) => Number.isInteger(month) && month >= 1 && month <= 12)
      .sort((a, b) => a - b);
    if (!Number.isInteger(year) || months.length === 0) return null;
    const groups: number[][] = [];
    for (const month of months) {
      const last = groups[groups.length - 1];
      if (last && month === last[last.length - 1] + 1) last.push(month);
      else groups.push([month]);
    }
    const suffix = language === "es" ? " de " : " ";
    const join = language === "es" ? " a " : " to ";
    return groups
      .map((group) => {
        const first = group[0];
        const last = group[group.length - 1];
        if (first === last) return `${monthName(first, language)}${suffix}${year}`;
        return `${monthName(first, language)}${join}${monthName(last, language)}${suffix}${year}`;
      })
      .join(" / ");
  }
  if (item.interval?.method) return localizedTerm(item.interval.method, language);
  return null;
}

// The one row of a resource estimate (`resource_estimate`), read the way the server's writer reads it:
// its range, the size it assumes and what that size is counted in. Null when the row is incomplete.
export type ResourceRow = {
  low: number;
  high: number;
  quantity: number;
  per: string;
  reference: string;
  basis: string;
  quantityLow?: number;
  quantityHigh?: number;
};

export function resourceRow(item: StepResult): ResourceRow | null {
  if (item.method?.operation !== "resource_estimate") return null;
  const table = item.table;
  if (!table || !Array.isArray(table.rows) || table.rows.length !== 1) return null;
  const columns = table.columns;
  const fields = ["low", "high", "reference", "quantity", "per"];
  if (!fields.every((field) => columns.filter((column) => column === field).length === 1)) return null;
  const row = table.rows[0];
  if (!Array.isArray(row) || row.length !== columns.length) return null;
  const get = (field: string) => row[columns.indexOf(field)];
  const number = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
  const low = number(get("low"));
  const high = number(get("high"));
  const quantity = number(get("quantity"));
  const per = get("per");
  const reference = get("reference");
  if (low === undefined || high === undefined || quantity === undefined || low > high) return null;
  if (typeof per !== "string" || !per || typeof reference !== "string") return null;
  const basis = columns.includes("basis") && typeof get("basis") === "string" ? (get("basis") as string) : "assumed";
  return {
    low,
    high,
    quantity,
    per,
    reference,
    basis,
    quantityLow: columns.includes("quantity_low") ? number(get("quantity_low")) : undefined,
    quantityHigh: columns.includes("quantity_high") ? number(get("quantity_high")) : undefined,
  };
}

// The short form of the estimate for a sentence: the size it assumes and the range. The reference
// goes to the sources and the limits, where it is written once.
function resourceText(item: StepResult, row: ResourceRow, language: string, units: Units): string {
  const copy = fillCopy(language);
  const unit = localizedUnit(item.unit, units, language);
  const join = language === "es" ? " a " : " to ";
  const range = `${copy.range}: ${language === "es" ? "de " : ""}${fillNumber(row.low, language)}${join}${fillNumber(row.high, language)}${unit ? ` ${unit}` : ""}`;
  const per = UNIT_NAMES[row.per]?.[language === "es" ? 1 : 0] ?? row.per;
  if (row.basis === "per_unit") return `${copy.per} ${row.per === "people" ? copy.person : per}; ${range}`;
  const label = row.basis === "input" ? copy.quantity : row.basis === "typical" ? copy.example : copy.assumed;
  return `${label}: ${fillNumber(row.quantity, language)} ${per}; ${range}`;
}

function tableText(item: StepResult, language: string, units: Units): string | null {
  const resource = resourceRow(item);
  if (resource) return resourceText(item, resource, language, units);
  const table = item.table;
  if (!table) return null;
  const columns = table.columns;
  const fields = ["horizon", "model_variance", "scenario_variance"] as const;
  if (!fields.every((field) => columns.filter((column) => column === field).length === 1)) return null;
  const indices = fields.map((field) => columns.indexOf(field));
  const rows = table.rows;
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const rawUnit = item.unit ?? "";
  const squared = rawUnit.includes("°C^2");
  const factor = units === "imperial" && squared ? (9 / 5) ** 2 : 1;
  const unit = units === "imperial" && squared ? rawUnit.replace("°C^2", "°F^2") : rawUnit;
  const copy = fillCopy(language);
  const parts: string[] = [];
  for (const row of rows) {
    if (!Array.isArray(row) || row.length !== columns.length) return null;
    const [horizon, model, scenario] = indices.map((index) => row[index]);
    if (
      typeof horizon !== "number" ||
      !Number.isInteger(horizon) ||
      typeof model !== "number" ||
      typeof scenario !== "number" ||
      !Number.isFinite(model) ||
      !Number.isFinite(scenario) ||
      model < 0 ||
      scenario < 0
    ) {
      return null;
    }
    parts.push(
      `${horizon}: ${copy.models} ${fillNumber(model * factor, language)}, ${copy.scenarios} ${fillNumber(scenario * factor, language)}`,
    );
  }
  return `${parts.join("; ")}${unit ? ` ${unit}` : ""}`;
}

function intervalText(item: StepResult, language: string, units: Units): string | null {
  const interval = item.interval;
  if (!interval || typeof interval.low !== "number" || typeof interval.high !== "number") return null;
  const operation = item.method?.operation;
  const trend = trendPresentation(item, units, language);
  // The composer's interval carries no sign: a bound is a position, not a change. A small trend
  // moves both bounds to the decade, with the same decimals as its slope.
  const low = trend?.low ?? formatMeasure(interval.low, item.unit, language, units, operation, false).text;
  const high = trend?.high ?? formatMeasure(interval.high, item.unit, language, units, operation, false).text;
  if (interval.level === undefined) return `${low}–${high}`;
  const level = `${fillNumber(interval.level * 100, language)}${language === "es" ? " %" : "%"}`;
  return language === "es" ? `intervalo del ${level}: de ${low} a ${high}` : `${level} interval: ${low} to ${high}`;
}

function statisticText(item: StepResult, language: string): string | null {
  const statistic = item.statistic;
  if (!statistic?.name) return null;
  const details = [localizedTerm(statistic.name, language)];
  if (typeof statistic.value === "number") details.push(fillNumber(statistic.value, language));
  for (const [key, label] of [["p_adjusted", language === "es" ? "p ajustada" : "adjusted p"], ["p", "p"], ["n", "n"]] as const) {
    const value = statistic[key];
    if (typeof value === "number" && !(key === "p" && statistic.p_adjusted !== undefined)) {
      // p-values follow the one rule (formatP); "p < 0.001" never reads "p = < 0.001".
      const shown = key === "n" ? fillNumber(value, language) : formatP(value, language);
      details.push(shown.startsWith("<") ? `${label} ${shown}` : `${label} = ${shown}`);
    }
  }
  return details.join(", ");
}

const DIAGNOSTIC_LABELS: Record<string, [string, string]> = {
  n_dates: ["dates", "fechas"],
  n_years: ["years", "años"],
  n_effective: ["effective sample", "muestra efectiva"],
  correction: ["correction", "corrección"],
  autocorrelation_lag1: ["lag-one autocorrelation", "autocorrelación de orden uno"],
  threshold_c: ["hot-cell threshold (°C, surface)", "umbral de celda caliente (°C, superficie)"],
  threshold_percentile: ["threshold percentile", "percentil del umbral"],
  threshold_base_start: ["threshold base from", "base del umbral desde"],
  threshold_base_end: ["threshold base to", "base del umbral hasta"],
  threshold_n_values: ["pixel-years in the base", "píxeles-año en la base"],
};

// A p-value read the same everywhere: below one in a thousand it is "< 0.001"; otherwise two
// significant figures.
export function formatP(value: number, language: string): string {
  if (value < 0.001) return `< ${new Intl.NumberFormat(language, { minimumFractionDigits: 3 }).format(0.001)}`;
  return new Intl.NumberFormat(language, { maximumSignificantDigits: 2 }).format(value);
}

// Every diagnostic a result carries, as label and value: the known ones in words, the rest by their key.
export function diagnosticEntries(item: StepResult, language: string): [string, string][] {
  const yes = language === "es" ? "sí" : "yes";
  const no = "no";
  return Object.entries(item.diagnostics ?? {}).map(([key, value]) => {
    const label = DIAGNOSTIC_LABELS[key]?.[language === "es" ? 1 : 0] ?? key;
    const shown =
      typeof value === "number"
        ? fillNumber(value, language)
        : typeof value === "boolean"
          ? value
            ? yes
            : no
          : typeof value === "object"
            ? JSON.stringify(value)
            : String(value);
    return [label, shown];
  });
}

function diagnosticsText(item: StepResult, language: string): string | null {
  if (!item.diagnostics) return null;
  const details = Object.entries(DIAGNOSTIC_LABELS).flatMap(([key, labels]) => {
    const value = item.diagnostics?.[key];
    if (value === undefined) return [];
    const displayed = typeof value === "number" ? fillNumber(value, language) : String(value);
    return [`${labels[language === "es" ? 1 : 0]}: ${displayed}`];
  });
  return details.length > 0 ? details.join(", ") : null;
}

function fillSeries(item: StepResult, language: string, units: Units): string | null {
  const operation = item.method?.operation;
  if (operation === "read_series") return stateText(item, language, units);
  if (operation === "climate_delta") return scenarioText(item, language, units);
  return null;
}

// An episode rarer than its record can estimate (return_level): an answer, not a gap. Its sentence fills the
// length of the record and the grid even though the result is an absence, as the composer does.
function rareEvent(item: StepResult): boolean {
  const diagnostics = item.diagnostics ?? {};
  return (
    item.method?.operation === "return_level" &&
    item.absence?.reason === "not_computed" &&
    Number.isInteger(diagnostics.event_year) &&
    Number.isInteger(diagnostics.n_years)
  );
}

// The composer should send filled sentences; while it does not, the web substitutes {result.value},
// {result.unit}, {result.series}, {result.method} and {result.table} from the results it already has,
// and hides any sentence it cannot complete.
export function fillText(
  value: Record<string, string> | undefined,
  results: StepResult[],
  language: string,
  units: Units,
): string | null {
  if (!value) return null;
  const text = value[language] ?? value.en ?? "";
  if (!text) return null;
  const byId = new Map(results.map((item) => [item.id, item]));
  const filled = text.replace(
    /\{([a-z0-9][a-z0-9_.-]*)\.(value|unit|series|method|table|interval|statistic|diagnostics|period|support|return_period|record|event_value)\}/g,
    (match, id: string, field: string) => {
      const item = byId.get(id);
      if (!item || (item.absence && !(rareEvent(item) && ["record", "support", "event_value"].includes(field)))) {
        return match;
      }
      const years = item.method?.operation === "return_level" ? item.diagnostics : undefined;
      if (field === "return_period" && !item.absence && Number.isInteger(years?.return_period_years)) {
        return fillNumber(years!.return_period_years as number, language);
      }
      if (field === "record" && Number.isInteger(years?.n_years)) return fillNumber(years!.n_years as number, language);
      // The wettest day of the episode, read from its daily series (return_level 1.2), in mm as the sentence says.
      if (field === "event_value" && typeof years?.event_day === "string" && Number.isFinite(years?.event_value)) {
        return fillNumber(years!.event_value as number, language);
      }
      const trend = trendPresentation(item, units, language);
      if (field === "value" && item.value !== undefined) {
        return trend
          ? trend.value
          : fillNumber(convertValue(item.value, item.unit, units, item.method?.operation), language);
      }
      if (field === "unit") return trend ? trend.unit : localizedUnit(item.unit, units, language);
      if (field === "series") return fillSeries(item, language, units) ?? match;
      if (field === "method") return methodText(item, language) ?? match;
      if (field === "table") return tableText(item, language, units) ?? match;
      if (field === "interval") return intervalText(item, language, units) ?? match;
      if (field === "statistic") return statisticText(item, language) ?? match;
      if (field === "diagnostics") return diagnosticsText(item, language) ?? match;
      if (field === "period" && item.period) {
        return windowText([item.period.start, item.period.end], language) ?? match;
      }
      if (field === "support" && item.support?.resolution_m !== undefined) {
        return `${fillNumber(item.support.resolution_m / 1000, language)} km`;
      }
      return match;
    },
  );
  return filled.includes("{") || filled.includes("}") ? null : filled;
}

// A chart's axis title: the variable's name and its unit, "Land surface temperature (°C)".
export function axisLabel(name: string | undefined, unit: string | undefined): string | null {
  if (name && unit) return `${name} (${unit})`;
  return name || unit || null;
}

export function unitOf(item: StepResult, units: Units = "metric"): string {
  const unit = convertUnit(item.unit, units);
  return unit && unit !== "1" ? prettyUnit(unit) : "";
}

// A period as a reader says it: whole years as "de 1981 a 2025", otherwise months, never ISO.
export function periodText(period: { start: string; end: string } | undefined, language: string): string {
  if (!period?.start || !period?.end) return "";
  return windowText([period.start, period.end], language) ?? `${period.start} – ${period.end}`;
}

// A resolution in the unit a reader uses: kilometres from one kilometre up, metres below.
export function resolutionText(metres: number, language: string): string {
  return metres >= 1000 ? `${fillNumber(metres / 1000, language)} km` : `${fillNumber(metres, language)} m`;
}

// An estimate from published coefficients is not a measurement; its evidence reads as such.
export function isEstimate(item: StepResult): boolean {
  return resourceRow(item) !== null;
}

// The classes that are not a direct measurement: they read as an asterisk after the value (DataClassMark),
// not as a tag.
export const MARKED_CLASSES = new Set(["reanalysis", "extrapolated", "approximated", "illustrative"]);

export function isMarked(value: string | undefined): boolean {
  return value !== undefined && MARKED_CLASSES.has(value);
}

export function DataClassTag({ value }: { value: string }) {
  const { t } = useI18n();
  if (isMarked(value)) return null;
  return (
    <span data-class={value} className="tag label inline-flex w-fit items-center rounded-xs border border-line-strong px-6 py-2 text-muted">
      {DATA_CLASS[value] ? t(DATA_CLASS[value]) : value}
    </span>
  );
}

// A result that was computed but whose interval spans both directions is not a missing figure: it
// is the answer "no clear difference". Every other absence keeps its reason.
export function noClearDifference(item: StepResult): boolean {
  return item.absence?.reason === "not_computed" && item.direction_assertable === false;
}

export function absenceLabel(item: StepResult, t: (key: MessageKey) => string): string {
  if (!item.absence) return "";
  if (noClearDifference(item)) return t("blocks.noClearDifference");
  return ABSENCE[item.absence.reason] ? t(ABSENCE[item.absence.reason]) : item.absence.reason;
}

// The server writes some details with the variable's id; the reader sees its catalogue name.
export function absenceDetail(item: StepResult, language: string, name: (id: string) => string): string {
  const detail = item.absence?.detail ? localized(item.absence.detail, language) : "";
  if (!detail || !item.variable) return detail;
  const label = name(item.variable);
  if (!label || label === item.variable) return detail;
  return detail.replace(new RegExp(`(^|[^A-Za-z0-9_])${item.variable}(?![A-Za-z0-9_])`, "g"), (_, lead: string) => `${lead}${language === "es" ? `«${label}»` : `“${label}”`}`);
}

export function Absence({ item }: { item: StepResult }) {
  const { t, language } = useI18n();
  const name = useVariableName(VARIABLES);
  if (!item.absence) return null;
  const detail = absenceDetail(item, language, name);
  return (
    <div className="flex flex-col gap-4">
      <span className="label text-amber-ink">{absenceLabel(item, t)}</span>
      {detail && <p className="small text-muted">{detail}</p>}
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-8 rounded-lg border border-line bg-paper p-16">
      <h2 className="title-2">{title}</h2>
      {children}
    </section>
  );
}

type SourceRef = StepResult["sources"][number];

// The archetype an estimate used, as the result carries it: its id doubles as the source id.
function archetypeOf(item: StepResult): { id?: string; name?: Record<string, string> } | undefined {
  const params = (item.method as { params?: Record<string, unknown> } | undefined)?.params;
  const archetype = params?.archetype;
  return archetype && typeof archetype === "object" ? (archetype as { id?: string; name?: Record<string, string> }) : undefined;
}

// What a figure is about: the archetype's name for an estimate, otherwise the variable's name.
export function figureName(item: StepResult, language: string, name: (id: string) => string): string {
  const archetype = archetypeOf(item);
  return (archetype?.name && localized(archetype.name, language)) || name(item.variable);
}

// One entry per distinct source, with the period of the first result that cites it: the citation's
// `[year]` is the data's year. "none" marks a result with no source and is never listed.
export function distinctSources(results: StepResult[]): { source: SourceRef; period: StepResult["period"] }[] {
  return [
    ...new Map(
      results
        .flatMap((item) => item.sources.map((source) => ({ source, period: item.period })))
        .filter((entry) => entry.source.source && entry.source.source !== "none")
        .map((entry) => [`${entry.source.source}|${entry.source.url}`, entry]),
    ).values(),
  ];
}

// A source's name: the catalogue's, else the archetype's when the source is an estimate's reference,
// else its id.
export function sourceDisplayName(
  source: SourceRef,
  results: StepResult[],
  catalogSources: CatalogSource[] | undefined,
  language: string,
): string {
  const named = catalogSources?.find((entry) => entry.id === source.source);
  if (named) return sourceName(source, catalogSources, language);
  for (const item of results) {
    const archetype = archetypeOf(item);
    if (!archetype?.id || !archetype.name) continue;
    if (archetype.id === source.source) return localized(archetype.name, language);
    // The reference of the example size, apart from the coefficients' one.
    if (`${archetype.id}-typical` === source.source) {
      return `${localized(archetype.name, language)} · ${fillCopy(language).example}`;
    }
  }
  return source.source;
}
