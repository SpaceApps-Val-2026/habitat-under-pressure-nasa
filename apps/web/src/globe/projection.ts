// Pure geometry for the cover globe: orthographic projection, view framing and
// horizon-band rules. Ported from .dev/design-drafts/.../globo_puntos.js and the
// G3 / B drafts; no canvas here, so it can be tested without a browser.

export const DEG = Math.PI / 180;

/** Below this container width the cover switches from the whole globe to the horizon band. */
export const WIDE_MIN_WIDTH = 600;

/** Whole-globe radius as a fraction of the shortest side (G3, 2026-11-09 draft). */
export const WIDE_RADIUS = 0.44;

/** Horizon radius as a multiple of the band width (B, 2026-11-12 draft). */
export const HORIZON_RADIUS = 1.15;

/** Distance from the top of the band to the top edge of the globe (design system, portada-movil). */
export const HORIZON_TOP = 58;

/** The view is centred this many degrees south of the place, so the place sits in the cap. */
export const HORIZON_DROP = 60;

/** Places below this latitude are shown as if they were here (design system, portada-movil). */
export const HORIZON_SOUTH_FLOOR = -30;

// G3 keeps the focus between these latitudes so the globe never tips over a pole.
const WIDE_NORTH_LIMIT = 62;
const WIDE_SOUTH_LIMIT = -55;

export type GlobeMode = "wide" | "horizon";

export type View = {
  /** Longitude at the centre of the disc, degrees east. */
  lon: number;
  /** Latitude at the centre of the disc, degrees north. */
  lat: number;
  /** Radius of the sphere on screen, CSS pixels. */
  R: number;
  /** Centre of the disc, CSS pixels. */
  x: number;
  y: number;
  mode: GlobeMode;
};

export type GeoPoint = { lat: number; lon: number };

export type ScreenPoint = {
  x: number;
  y: number;
  /** cos of the angular distance to the centre; > 0 means the near side. */
  cos: number;
};

export type ArcPoint = GeoPoint & {
  /** Radial lift for drawing an arc above the surface; 1 is the surface. */
  lift: number;
};

export type LonLatLine = [number, number][];

/** Latitude at which a place is drawn in the horizon band. */
export function markerLatitude(lat: number, mode: GlobeMode): number {
  return mode === "horizon" ? Math.max(lat, HORIZON_SOUTH_FLOOR) : lat;
}

/**
 * Framing for a canvas of `width` × `height` CSS pixels centred on a place.
 * Wide: radius = min(W, H) * 0.44, centred, latitude clamped to G3's band.
 * Horizon: radius = 1.15 W with the top edge 58 px into the band, centred 60°
 * south of the place (places south of 30° S are shown as 30° S).
 */
export function viewFor(width: number, height: number, lon: number, lat: number): View {
  if (width >= WIDE_MIN_WIDTH) {
    return {
      lon,
      lat: Math.max(WIDE_SOUTH_LIMIT, Math.min(WIDE_NORTH_LIMIT, lat)),
      R: Math.min(width, height) * WIDE_RADIUS,
      x: width / 2,
      y: height / 2,
      mode: "wide",
    };
  }
  const R = width * HORIZON_RADIUS;
  return {
    lon,
    lat: Math.max(lat, HORIZON_SOUTH_FLOOR) - HORIZON_DROP,
    R,
    x: width / 2,
    y: HORIZON_TOP + R,
    mode: "horizon",
  };
}

/**
 * Orthographic projection of a geographic point onto the view.
 * `lift` scales the radius, used to float arcs above the surface.
 */
export function project(lon: number, lat: number, view: View, lift = 1): ScreenPoint {
  const l = (lon - view.lon) * DEG;
  const p = lat * DEG;
  const p0 = view.lat * DEG;
  const cp = Math.cos(p);
  const cos = Math.sin(p0) * Math.sin(p) + Math.cos(p0) * cp * Math.cos(l);
  return {
    x: view.x + view.R * lift * cp * Math.sin(l),
    y: view.y - view.R * lift * (Math.cos(p0) * Math.sin(p) - Math.sin(p0) * cp * Math.cos(l)),
    cos,
  };
}

/** Shortest signed longitude step from `from` to `to`, in (-180, 180]. */
export function longitudeDelta(from: number, to: number): number {
  return ((to - from + 540) % 360) - 180;
}

/** Unit vector for a place, used by the great-circle arcs. */
export function unitVector(point: GeoPoint): [number, number, number] {
  const lat = point.lat * DEG;
  const lon = point.lon * DEG;
  return [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
}

/**
 * Great-circle arc between two places, lifted towards the middle so it reads as
 * a flight path instead of a chord. Same spherical interpolation as the drafts.
 */
export function greatCircle(from: GeoPoint, to: GeoPoint, steps = 48): ArcPoint[] {
  const A = unitVector(from);
  const B = unitVector(to);
  const dot = Math.max(-1, Math.min(1, A[0] * B[0] + A[1] * B[1] + A[2] * B[2]));
  const omega = Math.acos(dot);
  const points: ArcPoint[] = [];
  if (omega < 1e-9) {
    // Same point: a straight lift, no spherical interpolation to divide by zero.
    for (let i = 0; i <= steps; i++) {
      points.push({ lon: from.lon, lat: from.lat, lift: 1 + 0.14 * Math.sin((Math.PI * i) / steps) });
    }
    return points;
  }
  const sin = Math.sin(omega) || 1;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const k1 = Math.sin((1 - t) * omega) / sin;
    const k2 = Math.sin(t * omega) / sin;
    const x = k1 * A[0] + k2 * B[0];
    const y = k1 * A[1] + k2 * B[1];
    const z = k1 * A[2] + k2 * B[2];
    const length = Math.hypot(x, y, z);
    points.push({
      lon: Math.atan2(y, x) / DEG,
      lat: Math.asin(z / length) / DEG,
      lift: 1 + 0.14 * Math.sin(Math.PI * t),
    });
  }
  return points;
}

/** Cubic ease-in-out used for every flight and ring reveal. */
export function ease(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return c < 0.5 ? 4 * c * c * c : 1 - Math.pow(-2 * c + 2, 3) / 2;
}

/** Inner and outer radius of the ring of annual stripes around the selected place. */
export function ringRadii(view: View): [number, number] {
  if (view.mode === "horizon") {
    const inner = Math.max(22, view.R * 0.052);
    return [inner, inner + Math.max(11, view.R * 0.029)];
  }
  const inner = Math.max(26, view.R * 0.085);
  return [inner, inner + Math.max(12, view.R * 0.045)];
}
