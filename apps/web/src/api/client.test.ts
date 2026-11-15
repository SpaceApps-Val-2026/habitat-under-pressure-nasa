import { afterEach, expect, test, vi } from "vitest";

import { getAnalysis, getAnalysisResults, getCatalog, getHealth, listAnalyses, openRunEvents } from "./client";
import { RUN_EVENT_TYPES, type RunEvent } from "./events";
import eventSchema from "../../../../docs/contratos/event.schema.json";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

test("mock mode serves the contract examples with the route shapes", async () => {
  vi.stubEnv("VITE_API", "mock");

  const analysis = await getAnalysis("sagunt-impact-example");
  const results = await getAnalysisResults("sagunt-impact-example");
  const catalog = await getCatalog();

  expect(analysis.id).toBe("sagunt-impact-example");
  expect(results.map((item) => item.id)).toEqual(["r-s4-change", "r-s5-change", "r-s3-series"]);
  expect(catalog.operations.length).toBeGreaterThan(0);
});

test("live mode reads the health endpoint", async () => {
  vi.stubEnv("VITE_API", "live");
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "ok", version: "test", catalog_version: null })));
  vi.stubGlobal("fetch", fetchMock);

  await expect(getHealth()).resolves.toEqual({ status: "ok", version: "test", catalog_version: null });
  expect(fetchMock).toHaveBeenCalledWith("/health");
});

test("live mode asks for the analysis results by id", async () => {
  vi.stubEnv("VITE_API", "live");
  const fetchMock = vi.fn(async () => new Response("[]"));
  vi.stubGlobal("fetch", fetchMock);

  await expect(getAnalysisResults("sagunt-impact-example")).resolves.toEqual([]);
  expect(fetchMock).toHaveBeenCalledWith("/api/analyses/sagunt-impact-example/results");
});

test("an error status rejects", async () => {
  vi.stubEnv("VITE_API", "live");
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));

  await expect(getHealth()).rejects.toThrow("API 500");
});

test("mock mode lists the saved analyses and filters them", async () => {
  vi.stubEnv("VITE_API", "mock");

  const all = await listAnalyses();
  expect(all.items.length).toBeGreaterThanOrEqual(2);

  const trend = await listAnalyses({ design: "trend" });
  expect(trend.items.length).toBeGreaterThan(0);
  expect(trend.items.every((item) => item.design === "trend")).toBe(true);

  const search = await listAnalyses({ q: "precipitation" });
  expect(search.items.length).toBeGreaterThan(0);
  expect(search.items.every((item) => JSON.stringify(item.title).toLowerCase().includes("precipitation"))).toBe(true);
});

test("live mode sends the filters to the analyses route", async () => {
  vi.stubEnv("VITE_API", "live");
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [] })));
  vi.stubGlobal("fetch", fetchMock);

  await expect(listAnalyses({ q: "rain", design: "trend", featured: true })).resolves.toEqual({ items: [] });
  expect(fetchMock).toHaveBeenCalledWith("/api/analyses?q=rain&design=trend&featured=true");
});

test("the client listens to every event type in the contract", () => {
  const types = eventSchema.properties.type.enum as string[];
  expect([...RUN_EVENT_TYPES].sort()).toEqual([...types].sort());
});

test("live mode opens the stream and closes it when the run finishes", () => {
  vi.stubEnv("VITE_API", "live");

  class FakeSource {
    static last: FakeSource;
    listeners = new Map<string, (message: MessageEvent) => void>();
    closed = false;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(public url: string) {
      FakeSource.last = this;
    }
    addEventListener(type: string, listener: EventListener) {
      this.listeners.set(type, listener as (message: MessageEvent) => void);
    }
    close() {
      this.closed = true;
    }
    emit(type: string, data: unknown) {
      this.listeners.get(type)?.({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
  vi.stubGlobal("EventSource", FakeSource);

  const received: RunEvent[] = [];
  const close = openRunEvents("abc", { replay: true }, (event) => received.push(event));
  const source = FakeSource.last;
  expect(source.url).toBe("/api/analyses/abc/events?replay=1");

  source.emit("step_started", { analysis: "abc", seq: 1, type: "step_started", ts: "2026-11-14T10:00:00Z", data: {} });
  expect(received).toHaveLength(1);

  source.emit("analysis_finished", { analysis: "abc", seq: 2, type: "analysis_finished", ts: "2026-11-14T10:00:01Z", data: { status: "finished" } });
  expect(source.closed).toBe(true);

  close();
});
