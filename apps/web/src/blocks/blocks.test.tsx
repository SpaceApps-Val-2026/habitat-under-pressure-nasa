import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import analysisExample from "../../../../docs/contratos/examples/analysis.json";
import resultsExample from "../../../../docs/contratos/examples/results.json";
import type { analysis, result } from "../contracts";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";
import { NormCard } from "./NormCard";
import { LimitsBox } from "./LimitsBox";
import { ScenarioFan } from "./ScenarioFan";
import { TrendChart } from "./TrendChart";
import { UncertaintySplit } from "./UncertaintySplit";
import { ReportBlocks } from "./index";
import type { StoryBlock } from "./shared";
import { axisLabel, evidenceOf, formatMeasure, formatP, fillText } from "./shared";

const saved = analysisExample as unknown as analysis.SavedAnalysis;
const results = resultsExample as unknown as result.StepResult[];

beforeEach(() => localStorage.setItem("hup-units-choice", "metric"));
const story = saved.story?.journalistic ?? [];

function renderBlocks(blocks: StoryBlock[] = story, items = results) {
  return render(
    <I18nProvider><UnitsProvider>
      <ReportBlocks blocks={blocks} results={items} findings={saved.findings} style="journalistic" language="en" />
    </UnitsProvider></I18nProvider>,
  );
}

test("the journalistic story draws its key figures, evidence, limits and sources", () => {
  renderBlocks();

  expect(screen.getByText("−0.32")).toBeInTheDocument();
  expect(screen.getByText("+1.1")).toBeInTheDocument();
  expect(screen.getAllByText(/95%/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("Coincidence in time and place").length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText(/coincides in time and place/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText("It shows what changed, not who caused it.")).toBeInTheDocument();
  expect(screen.getByText("Needs a downloaded package")).toBeInTheDocument();
  expect(screen.getAllByText("landsat-c2-l2").length).toBeGreaterThanOrEqual(1);
});

test("an unknown block is skipped with a warning, not a crash", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  renderBlocks([{ block: "NoSuchBlock" }]);

  expect(warn).toHaveBeenCalledWith("Unknown report block: NoSuchBlock");
  expect(screen.queryByText("NoSuchBlock")).not.toBeInTheDocument();
  warn.mockRestore();
});

test("the hypotheses block carries the server's finding and limit in the report language", () => {
  render(
    <I18nProvider><UnitsProvider>
      <ReportBlocks blocks={[{ block: "Hypotheses", results: [results[0].id], text: {
        en: "Compatible with the hypothesis that summers with less rain have higher daily highs.",
        es: "Compatible con la hipótesis de que los veranos con menos lluvia tienen máximas más altas; no la demuestra.\nLa celda de POWER describe la comarca.",
      } }]} results={results} findings={[]} style="journalistic" language="es" />
    </UnitsProvider></I18nProvider>,
  );
  expect(screen.getByRole("heading", { name: "Hipótesis" })).toBeInTheDocument();
  expect(screen.getByText(/no la demuestra/)).toBeInTheDocument();
  expect(screen.getByText(/describe la comarca/)).toBeInTheDocument();
});

test("a result with an absence shows the absence, never a zero", () => {
  renderBlocks([{ block: "KeyFigure", results: ["r-s3-series"] }]);

  expect(screen.getByText("Needs a downloaded package")).toBeInTheDocument();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

test("a series draws a summary and, with low and high, says it has a band", () => {
  const base = results.find((item) => item.id === "r-s3-series")!;
  const series = {
    ...base,
    absence: undefined,
    series: [
      { t: "2020-01-01", v: 1, low: 0.5, high: 1.5 },
      { t: "2021-01-01", v: 2, low: 1.5, high: 2.5 },
    ],
  } as result.StepResult;
  render(
    <I18nProvider><UnitsProvider>
      <TrendChart block={{ block: "TrendChart", results: ["r-s3-series"] }} results={[series]} findings={[]} style="scientific" language="en" />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getByRole("img", { name: /uncertainty band/ })).toBeInTheDocument();
});

test("the series block shows the absence when the series is missing", () => {
  render(
    <I18nProvider><UnitsProvider>
      <TrendChart block={{ block: "TrendChart", results: ["r-s3-series"] }} results={results} findings={[]} style="scientific" language="en" />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getByText("Needs a downloaded package")).toBeInTheDocument();
});

test("the norm card lists related law and says it does not judge compliance", async () => {
  const load = vi.fn(async () => ({
    items: [{ celex: "32007L0060", title: "Directive 2007/60/EC on flood risk", date: "2007-10-23", url: "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32007L0060" }],
    note: { en: "A reference list." },
  }));
  render(
    <I18nProvider><UnitsProvider>
      <NormCard
        block={{ block: "NormCard", results: ["r-s5-change"] }}
        results={results}
        findings={[]}
        style="institutional"
        language="en"
        load={load}
      />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getByText("A reference list of EU law on the subject; it does not judge compliance.")).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "Directive 2007/60/EC on flood risk" })).toHaveAttribute(
    "href",
    "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32007L0060",
  );
  expect(screen.getByText("A reference list.")).toBeInTheDocument();
});

test("the norm card shows the absence when there is no legislation", async () => {  const load = vi.fn(async () => ({ items: [], absence: { reason: "not_computed" as const } }));
  render(
    <I18nProvider><UnitsProvider>
      <NormCard
        block={{ block: "NormCard", results: ["r-s5-change"] }}
        results={results}
        findings={[]}
        style="institutional"
        language="en"
        load={load}
      />
    </UnitsProvider></I18nProvider>,
  );

  await waitFor(() => expect(screen.getByText("Not computed")).toBeInTheDocument());
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

test("the scenario fan draws one line per scenario and names the band as model spread", () => {
  const base = results.find((item) => item.id === "r-s3-series")!;
  const fan = {
    ...base,
    absence: undefined,
    unit: "°C",
    method: { ...base.method, operation: "climate_delta" },
    series: [
      { t: "2030", scenario: "ssp126", v: 17.1, low: 16.5, high: 17.3 },
      { t: "2030", scenario: "ssp585", v: 17.3, low: 16.6, high: 17.6 },
      { t: "2050", scenario: "ssp126", v: 17.5, low: 16.9, high: 18.2 },
      { t: "2050", scenario: "ssp585", v: 17.8, low: 17.3, high: 18.7 },
    ],
  } as unknown as result.StepResult;
  render(
    <I18nProvider><UnitsProvider>
      <ScenarioFan block={{ block: "ScenarioFan", results: ["r-s3-series"] }} results={[fan]} findings={[]} style="journalistic" language="en" />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getByRole("img", { name: /median per scenario/ })).toBeInTheDocument();
  expect(screen.getByText("ssp126")).toBeInTheDocument();
  expect(screen.getByText("ssp585")).toBeInTheDocument();
  expect(screen.getByText(/Spread across models, not a confidence interval/)).toBeInTheDocument();
});

test("a fan with a point outside its own bounds is not drawn", () => {
  const base = results.find((item) => item.id === "r-s3-series")!;
  const broken = {
    ...base,
    absence: undefined,
    unit: "°C",
    method: { ...base.method, operation: "climate_delta" },
    series: [{ t: "2030", scenario: "ssp126", v: 17.1, low: 16.5, high: 17 }],
  } as unknown as result.StepResult;
  render(
    <I18nProvider><UnitsProvider>
      <ScenarioFan block={{ block: "ScenarioFan", results: ["r-s3-series"] }} results={[broken]} findings={[]} style="journalistic" language="en" />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getByText("The fan has no complete scenario points.")).toBeInTheDocument();
});

test("the variance split reads its columns by name and says how many models it rests on", () => {
  const base = results.find((item) => item.id === "r-s3-series")!;
  const split = {
    ...base,
    absence: undefined,
    type: "table",
    unit: "°C^2",
    method: { ...base.method, operation: "uncertainty_split" },
    table: {
      columns: ["horizon", "model_variance", "scenario_variance", "models", "scenarios"],
      rows: [
        [2030, 0.08929244537353509, 0.005054829025268524, ["a", "b", "c", "d", "e"], ["ssp126", "ssp245", "ssp370", "ssp585"]],
        [2040, 0.1577795814514162, 0.012125242900848416, ["a", "b", "c", "d", "e"], ["ssp126", "ssp245", "ssp370", "ssp585"]],
      ],
    },
  } as unknown as result.StepResult;
  render(
    <I18nProvider><UnitsProvider>
      <UncertaintySplit block={{ block: "UncertaintySplit", results: ["r-s3-series"] }} results={[split]} findings={[]} style="journalistic" language="en" />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getByRole("columnheader", { name: "Horizon" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Between models" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "0.0893" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "0.00505" })).toBeInTheDocument();
  expect(screen.getByText(/5 models · 4 scenarios · Estimated components/)).toBeInTheDocument();
});

test("variance in squared degrees converts with its own factor", () => {
  localStorage.setItem("hup-units-choice", "imperial");
  const base = results.find((item) => item.id === "r-s3-series")!;
  const split = {
    ...base,
    absence: undefined,
    type: "table",
    unit: "°C^2",
    method: { ...base.method, operation: "uncertainty_split" },
    table: {
      columns: ["horizon", "model_variance", "scenario_variance"],
      rows: [[2030, 0.08929244537353509, 0.005054829025268524]],
    },
  } as unknown as result.StepResult;
  render(
    <I18nProvider><UnitsProvider>
      <UncertaintySplit block={{ block: "UncertaintySplit", results: ["r-s3-series"] }} results={[split]} findings={[]} style="journalistic" language="en" />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getByRole("cell", { name: "0.289" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "0.0164" })).toBeInTheDocument();
  localStorage.setItem("hup-units-choice", "metric");
});

test("a repeated absence and a repeated limit are written once", () => {
  const base = results.find((item) => item.id === "r-s3-series")!;
  const absence = { reason: "needs_package" as const, detail: { en: "2015 is missing." } };
  const limit = { en: "The cell is larger than the area." };
  const first = { ...base, id: "r-a", absence, limits: [limit] } as unknown as result.StepResult;
  const second = { ...base, id: "r-b", absence, limits: [limit] } as unknown as result.StepResult;
  render(
    <I18nProvider><UnitsProvider>
      <LimitsBox block={{ block: "LimitsBox", results: ["r-a", "r-b"] }} results={[first, second]} findings={[]} style="journalistic" language="en" />
    </UnitsProvider></I18nProvider>,
  );

  expect(screen.getAllByText("Needs a downloaded package")).toHaveLength(1);
  expect(screen.getAllByText("2015 is missing.")).toHaveLength(1);
  expect(screen.getAllByText("The cell is larger than the area.")).toHaveLength(1);
});

test("measurements keep four significant digits and convert levels and differences correctly", () => {
  expect(formatMeasure(0.026111111111111196, "°C/year", "en", "metric")).toEqual({
    text: "+0.02611",
    unit: "°C/year",
  });
  // A rate converts by scale only: 0.027 °C/year is 0.049 °F/year, never 32 °F/year.
  expect(formatMeasure(0.027175116550116528, "°C/year", "en", "imperial", "trend")).toEqual({
    text: "+0.04892",
    unit: "°F/year",
  });
  // A contrast is a difference too.
  expect(formatMeasure(1.1, "°C", "en", "imperial", "baci_contrast")).toEqual({ text: "+1.98", unit: "°F" });
  // A level does take the offset.
  expect(formatMeasure(15.8, "°C", "en", "imperial", "read_series")).toEqual({ text: "+60.44", unit: "°F" });
});

test("the composer placeholders fill series, method, table and scenarios", () => {
  const state = {
    id: "r-state",
    method: { operation: "read_series", version: "1" },
    unit: "mm/month",
    series: [
      { t: "2024-10-01", v: 200 },
      { t: "2024-09-01", v: 202.72802305966616 },
    ],
  } as unknown as result.StepResult;
  expect(fillText({ en: "{r-state.series}" }, [state], "en", "metric")).toBe(
    "total of available observations 403 mm, maximum 203 mm (September 2024)",
  );
  expect(fillText({ es: "{r-state.series}" }, [state], "es", "metric")).toBe(
    "total de los datos disponibles 403 mm, máximo 203 mm (septiembre de 2024)",
  );
  // One value a year: the peak says the year; a daily series keeps its date.
  const yearly = (t: [string, string]) =>
    ({
      id: "r-year",
      method: { operation: "read_series", version: "1" },
      unit: "people",
      series: [
        { t: t[0], v: 10 },
        { t: t[1], v: 20 },
      ],
    }) as unknown as result.StepResult;
  expect(fillText({ en: "{r-year.series}" }, [yearly(["2021-01-01", "2022-01-01"])], "en", "metric")).toMatch(
    /\(2022\)$/,
  );
  expect(fillText({ en: "{r-year.series}" }, [yearly(["2022-01-01", "2022-01-02"])], "en", "metric")).toMatch(
    /\(2022-01-02\)$/,
  );

  const method = {
    id: "r-method",
    method: { operation: "rain_extreme", version: "1", params: { year: 2024, months: [10] } },
  } as unknown as result.StepResult;
  expect(fillText({ en: "{r-method.method}" }, [method], "en", "metric")).toBe("October 2024");
  expect(fillText({ es: "{r-method.method}" }, [method], "es", "metric")).toBe("octubre de 2024");

  // Return levels (A11): the period of each level and the length of the record are placeholders, and an
  // episode rarer than the record keeps its sentence although the result is an absence.
  const level = {
    id: "r-s2-level_10",
    method: { operation: "return_level", version: "1.0" },
    unit: "mm",
    value: 90.8,
    diagnostics: { n_years: 27, return_period_years: 10 },
  } as unknown as result.StepResult;
  expect(fillText({ es: "una vez cada {r-s2-level_10.return_period} años, {r-s2-level_10.record} años de registro" },
    [level], "es", "metric")).toBe("una vez cada 10 años, 27 años de registro");
  const rare = {
    id: "r-s2-event",
    method: { operation: "return_level", version: "1.0" },
    unit: "years",
    support: { resolution_m: 11100 },
    diagnostics: { n_years: 22, event_year: 2020 },
    absence: { reason: "not_computed", detail: { en: "Rarer than 22 years of record can estimate." } },
  } as unknown as result.StepResult;
  expect(fillText({ en: "rarer than {r-s2-event.record} years ({r-s2-event.support})" }, [rare], "en", "metric"))
    .toBe("rarer than 22 years (11.1 km)");
  expect(fillText({ en: "{r-s2-event.value}" }, [rare], "en", "metric")).toBeNull();
  expect(fillText({ en: "{r-s2-event.return_period}" }, [rare], "en", "metric")).toBeNull();
  const other = { ...rare, absence: { reason: "insufficient_qa" } } as unknown as result.StepResult;
  expect(fillText({ en: "{r-s2-event.record}" }, [other], "en", "metric")).toBeNull();
  // return_level 1.2: the wettest day of the episode is a placeholder too, also when the episode is rarer.
  const day = { ...rare, diagnostics: { n_years: 22, event_year: 2020, event_value: 149.38, event_day: "2020-11-05" } };
  expect(fillText({ es: "el día más lluvioso del episodio ({r-s2-event.event_value} mm)" },
    [day as unknown as result.StepResult], "es", "metric")).toBe("el día más lluvioso del episodio (149 mm)");
  expect(fillText({ en: "{r-s2-event.event_value}" }, [rare], "en", "metric")).toBeNull();

  const table = {
    id: "r-table",
    method: { operation: "uncertainty_split", version: "1" },
    unit: "°C^2",
    table: {
      columns: ["horizon", "model_variance", "scenario_variance"],
      rows: [
        [2030, 0.08929244537353509, 0.005054829025268524],
        [2040, 0.1577795814514162, 0.012125242900848416],
      ],
    },
  } as unknown as result.StepResult;
  expect(fillText({ en: "{r-table.table}" }, [table], "en", "metric")).toBe(
    "2030: models 0.0893, scenarios 0.00505; 2040: models 0.158, scenarios 0.0121 °C^2",
  );
  expect(fillText({ es: "{r-table.table}" }, [table], "es", "metric")).toBe(
    "2030: modelos 0,0893, escenarios 0,00505; 2040: modelos 0,158, escenarios 0,0121 °C^2",
  );

  const scenario = {
    id: "r-scenario",
    method: { operation: "climate_delta", version: "1" },
    unit: "°C",
    series: [{ t: "2030", scenario: "ssp126", v: 17.147486979166665, low: 16.529752604166667, high: 17.317135416666666 }],
  } as unknown as result.StepResult;
  expect(fillText({ en: "{r-scenario.series}" }, [scenario], "en", "metric")).toBe(
    "2030 (ssp126): 17.1 °C (16.5 to 17.3 °C)",
  );
  expect(fillText({ es: "{r-scenario.series}" }, [scenario], "es", "metric")).toBe(
    "2030 (ssp126): 17,1 °C (16,5 a 17,3 °C)",
  );
});

test("the composer fills interval, statistic and diagnostics placeholders", () => {
  const result = {
    id: "r-trend",
    unit: "°C/year",
    interval: { low: 0.01324, high: 0.0426, level: 0.95 },
    statistic: { name: "Sen slope", p: 0.01234, n: 44 },
    diagnostics: { n_years: 44, correction: "yue_wang_lag1_floor" },
    method: { operation: "trend", version: "1" },
  } as unknown as result.StepResult;
  const text = { en: "{r-trend.interval}; {r-trend.statistic}; {r-trend.diagnostics}" };
  expect(fillText(text, [result], "en", "metric")).toBe(
    "95% interval: 0.01324 to 0.0426; Sen slope, p = 0.012, n = 44; years: 44, correction: yue_wang_lag1_floor",
  );
  expect(fillText(text, [result], "es", "metric")).toBe(
    "intervalo del 95 %: de 0,01324 a 0,0426; pendiente de Sen, p = 0,012, n = 44; años: 44, corrección: yue_wang_lag1_floor",
  );
  localStorage.setItem("hup-units-choice", "metric");
});

test("a small trend reads per decade, with its interval on the same scale", () => {
  const result = {
    id: "r-trend",
    value: 0.027175116550116528,
    unit: "°C/year",
    interval: { low: 0.013223, high: 0.042601, level: 0.95, method: "Sen slope, Gilbert interval" },
    statistic: { name: "Sen slope", p: 0.01234, n: 44 },
    method: { operation: "trend", version: "1" },
  } as unknown as result.StepResult;
  const text = { en: "{r-trend.value} {r-trend.unit}; {r-trend.interval}; {r-trend.method}", es: "{r-trend.value} {r-trend.unit}; {r-trend.interval}; {r-trend.method}" };
  expect(fillText(text, [result], "en", "metric")).toBe(
    "0.27 °C per decade; 95% interval: 0.13 to 0.43; Sen slope, Gilbert interval",
  );
  expect(fillText(text, [result], "es", "metric")).toBe(
    "0,27 °C por década; intervalo del 95 %: de 0,13 a 0,43; pendiente de Sen, intervalo de Gilbert",
  );
});

const baseResult = results.find((item) => item.id === "r-s3-series")!;

test("the ring block draws the change against distance with its interval", () => {
  const ring = {
    ...baseResult,
    id: "r-ring",
    absence: undefined,
    type: "series",
    variable: "lst",
    unit: "°C",
    method: { ...baseResult.method, operation: "ring_contrast" },
    series: [
      { t: "1", v: 1.2, low: 0.8, high: 1.6 },
      { t: "2", v: 0.6, low: 0.2, high: 1 },
    ],
  } as unknown as result.StepResult;
  renderBlocks([{ block: "RingsDecay", results: ["r-ring"] }], [ring]);

  expect(screen.getByRole("heading", { name: "Land surface temperature" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Series: Land surface temperature" })).toBeInTheDocument();
});

test("a ring series of dates is not a distance and says it has no points", () => {
  const ring = {
    ...baseResult,
    id: "r-ring",
    absence: undefined,
    series: [{ t: "2024-01-01", v: 1 }],
  } as unknown as result.StepResult;
  renderBlocks([{ block: "RingsDecay", results: ["r-ring"] }], [ring]);

  expect(screen.getByText("The series has no values to plot.")).toBeInTheDocument();
});

test("the ring block shows an absence with its reason, never a zero", () => {
  const ring = {
    ...baseResult,
    id: "r-ring",
    absence: { reason: "insufficient_qa", detail: { en: "At most 13 years are covered." } },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "RingsDecay", results: ["r-ring"] }], [ring]);

  expect(screen.getByText("Insufficient quality")).toBeInTheDocument();
  expect(screen.getByText("At most 13 years are covered.")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("the scan matrix reads its columns by name, with the sign and the test in text", () => {
  const scan = {
    ...baseResult,
    id: "r-scan",
    absence: undefined,
    type: "table",
    variable: "air_temperature",
    unit: "mixed",
    data_class: "reanalysis",
    method: { ...baseResult.method, operation: "scan_trends" },
    sources: [{ source: "nasa-power", url: "https://power.larc.nasa.gov/", license: "Open" }],
    period: { start: "1995-01-01", end: "2025-12-31" },
    support: { resolution_m: 55000 },
    limits: [{ en: "The grid cell is at least as large as the area." }],
    table: {
      columns: ["input", "variable", "unit", "slope", "p_adjusted", "direction_assertable"],
      rows: [
        ["r-a", "air_temperature", "°C/year", 0.03685897435897459, 0.013992304839915315, true],
        ["r-b", "air_temperature_max", "°C/year", null, 0.07374606350652368, false],
      ],
    },
  } as unknown as result.StepResult;
  const rising = {
    ...baseResult,
    id: "r-trend-a",
    absence: undefined,
    type: "value",
    variable: "air_temperature",
    unit: "°C/year",
    value: 0.03685897435897459,
    interval: { level: 0.95, low: 0.011920289855072512, high: 0.06257246376811626 },
    statistic: { name: "Mann-Kendall", p_adjusted: 0.013992304839915315, n: 31 },
    method: { ...baseResult.method, operation: "scan_trends" },
  } as unknown as result.StepResult;
  const flat = {
    ...rising,
    id: "r-trend-b",
    variable: "air_temperature_max",
    value: undefined,
    interval: undefined,
    statistic: undefined,
    absence: { reason: "not_computed" as const, detail: { en: "The adjusted test does not support a direction." } },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "ScanMatrix", results: ["r-scan"] }], [scan, rising, flat]);

  expect(screen.getByRole("heading", { name: "Trend scan" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Air temperature, monthly mean" })).toBeInTheDocument();
  // Three significant figures, as in the key figures.
  expect(screen.getByText(/\+0\.0369(?!\d)/)).toBeInTheDocument();
  expect(screen.getByText("p = 0.014")).toBeInTheDocument();
  expect(screen.getByText("The adjusted test does not support a direction.")).toBeInTheDocument();
  expect(screen.getByText("The grid cell is at least as large as the area.")).toBeInTheDocument();
});

test("the scan matrix says when the table has no rows instead of inventing one", () => {
  const scan = {
    ...baseResult,
    id: "r-scan",
    absence: undefined,
    type: "table",
    table: { columns: ["horizon", "model_variance"], rows: [[2030, 0.1]] },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "ScanMatrix", results: ["r-scan"] }], [scan]);

  expect(screen.getByRole("heading", { name: "Trend scan" })).toBeInTheDocument();
  expect(screen.getByText("Not reported")).toBeInTheDocument();
});

test("the exposure counter shows the figure, its unit, class and limits", () => {
  const exposure = {
    ...baseResult,
    id: "r-exp",
    absence: undefined,
    type: "value",
    variable: "population_in_flood_area",
    unit: "people",
    value: 135.24040915090472,
    data_class: "approximated",
    evidence_level: "E0",
    method: { ...baseResult.method, operation: "exposure" },
    period: { start: "2024-10-29", end: "2024-10-29" },
    support: { resolution_m: 1000 },
    limits: [
      { en: "Observed extent for the event dated 2024-10-29." },
      { en: "Population estimate for 2025." },
    ],
  } as unknown as result.StepResult;
  renderBlocks([{ block: "ExposureCounter", results: ["r-exp"] }], [exposure]);

  expect(screen.getByRole("heading", { name: "People in the mapped flood area" })).toBeInTheDocument();
  expect(screen.getByText("135.2")).toBeInTheDocument();
  expect(screen.getByText("people")).toBeInTheDocument();
  // An approximated value carries the asterisk that explains its class, not a dashed tag.
  const mark = screen.getByRole("button", { name: "Approximated" });
  expect(mark).toHaveTextContent("*");
  fireEvent.click(mark);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Estimated by distributing or combining other sources");
  expect(screen.getByText("Observed extent for the event dated 2024-10-29.")).toBeInTheDocument();
  expect(screen.getByText(/Resolution: 1 km/)).toBeInTheDocument();
});

test("an exposure without coverage shows the absence, never a zero", () => {
  const exposure = {
    ...baseResult,
    id: "r-exp",
    type: "value",
    variable: "population_in_flood_area",
    unit: "people",
    value: undefined,
    method: { ...baseResult.method, operation: "exposure" },
    absence: { reason: "no_coverage", detail: { en: "No flood layer covers this zone." } },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "ExposureCounter", results: ["r-exp"] }], [exposure]);

  expect(screen.getByText("No coverage")).toBeInTheDocument();
  expect(screen.getByText("No flood layer covers this zone.")).toBeInTheDocument();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

test("an exposure without a figure says it is not reported", () => {
  const exposure = {
    ...baseResult,
    id: "r-exp",
    absence: undefined,
    type: "value",
    variable: "population_in_flood_area",
    unit: "people",
    value: undefined,
    method: { ...baseResult.method, operation: "exposure" },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "ExposureCounter", results: ["r-exp"] }], [exposure]);

  expect(screen.getByText("Not reported")).toBeInTheDocument();
});

test("a scan table with an absence shows its reason instead of a matrix", () => {
  const scan = {
    ...baseResult,
    id: "r-scan",
    type: "table",
    variable: "ndvi",
    method: { ...baseResult.method, operation: "scan_trends" },
    absence: { reason: "insufficient_qa", detail: { en: "No series met the trend test requirements." } },
    table: {
      columns: ["input", "variable", "unit", "slope", "p_adjusted", "direction_assertable"],
      rows: [],
    },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "ScanMatrix", results: ["r-scan"] }], [scan]);

  expect(screen.getByText("Insufficient quality")).toBeInTheDocument();
  expect(screen.getByText("No series met the trend test requirements.")).toBeInTheDocument();
});

test("the before and after block states the two windows, one shared range and its source", () => {
  const before = {
    ...baseResult,
    id: "r-before",
    absence: undefined,
    type: "layer",
    variable: "lst",
    unit: "°C",
    analysis: "a-example",
    method: { ...baseResult.method, operation: "zone_layer", params: { window: "before" } },
    period: { start: "2019-06-01", end: "2021-12-31" },
    layer: { path: "layers/lst.before.tif", format: "cog", range: [20, 30], scale: "heat" },
    limits: [{ en: "The file does not mark the outline of the zone." }],
  } as unknown as result.StepResult;
  const after = {
    ...before,
    id: "r-after",
    method: { ...baseResult.method, operation: "zone_layer", params: { window: "after" } },
    period: { start: "2023-01-01", end: "2025-09-30" },
    layer: { path: "layers/lst.after.tif", format: "cog", range: [20, 30], scale: "heat" },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "BeforeAfterMap", results: ["r-before", "r-after"] }], [before, after]);

  expect(screen.getByRole("heading", { name: "Before / after with control" })).toBeInTheDocument();
  expect(screen.getByText("Before")).toBeInTheDocument();
  expect(screen.getByText("After")).toBeInTheDocument();
  expect(screen.getAllByText(/Range: 20 °C – 30 °C/)).toHaveLength(2);
  expect(screen.getAllByText(/smap-l3/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText("The file does not mark the outline of the zone.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "See the data in the panel" })).toHaveAttribute("href", "/a/a-example/panel");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("a before and after block without frames says so instead of drawing an empty map", () => {
  const broken = {
    ...baseResult,
    id: "r-layer",
    absence: undefined,
    type: "layer",
    variable: "lst",
    method: { ...baseResult.method, operation: "zone_layer" },
    layer: undefined,
  } as unknown as result.StepResult;
  renderBlocks([{ block: "BeforeAfterMap", results: ["r-layer"] }], [broken]);

  expect(screen.getByText("This layer has no frames.")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("an absent before and after layer shows the absence with its reason", () => {
  const broken = {
    ...baseResult,
    id: "r-layer",
    type: "layer",
    variable: "lst",
    method: { ...baseResult.method, operation: "zone_layer" },
    absence: { reason: "not_computed", detail: { en: "Two comparable windows are needed." } },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "BeforeAfterMap", results: ["r-layer"] }], [broken]);

  expect(screen.getByText("Not computed")).toBeInTheDocument();
  expect(screen.getByText("Two comparable windows are needed.")).toBeInTheDocument();
});

// The shape of a real `resource_estimate` result (data centre water, 60 MW IT), trimmed.
const estimate = {
  id: "r-s8-estimate",
  type: "value",
  variable: "water",
  data_class: "illustrative",
  evidence_level: "E0",
  unit: "m3/year",
  value: 177840,
  zone: "z",
  sources: [{ source: "data-center-water", url: "https://doi.org/10.2833/3168794" }],
  limits: [{ en: "The size is the one stated in the question.", es: "El tamaño es el que dice la pregunta." }],
  method: {
    operation: "resource_estimate",
    version: "1.2",
    params: {
      archetype: { id: "data-center-water", name: { en: "Large data center water intake", es: "Agua de entrada de un centro de datos grande" } },
    },
  },
  table: {
    columns: ["resource", "unit", "low", "high", "archetype", "reference", "quantity", "per", "basis"],
    rows: [["water", "m3/year", 86040, 403680, "data-center-water", "European Commission (2025)", 60, "MW IT", "assumed"]],
  },
} as unknown as result.StepResult;
const absentReader = {
  id: "r-s1-series",
  type: "series",
  variable: "soil_moisture",
  data_class: "measured",
  sources: [{ source: "none", url: "" }],
  method: { operation: "read_series", version: "1.2" },
  absence: { reason: "not_computed", detail: { en: "No reader is available for the variable soil_moisture.", es: "x" } },
} as unknown as result.StepResult;

test("an estimate's table fills its sentence, shows its range and reads as an estimate", () => {
  const text = { en: "Water intake: {r-s8-estimate.value} {r-s8-estimate.unit}, {r-s8-estimate.table}.", es: "x" };
  expect(fillText(text, [estimate], "en", "metric")).toBe(
    "Water intake: 178,000 m³ per year, assumed: 60 MW IT; range: 86,000 to 404,000 m³ per year.",
  );
  renderBlocks(
    [
      { block: "KeyFigure", results: ["r-s8-estimate"], text },
      { block: "EvidenceBadge", results: ["r-s8-estimate"] },
      { block: "SourcesTable", results: ["r-s8-estimate", "r-s1-series"] },
      { block: "LimitsBox", results: ["r-s8-estimate", "r-s1-series"] },
    ],
    [estimate, absentReader],
  );

  expect(screen.getByText(/^Water intake: 178,000/)).toBeInTheDocument();
  expect(screen.getByText(/Range: 86,040 – 403,700/)).toBeInTheDocument();
  expect(screen.getAllByText("Estimate from published coefficients").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText("Described")).toBeNull();
  expect(screen.queryByText(/not composed/)).toBeNull();
  // The source is named by its archetype, and "none" is never listed.
  expect(screen.getAllByText(/Large data center water intake/).length).toBeGreaterThanOrEqual(2);
  expect(screen.queryByText("none")).toBeNull();
  // The absence names the variable, not its id.
  expect(screen.getByText(/the variable “Soil moisture”/)).toBeInTheDocument();
});

test("an interval across zero reads as no clear difference, not as not computed", () => {
  const flat = {
    ...absentReader,
    id: "r-s2-trend",
    type: "value",
    direction_assertable: false,
    absence: { reason: "not_computed", detail: { en: "The interval includes zero.", es: "El intervalo incluye el cero." } },
  } as unknown as result.StepResult;
  renderBlocks([{ block: "KeyFigure", results: ["r-s2-trend"] }], [flat]);

  expect(screen.getByText("No clear difference")).toBeInTheDocument();
  expect(screen.queryByText("Not computed")).toBeNull();
});

test("counts carry thousands separators in both languages", () => {
  expect(fillText({ en: "{r-s8-estimate.value}", es: "{r-s8-estimate.value}" }, [{ ...estimate, value: 1550 } as result.StepResult], "es", "metric")).toBe("1.550");
});

test("the norm card explains the law and lists what a person can do", async () => {
  const load = vi.fn(async () => ({
    items: [
      {
        celex: "32007L0060",
        title: "Directive 2007/60/EC",
        date: "2007-10-23",
        url: "https://eur-lex.europa.eu/eli/dir/2007/60/oj",
        visibility: "card",
        explained: {
          short_name: "Floods Directive",
          plain: "Each country maps flood zones.",
          citizen_options: [{ action: "Check whether a place is in a flood zone.", who_label: "Anyone", url: "https://example.org/map" }],
        },
      },
      { celex: "32000L0060", title: "Hidden act", date: "2000-10-23", url: "https://example.org/x", visibility: "hidden" },
    ],
    general_options: [{ id: "access_info", action: "Ask for environmental information.", who_label: "Anyone" }],
    disclaimer: "General information, not legal advice.",
  }));
  render(
    <I18nProvider><UnitsProvider>
      <NormCard block={{ block: "NormCard", results: ["r-s5-change"] }} results={results} findings={[]} style="journalistic" language="en" load={load as never} />
    </UnitsProvider></I18nProvider>,
  );

  expect(await screen.findByText("Floods Directive")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Check whether a place is in a flood zone." })).toBeInTheDocument();
  expect(screen.getByText("What you can do")).toBeInTheDocument();
  expect(screen.getByText("Ask for environmental information.")).toBeInTheDocument();
  expect(screen.getByText("General information, not legal advice.")).toBeInTheDocument();
  expect(screen.queryByText("Hidden act")).toBeNull();
});

test("a contrast against a control capped at E1 never reads as a trend without comparison", () => {
  const base = results.find((item) => item.type === "value")!;
  const contrast = { ...base, evidence_level: "E1", method: { operation: "baci_contrast", version: "1" } } as result.StepResult;
  const trend = { ...base, evidence_level: "E1", method: { operation: "trend", version: "1" } } as result.StepResult;
  const diverged = { ...trend, diagnostics: { pre_trend_control: true } } as result.StepResult;
  expect(evidenceOf(contrast)?.name).toBe("evidence.E1control.name");
  expect(evidenceOf(diverged)?.name).toBe("evidence.E1control.name");
  expect(evidenceOf(trend)?.name).toBe("evidence.E1.name");
  expect(evidenceOf({ ...contrast, evidence_level: "E3" } as result.StepResult)?.name).toBe("evidence.E3.name");
  expect(axisLabel("Land surface temperature", "°C")).toBe("Land surface temperature (°C)");
});

test("p-values follow one rule: two significant figures, and below one in a thousand, < 0.001", () => {
  expect(formatP(0.0123, "en")).toBe("0.012");
  expect(formatP(0.000072, "es")).toBe("< 0,001");
  expect(formatP(0.48, "es")).toBe("0,48");
});
