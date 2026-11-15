"""People living in hot cells: Landsat surface temperature pixels at or above a threshold, inside the zone.

The threshold is a parameter with no default: what counts as a hot cell is a decision of method, and the
ratified method is the 95th percentile of summer surface temperature of all the cells of the zone together,
base 2013-2020, fixed for every later year (A10 chains). Without a threshold the variable is an absence,
never a guess. It comes in one of two forms: `threshold_c` (°C), or `percentile` and `base_period`, as
AEMET (95th percentile of July-August daily maxima, 1971-2000) and ETCCDI TX90p do, moved from air
temperature to surface temperature and from days to cells
(.dev/notes/2026-11-14-umbral-celda-caliente-referente-oficial.md). See `base_threshold` for the exact sample.

With a threshold, each year of the per-pixel summer composite drawn by layers.zone_layers (same scenes, mask and
scale as the zone figures) is cut at it, and the people inside the hot pixels are counted as
masks.population_in_mask counts them: INE by census section where it covers the zone, GHSL otherwise. Population is
one year (the latest available) for every row.
"""
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.features import shapes
from shapely.geometry import shape

from hup_data import cancellation
from hup_data.readers import absent, landsat, register

UNIT = "people"
NEEDS = ("population_in_hot_cells needs a hot-cell threshold given with the request: threshold_c (surface "
         "temperature, °C), or percentile and base_period; the ratified method is the 95th percentile of "
         "summer surface temperature of all the cells of the zone, base 2013-2020, fixed for every year, "
         "and a default would still be invented.",
         "population_in_hot_cells necesita un umbral de celda caliente en la petición: threshold_c "
         "(temperatura de superficie, °C), o percentile y base_period; el método ratificado es el percentil "
         "95 de la temperatura de superficie del verano de todas las celdas de la zona, base 2013-2020, "
         "fijo para todos los años, y un valor por defecto seguiría siendo inventado.")
BOTH = ("Give either threshold_c or percentile with base_period, not both and not only part of them.",
        "Indica threshold_c o bien percentile con base_period, no ambos ni solo una parte.")
NOTE = ".dev/notes/2026-11-14-umbral-celda-caliente-referente-oficial.md"


@register("1", "population_in_hot_cells")
def read(variable: str, zone: dict, period: dict, *, percentile: float | None = None,
         base_period: dict | None = None) -> dict:
    """The door of read_series: with `percentile` and `base_period` (the catalogue's `threshold`, copied to the step),
    the result of `read_with_threshold`; without them, the absence that says what is missing."""
    if percentile is None and base_period is None:
        return absent("not_computed", *NEEDS, landsat._source(), origin="package")
    return read_with_threshold(zone, period, percentile=percentile, base_period=base_period)


def hot_geometry(values: np.ndarray, transform, nodata, threshold_c: float, outline):
    """Pixels at or above `threshold_c` as polygons (grid CRS, here EPSG:4326), cut to `outline`."""
    hot = np.isfinite(values) & (values != nodata) & (values >= threshold_c)
    if not hot.any():
        return shapely.Polygon()
    pieces = [shape(geometry) for geometry, _ in shapes(hot.astype("uint8"), mask=hot, transform=transform)]
    return shapely.make_valid(shapely.union_all(pieces)).intersection(outline)


def _frames(layer: dict):
    """(t, values, transform, nodata) of every frame of a zone_layers result that has a file."""
    from hup_data import store

    for frame in layer["frames"]:
        cancellation.checkpoint()
        if frame.get("absence") or not frame.get("file"):
            continue
        with rasterio.open(store.derived_path(layer["fingerprint"], frame["file"])) as cog:
            yield frame, cog.read(1), cog.transform, cog.nodata


def base_threshold(base_layer: dict, outline, percentile: float) -> dict:
    """The fixed threshold: `percentile` of the per-pixel summer medians of the base period, inside the zone.

    Sample: every pixel whose centre lies inside the zone, once per year of the base period, its value being the
    median of that summer's clear scenes (the frames of layers.zone_layers), all years pooled into one distribution.
    That is the statistic the threshold is later compared with, year by year; a percentile of single-date pixels has
    a wider spread and, applied to medians, would mark systematically fewer cells. AEMET and TX90p pool daily values
    of the base years; here the summer composite takes the place of the day and the pixel that of the station's
    record, one threshold for the whole zone. Linear interpolation between order statistics (numpy default).
    """
    pooled, years = [], []
    for frame, values, transform, nodata in _frames(base_layer):
        rows, cols = np.indices(values.shape)
        xs = transform.c + (cols + 0.5) * transform.a + (rows + 0.5) * transform.b
        ys = transform.f + (cols + 0.5) * transform.d + (rows + 0.5) * transform.e
        inside = shapely.contains_xy(outline, xs, ys)
        good = inside & np.isfinite(values) & (values != nodata)
        if good.any():
            pooled.append(values[good].astype("float64"))
            years.append(frame["t"])
    if not pooled:
        return {"value_c": None, "n_values": 0, "years": []}
    sample = np.concatenate(pooled)
    return {"value_c": float(np.percentile(sample, percentile)), "n_values": int(sample.size), "years": years}


def read_with_threshold(zone: dict, period: dict, threshold_c: float | None = None, *, percentile: float | None = None,
                        base_period: dict | None = None, layer: dict | None = None,
                        base_layer: dict | None = None) -> dict:
    """One row per year: t, v (people in hot pixels), hot_hectares, hot_pct (share of the zone), population_year.

    The threshold is `threshold_c`, or the `percentile` of the base period (`base_period`, a period like `period`;
    its months default to those of `period`) computed once by `base_threshold`; the result says which in
    "threshold". `layer` and `base_layer` are results of layers.zone_layers("lst", ...); drawn here when not given.
    """
    from hup_data import layers, masks
    from hup_data.readers.population import zone_geometry

    fixed = percentile is not None or base_period is not None
    if threshold_c is None and not fixed:
        return absent("not_computed", *NEEDS, landsat._source(), origin="package")
    if (threshold_c is not None and fixed) or (fixed and (percentile is None or base_period is None)):
        return absent("not_computed", *BOTH, landsat._source(), origin="package")
    if fixed and not 0 < percentile < 100:
        return absent("not_computed", "The percentile must lie between 0 and 100.",
                      "El percentil tiene que estar entre 0 y 100.", landsat._source(), origin="package")
    outline = zone_geometry(zone)
    threshold = {"value_c": threshold_c, "kind": "fixed_c"}
    if fixed:
        base = {**base_period, **({"months": period["months"]} if period.get("months") and
                                  "months" not in base_period else {})}
        base_layer = base_layer or layers.zone_layers("lst", zone, base)
        if base_layer.get("absence"):
            detail = base_layer["absence"]["detail"]
            return {**absent(base_layer["absence"]["reason"], detail["en"], detail["es"], landsat._source(),
                             "package"), "sources": base_layer.get("sources") or [landsat._source()]}
        computed = base_threshold(base_layer, outline, percentile)
        if computed["value_c"] is None:
            return absent("insufficient_qa", "No pixel of the zone has surface temperature in the base period.",
                          "Ningún píxel de la zona tiene temperatura de superficie en el periodo base.",
                          landsat._source(), "package")
        threshold_c = computed["value_c"]
        threshold = {"value_c": threshold_c, "kind": "base_percentile", "percentile": percentile,
                     "base_period": base, "base_years": computed["years"], "n_values": computed["n_values"]}
    layer = layer or layers.zone_layers("lst", zone, period)
    if layer.get("absence"):
        detail = layer["absence"]["detail"]
        return {**absent(layer["absence"]["reason"], detail["en"], detail["es"], landsat._source(), "package"),
                "sources": layer.get("sources") or [landsat._source()]}
    rows, counted, limits = [], None, []
    for frame, values, transform, nodata in _frames(layer):
        hot = hot_geometry(values, transform, nodata, threshold_c, outline)
        found = {"geometry": hot, "n_features": 0 if hot.is_empty else 1, "absence": None,
                 "sources": list(layer["sources"]), "coverage": {}, "limits": []}
        counted = masks.count_people(found, zone)
        if counted["absence"] is not None:
            detail = counted["absence"]["detail"]
            return {**absent(counted["absence"]["reason"], detail["en"], detail["es"], counted["sources"][-1],
                             "package"), "sources": counted["sources"]}
        metric = masks._local(outline)
        hot_ha = float(masks._project(hot, metric).area) / 1e4 if not hot.is_empty else 0.0
        zone_ha = float(masks._project(outline, metric).area) / 1e4
        rows.append({"t": f"{frame['t']}-01-01" if len(str(frame["t"])) == 4 else frame["t"],
                     "v": counted["population"], "hot_hectares": hot_ha, "hot_pct": 100 * hot_ha / zone_ha,
                     "population_year": counted["year"], "lst_valid_pct": frame["coverage"]["valid_pct"]})
        limits = counted["limits"]
    if not rows:
        return absent("insufficient_qa", "No year of surface temperature could be read over this zone.",
                      "No se ha podido leer ningún año de temperatura de superficie sobre esta zona.",
                      landsat._source(), "package")
    resolution = max(layer["support"].get("resolution_m", landsat.RESOLUTION["lst"]),
                     counted["support"].get("resolution_m") or 0)
    limits = [{"en": f"A hot cell is a Landsat surface temperature pixel at or above {threshold_c:.2f} °C in the "
                     "summer composite of each year; the threshold "
                     + ("is fixed on a base period (below)" if fixed else "was given with the request")
                     + ", it is not a measurement.",
               "es": f"Una celda caliente es un píxel de temperatura de superficie Landsat de {threshold_c:.2f} °C o "
                     "más en el compuesto de verano de cada año; el umbral "
                     + ("se fija sobre un periodo base (abajo)" if fixed else "llega con la petición")
                     + ", no es una medida."},
              {"en": "Surface temperature is the temperature of roofs and ground, not of the air people breathe.",
               "es": "La temperatura de superficie es la de tejados y suelo, no la del aire que se respira."},
              {"en": f"The same population ({counted['year']}) is used for every year: only the hot cells change.",
               "es": f"Se usa la misma población ({counted['year']}) en todos los años: solo cambian las celdas "
                     "calientes."}, *limits]
    if threshold["kind"] == "base_percentile":
        start, end = threshold["base_period"]["start"][:4], threshold["base_period"]["end"][:4]
        limits.insert(1, {
            "en": f"Threshold fixed once: percentile {percentile:g} of the per-pixel summer medians inside the zone in "
                  f"{start}-{end} ({threshold['n_values']} pixel-years), applied to every year. Method of AEMET and "
                  "ETCCDI TX90p moved from air temperature at a station to surface temperature in cells, ratified on "
                  f"14 November 2026 (see {NOTE}); the standard base is 30 years, here Landsat starts in 2013.",
            "es": f"Umbral fijado una vez: percentil {percentile:g} de las medianas de verano por píxel dentro de la "
                  f"zona en {start}-{end} ({threshold['n_values']} píxeles-año), aplicado a todos los años. Método de "
                  "AEMET y del TX90p de ETCCDI llevado de temperatura del aire en una estación a temperatura de "
                  f"superficie en celdas, ratificado el 14 de noviembre de 2026 (ver {NOTE}); la base estándar es de "
                  "30 años, aquí Landsat empieza en 2013."})
    return {"threshold": threshold, "data": pd.DataFrame(rows), "absence": None, "origin": "package",
            "sources": counted["sources"],
            "support": {"resolution_m": resolution, "since": landsat.FIRST_YEAR}, "unit": UNIT,
            "coverage": {"valid_pct": min(row["lst_valid_pct"] for row in rows), "n_dates": len(rows)},
            "limits": limits, "input_fingerprint": None}
