import { expect, test } from "vitest";

import type { StepState } from "./run-model";
import {
  labelBox,
  labelLines,
  nodeBox,
  overlaps,
  placeNodes,
  sceneNodes,
  shortName,
  VIEW,
  type PlanStepInfo,
} from "./run-scene";

const SOURCES: Record<string, string> = {
  "landsat-c2-l2": "Landsat 8/9 Collection 2 Level-2",
  "nasa-power": "NASA POWER",
  "imerg-monthly": "GPM IMERG Final Precipitation monthly",
  "ghsl-built-s": "GHSL built-up surface (R2023A)",
  "copernicus-dem": "Copernicus DEM GLO-30",
  "smap-l3": "SMAP L3 soil moisture 9 km",
};
const VARIABLES: Record<string, string> = {
  ndvi: "Vegetation greenness (NDVI)",
  lst: "Land surface temperature",
  air_temperature: "Air temperature, monthly mean",
  air_temperature_max: "Maximum air temperature, monthly mean",
  precipitation: "Precipitation, monthly total",
  built_up_surface: "Built-up surface",
  built_up_fraction: "Built-up fraction of the area",
  soil_moisture: "Soil moisture",
};
const PHRASES = [
  "Calculating the trend",
  "Scanning every series for a trend",
  "Comparing the site with its surroundings, before and after",
  "Evaluating the chain of greenness and heat",
  "Writing the findings",
  "Checking every figure against its result",
];

// The read steps of the real 14-step scan of Benicarló, in plan order.
const SCAN_READS: [string, string][] = [
  ["landsat-c2-l2", "ndvi"],
  ["landsat-c2-l2", "lst"],
  ["nasa-power", "air_temperature"],
  ["nasa-power", "air_temperature_max"],
  ["imerg-monthly", "precipitation"],
  ["ghsl-built-s", "built_up_surface"],
  ["ghsl-built-s", "built_up_fraction"],
  ["landsat-c2-l2", "ndvi"],
  ["landsat-c2-l2", "lst"],
  ["landsat-c2-l2", "lst"],
  ["copernicus-dem", "soil_moisture"],
  ["smap-l3", "soil_moisture"],
];

function plan(reads: number, computes: number): PlanStepInfo[] {
  const steps: PlanStepInfo[] = SCAN_READS.slice(0, reads).map(([source, variable], index) => ({
    id: `s${index + 1}`,
    operation: "read_series",
    type: "ingest",
    source,
    variable,
  }));
  for (let index = 0; index < computes; index += 1) {
    steps.push({ id: `c${index}`, operation: "trend", type: "compute", source: null, variable: null });
  }
  steps.push({ id: "w", operation: "write_findings", type: "write", source: null, variable: null });
  steps.push({ id: "r", operation: "review_findings", type: "review", source: null, variable: null });
  return steps;
}

function finishedRead(id: string, source: string, n: number): StepState {
  return {
    id,
    operation: "read_series",
    phrase: null,
    visualType: "satellite",
    started: true,
    finished: true,
    seconds: 1,
    startedAt: null,
    finishedAt: null,
    fraction: null,
    item: null,
    phase: null,
    results: [],
    dataReads: [{ source, origin: "remote", nItems: n, period: null }],
    warnings: [],
    alerts: [],
    error: null,
  };
}

function build(steps: PlanStepInfo[], read = true) {
  const states = new Map<string, StepState>();
  if (read) {
    for (const step of steps) if (step.source) states.set(step.id, finishedRead(step.id, step.source, 156));
  }
  let compute = 0;
  return sceneNodes({
    steps,
    states,
    planReady: true,
    placeName: "Benicarló, Baix Maestrat, Castelló, Comunitat Valenciana, España",
    labels: { question: "Understands the question", place: "Resolves the place" },
    sourceName: (id) => shortName(SOURCES[id] ?? id),
    variableName: (id) => VARIABLES[id] ?? null,
    variableTone: () => "tone-climate",
    stepLabel: (step) => (step.type === "ingest" ? `Reading ${step.variable}` : PHRASES[compute++ % PHRASES.length]),
    stepShort: (step) => ({ compute: "Calculation", write: "Writing", review: "Review" })[step.type] ?? step.operation,
    absenceText: () => null,
  });
}

const SIZES: [string, number, number][] = [
  ["trend, 4 steps", 1, 1],
  ["impact, 7 steps", 3, 2],
  ["scan, 9 steps", 6, 1],
  ["whatif, 11 steps", 7, 2],
  ["scan, 14 steps", 10, 2],
  ["whatif, 16 steps", 12, 2],
];

const CASES = SIZES.flatMap(([name, reads, computes]) => [
  [`${name}, while reading`, reads, computes, false] as const,
  [`${name}, with counts`, reads, computes, true] as const,
]);

test.each(CASES)("%s: no label covers another label or a node, and all stay inside the scene", (_name, reads, computes, read) => {
  const placed = placeNodes(build(plan(reads, computes), read), (n) => new Intl.NumberFormat("en").format(n));
  const labels = placed.map(labelBox);
  const circles = placed.map(nodeBox);
  for (let i = 0; i < placed.length; i += 1) {
    const box = labels[i];
    expect(box.left, placed[i].name).toBeGreaterThanOrEqual(0);
    expect(box.right, placed[i].name).toBeLessThanOrEqual(VIEW.w);
    expect(box.top, placed[i].name).toBeGreaterThanOrEqual(0);
    expect(box.bottom, placed[i].name).toBeLessThanOrEqual(VIEW.h);
    for (let j = 0; j < placed.length; j += 1) {
      if (i === j) continue;
      expect(overlaps(box, labels[j]), `${placed[i].name} / ${placed[j].name}`).toBe(false);
      expect(overlaps(box, circles[j]), `${placed[i].name} over node ${placed[j].name}`).toBe(false);
    }
    const core = { left: VIEW.cx - VIEW.core, top: VIEW.cy - VIEW.core, right: VIEW.cx + VIEW.core, bottom: VIEW.cy + VIEW.core };
    expect(overlaps(box, core), `${placed[i].name} over Delfos`).toBe(false);
  }
});

test("many reads of one source share a node that counts them all", () => {
  const nodes = build(plan(10, 2));
  const landsat = nodes.filter((node) => node.name.startsWith("Landsat"));
  expect(landsat).toHaveLength(1);
  expect(landsat[0].count).toBe(156 * 5);
  expect(landsat[0].steps).toHaveLength(5);
  expect(nodes.find((node) => node.name === "NASA POWER")?.count).toBe(312);
});

test("a few reads keep one node each, named by what they read", () => {
  const nodes = build(plan(3, 1));
  expect(nodes.filter((node) => node.kind === "data").map((node) => node.name)).toEqual([
    "Vegetation greenness (NDVI)",
    "Land surface temperature",
    "Air temperature, monthly mean",
  ]);
});

test("labels break between words and never cut one", () => {
  expect(labelLines("Checking every figure against its result")).toEqual(["Checking every", "figure against…"]);
  for (const phrase of PHRASES) {
    for (const line of labelLines(phrase)) {
      const words = line.replace("…", "").split(" ");
      for (const word of words) expect(phrase.split(" ")).toContain(word.replace(/[,]$/, "").replace(/,$/, "") || word);
    }
  }
  expect(shortName("Landsat 8/9 Collection 2 Level-2")).toBe("Landsat 8/9");
  expect(shortName("GHSL built-up surface (R2023A)")).toBe("GHSL built-up");
});

test("the inner ring keeps its distance from Delfos and between its nodes", () => {
  const placed = placeNodes(build(plan(10, 4)), (n) => String(n));
  const inner = placed.filter((node) => node.ring === "inner");
  expect(inner.length).toBeGreaterThanOrEqual(6);
  for (const node of inner) {
    expect(Math.hypot(node.x - VIEW.cx, node.y - VIEW.cy)).toBeGreaterThanOrEqual(VIEW.core + node.r + 40);
    for (const other of inner) {
      if (other === node) continue;
      expect(Math.hypot(node.x - other.x, node.y - other.y)).toBeGreaterThanOrEqual(4 * node.r);
    }
  }
});

test("calculation nodes carry a short name, never a cut sentence", () => {
  const placed = placeNodes(build(plan(10, 2)), (n) => String(n));
  for (const node of placed.filter((item) => item.kind !== "data")) {
    expect(node.lines.join(" ")).not.toContain("…");
  }
});
