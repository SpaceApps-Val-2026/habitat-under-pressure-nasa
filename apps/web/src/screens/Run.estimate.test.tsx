import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import type { PlanEstimate } from "../api/client";
import type { RunEvent } from "../api/events";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";

// A run reached either way: with the plan in the navigation state (the cover) or only from the saved analysis
// (a reload, the library). The saved analysis always brings its plan again, as a new object.
const planOf = () => ({
  request: { text: "¿Qué tal va todo por Alcalà de Xivert?", language: "es" },
  steps: [
    { id: "s1", type: "ingest", operation: "read_series", params: { variable: "ndvi" }, sources: ["landsat-c2-l2"] },
    { id: "s2", type: "compute", operation: "scan_trends", params: {} },
  ],
});

const now = new Date();
const events: RunEvent[] = [
  {
    analysis: "x",
    seq: 1,
    type: "step_started",
    ts: new Date(now.getTime() - 5000).toISOString(),
    step: "s1",
    data: { operation: "read_series", visual_type: "satellite", phrase: { en: "Reading {n} scenes from {source}" } },
  } as RunEvent,
];

const estimate: PlanEstimate = {
  total_seconds: 70.5,
  low_seconds: 60,
  high_seconds: 90,
  basis: "measured",
  steps: [
    {
      id: "s1",
      seconds: 60,
      low_seconds: 50,
      high_seconds: 80,
      basis: "measured",
      n: 0,
      kind: "read",
      source: "landsat-c2-l2",
      source_name: { en: "Landsat 8/9", es: "Landsat 8/9" },
      expected_origin: "cold",
    },
    {
      id: "s2",
      seconds: 1,
      low_seconds: 1,
      high_seconds: 2,
      basis: "prior",
      n: 0,
      kind: "compute",
      source: null,
      source_name: null,
      expected_origin: null,
    },
  ],
};

vi.mock("../api/client", async (original) => {
  const actual = await original<typeof import("../api/client")>();
  return {
    ...actual,
    // The saved analysis arrives a moment later, with its own copy of the plan.
    getAnalysis: vi.fn(() => new Promise((done) => setTimeout(() => done({ status: "running", plan: planOf() }), 20))),
    getCatalog: vi.fn(async () => ({ operations: [], sources: [], variables: [] })),
    getAnalysisResults: vi.fn(async () => []),
    // The estimate answers after the saved plan has arrived: the order of the recorded failing run.
    estimatePlan: vi.fn(() => new Promise((done) => setTimeout(() => done(estimate), 60))),
    openRunEvents: vi.fn((_id: string, _options: unknown, onEvent: (event: RunEvent) => void) => {
      for (const event of events) onEvent(event);
      return () => {};
    }),
  };
});

const client = await import("../api/client");
const { Run } = await import("./Run");

function renderRun(withState: boolean) {
  return render(
    <MemoryRouter initialEntries={[withState ? { pathname: "/run/x", state: { plan: planOf() } } : "/run/x"]}>
      <I18nProvider>
        <UnitsProvider>
          <Routes>
            <Route path="/run/:analysisId" element={<Run />} />
          </Routes>
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-lang", "es");
  vi.mocked(client.estimatePlan).mockClear();
});

test.each([
  ["with the plan handed over by the cover", true],
  ["with the plan only from the saved analysis", false],
])("the estimate shows %s", async (_name, withState) => {
  const { container } = renderRun(withState);

  expect(await screen.findByText(/Tiempo estimado: unos 1 min 10 s/, {}, { timeout: 4000 })).toBeInTheDocument();
  await waitFor(() => expect(container.querySelector(".run-waiting")).not.toBeNull(), { timeout: 4000 });
  expect(screen.getByText("Esperando ahora")).toBeInTheDocument();
  expect(screen.getByText("Landsat 8/9")).toBeInTheDocument();
  expect(client.estimatePlan).toHaveBeenCalledTimes(1);
});
