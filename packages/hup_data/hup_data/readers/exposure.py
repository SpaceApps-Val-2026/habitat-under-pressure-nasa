"""Align a catalogued flood map to a prepared population grid in an equal-area CRS."""

from fnmatch import fnmatchcase
from functools import partial
from math import isfinite

import pandas as pd
from numpy import column_stack
from numpy.ma import is_masked
from pyproj import CRS, Transformer
from rasterio.errors import WindowError
from rasterio.features import geometry_window
from shapely import box, prepare, transform, union_all
from shapely.geometry import Polygon

from hup_data import cancellation, paths
from hup_data.readers import vectors


def _project(geometry, crs):
    projector = Transformer.from_crs(4326, crs, always_xy=True)
    return transform(geometry, lambda xy: column_stack(projector.transform(*xy.T)))


def _polygon(geometry):
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if geometry.geom_type == "GeometryCollection":
        return union_all([_polygon(part) for part in geometry.geoms])
    return Polygon()


def _union(key, bbox, filters):
    layer = vectors.read_layer(key, bbox)
    if layer["absence"]:
        if layer["absence"]["reason"] == "no_coverage":
            return Polygon(), 0
        return None, 0
    rows = layer["data"]
    for field, value in filters.items():
        if field not in rows:
            return None, 0
        rows = rows[rows[field] == value]
    rows = rows.geometry.dropna()
    invalid = ~rows.is_valid
    fixed = rows.copy()
    fixed.loc[invalid] = rows.loc[invalid].make_valid()
    return union_all([_polygon(geometry) for geometry in fixed]), int(invalid.sum())


def _mask(spec, zone, bbox, crs):
    masks, covers, used, repairs = [], [], [], 0
    source = spec["source"]
    index = vectors.layers(source)
    matches = [
        {key for key in index if fnmatchcase(key.removeprefix(source + "/"), pattern)}
        for pattern in spec["masks"]
    ]
    covered = spec.get("coverage")
    if covered and not all(matches):
        return None
    if covered:
        scope, fixed = _union(covered, bbox, spec.get("coverage_filter", {}))
        if scope is None:
            return None
        covers.append(_project(scope, crs))
        repairs += fixed
    for key in sorted(set().union(*matches)):
        if not (paths.data_dir() / index[key]["path"]).is_file():
            if covered:
                return None
            continue
        if not covered:
            prefix = key.split("__")[0] + "__"
            aoi = next((k for k in index if k.startswith(prefix) and "_areaOfInterestA_" in k), None)
            image = next((k for k in index if k.startswith(prefix) and "_imageFootprintA_" in k), None)
            if aoi is None or image is None:
                continue
            aoi_geom, aoi_repaired = _union(aoi, bbox, {})
            image_geom, image_repaired = _union(image, bbox, {})
            if aoi_geom is None or image_geom is None:
                continue
            coverage = zone.intersection(_project(aoi_geom.intersection(image_geom), crs))
            if coverage.area == 0:
                continue
            repairs += aoi_repaired + image_repaired
        flooded, fixed = _union(key, bbox, spec.get("mask_filter", {}))
        if flooded is None:
            return None
        if covered:
            masks.append(_project(flooded, crs))
        else:
            covers.append(coverage)
            masks.append(coverage.intersection(_project(flooded, crs)))
        used.append(key.rsplit("/", 1)[-1])
        repairs += fixed
    if not used:
        return None
    footprint = zone.intersection(union_all(covers))
    if footprint.area <= 0:
        return None
    return footprint.intersection(union_all(masks)), 100 * footprint.area / zone.area, used, repairs


def _population(raster, flood):
    pixel_area = abs(raster.transform.a * raster.transform.e)
    area_tolerance = pixel_area * 1e-8  # Ignore floating-point slivers along transformed pixel edges.
    if flood.area <= area_tolerance:
        return [0.0], 0
    bounds = box(*raster.bounds)
    if flood.difference(bounds).area > area_tolerance:
        return None, 0
    flood = flood.intersection(bounds)
    try:
        window = geometry_window(raster, [flood])
    except WindowError:
        return None, 0
    values = raster.read(1, window=window, masked=True)
    affine = raster.window_transform(window)
    prepare(flood)
    populations = []
    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            pixel = box(*(affine @ (col, row)), *(affine @ (col + 1, row + 1)))
            if not flood.intersects(pixel):
                continue
            overlap = pixel_area if flood.covers(pixel) else flood.intersection(pixel).area
            if overlap <= area_tolerance:
                continue
            value = values[row, col]
            if is_masked(value) or not isfinite(float(value)) or value < 0:
                return None, len(populations)
            populations.append(float(value) * overlap / pixel.area)
    return populations or [0.0], len(populations)


def read_exposure(params, zone, period):
    from hup_data.readers import absent, ine_population, population

    year = params["grid"]["year"]
    candidates = [
        spec for spec in params["flood_layers"]
        if spec["start"] <= period["end"] and spec.get("end", "9999-12-31") >= period["start"]
    ]
    geom = population.zone_geometry(zone)
    crs = CRS.from_epsg(6933)
    area = _project(geom, crs)
    chosen = []
    for spec in candidates:
        cancellation.checkpoint()
        if chosen and (spec["source"], spec["kind"], spec["start"], spec.get("end")) != chosen[0][0]:
            continue
        if candidate := _mask(spec, area, zone["bbox"], crs):
            group = (spec["source"], spec["kind"], spec["start"], spec.get("end"))
            chosen.append((group, spec, candidate))

    pop = {"source": params["grid"]["source"], "url": params["grid"]["url"]}
    if chosen:
        census = ine_population.choose(geom)
        if census["ok"]:
            sources = census["sources"]
            choice = {"resolution_m": 100, "limits": []}
            count = partial(_count_ine, census=census)
        else:
            grids = {size: {year: epochs[year]} for size, epochs in population.ghsl_grids().items() if year in epochs}
            choice = population.ghsl_grid(geom, grids)
            if census.get("why"):
                choice["limits"].append(census["why"])
            if not (entries := choice["epochs"].get(year)):
                lookup = absent("needs_package", "The prepared GHSL epoch does not cover this area.",
                                "La época preparada de GHSL no cubre esta zona.", pop)
                return {**lookup, "limits": choice["limits"]}
            sources = [population.ghsl_source(choice["resolution_m"], entries)]
            count = partial(_count_ghsl, entries=entries, year=year)
        results = {}
        for _, spec, (flood, coverage, items, repairs) in chosen:
            category = spec.get("category", "exposure")
            results[category] = _exposure_result(spec, flood, crs, coverage, items, repairs, choice, sources, count)
        first = next(iter(results.values()))
        return {**first, "categories": results} if any(key != "exposure" for key in results) else first
    missing = absent("no_coverage", "No catalogued flood map covers this place and period.",
                     "Ningún mapa de inundación del catálogo cubre este lugar y periodo.", pop)
    return {**missing, "sources": [pop, *({"source": spec["source"], "url": spec["url"]}
                                         for spec in params["flood_layers"])]}


CLASS_LABELS = {
    "flooded_area": {"en": "Mapped flooded area", "es": "Área inundada cartografiada"},
    "flood_trace": {"en": "Mapped flood traces", "es": "Huellas de inundación cartografiadas"},
}


def _count_ine(geometry, census):
    from hup_data.readers import ine_population

    counts = ine_population.count(geometry, census)
    return {**counts, "cells": counts["sections"], "missing": 0.0,
            "limits": ine_population.limits(census, counts)}


def _count_ghsl(geometry, entries, year):
    from hup_data.readers import population

    counts = population.ghsl_count(geometry, entries)
    return {**counts, "limits": [{
        "en": f"Population estimate for {year}, distributed uniformly within each grid cell.",
        "es": f"Población estimada para {year}, repartida uniformemente dentro de cada celda.",
    }]}


def _exposure_result(spec, flood, crs, coverage, items, repairs, choice, population_sources, count):
    from hup_data.readers import absent

    source = {"source": spec["source"], "url": spec["url"], "items": items, "n_items": len(items)}
    if spec.get("version"):
        source["version"] = spec["version"]
    refs = [*population_sources, source]
    if spec.get("coverage"):
        refs.append({"source": spec["coverage"].split("/", 1)[0], "url": spec["coverage_url"]})
    label = CLASS_LABELS.get(spec.get("category"))
    if spec["kind"] == "observed":
        map_limit = {"en": f"Observed extent for the event dated {spec['start']}. The map reflects the source images.",
                     "es": f"Extensión observada del episodio del {spec['start']}. "
                           "El mapa refleja las imágenes de origen."}
    else:
        years = spec["return_years"]
        map_limit = {"en": f"Flood hazard for a return period of {years} years.",
                     "es": f"Peligrosidad de inundación para un periodo de retorno de {years} años."}
    limits = [map_limit, *choice["limits"]]
    if label:
        limits += [label, {
            "en": "This emergency map captures little of the built-up area. Flooded areas and traces are separate.",
            "es": "Este mapa de emergencia recoge poco casco urbano. Separa las áreas inundadas y las huellas.",
        }]
        if spec["category"] == "flood_trace":
            limits.append({"en": "Traces indicate signs left by the flood.",
                           "es": "Las huellas indican señales que dejó la inundación."})
    if coverage < 99.99:
        limits.append({"en": f"Only mapped areas enter the estimate ({coverage:.2f}% of the requested area).",
                       "es": f"Solo se estima en la zona cartografiada ({coverage:.2f}% de la zona solicitada)."})
    metadata = {"sources": refs, "limits": limits, "support": {"resolution_m": choice["resolution_m"]},
                "coverage": {"valid_pct": min(100, coverage)}}
    empty = flood.is_empty or flood.area <= 1e-6
    if empty:
        if coverage < 99.99:
            missing = absent("no_coverage", "No polygons in this category were found in the partially mapped area.",
                             "No hay polígonos de esta categoría en la zona cartografiada parcialmente.", source)
            return {**missing, **metadata}
        flood = Polygon()
    back = Transformer.from_crs(crs, 4326, always_xy=True)
    counts = count(transform(flood, lambda xy: column_stack(back.transform(*xy.T))))
    limits.extend(counts["limits"])
    if (not empty and counts["cells"] == 0) or counts["missing"] > 1e-9:
        missing = absent("insufficient_qa", "Population data is missing for part of the mapped area.",
                         "Faltan datos de población en parte de la zona cartografiada.", population_sources[0])
        return {**missing, **metadata}
    return {**metadata, "absence": None, "origin": "package", "unit": "people",
            "data": pd.DataFrame({"population": [counts["people"]], "detected": [counts["cells"] > 0]}),
            "diagnostics": {"repaired_geometries": repairs, "coverage_pct": min(100, coverage),
                            **({"census_sections": counts["sections"]} if "sections" in counts
                               else {"raster_cells": counts["cells"]})}}
