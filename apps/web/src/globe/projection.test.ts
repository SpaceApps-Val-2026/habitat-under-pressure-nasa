import { describe, expect, test } from "vitest";

import {
  ease,
  greatCircle,
  longitudeDelta,
  markerLatitude,
  project,
  ringRadii,
  viewFor,
} from "./projection";

const SAGUNT = { lat: 39.66, lon: -0.27 };

describe("wide view (container >= 600 px)", () => {
  test("uses 0.44 of the shorter side and centres the disc", () => {
    const view = viewFor(1200, 800, SAGUNT.lon, SAGUNT.lat);
    expect(view.mode).toBe("wide");
    expect(view.R).toBeCloseTo(352);
    expect(view.x).toBeCloseTo(600);
    expect(view.y).toBeCloseTo(400);
    expect(view.lon).toBeCloseTo(-0.27);
    expect(view.lat).toBeCloseTo(39.66);
  });

  test("switches to the horizon below 600 px", () => {
    expect(viewFor(599, 600, 0, 0).mode).toBe("horizon");
    expect(viewFor(600, 600, 0, 0).mode).toBe("wide");
  });

  test("keeps the centre inside G3's latitude band", () => {
    expect(viewFor(1200, 800, 0, 78.22).lat).toBeCloseTo(62);
    expect(viewFor(1200, 800, 0, -80).lat).toBeCloseTo(-55);
  });
});

describe("horizon geometry (container < 600 px)", () => {
  const view = viewFor(390, 230, SAGUNT.lon, SAGUNT.lat);

  test("radius is 1.15 of the width and the top edge sits 58 px into the band", () => {
    expect(view.mode).toBe("horizon");
    expect(view.R).toBeCloseTo(448.5);
    expect(view.x).toBeCloseTo(195);
    expect(view.y).toBeCloseTo(506.5);
    expect(view.y - view.R).toBeCloseTo(58);
  });

  test("centres the view 60 degrees south of the place", () => {
    expect(view.lon).toBeCloseTo(-0.27);
    expect(view.lat).toBeCloseTo(39.66 - 60);
  });

  test("puts the place in the cap, 60 degrees above the centre", () => {
    const point = project(SAGUNT.lon, markerLatitude(SAGUNT.lat, view.mode), view);
    expect(point.cos).toBeCloseTo(0.5);
    expect(point.x).toBeCloseTo(view.x);
    expect(point.y).toBeCloseTo(view.y - view.R * Math.sin(Math.PI / 3));
    expect(point.y).toBeGreaterThan(58);
    expect(point.y).toBeLessThan(230);
  });

  test("places south of 30 S are shown as 30 S", () => {
    const south = viewFor(390, 230, -62, -40);
    expect(south.lat).toBeCloseTo(-90);
    expect(markerLatitude(-40, "horizon")).toBe(-30);
    expect(markerLatitude(-40, "wide")).toBe(-40);

    const shown = project(-62, markerLatitude(-40, "horizon"), south);
    const raw = project(-62, -40, south);
    expect(shown.cos).toBeCloseTo(0.5);
    expect(raw.y).not.toBeCloseTo(shown.y, 1);
  });
});

describe("orthographic projection", () => {
  const view = viewFor(1000, 800, 10, 45);
  const equator = viewFor(1000, 800, 10, 0);

  test("the centre projects to the centre of the disc", () => {
    const point = project(10, 45, view);
    expect(point.x).toBeCloseTo(view.x);
    expect(point.y).toBeCloseTo(view.y);
    expect(point.cos).toBeCloseTo(1);
  });

  test("a quarter turn east on the equator lands on the right limb", () => {
    const point = project(100, 0, equator);
    expect(point.x).toBeCloseTo(equator.x + equator.R);
    expect(point.y).toBeCloseTo(equator.y);
    expect(point.cos).toBeCloseTo(0, 6);
  });

  test("the far side is hidden", () => {
    expect(project(-170, -45, view).cos).toBeLessThan(0);
  });
});

describe("longitude and great-circle paths", () => {
  test("longitude deltas take the short way round", () => {
    expect(longitudeDelta(170, -170)).toBeCloseTo(20);
    expect(longitudeDelta(-170, 170)).toBeCloseTo(-20);
    expect(longitudeDelta(10, 10)).toBe(0);
    expect(longitudeDelta(0, 180)).toBe(-180);
  });

  test("arcs start and end on the places and lift towards the middle", () => {
    const path = greatCircle(SAGUNT, { lat: 78.22, lon: 15.65 }, 24);
    expect(path).toHaveLength(25);
    expect(path[0].lon).toBeCloseTo(SAGUNT.lon);
    expect(path[0].lat).toBeCloseTo(SAGUNT.lat);
    expect(path[0].lift).toBeCloseTo(1);
    expect(path[24].lon).toBeCloseTo(15.65);
    expect(path[24].lat).toBeCloseTo(78.22);
    expect(path[12].lift).toBeCloseTo(1.14);
    for (const point of path) {
      expect(point.lat).toBeGreaterThanOrEqual(-90);
      expect(point.lat).toBeLessThanOrEqual(90);
    }
  });

  test("the same place gives a degenerate arc without NaNs", () => {
    for (const point of greatCircle(SAGUNT, SAGUNT, 4)) {
      expect(Number.isFinite(point.lon)).toBe(true);
      expect(Number.isFinite(point.lat)).toBe(true);
    }
  });
});

test("easing is clamped and goes from 0 to 1", () => {
  expect(ease(-1)).toBe(0);
  expect(ease(0)).toBe(0);
  expect(ease(0.5)).toBeCloseTo(0.5);
  expect(ease(1)).toBe(1);
  expect(ease(2)).toBe(1);
  expect(ease(0.2)).toBeLessThan(ease(0.7));
});

test("ring radii scale with the view mode", () => {
  const wide = ringRadii(viewFor(1200, 800, 0, 0));
  const horizon = ringRadii(viewFor(390, 230, 0, 0));
  expect(wide[0]).toBeGreaterThanOrEqual(26);
  expect(horizon[0]).toBeGreaterThanOrEqual(22);
  expect(wide[1]).toBeGreaterThan(wide[0]);
  expect(horizon[1]).toBeGreaterThan(horizon[0]);
});
