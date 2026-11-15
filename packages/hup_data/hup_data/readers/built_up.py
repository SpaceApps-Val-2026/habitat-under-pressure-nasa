"""GHSL built-up surface (GHS-BUILT-S R2023A, total, 100 m, World Mollweide), read by window from the prepared tiles.

Each cell holds the square metres of built-up surface inside its 100 × 100 m (0 to 10 000; 65 535 is no data, as over
the sea). The zone is reprojected to the grid, never the grid to the zone, and each cell counts by the share of its
area inside the zone. Mollweide is equal-area, so those shares are true areas.

What GHSL observed: Landsat for 1975, 1990, 2000 and 2014, and a Sentinel-2 composite for 2018. Every other epoch
is interpolated, and the epochs after 2018 are carried forward from the earlier ones (GHSL Data Package 2023,
section 2.1). A building finished after 2018 is therefore not seen by any epoch.
"""
import re
from math import floor

import numpy as np
import pandas as pd
import rasterio
import shapely
from pyproj import CRS, Transformer
from rasterio.errors import WindowError
from rasterio.features import geometry_window
from shapely.geometry import box, shape

from hup_data import cancellation, paths
from hup_data.prepare.common import read_index
from hup_data.readers import absent, register

SOURCE = "ghsl-built-s"
URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/"
VERSION = "R2023A V1-0"
EPOCHS = tuple(range(1975, 2031, 5))
LAST_OBSERVATION = 2018
NODATA = 65535
MAX_VALUE = 10000  # m² in a 100 m cell
# GHSL tile grid in Mollweide: 1000 km tiles counted from this corner (R1_C1 is the top left one).
TILE_ORIGIN, TILE_SIZE = (-18_041_000.0, 9_000_000.0), 1_000_000.0
NAME = re.compile(r"GHS_BUILT_S_E(\d{4})_GLOBE_R2023A_54009_100_V1_0(?:_R(\d+)_C(\d+))?\.tif$")
UNITS = {"built_up_surface": "m²", "built_up_fraction": "1"}


def _source(items: list[str] | None = None) -> dict:
    ref = {"source": SOURCE, "product": "GHS-BUILT-S", "version": VERSION, "url": URL, "license": "CC BY 4.0"}
    return ref if items is None else {**ref, "items": items, "n_items": len(items)}


def _files() -> dict[int, list[str]]:
    """Prepared files of the 100 m total product, by epoch."""
    found: dict[int, list[str]] = {}
    for key, entry in read_index("grids").items():
        match = NAME.search(key)
        if entry.get("source") == SOURCE and match and (paths.data_dir() / entry["path"]).is_file():
            found.setdefault(int(match.group(1)), []).append(entry["path"])
    return {epoch: sorted(files) for epoch, files in found.items()}


def tiles_for(area) -> list[str]:
    """GHSL tile ids (R<row>_C<col>) that a geometry in Mollweide touches."""
    west, south, east, north = area.bounds
    x0, y0 = TILE_ORIGIN
    cols = range(floor((west - x0) / TILE_SIZE) + 1, floor((east - x0) / TILE_SIZE) + 2)
    rows = range(floor((y0 - north) / TILE_SIZE) + 1, floor((y0 - south) / TILE_SIZE) + 2)
    return [f"R{row}_C{col}" for row in rows for col in cols
            if area.intersects(box(x0 + (col - 1) * TILE_SIZE, y0 - row * TILE_SIZE,
                                   x0 + col * TILE_SIZE, y0 - (row - 1) * TILE_SIZE))]


def _project(geometry, crs):
    """The zone in the grid's projection: the zone moves, the grid stays."""
    projector = Transformer.from_crs(4326, crs, always_xy=True)
    return shapely.transform(geometry, lambda xy: np.column_stack(projector.transform(*xy.T)))


def _equal_area(raster) -> bool:
    operation = CRS.from_user_input(raster.crs).coordinate_operation
    methods = ("equal area", "equal-area", "mollweide")
    return bool(operation) and any(method in operation.method_name.casefold() for method in methods) \
        and raster.transform.is_rectilinear and not raster.transform.b and not raster.transform.d


def cell_sums(raster, area) -> dict:
    """Built-up m², valid area, no-data area and touched cells of `area` (raster CRS) inside one raster.

    Each cell counts by the share of its area inside `area`. Raises ValueError on a value outside 0 to 10 000.
    """
    empty = {"built_m2": 0.0, "valid_m2": 0.0, "nodata_m2": 0.0, "cells": 0}
    area = area.intersection(box(*raster.bounds))
    if area.is_empty or area.area == 0:
        return empty
    try:
        window = geometry_window(raster, [area])
    except WindowError:
        return empty
    values = raster.read(1, window=window)
    affine = raster.window_transform(window)
    rows, cols = np.indices(values.shape)
    xa, ya = affine.c + cols * affine.a, affine.f + rows * affine.e
    xb, yb = xa + affine.a, ya + affine.e
    cells = shapely.box(np.minimum(xa, xb), np.minimum(ya, yb), np.maximum(xa, xb), np.maximum(ya, yb))
    shapely.prepare(area)
    overlap = shapely.area(shapely.intersection(cells, area))
    cell_area = abs(affine.a * affine.e)
    overlap[overlap <= cell_area * 1e-9] = 0.0  # floating-point slivers along cell edges
    nodata = values == (raster.nodata if raster.nodata is not None else NODATA)
    touched = overlap > 0
    if (values[touched & ~nodata] > MAX_VALUE).any():
        raise ValueError("value above 10000 m² in a 100 m cell")
    valid = touched & ~nodata
    return {"built_m2": float((values[valid] * overlap[valid]).sum() / cell_area),
            "valid_m2": float(overlap[valid].sum()), "nodata_m2": float(overlap[touched & nodata].sum()),
            "cells": int(valid.sum())}


@register("1", "built_up_surface", "built_up_fraction")
def read(variable: str, zone: dict, period: dict) -> dict:
    wanted = [epoch for epoch in EPOCHS if int(period["start"][:4]) <= epoch <= int(period["end"][:4])]
    if not wanted:
        return absent("no_coverage", "GHSL built-up surface has one epoch every five years from 1975 to 2030; "
                      "none falls in this period.",
                      "La superficie construida de GHSL tiene una época cada cinco años de 1975 a 2030; "
                      "ninguna cae en este periodo.", _source(), origin="package")
    files = _files()
    present = [epoch for epoch in wanted if epoch in files]
    if not present:
        return absent("needs_package", "No GHSL built-up surface tile is prepared for the epochs of this period "
                      f"({', '.join(map(str, wanted))}).",
                      "No hay ninguna tesela preparada de superficie construida de GHSL para las épocas de este "
                      f"periodo ({', '.join(map(str, wanted))}).", _source(), origin="package")
    geometry = shapely.make_valid(shape(zone["geometry"])) if zone.get("geometry") else box(*zone["bbox"])
    rows, items = [], []
    for epoch in present:
        totals = {"built_m2": 0.0, "valid_m2": 0.0, "nodata_m2": 0.0, "cells": 0}
        footprint = None
        for path in files[epoch]:
            cancellation.checkpoint()
            with rasterio.open(paths.data_dir() / path) as raster:
                if not _equal_area(raster):
                    return absent("insufficient_qa", "The built-up grid needs a north-up, equal-area projection.",
                                  "La rejilla de superficie construida necesita una proyección de área "
                                  "equivalente orientada al norte.", _source(), origin="package")
                area = _project(geometry, raster.crs)
                footprint = box(*raster.bounds) if footprint is None else footprint.union(box(*raster.bounds))
                try:
                    sums = cell_sums(raster, area)
                except ValueError:
                    return absent("insufficient_qa", "The built-up grid has values outside 0 to 10 000 m² per cell.",
                                  "La rejilla de superficie construida tiene valores fuera de 0 a 10 000 m² "
                                  "por celda.", _source(), origin="package")
            for name in totals:
                totals[name] += sums[name]
            items.append(path.rsplit("/", 1)[-1].removesuffix(".tif"))
        missing = area.difference(footprint)
        if missing.area > 1e-6 * area.area:
            tiles = ", ".join(tiles_for(missing))
            return absent("needs_package", f"The GHSL built-up tiles for {epoch} do not cover the whole zone; "
                          f"missing: {tiles}.",
                          f"Las teselas de superficie construida de GHSL de {epoch} no cubren toda la zona; "
                          f"faltan: {tiles}.", _source(), origin="package")
        if totals["valid_m2"] <= 0:
            return absent("insufficient_qa", "The zone falls only on cells without data (for example, the sea).",
                          "La zona cae solo en celdas sin dato (por ejemplo, el mar).", _source(), origin="package")
        rows.append({"t": f"{epoch}-01-01", "surface_m2": totals["built_m2"],
                     "fraction": totals["built_m2"] / totals["valid_m2"], "zone_area_m2": area.area,
                     "valid_area_m2": totals["valid_m2"], "n_valid": totals["cells"],
                     "after_last_observation": epoch > LAST_OBSERVATION})
    table = pd.DataFrame(rows)
    table.insert(1, "v", table["surface_m2"] if variable == "built_up_surface" else table["fraction"])
    zone_area = float(table["zone_area_m2"].iloc[0])
    valid_pct = round(100 * float(table["valid_area_m2"].min()) / zone_area, 2)
    limits = [
        {"en": "Built-up surface is the ground covered by buildings, estimated from satellite images in 100 m cells "
               "and apportioned by cell area. It is a footprint: it does not count floors.",
         "es": "La superficie construida es el suelo cubierto por edificios, estimado con imágenes de satélite en "
               "celdas de 100 m y repartido según el área de cada celda. Es huella: no cuenta plantas."},
        {"en": "A 100 m cell cannot outline a single building; read the figure for areas of several hectares.",
         "es": "Una celda de 100 m no delimita un edificio; la cifra sirve para áreas de varias hectáreas."},
    ]
    if any(epoch > LAST_OBSERVATION for epoch in present):
        limits.append({
            "en": "The last satellite observation of GHSL is 2018; epochs after it are carried forward from the "
                  "earlier ones and do not show what was built later.",
            "es": "La última observación de satélite de GHSL es de 2018; las épocas posteriores se prolongan desde "
                  "las anteriores y no muestran lo construido después."})
    if valid_pct < 99.99:
        limits.append({"en": f"Only {valid_pct}% of the zone has data; cells without data, such as open sea, "
                             "are left out.",
                       "es": f"Solo el {valid_pct}% de la zona tiene dato; se dejan fuera las celdas sin dato, "
                             "como el mar abierto."})
    skipped = [epoch for epoch in wanted if epoch not in present]
    if skipped:
        limits.append({"en": f"Epochs not prepared on this computer: {', '.join(map(str, skipped))}.",
                       "es": f"Épocas no preparadas en este ordenador: {', '.join(map(str, skipped))}."})
    return {"data": table, "absence": None, "origin": "package", "sources": [_source(sorted(set(items)))],
            "support": {"resolution_m": 100, "since": 1975, "cells": round(zone_area / 1e4, 2)},
            "coverage": {"valid_pct": valid_pct, "n_dates": len(table)},
            "unit": UNITS[variable], "limits": limits, "input_fingerprint": None}
