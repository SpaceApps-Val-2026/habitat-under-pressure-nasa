"""Landsat zone series on a common metric grid before temporal aggregation."""

from contextlib import closing
from datetime import UTC, datetime
from math import ceil, floor

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping, shape

from hup_data import cancellation, fingerprint, settings, store
from hup_data.readers import absent, landsat

CRS = "EPSG:6933"
VERSION = "2"
FAILED = object()  # a scene whose bands could not be read: unknown, not unusable


def _grid(zone, metres):
    outline = zone.get("geometry") or mapping(box(*zone["bbox"]))
    outline = transform_geom("EPSG:4326", CRS, outline)
    west, south, east, north = shape(outline).bounds
    x, y = floor(west / metres), ceil(north / metres)
    width, height = ceil(east / metres) - x, y - floor(south / metres)
    if min(width, height) < 1 or width * height > landsat.MAX_PIXELS:
        return None, None
    transform = from_origin(x * metres, y * metres, metres, metres)
    grid = {"crs": CRS, "width": width, "height": height, "transform": transform}
    return grid, geometry_mask([outline], (height, width), transform, invert=True, all_touched=True)


def _band(item, name, grid):
    with rasterio.open(item.assets[name].href) as source, WarpedVRT(source, **grid, nodata=0) as on_grid:
        return on_grid.read(1)


def _scene(item, grid, inside, variable):
    try:
        with rasterio.Env(**landsat.GDAL):
            quality = _band(item, "qa_pixel", grid)
            clear = landsat._clear(quality, inside)
            # Values are finite only on clear pixels, so a clear share under MIN_CLEAR can never be usable: the
            # value bands would be downloaded only to be dropped (a coast without a water tile: every scene).
            if clear.sum() / inside.sum() < landsat.MIN_CLEAR:
                return None
            if variable == "ndvi":
                red = _band(item, "red", grid)
                near_ir = _band(item, "nir08", grid)
                values, _ = landsat._physical(clear, red, near_ir, 0)
            else:
                thermal = _band(item, "lwir11", grid)
                _, values = landsat._physical(clear, np.zeros(1), np.zeros(1), thermal)
        usable = np.isfinite(values).sum() / inside.sum()
        if usable < landsat.MIN_CLEAR:
            return None
        return (item.datetime.date().isoformat(), item.properties["platform"], float(np.nanmedian(values)),
                float(usable), item.id)
    except KeyError:  # the scene has no such band: it can never be used
        return None
    except rasterio.errors.RasterioError:  # network, timeout or a broken file: it may be read next time
        return FAILED


def read(variable, zone, period, metres, min_years, tier1_only=True):
    """`tier1_only` (the default) keeps only Tier 1 scenes (see `landsat`) and enters the key; with False every tier
    enters, under the key the grid had before Tier 1 became the default."""
    source = landsat._source()
    support = {"resolution_m": metres, "since": landsat.FIRST_YEAR}
    if variable not in landsat.UNITS or metres < max(landsat.RESOLUTION.values()):
        return absent("not_computed", "This Landsat grid requires NDVI or surface temperature at 100 m or coarser.",
                      "Esta rejilla Landsat requiere NDVI o temperatura superficial a 100 m o más.", source)
    first = max(int(period["start"][:4]), landsat.FIRST_YEAR)
    last = min(int(period["end"][:4]), datetime.now(UTC).year)
    available = max(0, last - first + 1)
    if available < min_years:
        return {**absent("insufficient_qa", f"At most {available} years are covered. The test requires {min_years}.",
                         f"La cobertura alcanza como máximo {available} años. La prueba requiere {min_years}.",
                         source), "support": support}
    tile = landsat.water_tile(zone)
    fields = {"landsat_grid": VERSION, "variable": variable, "zone": zone.get("geometry") or zone["bbox"],
              "period": period, "metres": metres, "water": tile["sha256"] if tile else None}
    if tier1_only:
        fields["tier1_only"] = True
    key = fingerprint.sha(fields)
    if cached := store.get_derived(key):
        if "absent" in cached:  # every scene was read and none was usable: the same answer, without reading again
            return {**cached["absent"], "origin": "derived"}
        return {**cached["meta"], "data": pd.DataFrame(cached["rows"]), "origin": "derived"}
    if settings.load().offline:
        return absent("source_unreachable", "The aligned Landsat series is not cached.",
                      "La serie Landsat en rejilla común no está guardada en caché.", source)
    grid, inside = _grid(zone, metres)
    if grid is None:
        return absent("insufficient_qa", "The area cannot be represented on the requested Landsat grid.",
                      "La zona no se puede representar en la rejilla Landsat solicitada.", source)
    if tile:
        inside &= ~landsat._water(landsat.water_path(tile), CRS, grid["transform"], inside.shape)
    if not inside.any():
        return absent("no_coverage", "The requested grid has no land cells in this area.",
                      "La rejilla solicitada no tiene celdas terrestres en esta zona.", source)
    rows, left_out, failed = {}, 0, 0
    years = range(first, last + 1)
    searches = cancellation.map_items(lambda year: landsat._search(zone, year, period.get("months")), years,
                                      workers=landsat.SEARCH_WORKERS)
    with closing(searches):
        for year in years:
            try:
                items = next(searches)
            except Exception:  # the catalogue can be unavailable independently of scene pixels
                return absent("source_unreachable", f"Landsat scenes could not be listed for {year}.",
                              f"No se pudieron listar las escenas Landsat de {year}.", source, origin="remote")
            items = [item for item in items if period["start"] <= item.datetime.date().isoformat() <= period["end"]]
            if tier1_only:
                left_out += sum(landsat.tier(item.id) != "T1" for item in items)
                items = [item for item in items if landsat.tier(item.id) == "T1"]
            for row in cancellation.map_items(lambda item: _scene(item, grid, inside, variable), items,
                                             workers=landsat.SCENE_WORKERS):
                if row is FAILED:
                    failed += 1
                elif row and (row[:2] not in rows or row[3] > rows[row[:2]][3]):
                    rows[row[:2]] = row
    if not rows:
        result = absent("insufficient_qa", "No usable scenes were found on this grid in the requested period.",
                        "No se encontraron escenas útiles en esta rejilla y periodo.", source, origin="remote")
        # Saved under the key of the series, which holds the water tile: a tile downloaded later is a new key. Only
        # when every scene was read and no year can still grow; a scene that failed to read may be usable next time.
        if not failed and last < datetime.now(UTC).year:
            store.put_derived(key, {"absent": {name: value for name, value in result.items() if name != "origin"}})
        return result
    series = pd.DataFrame(sorted(rows.values()), columns=["t", "platform", "v", "clear", "item"])
    data = series[["t", "v"]]
    meta = {"absence": None, "support": support | {"cells": int(inside.sum())}, "unit": landsat.UNITS[variable],
            "coverage": {"valid_pct": float(100 * series["clear"].mean()), "n_dates": len(series)},
            "sources": [{**source, "n_items": len(series), "items": series["item"].head().tolist()}],
            "limits": [{"en": f"Scene pixels were aligned by nearest neighbour on a shared {metres} m equal-area grid.",
                        "es": f"Píxeles alineados por vecino más próximo en una rejilla equivalente de {metres} m."}],
            "input_fingerprint": key}
    meta["limits"].append(landsat.land_mask(tile)["detail"])
    if tier1_only:
        meta["limits"].append(landsat.tier1_limit(left_out))
    if tile:
        meta["sources"].append(landsat.water_source(tile))
    if last < datetime.now(UTC).year:
        store.put_derived(key, {"meta": meta, "rows": data.to_dict(orient="list")})
    return {**meta, "data": data, "origin": "remote"}
