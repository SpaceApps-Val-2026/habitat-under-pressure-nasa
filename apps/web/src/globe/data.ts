// Data layer for the animated cover globe.
//
// Sources and attribution:
// - Coastline: Natural Earth 1:50 m physical coastline, public domain.
//   Copied verbatim from .dev/design-drafts/2026-11-09-pantallas-v1/costa_mundo.json.
// - Places: NASA POWER monthly point API (MERRA-2 reanalysis): annual mean 2 m air
//   temperature (T2M), precipitation (PRECTOTCORR, mm/day) and root-zone soil wetness
//   (GWETROOT, 0-1), 1981-2024; Mann-Kendall with Hamed-Rao correction, Sen slope and
//   Benjamini-Hochberg correction over 36 tests; downloaded 2026-11-09.
//   Copied verbatim from .dev/design-drafts/2026-11-09-pantallas-v1/puntos_globo.json.
//   The JSON keeps the original `serie` spelling; this module exposes it as `series`
//   because that is the component contract.
//
// Every number here is modelled (reanalysis), not measured at the point.

import coastlineSource from "./data/coastline.json";
import placesSource from "./data/places.json";
import type { LonLatLine } from "./projection";

export type GlobeTheme = "change" | "pressure" | "water";

export type GlobeSeries = {
  series: number[];
  sen_dec: number;
  mean: number;
  q: number;
  sig: boolean;
};

export type GlobePlace = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  theme: GlobeTheme;
  observe?: string;
  t2m: number[];
  base: number;
  sen_dec: number;
  p: number;
  q: number;
  sig: boolean;
  prec: GlobeSeries;
  soil: GlobeSeries;
};

export const ATTRIBUTION = {
  coastline: "Coastline: Natural Earth 1:50 m (public domain).",
  places:
    "Places: NASA POWER point API (MERRA-2 reanalysis), 1981-2024; " +
    "Mann-Kendall with Hamed-Rao correction, Sen slope, Benjamini-Hochberg over 36 tests; " +
    "downloaded 2026-11-09. Modelled, not measured on the ground.",
} as const;

type RawSeries = {
  serie: number[];
  sen_dec: number;
  mean: number;
  q: number;
  sig: boolean;
};

type RawPlace = Omit<GlobePlace, "prec" | "soil"> & {
  prec: RawSeries;
  soil: RawSeries;
};

function adaptSeries(raw: RawSeries): GlobeSeries {
  return { series: raw.serie, sen_dec: raw.sen_dec, mean: raw.mean, q: raw.q, sig: raw.sig };
}

export const PLACES: GlobePlace[] = (placesSource.points as RawPlace[]).map((place) => ({
  id: place.id,
  name: place.name,
  lat: place.lat,
  lon: place.lon,
  theme: place.theme,
  observe: place.observe,
  t2m: place.t2m,
  base: place.base,
  sen_dec: place.sen_dec,
  p: place.p,
  q: place.q,
  sig: place.sig,
  prec: adaptSeries(place.prec),
  soil: adaptSeries(place.soil),
}));

export const COASTLINE: LonLatLine[] = coastlineSource as LonLatLine[];
