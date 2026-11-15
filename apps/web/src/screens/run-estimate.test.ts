import { expect, test } from "vitest";

import type { PlanEstimate, StepEstimate } from "../api/client";
import { messages } from "../i18n/messages";
import type { StepState } from "./run-model";
import { duration, pending, timeText, waitingHint, waitingRows, weightedProgress } from "./run-estimate";

function translator(language: "en" | "es") {
  return (key: string) =>
    key.split(".").reduce<unknown>((node, part) => (node as Record<string, unknown>)[part], messages[language]) as string;
}

const T0 = Date.parse("2026-11-15T09:00:00Z");

function step(id: string, startedAfter: number | null, finishedAfter: number | null = null): StepState {
  return {
    id,
    operation: "read_series",
    phrase: null,
    visualType: "satellite",
    started: startedAfter !== null,
    finished: finishedAfter !== null,
    seconds: null,
    startedAt: startedAfter === null ? null : T0 + startedAfter * 1000,
    finishedAt: finishedAfter === null ? null : T0 + finishedAfter * 1000,
    fraction: null,
    item: null,
    phase: null,
    results: [],
    dataReads: [],
    warnings: [],
    alerts: [],
    error: null,
  };
}

function est(id: string, seconds: number, extra: Partial<StepEstimate> = {}): StepEstimate {
  return {
    id,
    seconds,
    low_seconds: seconds,
    high_seconds: seconds,
    basis: "measured",
    n: 5,
    kind: "read",
    source: null,
    source_name: null,
    expected_origin: null,
    ...extra,
  };
}

const estimate: PlanEstimate = {
  total_seconds: 60,
  low_seconds: 50,
  high_seconds: 80,
  basis: "measured",
  steps: [est("s1", 40), est("s2", 20, { kind: "compute" }), est("s3", 40, { kind: "write" })],
};
const ids = ["s1", "s2", "s3"];
const at = (seconds: number) => T0 + seconds * 1000;

test("a finished step counts its whole estimate; a running one its elapsed time", () => {
  const states = new Map([
    ["s1", step("s1", 0, 30)],
    ["s2", step("s2", 30)],
  ]);
  // 40 (s1 done) + 10 (s2 running for 10 s of 20) out of 100.
  expect(weightedProgress(ids, states, estimate, at(40))).toBeCloseTo(0.5);
});

test("a running step slower than its estimate waits at 95 % of it, never at 100 %", () => {
  const states = new Map([["s1", step("s1", 0)]]);
  expect(weightedProgress(ids, states, estimate, at(500))).toBeCloseTo((0.95 * 40) / 100);
});

test("the bar never says the run is complete before it is", () => {
  const states = new Map([
    ["s1", step("s1", 0, 1)],
    ["s2", step("s2", 1, 2)],
    ["s3", step("s3", 2)],
  ]);
  expect(weightedProgress(ids, states, estimate, at(3000))).toBe(0.97);
});

test("a missing estimate gives no weighted progress; a step without one takes the mean of the others", () => {
  const states = new Map([["s1", step("s1", 0, 10)]]);
  expect(weightedProgress(ids, states, null, at(10))).toBeNull();
  expect(weightedProgress(ids, states, { ...estimate, steps: [] }, at(10))).toBeNull();
  const partial = { ...estimate, steps: [est("s1", 30)] };
  // s2 and s3 take the mean (30): 30 of 90.
  expect(weightedProgress(ids, states, partial, at(10))).toBeCloseTo(30 / 90);
});

test("durations round as a reader says them", () => {
  expect(duration(2)).toBe("5 s");
  expect(duration(42)).toBe("40 s");
  expect(duration(88)).toBe("1 min 30 s");
  expect(duration(120)).toBe("2 min");
  expect(duration(725)).toBe("12 min");
});

test("the time line in Spanish and English", () => {
  const es = translator("es");
  const en = translator("en");
  const tight = { ...estimate, total_seconds: 40, low_seconds: 35, high_seconds: 55 };

  expect(timeText(tight, 15, false, es)).toBe("Tiempo estimado: unos 40 s · quedan unos 25 s");
  expect(timeText(tight, 15, false, en)).toBe("Estimated time: about 40 s · about 25 s left");
  // A wide range is said as a range.
  expect(timeText({ ...tight, low_seconds: 20, high_seconds: 60 }, 0, false, es)).toBe(
    "Tiempo estimado: entre 20 s y 1 min · quedan unos 40 s",
  );
  // Nothing measured yet.
  expect(timeText({ ...tight, basis: "prior" }, 0, false, es)).toMatch(/^Estimación inicial: /);
  // Past the high estimate: no number.
  expect(timeText(tight, 70, false, es)).toBe("Tiempo estimado: unos 40 s · Está tardando más de lo habitual");
  // Never a countdown at zero while it still runs.
  expect(timeText(tight, 39, false, es)).toBe("Tiempo estimado: unos 40 s · ya casi");
  expect(timeText(tight, 39, false, es)).not.toMatch(/quedan unos 0/);
});

test("the waiting list comes from the events: running steps, and a finished one only for a moment", () => {
  const states = new Map([
    ["s1", step("s1", 0, 9)],
    ["s2", step("s2", 9)],
  ]);
  const estimates = new Map(estimate.steps.map((item) => [item.id, item]));
  const rows = waitingRows(ids, states, estimates, () => "compute", at(10));
  expect(rows.map((row) => [row.id, row.done])).toEqual([
    ["s1", true],
    ["s2", false],
  ]);
  expect(rows[1].fraction).toBeCloseTo(1 / 20);
  expect(waitingRows(ids, states, estimates, () => "compute", at(20)).map((row) => row.id)).toEqual(["s2"]);
  expect(pending(ids, states, estimates)).toEqual({ count: 1, seconds: 40 });
});

test("the hint says what the source usually takes, for the case at hand", () => {
  const es = translator("es");
  expect(waitingHint(est("s1", 12), es)).toBe("habitual: unos 10 s");
  expect(waitingHint(est("s1", 0.3, { expected_origin: "cached" }), es)).toBe("ya en este equipo: menos de 1 s");
  expect(waitingHint(est("s1", 90, { expected_origin: "cold" }), es)).toBe("primera lectura de este lugar: unos 1 min 30 s");
});

test("a step running past its high estimate no longer promises its usual time", () => {
  const es = translator("es");
  const en = translator("en");
  const cached = est("s1", 0.3, { expected_origin: "cached", high_seconds: 0.8 });
  expect(waitingHint(cached, es, 1)).toBe("ya en este equipo: menos de 1 s");
  expect(waitingHint(cached, es, 40)).toBe("tarda más de lo previsto");
  expect(waitingHint(est("s1", 12, { high_seconds: 20 }), en, 25)).toBe("taking longer than expected");
  expect(waitingHint(est("s1", 12, { high_seconds: 20 }), en, 15)).toBe("usual: about 10 s");
});
