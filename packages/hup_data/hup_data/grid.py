"""The common grid of a chain: every series of it on the coarsest resolution among them, declared in `support`.

Rule of the contract (common.schema.json, support): grids are never mixed without resampling to the coarsest one.
`evaluate_chain` (hup_ops.association) compares `support.resolution_m` exactly, so the series are put on one grid
here, before the chain is evaluated, and never refined:

- Landsat (ndvi, lst): the existing aligned reader, `read_series(..., grid_resolution_m=metres)`.
- Readers that integrate over the zone (built-up surface, flood maps...): the same reader over the union of the cells
  of `metres` (equal-area EPSG:6933, aligned to multiples of `metres`) that touch the zone. The figure is then that
  of those cells, not of the zone outline, and the result says so.
- A series already at `metres` is returned as it is; one coarser than `metres` is an absence: it cannot be refined.
"""
from math import ceil, floor

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import box, mapping, shape

from hup_data import readers
from hup_data.readers import absent

CRS = "EPSG:6933"  # the equal-area grid of readers/landsat_grid.py
LANDSAT = {"ndvi", "lst"}
URL = "https://github.com/SpaceApps-Val-2026/Habitat-Under-Pressure"


def coarsest(results: list[dict]) -> float | None:
    """The coarsest `support.resolution_m` among results with data, or None when none declares one."""
    found = [r["support"]["resolution_m"] for r in results
             if r.get("absence") is None and (r.get("support") or {}).get("resolution_m")]
    return max(found, default=None)


def cells_zone(zone: dict, metres: float) -> tuple[dict, int]:
    """The union of the `metres` cells (EPSG:6933) that touch the zone, as a zone in EPSG:4326, and how many."""
    outline = shape(zone["geometry"]) if zone.get("geometry") else box(*zone["bbox"])
    forward = Transformer.from_crs(4326, CRS, always_xy=True)
    back = Transformer.from_crs(CRS, 4326, always_xy=True)
    projected = shapely.transform(outline, lambda xy: np.column_stack(forward.transform(*xy.T)))
    west, south, east, north = projected.bounds
    shapely.prepare(projected)
    cells = [box(x * metres, y * metres, (x + 1) * metres, (y + 1) * metres)
             for x in range(floor(west / metres), ceil(east / metres))
             for y in range(floor(south / metres), ceil(north / metres))]
    touched = [cell for cell in cells if projected.intersects(cell) and projected.intersection(cell).area > 0]
    union = shapely.segmentize(shapely.union_all(touched), metres / 10)
    geometry = shapely.transform(union, lambda xy: np.column_stack(back.transform(*xy.T)))
    made = {"id": f"{zone.get('id', 'zone')}-grid-{metres:g}m",
            "name": f"{zone.get('name', 'zone')}: {metres:g} m cells",
            "bbox": [round(v, 6) for v in geometry.bounds], "geometry": mapping(geometry),
            "source": f"Cells of {metres:g} m (EPSG:6933) touching {zone.get('id', 'the zone')}"}
    return made, len(touched)


def to_resolution(variable: str, zone: dict, period: dict, metres: float, *, min_years: int = 1) -> dict:
    """The series of `variable` for `zone` on a grid of `metres`, with `support.resolution_m == metres`."""
    if variable in LANDSAT:
        return readers.read_series(variable, zone, period, grid_resolution_m=metres, grid_min_years=min_years)
    native = readers.read_series(variable, zone, period)
    if native["absence"] is not None:
        return native
    have = (native.get("support") or {}).get("resolution_m")
    if have == metres:
        return native
    if have is not None and have > metres:
        return {**absent("not_computed", f"{variable} is at {have:g} m and cannot be refined to {metres:g} m: the "
                         "common grid is the coarsest of the chain.",
                         f"{variable} está a {have:g} m y no se puede afinar a {metres:g} m: la rejilla común es la "
                         "más gruesa de la cadena.", native["sources"][0]), "sources": native["sources"]}
    cells, n = cells_zone(zone, metres)
    regridded = readers.read_series(variable, cells, period)
    if regridded["absence"] is not None:
        return regridded
    native_text = f"{have:g} m" if have else "vector maps without a grid"
    native_es = f"{have:g} m" if have else "mapas vectoriales sin rejilla"
    limit = {"en": f"Put on a common {metres:g} m grid (from {native_text}): the figure is that of the {n} cells of "
                   f"{metres:g} m that touch the zone, not of the zone outline.",
             "es": f"Llevado a una rejilla común de {metres:g} m (desde {native_es}): la cifra es la de las {n} celdas "
                   f"de {metres:g} m que tocan la zona, no la del contorno de la zona."}
    support = {**{k: v for k, v in (regridded.get("support") or {}).items() if k == "since"},
               "resolution_m": metres, "cells": n}
    return {**regridded, "support": support, "limits": [*regridded.get("limits", []), limit]}


def on_common_grid(variables: list[str], zone: dict, period: dict, *, min_years: int = 1) -> dict[str, dict]:
    """Every variable on the coarsest native resolution among them. Variables with no grid of their own (vectors)
    follow the others; if none has a grid, each is returned as read."""
    from hup_data.readers import landsat

    native = {variable: {"absence": None, "support": {"resolution_m": landsat.RESOLUTION[variable]}}
              if variable in LANDSAT else readers.read_series(variable, zone, period) for variable in variables}
    metres = coarsest(list(native.values()))
    if metres is None:
        return {variable: readers.read_series(variable, zone, period) for variable in variables}
    return {variable: to_resolution(variable, zone, period, metres, min_years=min_years) for variable in variables}
