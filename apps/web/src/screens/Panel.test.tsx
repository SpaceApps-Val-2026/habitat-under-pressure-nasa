import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test } from "vitest";

import analysisExample from "../../../../docs/contratos/examples/analysis.json";
import resultsExample from "../../../../docs/contratos/examples/results.json";
import type { analysis, result } from "../contracts";
import type { AnalysisLayer } from "../api/client";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";
import { PanelView, compareFrames, rasterLegend } from "./Panel";

const saved = analysisExample as unknown as analysis.SavedAnalysis;
const results = resultsExample as unknown as result.StepResult[];

beforeEach(() => {
  localStorage.setItem("hup-units-choice", "metric");
  localStorage.removeItem("hup-lang");
});

function renderPanel(value = saved, items = results, layers: AnalysisLayer[] = []) {
  return render(
    <MemoryRouter>
      <I18nProvider><UnitsProvider>
        <PanelView analysis={value} results={items} layers={layers} />
      </UnitsProvider></I18nProvider>
    </MemoryRouter>,
  );
}

test("each figure carries its unit, interval, class, evidence, source and period", () => {
  renderPanel();

  const ndvi = screen.getByText("−0.32").closest("figure") as HTMLElement;
  expect(ndvi).toHaveTextContent("NDVI");
  expect(ndvi).toHaveTextContent("−0.33 – −0.3");
  expect(ndvi).toHaveTextContent("Measured");
  expect(ndvi).toHaveTextContent("Coincidence in time and place");
  expect(ndvi).toHaveTextContent("Period: June 2023 to September 2025");
  expect(ndvi).toHaveTextContent("Dates: 151");
  expect(ndvi).toHaveTextContent("Resolution: 30 m");
  expect(ndvi).toHaveTextContent("landsat-c2-l2");

  const lst = screen.getByText("+1.1").closest("figure") as HTMLElement;
  expect(lst).toHaveTextContent("°C");
  expect(lst).toHaveTextContent("Resolution: 100 m");
  expect(lst).toHaveTextContent("Surface temperature, not air temperature.");
});

test("each figure carries the reviewed sentence that cites it", () => {
  renderPanel();

  expect(screen.getByText(/hotter than its surroundings/)).toBeInTheDocument();
  expect(screen.getByText(/greenness fell by/)).toBeInTheDocument();
});

test("the absent result is shown as an absence with its reason, never as a zero", () => {
  renderPanel();

  expect(screen.getAllByText("Needs a downloaded package").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText("SMAP has not been downloaded for this area.")).toBeInTheDocument();
  const figures = [...document.querySelectorAll(".figure")].map((node) => node.textContent?.trim());
  expect(figures).not.toContain("0");
});

test("the panel links to the three reports and names the zone", () => {
  renderPanel();

  expect(screen.getByRole("link", { name: "Journalistic" })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example",
  );
  expect(screen.getByRole("link", { name: "Data" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Institutional" })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example/report/institutional",
  );
  expect(screen.getByRole("link", { name: "Scientific" })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example/report/scientific",
  );
  expect(screen.getAllByText(/Parc Sagunt II/).length).toBeGreaterThanOrEqual(1);
});

test("without results the panel says so", () => {
  renderPanel(saved, []);

  expect(screen.getByText(/has no results yet/i)).toBeInTheDocument();
});

test("results of a kind the panel does not draw are said, not reported as an absence", () => {
  const table = { ...results[0], id: "r-table", type: "table" } as result.StepResult;
  renderPanel(saved, [table]);

  expect(screen.getByText(/does not draw yet/)).toBeInTheDocument();
  expect(screen.queryByText(/has no results yet/i)).not.toBeInTheDocument();
});

test("layers are drawn by the map, so they are not announced as undrawn", () => {
  const layer = { ...results[0], id: "r-layer", type: "layer" } as result.StepResult;
  renderPanel(saved, [layer]);

  expect(screen.queryByText(/does not draw yet/)).not.toBeInTheDocument();
});

test("a scenario panel draws the fan and the variance split, not two anonymous series", () => {
  const base = results.find((item) => item.id === "r-s3-series")!;
  const fan = {
    ...base,
    id: "r-fan",
    absence: undefined,
    unit: "°C",
    method: { ...base.method, operation: "climate_delta" },
    series: [
      { t: "2030", scenario: "ssp126", v: 17.1, low: 16.5, high: 17.3 },
      { t: "2030", scenario: "ssp585", v: 17.3, low: 16.6, high: 17.6 },
    ],
  } as unknown as result.StepResult;
  const split = {
    ...base,
    id: "r-split",
    absence: undefined,
    type: "table",
    unit: "°C^2",
    method: { ...base.method, operation: "uncertainty_split" },
    table: {
      columns: ["horizon", "model_variance", "scenario_variance"],
      rows: [[2030, 0.08929244537353509, 0.005054829025268524]],
    },
  } as unknown as result.StepResult;
  renderPanel(saved, [fan, split]);

  expect(screen.getByRole("img", { name: /median per scenario/ })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Between models" })).toBeInTheDocument();
  expect(screen.queryByText(/does not draw yet/i)).not.toBeInTheDocument();
});

test("a running analysis says that it is still running", () => {
  renderPanel({ ...saved, status: "running" });

  expect(screen.getByText(/still running/)).toBeInTheDocument();
});

test("without WebGL the map falls back and says the outline is only the outer extent", () => {
  renderPanel();

  expect(screen.getByText(/needs WebGL/i)).toBeInTheDocument();
  expect(screen.getByText(/outer extent, not its boundary/i)).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Map of the analysis zone" })).toBeInTheDocument();
});

test("a layer lists its frames, range and absence", () => {
  const layer: AnalysisLayer = {
    variable: "lst",
    name: { en: "Land surface temperature", es: "Temperatura de superficie" },
    unit: "°C",
    data_class: "measured",
    scale: "heat",
    range: [20, 30],
    absence: null,
    frames: [
      { t: "2020", url: null, absence: { reason: "no_coverage", detail: { en: "No clear dates." } } },
      { t: "2021", url: "/api/analyses/sagunt-impact-example/files/layers/lst.2021.tif" },
    ],
  };
  renderPanel(saved, results, [layer]);

  expect(screen.getByRole("heading", { name: "Layers" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Layer" })).toHaveValue("0");
  expect(screen.getByRole("combobox", { name: "Frame" })).toHaveValue("0");
  expect(screen.getByText(/Range: 20 °C – 30 °C/)).toBeInTheDocument();
  expect(screen.getByText("No clear dates.")).toBeInTheDocument();
});

test("the map legend of a dimensionless index carries no unit and few round marks", () => {
  const legend = rasterLegend([-0.026, 0.41407], "1", "NDVI", "en", "metric");

  expect(legend.title).toBe("NDVI");
  expect(legend.ticks.map((tick) => tick.label)).toEqual(["0.0", "0.1", "0.2", "0.3", "0.4"]);
  expect(legend.ticks.every((tick) => !tick.label.includes("1 ") && !tick.label.endsWith(" 1"))).toBe(true);
});

test("the map legend puts the unit once in the title and keeps three to five marks inside the range", () => {
  const ranges: [number, number][] = [
    [22.86, 33.77],
    [0, 1],
    [-0.4, 0.9],
    [1013, 1027],
    [0.0012, 0.0049],
  ];
  for (const range of ranges) {
    const legend = rasterLegend(range, "°C", "LST", "en", "metric");
    expect(legend.title).toBe("LST (°C)");
    expect(legend.ticks.length).toBeGreaterThanOrEqual(3);
    expect(legend.ticks.length).toBeLessThanOrEqual(5);
    for (const tick of legend.ticks) {
      expect(tick.value).toBeGreaterThanOrEqual(range[0]);
      expect(tick.value).toBeLessThanOrEqual(range[1]);
      expect(tick.label).not.toContain("°C");
      expect(tick.position).toBeCloseTo((tick.value - range[0]) / (range[1] - range[0]));
    }
  }
  expect(rasterLegend([22.86, 33.77], "°C", "LST", "en", "metric").ticks.map((tick) => tick.label)).toEqual([
    "24",
    "26",
    "28",
    "30",
    "32",
  ]);
});

test("the map legend writes its numbers in the screen language and the chosen units", () => {
  expect(rasterLegend([-0.026, 0.41407], "1", "NDVI", "es", "metric").ticks.map((tick) => tick.label)).toEqual([
    "0,0",
    "0,1",
    "0,2",
    "0,3",
    "0,4",
  ]);
  expect(rasterLegend([-0.45, 0.2], undefined, "x", "es", "metric").ticks.map((tick) => tick.label)).toEqual([
    "−0,4",
    "−0,2",
    "0,0",
    "0,2",
  ]);
  expect(rasterLegend([1000, 3000], "m", "x", "es", "metric").ticks.map((tick) => tick.label)).toEqual([
    "1000",
    "1500",
    "2000",
    "2500",
    "3000",
  ]);
  expect(rasterLegend([1000, 3000], "m", "x", "en", "metric").ticks.map((tick) => tick.label)).toEqual([
    "1,000",
    "1,500",
    "2,000",
    "2,500",
    "3,000",
  ]);
  const imperial = rasterLegend([20, 30], "°C", "LST", "en", "imperial");
  expect(imperial.title).toBe("LST (°F)");
  expect(imperial.ticks.map((tick) => tick.label)).toEqual(["70", "75", "80", "85"]);
});

test("the layer texts on the dark stage use the stage colours", () => {
  const layer: AnalysisLayer = {
    variable: "ndvi",
    name: { en: "Vegetation index (NDVI)" },
    unit: "1",
    data_class: "measured",
    scale: "green",
    range: [-0.03, 0.41],
    absence: null,
    limits: [{ en: "Greenness is not biodiversity." }],
    frames: [{ t: "2021", url: "/api/analyses/x/files/layers/ndvi.2021.tif" }],
  };
  renderPanel(saved, results, [layer]);

  const section = screen.getByRole("heading", { name: "Layers" }).closest("section") as HTMLElement;
  expect(screen.getByText(/Range:/)).toHaveClass("text-orbit-ink");
  expect(screen.getByText("Greenness is not biodiversity.").closest("ul")).toHaveClass("text-orbit-ink");
  // The data-class tag is shared and keeps its paper classes; its wrapper repaints it with the stage ones.
  const paperText = [...section.querySelectorAll(".text-muted")].filter(
    (node) => !node.parentElement?.className.includes("[&>span]:text-orbit-muted"),
  );
  expect(paperText).toEqual([]);
  expect(section.querySelector(".text-signal, .text-amber-ink")).toBeNull();
});

test("the bbox caption follows the interface language", () => {
  localStorage.setItem("hup-lang", "es");
  renderPanel();

  expect(screen.getByText(/−0,35, 39,6/)).toBeInTheDocument();
});

const windows: AnalysisLayer = {
  variable: "ndvi",
  name: { en: "Vegetation index (NDVI)" },
  unit: "1",
  data_class: "measured",
  scale: "green",
  range: [0.03, 0.61],
  absence: null,
  frames: [
    { t: "2019-2021", window: "before", url: "/api/analyses/x/files/layers/ndvi.before.tif" },
    { t: "2023-2025", window: "after", url: "/api/analyses/x/files/layers/ndvi.after.tif" },
  ],
};

test("the curtain needs a picture on both sides and no declared absence", () => {
  const [before, after] = windows.frames;
  expect(compareFrames([before, after])).toEqual({ before, after });
  expect(compareFrames([{ ...after, url: null }])).toBeNull();
  expect(compareFrames([{ ...after, absence: { reason: "no_coverage" } }])).toBeNull();
  expect(compareFrames([{ t: "2020", url: "/api/analyses/x/files/layers/ndvi.2020.tif" }])).toBeNull();
});

test("a before and an after window become a named curtain instead of a frame selector", () => {
  const { container } = renderPanel(saved, results, [windows]);

  expect(screen.getByText("Before · 2019-2021")).toBeInTheDocument();
  expect(screen.getByText("After · 2023-2025")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Layer" })).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Frame" })).not.toBeInTheDocument();

  const slider = screen.getByRole("slider", { name: "Before / After" }) as HTMLInputElement;
  expect(slider.value).toBe("50");
  fireEvent.change(slider, { target: { value: "20" } });
  expect(slider.value).toBe("20");
  const divider = container.querySelector(".panel-map-divider") as HTMLElement;
  expect(divider.style.left).toBe("20%");
});

test("when one window has no data the selector returns and says which side is missing", () => {
  const [before] = windows.frames;
  const layer: AnalysisLayer = {
    ...windows,
    frames: [
      before,
      {
        t: "2023-2025",
        window: "after",
        url: null,
        absence: { reason: "no_coverage", detail: { en: "No clear dates after the event." } },
      },
    ],
  };
  renderPanel(saved, results, [layer]);

  expect(screen.getByRole("combobox", { name: "Frame" })).toBeInTheDocument();
  expect(screen.queryByRole("slider", { name: "Before / After" })).not.toBeInTheDocument();
  expect(screen.getByText(/After · 2023-2025: No clear dates after the event\./)).toBeInTheDocument();
});
