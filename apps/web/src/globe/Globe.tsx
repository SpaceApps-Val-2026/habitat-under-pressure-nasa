import { useEffect, useRef, useState } from "react";

import type { GlobePlace, GlobeTheme } from "./data";
import { COASTLINE, PLACES } from "./data";
import {
  WIDE_MIN_WIDTH,
  ease,
  greatCircle,
  longitudeDelta,
  markerLatitude,
  project,
  ringRadii,
  viewFor,
  type LonLatLine,
  type View,
} from "./projection";

export type { GlobePlace } from "./data";
export { PLACES } from "./data";

// Colours of the night globe. Taken from the design-system tokens (--orbit*,
// --theme-*) and the 2026-11-09 G3 draft; named here so the JSX stays free of
// literals. Only `change`, `pressure` and `water` differ per theme.
const PALETTE = {
  // --orbit: the stage the rest of the app uses, so the band has no tone seam with the AppBar.
  space: "#0C1B22",
  oceanNear: "#14323E",
  oceanFar: "#0A1C24",
  atmosphere: "rgba(95,199,188,0.30)",
  atmosphereEdge: "rgba(95,199,188,0)",
  rim: "rgba(95,199,188,0.40)",
  grid: "rgba(143,166,172,0.09)",
  coast: "rgba(190,226,222,0.55)",
  coastHalo: "rgba(95,199,188,0.14)",
  hollow: "rgba(12,27,34,0.85)",
  ink: "#0C1B22",
  heat: ["#2A6E99", "#8FB1C8", "#ECE5DA", "#E9A15A", "#B5432B"],
} as const;

type MarkerShape = "circle" | "square" | "triangle";

// Shape distinguishes the theme without relying on colour alone.
const THEMES: Record<GlobeTheme, { color: string; shape: MarkerShape }> = {
  change: { color: "#F2B84B", shape: "circle" },
  pressure: { color: "#F0715F", shape: "square" },
  water: { color: "#5FB0E8", shape: "triangle" },
};

const TAU = Math.PI * 2;

// Tour timing, in milliseconds. Same rhythm as the G3 draft.
const START_MS = 1400;
const FLY_MS = 1700;
const RING_MS = 1400;
const DWELL_MS = 2600;

// Every new stop forgets 15 % of the old trail; visited places dim to a floor.
const FADE = 0.15;
const FLOOR = 0.22;
const RECENT = 8;

// Annual anomalies are clamped to this many degrees for the ring of stripes.
const HEAT_SPAN = 2.5;

type Arc = { from: number; to: number; progress: number; alpha: number };

type Flight = {
  index: number;
  fromLon: number;
  fromLat: number;
  toLon: number;
  toLat: number;
  t: number;
  arc: Arc | null;
};

type Phase = "idle" | "fly" | "ring" | "dwell";

type TourState = {
  lon: number;
  lat: number;
  phase: Phase;
  phaseMs: number;
  flight: Flight | null;
  selected: number;
  ring: number;
  pulse: number;
  arcs: Arc[];
  glow: number[];
  recent: number[];
  prev: number;
};

function buildGrid(): LonLatLine[] {
  const lines: LonLatLine[] = [];
  for (let lon = -180; lon < 180; lon += 20) {
    const line: [number, number][] = [];
    for (let lat = -90; lat <= 90; lat += 3) line.push([lon, lat]);
    lines.push(line);
  }
  for (let lat = -60; lat <= 60; lat += 20) {
    const line: [number, number][] = [];
    for (let lon = -180; lon <= 180; lon += 3) line.push([lon, lat]);
    lines.push(line);
  }
  return lines;
}

const GRID = buildGrid();

function seedArcs(): Arc[] {
  // At rest, chain the places of the same theme, as the G3 draft does.
  const arcs: Arc[] = [];
  for (let i = 1; i < PLACES.length; i++) {
    if (PLACES[i].theme === PLACES[i - 1].theme) {
      arcs.push({ from: i - 1, to: i, progress: 1, alpha: 0.5 });
    }
  }
  return arcs;
}

function parseHex(hex: string): number[] {
  return [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16));
}

/** Heat scale of the annual stripes: blue (cold) to red (warm). */
function heatColor(anomaly: number): string {
  const t = Math.max(-1, Math.min(1, anomaly / HEAT_SPAN));
  const u = ((t + 1) / 2) * (PALETTE.heat.length - 1);
  const i = Math.min(PALETTE.heat.length - 2, Math.floor(u));
  const f = u - i;
  const a = parseHex(PALETTE.heat[i]);
  const b = parseHex(PALETTE.heat[i + 1]);
  const mixed = a.map((value, k) => Math.round(value + (b[k] - value) * f));
  return `rgb(${mixed[0]},${mixed[1]},${mixed[2]})`;
}

function strokeLines(
  ctx: CanvasRenderingContext2D,
  lines: LonLatLine[],
  view: View,
  style: string,
  width: number,
  threshold = 0,
) {
  ctx.strokeStyle = style;
  ctx.lineWidth = width;
  ctx.beginPath();
  for (const line of lines) {
    let pen = false;
    for (const [lon, lat] of line) {
      const point = project(lon, lat, view);
      if (point.cos <= threshold) {
        pen = false;
        continue;
      }
      if (pen) ctx.lineTo(point.x, point.y);
      else {
        ctx.moveTo(point.x, point.y);
        pen = true;
      }
    }
  }
  ctx.stroke();
}

function markerPath(ctx: CanvasRenderingContext2D, shape: MarkerShape, x: number, y: number, r: number) {
  ctx.beginPath();
  if (shape === "circle") {
    ctx.arc(x, y, r, 0, TAU);
  } else if (shape === "square") {
    ctx.rect(x - r * 0.88, y - r * 0.88, r * 1.76, r * 1.76);
  } else {
    ctx.moveTo(x, y - r * 1.1);
    ctx.lineTo(x + r * 1.05, y + r * 0.75);
    ctx.lineTo(x - r * 1.05, y + r * 0.75);
    ctx.closePath();
  }
}

function drawMarker(
  ctx: CanvasRenderingContext2D,
  place: GlobePlace,
  x: number,
  y: number,
  r: number,
) {
  const theme = THEMES[place.theme];
  markerPath(ctx, theme.shape, x, y, r);
  if (place.sig) {
    ctx.fillStyle = theme.color;
    ctx.fill();
    ctx.lineWidth = 1.4;
    ctx.strokeStyle = PALETTE.ink;
    ctx.stroke();
  } else {
    ctx.fillStyle = PALETTE.hollow;
    ctx.fill();
    ctx.lineWidth = 2.2;
    ctx.strokeStyle = theme.color;
    ctx.stroke();
  }
}

function drawScene(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  state: TourState,
) {
  const view = viewFor(width, height, state.lon, state.lat);
  const R = view.R;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = PALETTE.space;
  ctx.fillRect(0, 0, width, height);

  // Atmosphere glow outside the disc.
  const atmosphere = ctx.createRadialGradient(view.x, view.y, R * 0.97, view.x, view.y, R * 1.2);
  atmosphere.addColorStop(0, PALETTE.atmosphere);
  atmosphere.addColorStop(1, PALETTE.atmosphereEdge);
  ctx.fillStyle = atmosphere;
  ctx.beginPath();
  ctx.arc(view.x, view.y, R * 1.2, 0, TAU);
  ctx.fill();

  // Night side of the sphere.
  const ocean = ctx.createRadialGradient(
    view.x - R * 0.35,
    view.y - R * 0.4,
    R * 0.05,
    view.x,
    view.y,
    R,
  );
  ocean.addColorStop(0, PALETTE.oceanNear);
  ocean.addColorStop(1, PALETTE.oceanFar);
  ctx.fillStyle = ocean;
  ctx.beginPath();
  ctx.arc(view.x, view.y, R, 0, TAU);
  ctx.fill();

  ctx.strokeStyle = PALETTE.rim;
  ctx.lineWidth = 1.2;
  ctx.stroke();

  strokeLines(ctx, GRID, view, PALETTE.grid, 1);

  // Coastline: a cheap halo pass instead of shadowBlur, which is costly on phones.
  ctx.lineJoin = "round";
  strokeLines(ctx, COASTLINE, view, PALETTE.coastHalo, 3.2);
  strokeLines(ctx, COASTLINE, view, PALETTE.coast, 1);

  // Great-circle trails between consecutive stops.
  for (const arc of state.arcs) {
    if (arc.alpha <= 0) continue;
    const from = PLACES[arc.from];
    const to = PLACES[arc.to];
    const path = greatCircle(
      { lon: from.lon, lat: markerLatitude(from.lat, view.mode) },
      { lon: to.lon, lat: markerLatitude(to.lat, view.mode) },
    );
    const shown = Math.max(1, Math.round(path.length * arc.progress));
    ctx.strokeStyle = THEMES[to.theme].color;
    ctx.globalAlpha = 0.85 * arc.alpha;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    let pen = false;
    for (let i = 0; i < shown; i++) {
      const point = path[i];
      const screen = project(point.lon, point.lat, view, point.lift);
      if (screen.cos < -0.08) {
        pen = false;
        continue;
      }
      if (pen) ctx.lineTo(screen.x, screen.y);
      else {
        ctx.moveTo(screen.x, screen.y);
        pen = true;
      }
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  const radius = Math.max(3.2, Math.min(6.4, R * 0.02));

  PLACES.forEach((place, index) => {
    const point = project(place.lon, markerLatitude(place.lat, view.mode), view);
    if (point.cos <= 0) return;
    const alpha = index === state.selected ? 1 : state.glow[index];
    if (alpha <= 0.02) return;
    ctx.globalAlpha = alpha;
    const haloRadius = radius * 3.4;
    const halo = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, haloRadius);
    const color = THEMES[place.theme].color;
    halo.addColorStop(0, `${color}AA`);
    halo.addColorStop(1, `${color}00`);
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(point.x, point.y, haloRadius, 0, TAU);
    ctx.fill();
    drawMarker(ctx, place, point.x, point.y, radius);
    ctx.globalAlpha = 1;
  });

  // Selected place: its 44 years as a ring of stripes. No numeral: the parent
  // owns every visible label.
  if (state.selected >= 0) {
    const place = PLACES[state.selected];
    const point = project(place.lon, markerLatitude(place.lat, view.mode), view);
    if (point.cos > 0) {
      const [r0, r1] = ringRadii(view);
      const n = place.t2m.length;
      const shown = Math.round(n * state.ring);
      const step = TAU / n;
      for (let k = 0; k < shown; k++) {
        const a0 = -Math.PI / 2 + k * step;
        const a1 = a0 + step * 0.92;
        ctx.fillStyle = heatColor(place.t2m[k] - place.base);
        ctx.beginPath();
        ctx.arc(point.x, point.y, r1, a0, a1);
        ctx.arc(point.x, point.y, r0, a1, a0, true);
        ctx.closePath();
        ctx.fill();
      }
      ctx.strokeStyle = THEMES[place.theme].color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(point.x, point.y, r1 + 3, 0, TAU);
      ctx.stroke();
      if (state.pulse > 0) {
        ctx.globalAlpha = state.pulse;
        ctx.beginPath();
        ctx.arc(point.x, point.y, r1 + 3 + (1 - state.pulse) * 26, 0, TAU);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }
  }
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export type GlobeProps = {
  /** aria-label of the canvas; visible copy belongs to the screen. */
  label: string;
  className?: string;
  /** Stops the tour (tests, printing). */
  paused?: boolean;
  /** Place shown at rest; defaults to Parc Sagunt II. */
  initialId?: string;
  /** Called once per settled place. */
  onPlaceChange?: (place: GlobePlace) => void;
};

export function Globe({
  label,
  className,
  paused = false,
  initialId = "sagunt",
  onPlaceChange,
}: GlobeProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const callbackRef = useRef(onPlaceChange);
  const [reduced, setReduced] = useState(prefersReducedMotion);

  useEffect(() => {
    callbackRef.current = onPlaceChange;
  }, [onPlaceChange]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    const initial = PLACES.find((place) => place.id === initialId) ?? PLACES[0];
    const initialIndex = PLACES.indexOf(initial);
    const emit = (index: number) => callbackRef.current?.(PLACES[index]);

    // Without a 2D context (no canvas support, or jsdom) the page still gets
    // the resting place, so the text around it is never empty.
    if (!context) {
      emit(initialIndex);
      return;
    }

    // Aliases with the narrowed types: TypeScript does not carry the guards
    // into the hoisted function declarations below.
    const cv: HTMLCanvasElement = canvas;
    const ctx: CanvasRenderingContext2D = context;

    const state: TourState = {
      lon: initial.lon,
      lat: initial.lat,
      phase: "idle",
      phaseMs: 0,
      flight: null,
      selected: initialIndex,
      ring: 1,
      pulse: 0,
      arcs: [],
      glow: PLACES.map(() => 1),
      recent: [initialIndex],
      prev: initialIndex,
    };

    let width = 0;
    let height = 0;
    let wide = false;
    let running = false;
    let raf = 0;
    let last = 0;

    function resize(): boolean {
      const rect = cv.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      if (!w || !h) return false;
      width = w;
      height = h;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const pixelWidth = Math.round(w * dpr);
      const pixelHeight = Math.round(h * dpr);
      if (cv.width !== pixelWidth || cv.height !== pixelHeight) {
        cv.width = pixelWidth;
        cv.height = pixelHeight;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return true;
    }

    function draw() {
      if (!width || !height) return;
      drawScene(ctx, width, height, state);
    }

    function nextIndex(): number {
      const pool = PLACES.map((_, index) => index).filter(
        (index) => !state.recent.includes(index),
      );
      const picked = pool[Math.floor(Math.random() * pool.length)];
      state.recent.push(picked);
      if (state.recent.length > RECENT) state.recent.shift();
      return picked;
    }

    function settle(index: number) {
      state.selected = index;
      state.ring = 0;
      state.pulse = 1;
      state.glow[index] = 1;
      state.prev = index;
      state.phase = "ring";
      state.phaseMs = 0;
      state.flight = null;
      emit(index);
    }

    function visit(index: number) {
      state.arcs.forEach((arc) => {
        arc.alpha -= FADE;
      });
      state.arcs = state.arcs.filter((arc) => arc.alpha > 0);
      state.glow = state.glow.map((glow) => Math.max(FLOOR, glow - FADE));
      const target = PLACES[index];
      const arc: Arc | null =
        state.prev >= 0 ? { from: state.prev, to: index, progress: 0, alpha: 1 } : null;
      if (arc) state.arcs.push(arc);
      state.selected = -1;
      state.phase = "fly";
      state.phaseMs = 0;
      state.flight = {
        index,
        fromLon: state.lon,
        fromLat: state.lat,
        toLon: target.lon,
        toLat: target.lat,
        t: 0,
        arc,
      };
    }

    function step(dt: number) {
      if (state.phase === "idle") {
        state.phaseMs += dt;
        if (state.phaseMs >= START_MS) visit(nextIndex());
        return;
      }
      if (state.phase === "fly" && state.flight) {
        const flight = state.flight;
        flight.t = Math.min(1, flight.t + dt / FLY_MS);
        const t = ease(flight.t);
        state.lon = flight.fromLon + longitudeDelta(flight.fromLon, flight.toLon) * t;
        state.lat = flight.fromLat + (flight.toLat - flight.fromLat) * t;
        if (flight.arc) flight.arc.progress = t;
        if (flight.t >= 1) settle(flight.index);
        return;
      }
      if (state.phase === "ring") {
        state.phaseMs += dt;
        const t = Math.min(1, state.phaseMs / RING_MS);
        state.ring = t;
        state.pulse = 1 - t;
        if (t >= 1) {
          state.phase = "dwell";
          state.phaseMs = 0;
        }
        return;
      }
      if (state.phase === "dwell") {
        state.phaseMs += dt;
        if (state.phaseMs >= DWELL_MS) visit(nextIndex());
      }
    }

    function frame(now: number) {
      if (!running) return;
      const dt = last ? Math.min(64, now - last) : 16;
      last = now;
      step(dt);
      draw();
      if (running) raf = requestAnimationFrame(frame);
    }

    function schedule() {
      if (running || typeof requestAnimationFrame !== "function") return;
      running = true;
      last = 0;
      raf = requestAnimationFrame(frame);
    }

    function stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      last = 0;
    }

    function onResize() {
      if (!resize()) return;
      const nextWide = width >= WIDE_MIN_WIDTH;
      if (nextWide !== wide) {
        wide = nextWide;
        state.arcs = wide ? seedArcs() : [];
      }
      draw();
      if (!reduced && !paused) schedule();
    }

    function onVisibility() {
      if (document.hidden) stop();
      else if (!reduced && !paused) schedule();
    }

    const hasSize = resize();
    // The same-theme trail belongs to the whole-globe view; in the horizon band
    // it would cross the cap as unrelated spaghetti. Recompute it when the
    // container crosses the breakpoint, not only at mount.
    wide = width >= WIDE_MIN_WIDTH;
    state.arcs = wide ? seedArcs() : [];
    draw();
    emit(initialIndex);

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(onResize);
      observer.observe(cv);
    } else {
      window.addEventListener("resize", onResize);
    }
    document.addEventListener("visibilitychange", onVisibility);

    if (!reduced && !paused && hasSize && !document.hidden) schedule();

    return () => {
      stop();
      observer?.disconnect();
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [initialId, paused, reduced]);

  return <canvas ref={canvasRef} role="img" aria-label={label} className={className} />;
}
