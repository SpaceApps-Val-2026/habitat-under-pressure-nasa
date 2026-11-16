import { expect, test } from "vitest";
import { evidenceOf } from "./shared";
import type { StepResult } from "./shared";

const series = (data_class: string, extra: object = {}) =>
  ({
    id: "r",
    variable: "population_in_hot_cells",
    type: "series",
    data_class,
    evidence_level: "E0",
    method: { operation: "read_series" },
    series: [{ t: "2013-01-01", v: 1 }],
    ...extra,
  }) as unknown as StepResult;

test("a series read from its source at E0 is not described with nothing measured", () => {
  expect(evidenceOf(series("approximated"))).toEqual({
    name: "evidence.E0series.name",
    note: "evidence.E0approximated.note",
  });
  expect(evidenceOf(series("measured"))?.note).toBe("evidence.E0series.note");
});

test("other E0 results keep the described wording", () => {
  expect(evidenceOf(series("measured", { method: { operation: "rain_extreme" } }))?.name).toBe("evidence.E0.name");
  expect(evidenceOf(series("measured", { absence: { reason: "no_coverage" } }))?.name).toBe("evidence.E0.name");
});
