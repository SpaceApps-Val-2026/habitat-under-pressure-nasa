"""Population in a zone, from the prepared grids: GHSL (people now, 100 m or 1 km) and SEDAC SSP (by decade, scenario).

Both are sums, never means: each cell adds its people times the share of the cell that the zone covers, which assumes
people are spread evenly inside a cell. GHSL is in Mollweide, an equal-area projection, so the zone is reprojected to
the grid and the grid is never resampled. SEDAC is in degrees: shares are measured in a Lambert equal-area projection
centred on the zone. `support.cells` is how many cells the zone holds; 1 or fewer means a cell is larger than the zone.

GHSL comes as one global 1 km grid and as 100 m tiles of 1000 x 1000 km (only the tiles of the cases are prepared).
The reader uses the finest grid whose prepared files cover the whole zone, and falls back to 1 km, saying so, if not.

The variable `population` (`population_now`) answers first with the INE (readers/ine_population.py): in Spain, when
the whole zone lies within census sections with an INE figure on this computer, the official municipal figures
spread by section and then by residential built-up surface at 100 m. Elsewhere, or without those figures, GHSL.
"""
import json
import re
from math import floor

import numpy as np
import pandas as pd
import rasterio
import shapely
from pyproj import CRS
from rasterio.windows import Window, from_bounds
from shapely.geometry import box, shape

from hup_data import cancellation, paths
from hup_data.prepare.common import read_index
from hup_data.readers import absent, register
from hup_data.readers.exposure import _project

GHSL_URL = "https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php"
SEDAC_URL = "https://doi.org/10.7927/m30p-j498"
_GHSL = re.compile(r"^ghsl/[^/]+/GHS_POP_E(\d{4})_GLOBE_R2023A_54009_(\d+)_V1_0(?:_R\d+_C\d+)?\.tif$")
# GHSL tile grid in Mollweide: 1000 km tiles counted from this corner (R1_C1 is the top left one).
TILE_ORIGIN, TILE_SIZE = (-18_041_000.0, 9_000_000.0), 1_000_000.0
_SEDAC = re.compile(r"^sedac-ssp/.*/(?:(ssp\d)_(\d{4})|(\d{4})total)\.nc$")
_LAEA = "+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"


def zone_geometry(zone: dict):
    """The outline of a zone in EPSG:4326, or its box when it has none."""
    return shapely.make_valid(shape(zone["geometry"])) if zone.get("geometry") else box(*zone["bbox"])


def _url(entry: dict, fallback: str) -> str:
    """The download url of the package a prepared file came from, as the manifest records it."""
    manifest = paths.manifest()
    if manifest.is_file():
        for text in manifest.read_text(encoding="utf-8").splitlines():
            line = json.loads(text) if text.strip() else {}
            if line.get("path") == entry.get("from") and str(line.get("ingest", {}).get("url")).startswith("http"):
                return line["ingest"]["url"]
    return fallback


def _source(source: str, product: str, entries: list[dict], fallback: str) -> dict:
    single = len({entry.get("from") for entry in entries}) == 1  # several packages: cite the dataset instead
    ref = {"source": source, "product": product, "url": _url(entries[0], fallback) if single else fallback}
    if entries:
        ref.update({key: entries[0][key] for key in ("version", "license") if entries[0].get(key)})
        ref["n_items"] = len(entries)
    return ref


def cells(grid, outline, area_crs=None) -> dict | None:
    """The cells of an open grid that `outline` (already in the grid's CRS) touches, and the share of each it covers.

    Shares are areas measured in the grid's CRS, or in `area_crs` when the grid is not equal-area. Returns None when
    the outline misses the grid. Keys: window, share (array like the window), cell_m2 (mean area of a cell).
    """
    bounds = box(*grid.bounds)
    if outline.is_empty or not outline.intersects(bounds):
        return None
    if outline.difference(bounds).area > 1e-9 * outline.area:  # beyond floating-point slivers at the edge
        return {"outside": True}
    window = from_bounds(*outline.bounds, transform=grid.transform)
    col0, row0 = int(np.floor(window.col_off)), int(np.floor(window.row_off))
    col1, row1 = int(np.ceil(window.col_off + window.width)), int(np.ceil(window.row_off + window.height))
    col0, row0, col1, row1 = max(col0, 0), max(row0, 0), min(col1, grid.width), min(row1, grid.height)
    if col1 <= col0 or row1 <= row0:
        return None
    window = Window(col0, row0, col1 - col0, row1 - row0)
    affine = grid.window_transform(window)
    cols, rows = np.meshgrid(np.arange(window.width), np.arange(window.height))
    x0, y0 = affine @ (cols, rows)
    x1, y1 = affine @ (cols + 1, rows + 1)
    boxes = shapely.box(np.minimum(x0, x1), np.minimum(y0, y1), np.maximum(x0, x1), np.maximum(y0, y1))
    shapely.prepare(outline)
    parts = shapely.intersection(boxes, outline)  # cut in the grid's CRS, so a whole cell stays that very cell
    if area_crs is not None:
        boxes, parts = _project(boxes, area_crs), _project(parts, area_crs)
    share = shapely.area(parts) / shapely.area(boxes)
    return {"window": window, "share": share, "cell_m2": float(shapely.area(boxes).mean())}


def total(values: np.ndarray, nodata, share: np.ndarray) -> dict:
    """People in the zone from one window of values; cells with no data are left out and their share counted."""
    missing = ~np.isfinite(values) | (values < 0)
    if nodata is not None:
        missing |= values == nodata
    held, lost = float(share.sum()), float(share[missing].sum())
    people = float((np.where(missing, 0.0, values) * share).sum())
    return {"people": people, "cells": held, "missing": lost}


def _limits(epoch: str, held: float, missing: float) -> list[dict]:
    limits = [{"en": f"Population estimate for {epoch}, spread evenly within each grid cell.",
               "es": f"Población estimada para {epoch}, repartida uniformemente dentro de cada celda."}]
    if held <= 1:
        limits.append({"en": "The zone is smaller than one grid cell: the figure is a share of that cell.",
                       "es": "La zona es menor que una celda: la cifra es una parte de esa celda."})
    if missing > 0:
        limits.append({"en": f"{100 * missing / held:.1f}% of the zone has no population data and is left out.",
                       "es": f"El {100 * missing / held:.1f}% de la zona no tiene datos de población y queda fuera."})
    return limits


def _result(table: pd.DataFrame, source: dict, held: float, missing: float, cell_m2: float, epoch: str) -> dict:
    return {"data": table, "absence": None, "origin": "cache", "sources": [source], "unit": "people",
            "support": {"resolution_m": round(cell_m2 ** 0.5), "cells": round(held, 3)},
            "coverage": {"valid_pct": round(100 * (held - missing) / held, 2), "n_dates": int(table["t"].nunique())},
            "limits": _limits(epoch, held, missing), "input_fingerprint": None}


def ghsl_grids() -> dict[int, dict[int, list[dict]]]:
    """Resolution in metres → epoch → index entries of the GHSL population files prepared here (one per tile)."""
    found: dict[int, dict[int, list[dict]]] = {}
    for key, entry in sorted(read_index("grids").items()):
        match = _GHSL.match(key)
        if match and (paths.data_dir() / entry["path"]).is_file():
            found.setdefault(int(match.group(2)), {}).setdefault(int(match.group(1)), []).append(entry)
    return found


def tiles_for(area) -> list[str]:
    """GHSL tile ids (R<row>_C<col>) that a geometry in Mollweide touches."""
    west, south, east, north = area.bounds
    x0, y0 = TILE_ORIGIN
    cols = range(floor((west - x0) / TILE_SIZE) + 1, floor((east - x0) / TILE_SIZE) + 2)
    rows = range(floor((y0 - north) / TILE_SIZE) + 1, floor((y0 - south) / TILE_SIZE) + 2)
    return [f"R{row}_C{col}" for row in rows for col in cols
            if area.intersects(box(x0 + (col - 1) * TILE_SIZE, y0 - row * TILE_SIZE,
                                   x0 + col * TILE_SIZE, y0 - (row - 1) * TILE_SIZE))]


def _label(resolution: int) -> str:
    return f"{resolution // 1000} km" if resolution % 1000 == 0 else f"{resolution} m"


def ghsl_grid(geometry, grids: dict | None = None) -> dict:
    """The finest GHSL grid prepared here whose files cover the whole of `geometry` (EPSG:4326).

    Returns {"resolution_m", "epochs": {epoch: [entries]}, "limits", "missing"}: `epochs` is empty when no grid covers
    the zone, and `missing` names the tiles of the finest grid that would be needed (None when nothing is prepared).
    """
    limits, missing = [], None
    for resolution, epochs in sorted((ghsl_grids() if grids is None else grids).items()):
        covering, short = {}, {}
        for epoch, entries in sorted(epochs.items()):
            footprint, outline = None, None
            for entry in entries:
                with rasterio.open(paths.data_dir() / entry["path"]) as grid:
                    outline = _project(geometry, CRS.from_user_input(grid.crs)) if outline is None else outline
                    footprint = box(*grid.bounds) if footprint is None else footprint.union(box(*grid.bounds))
            left = outline.difference(footprint)
            if left.area <= 1e-9 * outline.area:
                covering[epoch] = entries
            else:
                short[epoch] = tiles_for(left)
        if covering:
            if short:
                limits.append({"en": f"GHSL {_label(resolution)} tiles for {', '.join(map(str, short))} do not cover "
                                     "the zone; that epoch is left out.",
                               "es": f"Las teselas GHSL de {_label(resolution)} de {', '.join(map(str, short))} no "
                                     "cubren la zona; esa época queda fuera."})
            return {"resolution_m": resolution, "epochs": covering, "limits": limits, "missing": None}
        tiles = sorted({tile for names in short.values() for tile in names})
        missing = missing or tiles
        limits.append({"en": f"The GHSL {_label(resolution)} grid is prepared but does not cover the whole zone "
                             f"(missing tiles: {', '.join(tiles)}); a coarser grid is used.",
                       "es": f"La rejilla GHSL de {_label(resolution)} está preparada pero no cubre toda la zona "
                             f"(faltan las teselas {', '.join(tiles)}); se usa una rejilla más gruesa."})
    return {"resolution_m": None, "epochs": {}, "limits": limits, "missing": missing}


def ghsl_count(geometry, entries: list[dict]) -> dict:
    """People of one GHSL epoch inside `geometry` (EPSG:4326), summed over the files (tiles) of that epoch.

    Keys: people, cells (share-weighted cells held), missing (of them, with no data), cell_m2.
    """
    counted = {"people": 0.0, "cells": 0.0, "missing": 0.0, "cell_m2": None}
    for entry in entries:
        cancellation.checkpoint()
        with rasterio.open(paths.data_dir() / entry["path"]) as grid:
            outline = _project(geometry, CRS.from_user_input(grid.crs)).intersection(box(*grid.bounds))
            found = cells(grid, outline) if outline.area > 0 else None
            if found is None or "outside" in found:
                continue
            values = grid.read(1, window=found["window"]).astype("float64")
            for key, value in total(values, grid.nodata, found["share"]).items():
                counted[key] += value
            counted["cell_m2"] = found["cell_m2"]
    return counted


def ghsl_source(resolution: int | None, entries: list[dict]) -> dict:
    """The GHSL reference, with the resolution in the product and the files read as items."""
    product = "GHS-POP" if resolution is None else f"GHS-POP {_label(resolution)}"
    ref = _source("ghsl", product, entries, GHSL_URL)
    if entries:
        ref["items"] = sorted({entry["path"].rsplit("/", 1)[-1].removesuffix(".tif") for entry in entries})
    return ref


def ghsl_population(variable: str, zone: dict, period: dict | None = None) -> dict:
    """People in the zone for each GHSL epoch prepared at the finest grid that covers it. Table columns t (year), v."""
    geometry, grids = zone_geometry(zone), ghsl_grids()
    choice = ghsl_grid(geometry, grids)
    entries = [entry for epoch in sorted(choice["epochs"]) for entry in choice["epochs"][epoch]]
    source = ghsl_source(choice.get("resolution_m"), entries)
    if not choice["epochs"]:
        if not grids:
            return absent("needs_package", "The GHSL population grid has not been prepared on this computer.",
                          "La rejilla de población GHSL no está preparada en este ordenador.", source)
        if 1000 not in grids and choice["missing"]:
            tiles = ", ".join(choice["missing"])
            return absent("needs_package", f"The GHSL population tiles prepared here do not cover the zone; "
                          f"missing: {tiles}.",
                          f"Las teselas de población GHSL preparadas no cubren la zona; faltan: {tiles}.", source)
        return absent("no_coverage", "The zone lies partly or wholly outside the population grid.",
                      "La zona queda en parte o del todo fuera de la rejilla de población.", source)
    rows = [{"t": epoch, **ghsl_count(geometry, choice["epochs"][epoch])} for epoch in sorted(choice["epochs"])]
    last = rows[-1]
    if last["cells"] - last["missing"] <= 0:
        return absent("no_coverage", "The population grid has no data inside the zone.",
                      "La rejilla de población no tiene datos dentro de la zona.", source)
    table = pd.DataFrame({"t": [row["t"] for row in rows], "v": [row["people"] for row in rows]})
    result = _result(table, source, last["cells"], last["missing"], last["cell_m2"],
                     ", ".join(str(row["t"]) for row in rows))
    result["support"]["resolution_m"] = choice["resolution_m"]
    result["limits"] += choice["limits"]
    return result


def population_now(variable: str, zone: dict, period: dict | None = None) -> dict:
    """People in the zone: INE by census section where it covers the whole zone, GHSL (ghsl_population) elsewhere.

    With the INE the table has one row (t, the year of the figures, and v) and `sources` names the INE tables, the
    INE section boundaries and the built-up grid; `support.resolution_m` is 100 (section and 100 m cell).
    """
    from hup_data.readers import ine_population

    geometry = zone_geometry(zone)
    chosen = ine_population.choose(geometry)
    if not chosen["ok"]:
        result = ghsl_population(variable, zone, period)
        if chosen["why"] and result["absence"] is None:
            result["limits"].append(chosen["why"])
        return result
    counted = ine_population.count(geometry, chosen)
    zone_m2 = _project(geometry, ine_population.MOLLWEIDE).area
    return {"data": pd.DataFrame({"t": [chosen["year"]], "v": [counted["people"]]}), "absence": None,
            "origin": "package", "sources": chosen["sources"], "unit": "people",
            "support": {"resolution_m": 100, "cells": round(zone_m2 / 1e4, 3)},
            "coverage": {"valid_pct": round(100 * (1 - chosen["outside"]), 2), "n_dates": 1},
            "limits": ine_population.limits(chosen, counted), "input_fingerprint": None}


def sedac_grids() -> dict[tuple[str, int], dict]:
    """(scenario, year) → index entry of the SEDAC SSP total-population grids. The base year 2000 is "historical"."""
    found = {}
    for key, entry in read_index("grids").items():
        match = _SEDAC.match(key)
        if match and (paths.data_dir() / entry["path"]).is_file():
            scenario, year = (match.group(1), match.group(2)) if match.group(1) else ("historical", match.group(3))
            found[(scenario, int(year))] = entry
    return found


def sedac_population(variable: str, zone: dict, period: dict | None = None) -> dict:
    """People in the zone by decade and SSP scenario. Table columns t (year), v and scenario."""
    grids = sedac_grids()
    if period:
        first, last = int(period["start"][:4]), int(period["end"][:4])
        grids = {key: entry for key, entry in grids.items() if first <= key[1] <= last}
    source = _source("sedac-ssp", "SSP population, 1/8 degree", list(grids.values()), SEDAC_URL)
    if not sedac_grids():
        return absent("needs_package", "The SEDAC SSP population grids have not been prepared on this computer.",
                      "Las rejillas de población SSP de SEDAC no están preparadas en este ordenador.", source)
    if not grids:
        return absent("no_coverage", "SEDAC SSP has population by decade from 2000 to 2100 only.",
                      "SEDAC SSP solo tiene población por décadas de 2000 a 2100.", source)
    geometry = zone_geometry(zone)
    centre = geometry.centroid
    area_crs = CRS.from_proj4(_LAEA.format(lat=centre.y, lon=centre.x))
    rows, found, transform = [], None, None
    for (scenario, year), entry in sorted(grids.items()):
        with rasterio.open(paths.data_dir() / entry["path"]) as grid:
            if found is None or grid.transform != transform:
                transform = grid.transform
                found = cells(grid, geometry, area_crs)
                if found is None or "outside" in found:
                    return absent("no_coverage", "SEDAC SSP covers latitudes from 55.9° S to 83.7° N on land only.",
                                  "SEDAC SSP cubre de 55,9° S a 83,7° N y solo tierra firme.", source)
            values = grid.read(1, window=found["window"]).astype("float64")
            rows.append({"t": year, "scenario": scenario, **total(values, grid.nodata, found["share"])})
    if all(row["cells"] - row["missing"] <= 0 for row in rows):
        return absent("no_coverage", "SEDAC SSP has no population data inside the zone: it only covers land.",
                      "SEDAC SSP no tiene datos de población dentro de la zona: solo cubre tierra firme.", source)
    table = pd.DataFrame({"t": [row["t"] for row in rows], "v": [row["people"] for row in rows],
                          "scenario": [row["scenario"] for row in rows]})
    missing = max(row["missing"] for row in rows)
    result = _result(table, source, rows[0]["cells"], missing, found["cell_m2"], "each decade and scenario")
    result["limits"].append({"en": "Projections of the shared socioeconomic pathways (SSP), not a forecast.",
                             "es": "Proyecciones de las trayectorias socioeconómicas compartidas (SSP), no una "
                                   "previsión."})
    return result


# 3: the part of a coastal zone outside every census section counts as no one (Sagunt answers with the INE); saved
# series of version 2, some of them GHSL read before the INE was prepared, are not reused.
register("3", "population")(population_now)
register("1", "population_projected")(sedac_population)
