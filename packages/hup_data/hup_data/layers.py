"""Map layers (A11): the clip of a gridded variable over a zone, one Cloud-Optimized GeoTIFF per year.

`write_cog` writes any array as a COG the browser reads by ranges: 512-pixel blocks, overviews with `average`
for values and `nearest` for classes, DEFLATE (never ZSTD or LERC: the browser would need extra decoders), the
nodata value and the projection declared. `zone_layers` clips a gridded source to the cells that touch a zone,
one frame per year of the period, all sharing one value range, and keeps them in the derived tier keyed by
zone, variable, period, scenario and the version of what was read. `layer_result` turns one frame into the body
of a `type: layer` result (docs/contratos/result.schema.json).

Global grids are never served whole: only the cells that touch the zone. Cells outside the zone outline are
nodata, and nodata is never a colour of the scale. No coverage gives an absence with its reason, never an
empty image.
"""
import calendar
import re
from pathlib import Path

import h5py
import numpy as np
import rasterio
import xarray as xr
import yaml
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from hup_data import cancellation, coverage, fingerprint, paths, store

VERSION = "1"  # bump when the files or the metadata change shape, so that old layers are not reused
NODATA = -9999.0
BLOCK = 512
KINDS = {"values": "AVERAGE", "classes": "NEAREST"}
# Landsat pixels (A11b): per-pixel composites around the zone, per year or per window of the plan.
PIXEL_VARIABLES = ("ndvi", "lst")
PIXEL_VERSION = "2"
MARGIN_KM = 2.0  # surroundings drawn around the zone when the plan names no reference distance
MAX_SCENES = 24  # clear dates per frame at most, spread over its years
RANGE_PERCENTILES = (2, 98)


# ---------------------------------------------------------------- writing and checking COGs
def write_cog(array: np.ndarray, path, *, nodata: float, kind: str, transform, crs: str = "EPSG:4326") -> None:
    """Write a 2-D array (rows north to south) or a 3-D one (bands first) as a COG, atomically.

    `kind` is "values" (overviews averaged) or "classes" (overviews take the nearest class). Pixels equal to
    `nodata` are declared as no data; a valid pixel must never hold that value.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    bands = array[np.newaxis] if array.ndim == 2 else array
    profile = {"driver": "COG", "count": bands.shape[0], "height": bands.shape[1], "width": bands.shape[2],
               "dtype": bands.dtype.name, "crs": crs, "transform": transform, "nodata": nodata,
               "BLOCKSIZE": BLOCK, "COMPRESS": "DEFLATE", "OVERVIEW_RESAMPLING": KINDS[kind],
               "RESAMPLING": KINDS[kind]}
    with MemoryFile() as memory:
        with memory.open(**profile) as target:
            target.write(bands)
        data = memory.read()
    store.write_atomic(Path(path), data)


def check_cog(path) -> list[str]:
    """What keeps a file from being a COG the browser can read by ranges. Empty when it is one.

    The same checks as rio-cogeo's validation that matter here: COG layout, tiled in 512-pixel blocks, DEFLATE,
    declared nodata and projection, and overviews whenever the image is larger than one block.
    """
    problems = []
    with rasterio.open(path) as source:
        structure = source.tags(ns="IMAGE_STRUCTURE")
        if structure.get("LAYOUT") != "COG":
            problems.append("layout is not COG")
        if not source.profile.get("tiled") or set(source.block_shapes) != {(BLOCK, BLOCK)}:
            problems.append(f"not tiled in {BLOCK}-pixel blocks")
        if structure.get("COMPRESSION") != "DEFLATE":
            problems.append("compression is not DEFLATE")
        if source.nodata is None:
            problems.append("no nodata value")
        if source.crs is None:
            problems.append("no projection")
        if max(source.width, source.height) > BLOCK and not source.overviews(1):
            problems.append("no overviews")
    return problems


# ---------------------------------------------------------------- the catalog entry of a variable
def _catalog(variable: str) -> dict:
    file = paths.CATALOG_DIR / "variables.yaml"
    loaded = yaml.safe_load(file.read_text(encoding="utf-8")) if file.is_file() else []
    entries = loaded.get("variables", []) if isinstance(loaded, dict) else loaded or []
    return next((entry for entry in entries if entry.get("id") == variable), {})


# ---------------------------------------------------------------- gridded sources
def _cells(axis: np.ndarray, low: float, high: float, half: float) -> np.ndarray:
    """Indices of the cells that touch [low, high]; a zone inside one cell gets that cell."""
    return np.flatnonzero((axis + half > low) & (axis - half < high))


def _imerg(variable: str, zone: dict, period: dict, scenario: str | None) -> dict:
    """IMERG monthly: for each year, the mean monthly total (mm/month) of the months of the period."""
    from hup_data.readers import imerg

    source = imerg._source()
    rows = sorted((row for row in coverage.query(imerg.VARIABLE, zone["bbox"], period)
                   if row["source"] == imerg.SOURCE), key=lambda row: row["period"]["start"])
    if not rows:
        return {"absence": ("needs_package", "Monthly precipitation (IMERG) is not downloaded for this period.",
                            "La precipitación mensual (IMERG) no está descargada para este periodo."),
                "sources": [source]}
    west, south, east, north = zone["bbox"]
    months = period.get("months") or list(range(1, 13))
    by_year: dict[int, list[np.ndarray]] = {}
    lons = lats = None
    for row in rows:
        start = row["period"]["start"]
        year, month = int(start[:4]), int(start[5:7])
        if month not in months or not (period["start"] <= start <= period["end"]):
            continue
        with h5py.File(paths.data_dir() / row["path"], "r") as file:
            grid = file["Grid"]
            if lons is None:
                lon_axis, lat_axis = grid["lon"][:].astype("float64"), grid["lat"][:].astype("float64")
                lon_i = _cells(lon_axis, west, east, imerg.CELL / 2)
                lat_i = _cells(lat_axis, south, north, imerg.CELL / 2)
                lons, lats = lon_axis[lon_i], lat_axis[lat_i]
                lon_s, lat_s = slice(lon_i[0], lon_i[-1] + 1), slice(lat_i[0], lat_i[-1] + 1)
            block = grid["precipitation"][0, lon_s, lat_s].astype("float64").T  # (lon, lat) on disk → (lat, lon)
            fill = float(np.ravel(grid["precipitation"].attrs["_FillValue"])[0])
        block[block == fill] = np.nan
        by_year.setdefault(year, []).append(block * 24 * calendar.monthrange(year, month)[1])
    first, last = int(period["start"][:4]), int(period["end"][:4])
    frames = {}
    for year in range(first, last + 1):
        cancellation.checkpoint()
        expected = [m for m in months if f"{year}-{m:02d}-01" >= period["start"][:8] + "01"
                    and f"{year}-{m:02d}-01" <= period["end"]]
        found = by_year.get(year, [])
        if not expected:
            continue
        if len(found) < len(expected):
            frames[year] = {"absence": ("insufficient_qa",
                                        f"{year} has {len(found)} of its {len(expected)} months downloaded.",
                                        f"{year} tiene {len(found)} de sus {len(expected)} meses descargados.")}
            continue
        with np.errstate(invalid="ignore"):
            frames[year] = {"grid": np.nanmean(np.stack(found), axis=0) if found else None, "n_dates": len(found)}
    files = [row["path"] for row in rows]
    return {"lat": lats, "lon": lons, "res": imerg.CELL, "frames": frames, "unit": "mm/month",
            "sources": [{**source, "n_items": sum(f.get("n_dates", 0) for f in frames.values())}],
            "support": {"resolution_m": 11100, "since": 1998},
            "version": {"source": source["version"], "files": files},
            "method": {"en": "Mean of the monthly totals of the months of the period, in each year.",
                       "es": "Media de los totales mensuales de los meses del periodo, en cada año."}}


def _climate(variable: str, zone: dict, period: dict, scenario: str | None) -> dict:
    """Climate cube: for each year, the median of the models of one scenario (historical where it has no year)."""
    from hup_data.prepare import climate
    from hup_data.readers import climate as reader

    meta = climate.read_meta()
    source = reader._source(meta)
    if meta is None:
        return {"absence": ("needs_package", "The climate cube has not been built on this computer.",
                            "El cubo del clima no está construido en este ordenador."), "sources": [source]}
    if variable not in meta["variables"]:
        return {"absence": ("not_computed", f"The climate cube has no indicator named {variable}.",
                            f"El cubo del clima no tiene un indicador llamado {variable}."), "sources": [source]}
    west, south, east, north = zone["bbox"]
    half = meta["resolution_deg"] / 2
    frames = {}
    with xr.open_zarr(climate.cube_path(), consolidated=False, chunks=None) as cube:
        lat_i = _cells(cube.lat.values, south, north, half)
        lon_i = _cells(cube.lon.values, west, east, half)
        if lat_i.size == 0 or lon_i.size == 0:
            return {"absence": ("no_coverage", "This source does not cover this zone.",
                                "Esta fuente no cubre esta zona."), "sources": [source]}
        block = cube[variable].isel(lat=lat_i, lon=lon_i)
        block = block.sel(year=slice(int(period["start"][:4]), int(period["end"][:4]))).load()
        lats, lons = cube.lat.values[lat_i], cube.lon.values[lon_i]
    scenarios = list(block.scenario.values)
    for year in block.year.values.tolist():
        cancellation.checkpoint()
        tried = ([scenario] if scenario in scenarios else []) + (["historical"] if "historical" in scenarios else [])
        for name in tried:
            values = block.sel(year=year, scenario=name)
            n_models = int(values.notnull().any(dim=["lat", "lon"]).sum())
            if n_models:
                frames[int(year)] = {"grid": values.median(dim="model", skipna=True).values.astype("float64"),
                                     "n_dates": n_models, "scenario": name}
                break
        else:
            frames[int(year)] = {"absence": ("needs_package",
                                             f"The climate files on this computer have no {year} for this scenario.",
                                             f"Los archivos de clima de este ordenador no tienen {year} para este "
                                             "escenario.")}
    unit = reader.UNITS.get(meta["variables"][variable], meta["variables"][variable])
    return {"lat": lats, "lon": lons, "res": meta["resolution_deg"], "frames": frames, "unit": unit,
            "sources": [source], "support": {"resolution_m": round(meta["resolution_deg"] * reader.METRES_PER_DEGREE)},
            "version": {"cube": meta["input_fingerprint"]},
            "method": {"en": "Median of the climate models in each year; one run per model.",
                       "es": "Mediana de los modelos climáticos en cada año; una simulación por modelo."}}


def _providers() -> dict:
    """Variable → gridded source that can draw it. Grows with every gridded reader, never with a place."""
    from hup_data.prepare import climate

    meta = climate.read_meta()
    found = {"precipitation": _imerg}
    for name in (meta or {}).get("variables", {}):
        found[name] = _climate
    return found


def variables() -> list[str]:
    """Variables this computer can draw as a map layer. Landsat ones are drawn from the network or the store."""
    return sorted({*_providers(), *PIXEL_VARIABLES})


# ---------------------------------------------------------------- one series of frames for a zone
def _text(reason: str, en: str, es: str) -> dict:
    return {"reason": reason, "detail": {"en": en, "es": es}}


def _absent(variable: str, absence: tuple, sources: list[dict]) -> dict:
    entry = _catalog(variable)
    return {"variable": variable, "absence": _text(*absence), "sources": sources, "frames": [],
            "unit": entry.get("unit"), "data_class": entry.get("data_class", "measured"), "origin": "cache"}


def _frames_wanted(period: dict, windows: dict | None) -> list[dict]:
    """One frame per window of the plan (in its order), or one per year of the period. Clipped to the period."""
    first, last = int(period["start"][:4]), int(period["end"][:4])
    spans = ([(name, int(years[0]), int(years[1])) for name, years in windows.items()] if windows
             else [(None, year, year) for year in range(first, last + 1)])
    wanted = []
    for name, low, high in spans:
        frame = {"t": str(low) if low == high and name is None else f"{low}-{high}", "years": [low, high],
                 "period": {"start": max(period["start"], f"{low}-01-01"), "end": min(period["end"], f"{high}-12-31"),
                            **({"months": period["months"]} if period.get("months") else {})}}
        if name is not None:
            frame["window"] = name
        wanted.append(frame)
    return wanted


def _spread(table, limit: int) -> list[str]:
    """At most `limit` scene ids, evenly spread in time, so that no season or year weighs more than it should."""
    ids = table.sort_values(["t", "item"])["item"].tolist()
    if len(ids) <= limit:
        return ids
    return [ids[round(i * (len(ids) - 1) / (limit - 1))] for i in range(limit)]


def _pixel_layers(variable: str, zone: dict, period: dict, windows: dict | None, margin_km: float | None,
                  land_only: bool = False) -> dict:
    """Landsat NDVI or surface temperature as per-pixel composites over the zone and its surroundings.

    Each frame (a year, or a window such as before/after) is the per-pixel median of up to MAX_SCENES of the clear
    dates the reader already chose for the zone, with the reader's own mask and scale. Both variables come from
    the same pass: the other one is saved too, so asking for it next costs nothing. With `land_only`, permanent
    water is left out with the reader's own land mask, and the layer says whether a JRC tile was there to do it.
    """
    from hup_data import settings
    from hup_data.readers import landsat

    margin = MARGIN_KM if margin_km is None else float(margin_km)
    tile = landsat.water_tile(zone) if land_only else None
    wanted = _frames_wanted(period, windows)
    chosen, available = {}, {}
    for frame in wanted:
        if frame["period"]["start"] > frame["period"]["end"]:
            frame["absence"] = ("no_coverage", "This window is outside the period of the analysis.",
                                "Esta ventana queda fuera del periodo del análisis.")
            continue
        found = landsat.scene_table(zone, frame["period"], land_only=land_only)
        if found["absence"] is not None:
            if found["absence"]["reason"] == "no_coverage":
                frame["absence"] = ("no_coverage", found["absence"]["detail"]["en"], found["absence"]["detail"]["es"])
                continue
            return _absent(variable, (found["absence"]["reason"], found["absence"]["detail"]["en"],
                                      found["absence"]["detail"]["es"]), found["sources"])
        table = found["data"]
        available[frame["t"]] = len(table)
        if table.empty:
            frame["absence"] = ("insufficient_qa", "No scene was clear enough over this zone in this window.",
                                "Ninguna escena estaba lo bastante despejada sobre esta zona en esta ventana.")
            continue
        chosen[frame["t"]] = _spread(table, MAX_SCENES)
    source = landsat._source()
    if not chosen:
        reasons = [frame["absence"] for frame in wanted if frame.get("absence")]
        return _absent(variable, reasons[0] if reasons else ("no_coverage", "There is no window to draw.",
                                                             "No hay ninguna ventana que dibujar."), [source])

    def key_of(name: str) -> str:
        return fingerprint.sha({"layer": name, "layers": VERSION, "pixels": PIXEL_VERSION,
                                "zone": fingerprint.zone_key(zone), "bbox": zone["bbox"], "period": period,
                                "windows": windows, "margin_km": margin,
                                "data": {"source": source["version"], "items": chosen},
                                # only when asked for, so that the keys of the layers already drawn do not change
                                **({"land": tile["item"] if tile else None} if land_only else {})})

    saved = store.get_derived(key_of(variable))
    if saved is not None:
        return {**saved, "origin": "derived"}
    if settings.load().offline:
        return _absent(variable, ("source_unreachable",
                                  "Offline, and the pixels of these scenes have not been drawn before.",
                                  "Sin red, y los píxeles de estas escenas no se habían dibujado antes."), [source])
    grid = landsat.pixel_grid(zone["bbox"], margin)
    composites = {}
    for t, ids in chosen.items():
        cancellation.checkpoint()
        try:
            composites[t] = landsat.composite(ids, grid, landsat.water_path(tile))
        except Exception:  # signing or searching the scenes failed: the network, for the person
            return _absent(variable, ("source_unreachable", "The Landsat scenes could not be read now.",
                                      "No se han podido leer ahora las escenas de Landsat."), [source])
    west, south, east, north = zone["bbox"]
    outline = zone.get("geometry") or {"type": "Polygon", "coordinates": [[[west, south], [east, south],
                                                                          [east, north], [west, north],
                                                                          [west, south]]]}
    inside = geometry_mask([outline], (grid["height"], grid["width"]), grid["transform"], invert=True,
                           all_touched=True)
    result = None
    for name in PIXEL_VARIABLES:
        key = key_of(name)
        if name != variable and store.get_derived(key) is not None:
            continue
        meta = _write_pixels(name, key, zone, period, windows, margin, grid, inside,
                             [dict(frame) for frame in wanted], composites, available, source,
                             landsat.land_mask(tile) if land_only else None, tile)
        if name == variable:
            result = meta
    return result


def _write_pixels(variable, key, zone, period, windows, margin, grid, inside, wanted, composites, available,
                  source, land=None, tile=None) -> dict:
    from hup_data.readers import landsat

    valid = {}
    for frame in wanted:
        got = composites.get(frame["t"])
        if frame.get("absence") or got is None:
            continue
        values = got[variable]
        if values is None or not np.isfinite(values).any():
            frame["absence"] = ("source_unreachable" if got["failed"] and not got["n_dates"] else "insufficient_qa",
                                "No pixel of this window could be read clear.",
                                "Ningún píxel de esta ventana se ha podido leer despejado.")
            continue
        valid[frame["t"]] = values
    if not valid:
        reasons = [frame["absence"] for frame in wanted if frame.get("absence")]
        return _absent(variable, reasons[0], [source])
    pooled = np.concatenate([values[np.isfinite(values)] for values in valid.values()])
    low, high = np.percentile(pooled, RANGE_PERCENTILES)
    value_range = [round(float(low), 6), round(float(high), 6)]  # one range for every frame
    frames, used = [], 0
    for frame in wanted:
        entry = {key_: frame[key_] for key_ in ("t", "window", "years") if key_ in frame}
        if frame["t"] not in valid:
            entry["absence"] = _text(*frame["absence"])
        else:
            values = valid[frame["t"]]
            suffix = f".{re.sub('[^a-z0-9]', '', (frame.get('window') or frame['t']).lower()) or 'w'}.tif"
            write_cog(np.where(np.isfinite(values), values, NODATA).astype("float32"), store.derived_path(key, suffix),
                      nodata=NODATA, kind="values", transform=grid["transform"])
            n_dates = composites[frame["t"]]["n_dates"]
            used += n_dates
            share = float((np.isfinite(values) & inside).sum()) / max(int(inside.sum()), 1)  # of the zone itself
            entry.update(file=suffix, coverage={"valid_pct": round(100 * share, 1), "n_dates": n_dates},
                         available_dates=available.get(frame["t"], n_dates))
        frames.append(entry)
    catalog = _catalog(variable)
    limits = [{"en": "Per-pixel median of the clear observations of each window, with the same quality mask, scale "
                     "and choice of scenes as the figures of the zone.",
               "es": "Mediana por píxel de las observaciones despejadas de cada ventana, con la misma máscara de "
                     "calidad, escala y selección de escenas que las cifras de la zona."},
              {"en": f"The zone and {margin:g} km around it are drawn, so that it can be compared with its "
                     "surroundings; the file does not mark the outline of the zone.",
               "es": f"Se dibujan la zona y {margin:g} km a su alrededor, para compararla con su entorno; el "
                     "archivo no marca el contorno de la zona."},
              {"en": "Pixels flagged as water, cloud or shadow in every date are left blank.",
               "es": "Los píxeles marcados como agua, nube o sombra en todas las fechas quedan en blanco."},
              {"en": f"The colour scale spans percentiles {RANGE_PERCENTILES[0]} to {RANGE_PERCENTILES[1]} of all "
                     "frames; values beyond take the end colours.",
               "es": f"La escala de color va del percentil {RANGE_PERCENTILES[0]} al {RANGE_PERCENTILES[1]} de "
                     "todas las ventanas; los valores de fuera toman los colores de los extremos."}]
    if any(frame.get("available_dates", 0) > frame["coverage"]["n_dates"] for frame in frames if "coverage" in frame):
        limits.append({"en": f"Each window uses at most {MAX_SCENES} of its clear dates, spread over its years.",
                       "es": f"Cada ventana usa como mucho {MAX_SCENES} de sus fechas despejadas, repartidas en "
                             "sus años."})
    if grid["pixel_m"] > landsat.PIXEL_M:
        limits.append({"en": f"Large area: drawn at about {grid['pixel_m']} m per pixel instead of 30 m, taking the "
                             "nearest Landsat pixel.",
                       "es": f"Área grande: se dibuja a unos {grid['pixel_m']} m por píxel en vez de 30 m, con el "
                             "píxel de Landsat más cercano."})
    if catalog.get("limit"):
        limits.append(catalog["limit"])
    if land:
        limits.append(land["detail"])
    support = {"resolution_m": max(grid["pixel_m"], landsat.RESOLUTION[variable]), "since": landsat.FIRST_YEAR,
               "revisit_days": 8, "cells": int(inside.sum())}
    items = sorted({item for got in composites.values() for item in got["items"]})
    sources = [{**source, "n_items": used, **({"items": items[:5]} if items else {})}]
    if tile:
        sources.append(landsat.water_source(tile))
    meta = {"variable": variable, "fingerprint": key, "format": "cog", "crs": "EPSG:4326", "bounds": grid["bounds"],
            "shape": [grid["height"], grid["width"]], "nodata": NODATA, "range": value_range,
            "range_percentiles": list(RANGE_PERCENTILES), "unit": landsat.UNITS[variable],
            "data_class": catalog.get("data_class", "measured"), "scale": catalog.get("layer", "value"),
            "name": catalog.get("name"), "sources": sources, "support": support, "limits": limits, "period": period,
            "scenario": None, "windows": windows, "margin_km": margin, "frames": frames, "absence": None}
    store.put_derived(key, meta)  # the files are already written: the .json goes last
    return {**meta, "origin": "remote"}


def zone_layers(variable: str, zone: dict, period: dict, scenario: str | None = None, *,
                windows: dict | None = None, margin_km: float | None = None, land_only: bool = False) -> dict:
    """The clip of a gridded variable over a zone, one COG per year, in the derived tier. Reused when it exists.

    Returns the layer metadata: bounds (EPSG:4326), shared range, unit, data class, scale, sources, support,
    limits and one frame per year with its file suffix, or its absence. The whole series is an absence, with its
    reason, when the source has nothing here.

    Landsat variables (ndvi, lst) are per-pixel composites over the zone plus `margin_km` around it (by default
    MARGIN_KM), one frame per year or, with `windows` (for example {"before": [2019, 2021], "after": [2023,
    2025]}), one per window, named in `frames[].window`. Gridded sources ignore both. `land_only` leaves out
    permanent water (JRC) from the Landsat pixels as the reader does for the zone figures; off by default.
    """
    if variable in PIXEL_VARIABLES:
        return _pixel_layers(variable, zone, period, windows, margin_km, land_only)
    provider = _providers().get(variable)
    if provider is None:
        return _absent(variable, ("not_computed", f"No gridded source can draw {variable} as a map yet.",
                                  f"Ninguna fuente en rejilla puede dibujar {variable} como mapa todavía."),
                       [{"source": "none", "url": "https://github.com/SpaceApps-Val-2026/Habitat-Under-Pressure"}])
    grid = provider(variable, zone, period, scenario)
    if "absence" in grid:
        return _absent(variable, grid["absence"], grid["sources"])
    key = fingerprint.sha({"layer": variable, "layers": VERSION, "zone": fingerprint.zone_key(zone),
                           "bbox": zone["bbox"], "period": period, "scenario": scenario, "data": grid["version"]})
    saved = store.get_derived(key)
    if saved is not None:
        return {**saved, "origin": "derived"}

    lat, lon, res = grid["lat"], grid["lon"], grid["res"]
    # Axes may be stored as float32: round the edges, so that 39.6 is not written as 39.59999.
    edges = (lon[0] - res / 2, lat[0] - res / 2, lon[-1] + res / 2, lat[-1] + res / 2)
    bounds = [round(float(edge), 6) for edge in edges]
    transform = from_origin(bounds[0], bounds[3], res, res)
    inside = np.ones((lat.size, lon.size), dtype=bool)
    if zone.get("geometry"):  # rows north to south, as in the file
        inside = geometry_mask([zone["geometry"]], inside.shape, transform, invert=True, all_touched=True)
    valid = {}
    for year, frame in grid["frames"].items():
        if frame.get("grid") is not None:
            values = frame["grid"][::-1]  # sources keep latitude growing north; the file goes north to south
            values = np.where(inside & np.isfinite(values), values, np.nan)
            if np.isfinite(values).any():
                valid[year] = values
                continue
            frame["absence"] = ("insufficient_qa", f"The source has no valid value here in {year}.",
                                f"La fuente no tiene ningún valor válido aquí en {year}.")
    if not valid:
        return _absent(variable, ("insufficient_qa", "The source has no valid value over this zone in this period.",
                                  "La fuente no tiene ningún valor válido sobre esta zona en este periodo."),
                       grid["sources"])
    pooled = np.concatenate([values[np.isfinite(values)] for values in valid.values()])
    value_range = [round(float(pooled.min()), 6), round(float(pooled.max()), 6)]  # one range for every year
    files, frames = {}, []
    cells = int(inside.sum())
    for year in sorted(grid["frames"]):
        frame = grid["frames"][year]
        entry = {"t": str(year)}
        if frame.get("scenario"):
            entry["scenario"] = frame["scenario"]
        if year not in valid:
            entry["absence"] = _text(*frame["absence"])
        else:
            values = valid[year]
            suffix = f".{year}.tif"
            path = store.derived_path(key, suffix)
            write_cog(np.where(np.isfinite(values), values, NODATA).astype("float32"), path, nodata=NODATA,
                      kind="values", transform=transform)
            files[suffix] = path
            entry.update(file=suffix, coverage={"valid_pct": round(100 * float(np.isfinite(values).sum()) / cells, 1),
                                                "n_dates": frame["n_dates"]})
        frames.append(entry)
    entry = _catalog(variable)
    limits = [grid["method"]]
    if entry.get("limit"):
        limits.append(entry["limit"])
    if zone.get("geometry"):
        limits.append({"en": "Cells that touch the outline of the zone are drawn whole.",
                       "es": "Las celdas que tocan el contorno de la zona se dibujan enteras."})
    support = {**grid["support"], "cells": cells}
    meta = {"variable": variable, "fingerprint": key, "format": "cog", "crs": "EPSG:4326", "bounds": bounds,
            "shape": [int(lat.size), int(lon.size)], "nodata": NODATA, "range": value_range, "unit": grid["unit"],
            "data_class": entry.get("data_class", "measured"), "scale": entry.get("layer", "value"),
            "name": entry.get("name"), "sources": grid["sources"], "support": support, "limits": limits,
            "period": period, "scenario": scenario, "frames": frames, "absence": None}
    store.put_derived(key, meta)  # the files are already written: the .json goes last
    return {**meta, "origin": "package" if grid["version"].get("files") else "cache"}


def layer_result(meta: dict, frame: dict, path: str) -> dict:
    """The body of a `type: layer` result for one frame, without id, analysis, step and fingerprint.

    `path` is where the file is served from, relative to the analysis folder.
    """
    first, last = frame.get("years") or (frame["t"], frame["t"])  # a window of years, or one year
    params = {"scenario": meta["scenario"]} if meta.get("scenario") else {}
    if frame.get("window"):
        params["window"] = frame["window"]
    body = {"type": "layer", "variable": meta["variable"], "data_class": meta["data_class"],
            "period": {"start": max(meta["period"]["start"], f"{first}-01-01"),
                       "end": min(meta["period"]["end"], f"{last}-12-31"),
                       **({"months": meta["period"]["months"]} if meta["period"].get("months") else {})},
            "sources": meta["sources"], "support": meta.get("support", {}), "limits": meta.get("limits", []),
            "method": {"operation": "zone_layer", "version": VERSION, "params": params}}
    if meta["unit"]:
        body["unit"] = meta["unit"]
    if frame.get("absence"):
        return {**body, "absence": frame["absence"]}
    return {**body, "coverage": frame["coverage"],
            "layer": {"path": path, "format": "cog", "range": meta["range"], "scale": meta["scale"]}}
