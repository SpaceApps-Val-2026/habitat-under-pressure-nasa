import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import rawEvents from "../../../../docs/contratos/examples/events.jsonl?raw";
import rawPlan from "../../../../docs/contratos/examples/plan.json";
import { parseEvents, type RunEvent } from "../api/events";
import type { PlanEstimate } from "../api/client";
import type { StepResult } from "../blocks/shared";
import type { plan } from "../contracts";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";
import { RunView } from "./Run";

const events = parseEvents(rawEvents);
const examplePlan = rawPlan as unknown as plan.AnalysisPlan;

function view(items: RunEvent[], connected = true, openIn = 3000, onCancel?: () => void, onReplay?: () => void) {
  return (
    <MemoryRouter>
      <I18nProvider>
<UnitsProvider>
        <RunView
          analysisId="sagunt-impact-example"
          events={items}
          connected={connected}
          unreachable={false}
          analysisPlan={examplePlan}
          openIn={openIn}
          historyWindowMs={0}
          onCancel={onCancel}
          onReplay={onReplay}
        />
      </UnitsProvider>
</I18nProvider>
    </MemoryRouter>
  );
}

function renderRun(items: RunEvent[] = events, connected = true, openIn = 3000) {
  return render(view(items, connected, openIn));
}

afterEach(() => {
  vi.useRealTimers();
});

test("the example run shows every step, its origins and its warning", () => {
  renderRun();

  expect(screen.getByText("Finished")).toBeInTheDocument();
  expect(screen.getByText("Remote read")).toBeInTheDocument();
  expect(screen.getByText("Cached")).toBeInTheDocument();
  expect(screen.getAllByText(/SMAP has not been downloaded/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText(/151 items/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getByRole("link", { name: "Open the report" })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example/report/journalistic",
  );
  expect(screen.getByRole("link", { name: "Open the panel" })).toHaveAttribute("href", "/a/sagunt-impact-example/panel");
});

test("what the person reads carries no plan, result or analysis identifiers", () => {
  const { container } = renderRun();
  const read = container.textContent ?? "";

  expect(read).not.toMatch(/(^|[^a-z])s[1-9]([^0-9a-z]|$)/);
  expect(read).not.toMatch(/r-s\d/);
  expect(read).not.toContain("sagunt-impact-example");
  // A finished run has nothing left to fast-forward.
  expect(screen.queryByRole("button", { name: "See the final state" })).not.toBeInTheDocument();
});

test("the scene names its state and the answer fills block by block", () => {
  const { container } = renderRun();

  expect(screen.getByRole("img", { name: /Delfos in the centre/ })).toBeInTheDocument();
  expect(screen.getByText("Computed; the review raised a notice.")).toBeInTheDocument();
  expect(screen.getByText("The sentence is written.")).toBeInTheDocument();
  expect(screen.queryByText("The review raised no notice.")).not.toBeInTheDocument();
  // The SMAP warning is a limit; the node of its step stays off.
  expect(container.querySelector(".run-node.is-absent")).not.toBeNull();
});

test("while running there is one Cancel button; replay only once it has finished", () => {
  const onCancel = vi.fn();
  const onReplay = vi.fn();
  const running = events.filter((event) => event.type !== "analysis_finished");
  const { rerender } = render(view(running, true, 3000, onCancel, onReplay));

  expect(screen.queryByRole("button", { name: "See the final state" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Replay run" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Back to the library" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onCancel).toHaveBeenCalledTimes(1);

  rerender(view(events, true, 3000, onCancel, onReplay));
  expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Replay run" })).toBeInTheDocument();
});

test("a run that errors says so and stops", () => {
  const failure = {
    analysis: "sagunt-impact-example",
    seq: 23,
    type: "error",
    ts: "2026-11-14T10:00:56Z",
    data: { code: "source_unreachable", text: { en: "The source did not answer." } },
  } as RunEvent;
  renderRun([...events, failure]);

  expect(screen.getByText("Failed")).toBeInTheDocument();
  expect(screen.getByText("The source did not answer.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Open the panel" })).not.toBeInTheDocument();
  expect(screen.queryByText("Waiting for the first event…")).not.toBeInTheDocument();
});

test("the four origins are labelled, not only coloured", () => {
  const origins = ["derived", "cache", "package", "remote"];
  const reads = origins.map((origin, index) => {
    const step = `s${index + 1}`;
    return [
      { analysis: "x", seq: index * 3 + 1, type: "step_started", ts: "2026-11-14T10:00:00Z", step, data: { operation: "read_series", visual_type: "satellite", phrase: { en: "Reading" } } },
      { analysis: "x", seq: index * 3 + 2, type: "data_read", ts: "2026-11-14T10:00:01Z", step, data: { source: "landsat", origin, n_items: 151 } },
      { analysis: "x", seq: index * 3 + 3, type: "step_finished", ts: "2026-11-14T10:00:02Z", step, data: { seconds: 0.1, results: [] } },
    ];
  }).flat();
  const finished = { analysis: "x", seq: 99, type: "analysis_finished", ts: "2026-11-14T10:00:03Z", data: { status: "finished" } };
  renderRun([...reads, finished] as RunEvent[]);

  expect(screen.getByText("Reused result")).toBeInTheDocument();
  expect(screen.getByText("Cached")).toBeInTheDocument();
  expect(screen.getByText("From package")).toBeInTheDocument();
  expect(screen.getByText("Remote read")).toBeInTheDocument();
});

test("before the first event the screen says it is connecting", () => {
  renderRun([], false);

  expect(screen.getByText("Connecting…")).toBeInTheDocument();
  expect(screen.getByText("Waiting for the first event…")).toBeInTheDocument();
});

test("a progress event reads its counter and phase", () => {
  render(
    <MemoryRouter>
      <I18nProvider>
<UnitsProvider>
        <RunView
          analysisId="x"
          analysisPlan={examplePlan}
          connected
          unreachable={false}
          events={[
            { analysis: "x", seq: 1, type: "step_started", ts: "2026-11-14T10:00:00Z", step: "s1", data: { operation: "read_series", visual_type: "satellite", phrase: { en: "Reading" } } },
            { analysis: "x", seq: 2, type: "progress", ts: "2026-11-14T10:00:20Z", step: "s1", data: { fraction: 0.5, item: "scene 76 of 151", phase: "download" } },
          ] as RunEvent[]}
        />
      </UnitsProvider>
</I18nProvider>
    </MemoryRouter>,
  );

  const bars = screen.getAllByRole("progressbar") as HTMLProgressElement[];
  expect(bars.some((bar) => bar.value === 0.5)).toBe(true);
  expect(screen.getByText("scene 76 of 151")).toBeInTheDocument();
  expect(screen.getByText("Downloading")).toBeInTheDocument();
});

test("the scene waits for a run that is still going", () => {
  vi.useFakeTimers();
  renderRun(events.filter((event) => event.seq <= 3));

  expect(screen.queryByText("What is happening")).not.toBeInTheDocument();
  act(() => {
    vi.advanceTimersByTime(3000);
  });
  expect(screen.getByText("What is happening")).toBeInTheDocument();
});

test("a data read sends a pulse towards Delfos", async () => {
  const { container, rerender } = render(view(events));
  const read = {
    analysis: "sagunt-impact-example",
    seq: 90,
    type: "data_read",
    ts: "2026-11-14T10:01:00Z",
    step: "s1",
    data: { source: "landsat-c2-l2", origin: "remote", n_items: 3 },
  } as RunEvent;

  rerender(view([...events, read]));
  await waitFor(() => expect(container.querySelector(".run-pulse")).not.toBeNull());
});

test("a step error stays inside its step, once", () => {
  const failure = {
    analysis: "x",
    seq: 23,
    type: "error",
    ts: "2026-11-14T10:00:56Z",
    step: "s1",
    data: { code: "boom", text: { en: "The step broke." } },
  } as RunEvent;
  const { container } = renderRun([...events, failure]);

  expect(container.querySelector(".run-node.is-error")).not.toBeNull();
  expect(screen.getAllByText("The step broke.")).toHaveLength(1);
  expect(screen.queryByRole("link", { name: "Open the panel" })).not.toBeInTheDocument();
});

test("a run that cannot be reached says so instead of waiting forever", () => {
  render(
    <MemoryRouter>
      <I18nProvider>
<UnitsProvider>
        <RunView analysisId="missing" analysisPlan={examplePlan} events={[]} connected={false} unreachable />
      </UnitsProvider>
</I18nProvider>
    </MemoryRouter>,
  );

  expect(screen.getByText("Could not connect")).toBeInTheDocument();
  expect(screen.getByText("The run stream could not be opened.")).toBeInTheDocument();
  expect(screen.queryByText("Waiting for the first event…")).not.toBeInTheDocument();
});

test("a single item is written in the singular", () => {
  renderRun([
    { analysis: "x", seq: 1, type: "step_started", ts: "2026-11-14T10:00:00Z", step: "s1", data: { operation: "read_series", visual_type: "satellite", phrase: { en: "Reading" } } },
    { analysis: "x", seq: 2, type: "data_read", ts: "2026-11-14T10:00:01Z", step: "s1", data: { source: "landsat", origin: "cache", n_items: 1 } },
    { analysis: "x", seq: 3, type: "analysis_finished", ts: "2026-11-14T10:00:02Z", data: { status: "finished" } },
  ] as RunEvent[]);

  expect(screen.getAllByText(/1 item/).length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText(/1 items/)).not.toBeInTheDocument();
});

test("the event log keeps the time and the step", () => {
  const { container } = renderRun();
  const rows = container.querySelectorAll(".run-stage-log li");

  expect(rows.length).toBe(events.length);
  expect(rows[0].querySelector("time")).not.toBeNull();
  expect(within(rows[0] as HTMLElement).getByText("Data read")).toBeInTheDocument();
});

test("the run folds what Delfos decided and does not link the plan", () => {
  const decided = {
    ...examplePlan,
    answer: { ...examplePlan.answer, decided: [{ field: "period", text: { en: "Period: 1981 to 2024, the whole record." } }] },
  } as unknown as plan.AnalysisPlan;
  const { container } = render(
    <MemoryRouter>
      <I18nProvider>
        <UnitsProvider>
          <RunView analysisId="x" analysisPlan={decided} events={events} connected unreachable={false} />
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );

  expect(screen.getByText("How Delfos decided")).toBeInTheDocument();
  expect(container.querySelector("details.run-stage-decided")?.hasAttribute("open")).toBe(false);
  expect(screen.getByText("Period: 1981 to 2024, the whole record.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "See the full plan" })).not.toBeInTheDocument();
});

const readPlan = {
  ...examplePlan,
  steps: [
    { id: "s1", type: "ingest", operation: "read_series", version: "1.0", params: { variable: "air_temperature" }, depends_on: [], sources: ["nasa-power"] },
    { id: "s2", type: "compute", operation: "trend", version: "1.0", params: {}, depends_on: ["s1"] },
  ],
} as unknown as plan.AnalysisPlan;
const readPhrase = { en: "Reading {n} scenes from {source}", es: "Leyendo {n} escenas de {source}" };
const started = { analysis: "x", seq: 1, type: "step_started", ts: "2026-11-14T10:00:00Z", step: "s1", data: { operation: "read_series", visual_type: "satellite", phrase: readPhrase } } as RunEvent;
const read = { analysis: "x", seq: 2, type: "data_read", ts: "2026-11-14T10:00:01Z", step: "s1", data: { source: "nasa-power", origin: "remote", n_items: 156, period: { start: "2013-01-01", end: "2025-12-01" } } } as RunEvent;
const readDone = { analysis: "x", seq: 3, type: "step_finished", ts: "2026-11-14T10:00:02Z", step: "s1", data: { seconds: 1, results: ["r-s1-series"] } } as RunEvent;

function readView(items: RunEvent[], results: StepResult[] = []) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <UnitsProvider>
          <RunView
            analysisId="x"
            analysisPlan={readPlan}
            events={items}
            connected
            unreachable={false}
            openIn={0}
            sources={[{ id: "nasa-power", name: "NASA POWER" }]}
            variables={[{ id: "air_temperature", name: { en: "Air temperature" }, layer: "climate" }]}
            results={results}
          />
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

test("while a read runs it names what it reads, with a spinner, and never a missing source", async () => {
  const { container } = readView([started]);

  expect(screen.getAllByText("Reading air temperature").length).toBeGreaterThanOrEqual(1);
  expect(container.querySelector(".run-spinner")).not.toBeNull();
  await waitFor(() => expect(container.querySelector(".run-stage-scene")).not.toBeNull());
  const read = container.textContent ?? "";
  expect(read).not.toMatch(/Source not reported|…|\{n\}|\{source\}/);
});

test("once the count arrives the read says it, the node counts and dates read as years", async () => {
  const { container } = readView([started, read, readDone]);

  await waitFor(() => expect(container.querySelector(".run-stage-scene")).not.toBeNull());
  expect(screen.getAllByText(/156 scenes from NASA POWER/).length).toBeGreaterThanOrEqual(1);
  const labels = [...container.querySelectorAll(".run-node-label")].map((label) => label.textContent ?? "");
  expect(labels.some((label) => label.includes("Air temperature") && label.includes("156"))).toBe(true);
  expect(container.querySelector(".run-stage-build")?.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  expect(container.querySelector(".run-node.is-done .run-check")).not.toBeNull();
});

test("a finished run stops the spinner", () => {
  const { container } = renderRun();
  expect(container.querySelector(".run-spinner")).toBeNull();
});

test("the key figure card shows the value a calculation saved", async () => {
  const trend = {
    id: "r-s2-trend",
    type: "value",
    variable: "air_temperature",
    unit: "°C/year",
    value: 0.0269,
    direction_assertable: true,
    method: { operation: "trend", version: "1.1", params: {} },
    interval: { level: 0.95, low: 0.0142, high: 0.0406 },
  } as unknown as StepResult;
  localStorage.setItem("hup-units-choice", "metric");
  const { container } = readView([started, read, readDone], [trend]);

  await waitFor(() => expect(container.querySelector(".run-card-figure")).not.toBeNull());
  expect(container.querySelector(".run-card-figure")?.textContent).toMatch(/0[.,]27/);
});

test("a phrase the catalogue has only in English never shows on a Spanish screen", async () => {
  localStorage.setItem("hup-lang", "es");
  const scanPlan = {
    ...examplePlan,
    steps: [{ id: "s1", type: "compute", operation: "scan_trends", version: "1.0", params: {}, depends_on: [] }],
  } as unknown as plan.AnalysisPlan;
  const scan = {
    analysis: "x",
    seq: 1,
    type: "step_started",
    ts: "2026-11-14T10:00:00Z",
    step: "s1",
    data: { operation: "scan_trends", visual_type: "compute", phrase: { en: "Calculating scan trends" } },
  } as RunEvent;
  const { container } = render(
    <MemoryRouter>
      <I18nProvider>
        <UnitsProvider>
          <RunView
            analysisId="x"
            analysisPlan={scanPlan}
            events={[scan]}
            connected
            unreachable={false}
            openIn={0}
            operations={[{ id: "scan_trends", visual_type: "compute", phrase: { en: "Calculating scan trends" } }]}
          />
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(container.querySelector(".run-stage-scene")).not.toBeNull());
  expect(container.textContent).not.toContain("Calculating scan trends");
  expect(screen.getAllByText("Cálculo").length).toBeGreaterThanOrEqual(1);
  localStorage.removeItem("hup-lang");
});

test("a calculation node takes the operation's own short name when the catalogue has one", async () => {
  const chainPlan = {
    ...examplePlan,
    steps: [{ id: "s1", type: "compute", operation: "evaluate_chain", version: "1.0", params: {}, depends_on: [] }],
  } as unknown as plan.AnalysisPlan;
  const { container } = render(
    <MemoryRouter>
      <I18nProvider>
        <UnitsProvider>
          <RunView
            analysisId="x"
            analysisPlan={chainPlan}
            events={[]}
            connected
            unreachable={false}
            openIn={0}
            operations={[
              {
                id: "evaluate_chain",
                visual_type: "compute",
                phrase: { en: "Checking the links of the chain of evidence" },
                name: { en: "Chains" },
              },
            ]}
          />
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(container.querySelector(".run-stage-scene")).not.toBeNull());
  const labels = [...container.querySelectorAll(".run-node-label")].map((label) => label.textContent);
  expect(labels).toContain("Chains");
});

test("until the first event the bar moves as on the cover, then counts steps", () => {
  const { container, rerender } = render(view([], true));
  expect(container.querySelector(".run-stage-fill.is-indeterminate")).not.toBeNull();

  rerender(view(events.slice(0, 2), true));
  expect(container.querySelector(".run-stage-fill.is-indeterminate")).toBeNull();
});

test("opened on a run under way, the history sets every state without animating, and the running step flows", async () => {
  // The past of a run: two reads done, the third still running when the page is reloaded.
  const history = [
    { analysis: "x", seq: 1, type: "step_started", ts: "2026-11-14T10:00:00Z", step: "s1", data: { operation: "read_series", visual_type: "satellite", phrase: readPhrase } },
    { analysis: "x", seq: 2, type: "data_read", ts: "2026-11-14T10:00:01Z", step: "s1", data: { source: "nasa-power", origin: "remote", n_items: 156 } },
    { analysis: "x", seq: 3, type: "step_finished", ts: "2026-11-14T10:00:02Z", step: "s1", data: { seconds: 1, results: ["r-s1-series"] } },
    { analysis: "x", seq: 4, type: "step_started", ts: "2026-11-14T10:00:03Z", step: "s2", data: { operation: "trend", visual_type: "compute", phrase: { en: "Calculating the trend" } } },
  ] as RunEvent[];
  const { container } = render(
    <MemoryRouter>
      <I18nProvider>
        <UnitsProvider>
          <RunView analysisId="x" analysisPlan={readPlan} events={history} connected unreachable={false} openIn={0} />
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(container.querySelector(".run-stage-scene")).not.toBeNull());
  expect(container.querySelector(".run-node.is-done")).not.toBeNull();
  expect(container.querySelector(".run-node.is-active")).not.toBeNull();
  expect(container.querySelector(".run-edge.is-active")).not.toBeNull();
  expect(container.querySelector(".run-spinner")).not.toBeNull();
  expect(container.querySelector(".run-stage-rest")).toBeNull();
  // History does not travel: no pulse for what had already happened.
  expect(container.querySelector(".run-pulse")).toBeNull();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
});

const estimateFor = (seconds: Record<string, number>): PlanEstimate => ({
  total_seconds: 60,
  low_seconds: 50,
  high_seconds: 80,
  basis: "measured",
  steps: Object.entries(seconds).map(([id, value]) => ({
    id,
    seconds: value,
    low_seconds: value,
    high_seconds: value,
    basis: "measured",
    n: 4,
    kind: id === "s1" ? "read" : "compute",
    source: id === "s1" ? "nasa-power" : null,
    source_name: id === "s1" ? { en: "NASA POWER", es: "NASA POWER" } : null,
    expected_origin: id === "s1" ? "cold" : null,
  })),
});

function estimateView(items: RunEvent[], estimate: PlanEstimate | null, clock: number) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <UnitsProvider>
          <RunView
            analysisId="x"
            analysisPlan={readPlan}
            events={items}
            connected
            unreachable={false}
            openIn={0}
            estimate={estimate}
            clock={clock}
          />
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

test("with an estimate the bar weighs the time of each step and the screen lists what it waits for", async () => {
  // s1 (read, 40 s usual) has run 20 s; s2 (compute, 60 s) is pending: 20 of 100 → 20 %.
  const clock = Date.parse("2026-11-14T10:00:20Z");
  const { container } = estimateView([started], estimateFor({ s1: 40, s2: 60 }), clock);

  await waitFor(() => expect(container.querySelector(".run-waiting")).not.toBeNull());
  expect((container.querySelector(".run-stage-fill") as HTMLElement).style.width).toBe("20%");
  expect(screen.getByText(/Estimated time: about 1 min · about 40 s left/)).toBeInTheDocument();
  const waiting = container.querySelector(".run-waiting") as HTMLElement;
  expect(within(waiting).getByText("Waiting now")).toBeInTheDocument();
  expect(within(waiting).getByText("NASA POWER")).toBeInTheDocument();
  expect(within(waiting).getByText("first read of this place: about 40 s")).toBeInTheDocument();
  expect(within(waiting).getByText(/Next: 1 step · about 1 min/)).toBeInTheDocument();
  expect((waiting.querySelector(".run-waiting-track i") as HTMLElement).style.width).toBe("50%");
});

test("without an estimate (route missing or failed) the bar counts finished steps and lists nothing", async () => {
  const { container } = estimateView([started, read, readDone], null, Date.parse("2026-11-14T10:00:20Z"));

  await waitFor(() => expect(container.querySelector(".run-stage-scene")).not.toBeNull());
  expect((container.querySelector(".run-stage-fill") as HTMLElement).style.width).toBe("50%");
  expect(container.querySelector(".run-waiting")).toBeNull();
  expect(screen.queryByText(/Estimated time/)).not.toBeInTheDocument();
});
