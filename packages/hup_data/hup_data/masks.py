"""Masks from the prepared official layers: the part of a layer inside a zone, its surface, and the people in it.

The layer is read by window from its GeoParquet (readers/vectors.py). Surfaces are measured in a Lambert equal-area
projection centred on the zone, so hectares are true hectares anywhere in the world. A zone outside the layer is
`no_coverage`: the layer says nothing there, which is not the same as zero hectares. A zone inside the layer with no
feature in it is a mask of zero hectares. People come from the INE by census section, spread by residential built-up
surface at 100 m, when the zone lies within sections with an INE figure (readers/ine_population.py); otherwise from
GHSL, through the population reader, on its finest grid.
"""
import shapely
from pyproj import CRS
from shapely.geometry import box

from hup_data.readers import ine_population, population, vectors
from hup_data.readers.exposure import _polygon, _project

REPO_URL = "https://github.com/SpaceApps-Val-2026/Habitat-Under-Pressure"


def _local(geometry) -> CRS:
    centre = geometry.centroid
    return CRS.from_proj4(f"+proj=laea +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m")


def layer_source(key: str, entry: dict | None) -> dict:
    """Where a layer comes from: its source, the layer name, licence and the url of its package."""
    source, product = key.split("/", 1)
    ref = {"source": entry["source"] if entry else source, "product": product,
           "url": population._url(entry, REPO_URL) if entry else REPO_URL}
    ref.update({name: entry[name] for name in ("version", "license") if entry and entry.get(name)})
    return ref


def zone_mask(layer_key: str, zone: dict, where: dict | None = None) -> dict:
    """The part of layer `layer_key` inside `zone` (EPSG:4326), its surface in hectares and its share of the zone.

    `where` keeps only the features whose columns hold the given values, e.g. {"notation": "Flooded area"}; a column
    the layer lacks is `insufficient_qa`.

    Returns {"geometry", "hectares", "percent", "n_features", "repaired", "absence", "sources", "coverage", "limits"}.
    `coverage.valid_pct` is the share of the zone inside the extent of the layer; outside it nothing is known.
    """
    outline = population.zone_geometry(zone)
    found = vectors.read_layer(layer_key, list(outline.bounds))
    result = {"geometry": None, "hectares": None, "percent": None, "n_features": 0, "repaired": 0,
              "absence": found["absence"], "sources": [layer_source(layer_key, found["entry"])], "coverage": {},
              "limits": []}
    if found["absence"] is not None:
        return result
    extent = box(*found["entry"]["bbox"]) if found["entry"].get("bbox") else outline
    covered = outline.intersection(extent)
    metric = _local(outline)
    zone_m2 = float(_project(outline, metric).area)
    covered_pct = 100 * float(_project(covered, metric).area) / zone_m2
    if covered_pct <= 0:
        result["absence"] = {"reason": "no_coverage", "detail": {
            "en": "This layer does not cover the zone; it says nothing about it.",
            "es": "Esta capa no cubre la zona; no dice nada sobre ella."}}
        return result
    rows = found["data"]
    for column, value in (where or {}).items():
        if column not in rows:
            result["absence"] = {"reason": "insufficient_qa", "detail": {
                "en": f"The layer has no column {column} to select its features.",
                "es": f"La capa no tiene la columna {column} para elegir sus elementos."}}
            return result
        rows = rows[rows[column] == value]
    geometries = rows.geometry.dropna()
    invalid = ~geometries.is_valid
    geometries = [_polygon(shapely.make_valid(g)) if bad else _polygon(g) for g, bad in zip(geometries, invalid,
                                                                                            strict=True)]
    shapely.prepare(outline)
    parts = [part for part in (g.intersection(outline) for g in geometries if outline.intersects(g))
             if not part.is_empty and part.area > 0]
    mask = _polygon(shapely.union_all(parts)) if parts else shapely.Polygon()
    mask_m2 = float(_project(mask, metric).area) if parts else 0.0
    result.update(geometry=mask, hectares=mask_m2 / 1e4, percent=100 * mask_m2 / zone_m2, n_features=len(parts),
                  repaired=int(invalid.sum()), coverage={"valid_pct": round(min(covered_pct, 100.0), 2)})
    if covered_pct < 99.99:
        result["limits"].append({
            "en": f"The layer covers {covered_pct:.2f}% of the zone; the rest is unknown, not zero.",
            "es": f"La capa cubre el {covered_pct:.2f}% de la zona; el resto es desconocido, no cero."})
    return result


def population_in_mask(layer_key: str, zone: dict) -> dict:
    """People in the part of layer `layer_key` inside `zone`: INE by section where it covers the zone, else GHSL.

    The source is chosen with the zone, as `population.population_now` does. With the INE: figures of its latest year,
    spread inside each section by residential built-up surface at 100 m. With GHSL: the finest grid prepared here that
    covers the whole zone (100 m tiles, else 1 km), latest epoch. Returns the keys of zone_mask plus "population",
    "year", "support" (resolution_m and cells of the mask) and the sources of the layer and of the population.
    Absences keep both sources.
    """
    return count_people(zone_mask(layer_key, zone), zone)


def count_people(found: dict, zone: dict) -> dict:
    """People inside `found["geometry"]` (EPSG:4326, already cut to `zone`), chosen and counted as population_in_mask
    does. `found` needs the keys geometry, n_features, absence, sources and limits; it is updated and returned."""
    chosen = ine_population.choose(population.zone_geometry(zone))
    if chosen["ok"]:
        found.update(population=None, year=chosen["year"], support={}, sources=[*found["sources"], *chosen["sources"]])
        if found["absence"] is not None:
            return found
        counted = ine_population.count(found["geometry"], chosen)
        mask_m2 = population._project(found["geometry"], ine_population.MOLLWEIDE).area if found["n_features"] else 0
        found.update(population=counted["people"], support={"resolution_m": 100, "cells": round(mask_m2 / 1e4, 3)})
        found["limits"] += ine_population.limits(chosen, counted)
        return found
    grids = population.ghsl_grids()
    choice = population.ghsl_grid(population.zone_geometry(zone), grids)
    year = max(choice["epochs"], default=None)
    entries = choice["epochs"].get(year, [])
    ghsl = population.ghsl_source(choice["resolution_m"], entries)
    found.update(population=None, year=None, support={}, sources=[*found["sources"], ghsl])
    if found["absence"] is not None:
        return found
    if not grids:
        found["absence"] = {"reason": "needs_package", "detail": {
            "en": "The GHSL population grid has not been prepared on this computer.",
            "es": "La rejilla de población GHSL no está preparada en este ordenador."}}
        return found
    if year is None:
        tiles = ", ".join(choice["missing"] or [])
        found["absence"] = {"reason": "needs_package", "detail": {
            "en": f"The GHSL population tiles prepared here do not cover the zone; missing: {tiles}.",
            "es": f"Las teselas de población GHSL preparadas no cubren la zona; faltan: {tiles}."}} \
            if 1000 not in grids and tiles else {"reason": "no_coverage", "detail": {
                "en": "The mask lies partly or wholly outside the population grid.",
                "es": "La máscara queda en parte o del todo fuera de la rejilla de población."}}
        return found
    found["year"] = year
    if found["n_features"] == 0:
        found.update(population=0.0, support={"resolution_m": choice["resolution_m"], "cells": 0})
        return found
    counted = population.ghsl_count(found["geometry"], entries)
    found.update(population=counted["people"],
                 support={"resolution_m": choice["resolution_m"], "cells": round(counted["cells"], 3)})
    found["limits"] += population._limits(str(year), counted["cells"], counted["missing"]) + choice["limits"]
    if chosen["why"]:
        found["limits"].append(chosen["why"])
    return found
