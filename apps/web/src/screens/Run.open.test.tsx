import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import rawEvents from "../../../../docs/contratos/examples/events.jsonl?raw";
import { parseEvents, type RunEvent } from "../api/events";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";

const events = parseEvents(rawEvents);

// The run screen with a stream that delivers the whole example run at once.
vi.mock("../api/client", async (original) => {
  const actual = await original<typeof import("../api/client")>();
  return {
    ...actual,
    getAnalysis: vi.fn(async () => ({ plan: null })),
    getCatalog: vi.fn(async () => ({ operations: [], sources: [] })),
    openRunEvents: vi.fn((_id: string, _options: unknown, onEvent: (event: RunEvent) => void) => {
      for (const event of events) onEvent(event);
      return () => {};
    }),
  };
});

const { Run } = await import("./Run");

function ReportProbe() {
  const { analysisId } = useParams();
  return <p>REPORT {analysisId}</p>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <I18nProvider>
<UnitsProvider>
        <Routes>
          <Route path="/run/:analysisId" element={<Run openAfter={1200} />} />
          <Route path="/a/:analysisId/report/journalistic" element={<ReportProbe />} />
        </Routes>
      </UnitsProvider>
</I18nProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.useRealTimers();
});

test("a finished run says so, then opens the journalistic report by itself", async () => {
  vi.useFakeTimers();
  renderAt("/run/sagunt-impact-example");

  expect(screen.getByText("The answer is ready. Opening the report…")).toBeInTheDocument();
  expect(screen.queryByText(/^REPORT/)).not.toBeInTheDocument();
  await act(async () => {
    vi.advanceTimersByTime(1200);
  });
  expect(screen.getByText("REPORT sagunt-impact-example")).toBeInTheDocument();
});

test("a replay stays on the run screen", async () => {
  vi.useFakeTimers();
  renderAt("/run/sagunt-impact-example?replay=1");

  await act(async () => {
    vi.advanceTimersByTime(5000);
  });
  expect(screen.queryByText(/^REPORT/)).not.toBeInTheDocument();
});
