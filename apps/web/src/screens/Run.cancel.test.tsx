import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import rawEvents from "../../../../docs/contratos/examples/events.jsonl?raw";
import { ApiError } from "../api/client";
import { parseEvents, type RunEvent } from "../api/events";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";

const all = parseEvents(rawEvents);
const running = all.filter((event) => event.type !== "analysis_finished");
const cancelledEvent = {
  analysis: "x",
  seq: 999,
  type: "analysis_cancelled",
  ts: "2026-11-14T10:05:00Z",
  data: { status: "cancelled" },
} as unknown as RunEvent;

const stream = { events: running as RunEvent[] };
const saved = { status: "running", plan: { request: { text: "Is Alzira warmer?", language: "en" }, steps: [] } };

vi.mock("../api/client", async (original) => {
  const actual = await original<typeof import("../api/client")>();
  return {
    ...actual,
    getAnalysis: vi.fn(async () => saved),
    getCatalog: vi.fn(async () => ({ operations: [], sources: [], variables: [] })),
    getAnalysisResults: vi.fn(async () => []),
    cancelAnalysis: vi.fn(),
    estimatePlan: vi.fn(async () => Promise.reject(new Error("no route"))),
    openRunEvents: vi.fn((_id: string, _options: unknown, onEvent: (event: RunEvent) => void) => {
      for (const event of stream.events) onEvent(event);
      return () => {};
    }),
  };
});

const client = await import("../api/client");
const { Run } = await import("./Run");
const cancelAnalysis = vi.mocked(client.cancelAnalysis);
const estimatePlan = vi.mocked(client.estimatePlan);

function HomeProbe() {
  const state = useLocation().state as { question?: string; cancelled?: boolean; leftRun?: boolean } | null;
  return <p>HOME {state?.question} {state?.cancelled ? "cancelled" : ""} {state?.leftRun ? "left" : ""}</p>;
}

function ReportProbe() {
  const { analysisId } = useParams();
  return <p>REPORT {analysisId}</p>;
}

function renderRun() {
  return render(
    <MemoryRouter initialEntries={["/run/x"]}>
      <I18nProvider>
        <UnitsProvider>
          <Routes>
            <Route path="/" element={<HomeProbe />} />
            <Route path="/run/:analysisId" element={<Run openAfter={0} />} />
            <Route path="/a/:analysisId/report/journalistic" element={<ReportProbe />} />
          </Routes>
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-lang", "en");
  stream.events = running;
  saved.status = "running";
  cancelAnalysis.mockReset();
});

afterEach(() => vi.useRealTimers());

test("Cancel stops the run on the server and goes home with the question and the cancelled note", async () => {
  let accept: (value: { analysis_id: string; status: string; accepted: boolean }) => void = () => {};
  cancelAnalysis.mockImplementation(() => new Promise((done) => (accept = done)));
  renderRun();

  const button = await screen.findByRole("button", { name: "Cancel" });
  await waitFor(() => expect(screen.getByText(/Is Alzira warmer\?/)).toBeInTheDocument());
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: "Cancelling…" })).toBeDisabled();
  expect(cancelAnalysis).toHaveBeenCalledWith("x");

  await act(async () => accept({ analysis_id: "x", status: "cancelled", accepted: true }));
  expect(await screen.findByText(/HOME Is Alzira warmer\? cancelled/)).toBeInTheDocument();
});

test("a run that had already finished opens its report instead", async () => {
  cancelAnalysis.mockResolvedValue({ analysis_id: "x", status: "finished", accepted: false });
  renderRun();

  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("REPORT x")).toBeInTheDocument();
});

test("a failed cancel request falls back to the honest note", async () => {
  cancelAnalysis.mockRejectedValue(new ApiError(404, "not_found"));
  renderRun();

  await waitFor(() => expect(screen.getByText(/Is Alzira warmer\?/)).toBeInTheDocument());
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText(/HOME Is Alzira warmer\? +left/)).toBeInTheDocument();
});

test("cancelled from another tab: the screen says so, opens no report and offers to ask again", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  stream.events = [...running, cancelledEvent];
  renderRun();

  expect((await screen.findAllByText("Cancelled")).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("The analysis was cancelled before it finished; it has no answer.").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  await act(async () => {
    vi.advanceTimersByTime(3000);
  });
  expect(screen.queryByText(/^REPORT/)).not.toBeInTheDocument();

  await waitFor(() => expect(screen.getByText(/Is Alzira warmer\?/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Ask again" }));
  expect(await screen.findByText(/HOME Is Alzira warmer\?/)).toBeInTheDocument();
});

test("a saved analysis marked cancelled shows the cancelled state even if its events do not say it", async () => {
  saved.status = "cancelled";
  renderRun();

  expect(await screen.findByRole("button", { name: "Ask again" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
});

test("the estimate is asked once for the plan; when the route fails the run reads as before", async () => {
  const { container } = renderRun();

  await waitFor(() => expect(estimatePlan).toHaveBeenCalledTimes(1));
  expect(estimatePlan).toHaveBeenCalledWith(saved.plan);
  expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
  expect(container.querySelector(".run-waiting")).toBeNull();
  expect(screen.queryByText(/Estimated time/)).not.toBeInTheDocument();
});
