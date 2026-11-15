import { expect, test } from "vitest";

import { COASTLINE, PLACES } from "./data";

test("the twelve places keep their three themes and complete series", () => {
  expect(PLACES).toHaveLength(12);
  const themes = new Set(PLACES.map((place) => place.theme));
  expect([...themes].sort()).toEqual(["change", "pressure", "water"]);
  for (const theme of themes) {
    expect(PLACES.filter((place) => place.theme === theme)).toHaveLength(4);
  }
  for (const place of PLACES) {
    expect(place.t2m).toHaveLength(44);
    expect(place.prec.series).toHaveLength(44);
    expect(place.soil.series).toHaveLength(44);
    expect(typeof place.sig).toBe("boolean");
    expect(Number.isFinite(place.sen_dec)).toBe(true);
  }
  expect(PLACES.filter((place) => place.sig)).toHaveLength(9);
});

test("Parc Sagunt II is the default place", () => {
  const sagunt = PLACES.find((place) => place.id === "sagunt");
  expect(sagunt?.name).toContain("Sagunt");
  expect(sagunt?.theme).toBe("pressure");
});

test("the coastline keeps every line of the Natural Earth copy", () => {
  expect(COASTLINE).toHaveLength(602);
  expect(COASTLINE.every((line) => line.every(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat)))).toBe(
    true,
  );
});
