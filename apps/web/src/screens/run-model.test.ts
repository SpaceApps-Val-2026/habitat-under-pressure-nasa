import rawEvents from "../../../../docs/contratos/examples/events.jsonl?raw";
import { expect, test } from "vitest";

import { parseEvents } from "../api/events";
import { stepsOf } from "./run-model";

test("the example replays as 22 ordered events", () => {
  const events = parseEvents(rawEvents);
  expect(events).toHaveLength(22);
  expect(events.map((event) => event.seq)).toEqual(Array.from({ length: 22 }, (_, index) => index + 1));
});

test("the steps keep their status, origins and warning", () => {
  const steps = stepsOf(parseEvents(rawEvents));

  expect(steps.map((step) => step.id)).toEqual(["s1", "s2", "s3", "s4", "s5", "s6", "s7"]);
  expect(steps[0].dataReads[0]).toMatchObject({ origin: "remote", nItems: 151 });
  expect(steps[0].item).toBe("scene 76 of 151");
  expect(steps[1].dataReads[0]).toMatchObject({ origin: "cache" });
  // s3 never gets a data_read: the package is missing, so it warns and finishes with nothing.
  expect(steps[2].dataReads).toHaveLength(0);
  expect((steps[2].warnings[0].data as Record<string, unknown>).code).toBe("needs_package");
  expect(steps[3].results).toEqual(["r-s4-change"]);
  expect(steps[3].seconds).toBe(2);
  expect(steps.every((step) => step.finished)).toBe(true);
});

test("events arriving twice are not counted twice", () => {
  const events = parseEvents(rawEvents);
  const twice = [...events, ...events.slice(0, 3)];
  expect(stepsOf(twice)).toHaveLength(7);
  expect(stepsOf(twice)[0].dataReads).toHaveLength(1);
});
