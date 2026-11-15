"""Copernicus HRL Imperviousness Density at 10 m (EPSG:3035): the share of the zone's soil that is sealed, by year.

Each 10 m pixel holds the percentage of its ground that is sealed (0 to 100); 255 is outside the mapped area (the
raster attribute table of the EEA image service), as is the nodata the file declares. The zone is reprojected to the
grid, never the grid to the zone. Pixels wholly inside the zone count in full and pixels on its edge by the share of
their area inside it; LAEA (EPSG:3035) is equal-area, so those shares are true areas.

Files are found from the manifest, for any year registered under the source: a loose GeoTIFF (format cog), or a ZIP
of EEA tiles whose GeoTIFF members `python -m hup_data.prepare population` has unpacked into ready/grids. The year
comes from the manifest line: its period, or else the year written in its version.
"""
import re

import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window, rasterize
from shapely.geometry import box, shape

from hup_data import cancellation, paths
from hup_data.prepare.common import manifest_lines, read_index
from hup_data.readers import absent, register
from hup_data.readers.built_up import _equal_area, _project

SOURCE = "copernicus-imperviousness"
URL = "https://land.copernicus.eu/en/products/high-resolution-layer-imperviousness"
REFERENCE_YEARS = (2018, 2021, 2024)  # the 10 m Imperviousness Density campaigns (CLMS product page)
OUTSIDE = 255  # "outside area" in the raster attribute table of the EEA image service
DOI = {2018: "10.2909/3bf542bd-eebd-4d73-b53c-a0243f2ed862",  # 10 m rasters, from each CLMS product page
       2021: "10.2909/34ef6334-d432-4041-a3da-67e156d6501d",
       2024: "10.2909/f0bcb6f0-d775-4218-bec1-13adea01ff8f"}
YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def _source(items: list[str] | None = None, years: list[int] | None = None) -> dict:
    ref = {"source": SOURCE, "product": "HRL Imperviousness Density, 10 m", "url": URL,
           "license": "Copernicus Land Monitoring Service data policy: free, full and open access"}
    if years:  # the source reference has no DOI field: the DOI of each year goes in the version
        ref["version"] = "; ".join(f"{year} (DOI {DOI[year]})" if year in DOI else str(year) for year in years)
    return ref if items is None else {**ref, "items": items, "n_items": len(items)}


def _year(line: dict) -> int | None:
    if line.get("period"):
        return int(line["period"]["start"][:4])
    match = YEAR.search(line.get("version") or "")
    return int(match.group(1)) if match else None


def _files() -> tuple[dict[int, list[str]], dict[int | None, list[str]]]:
    """GeoTIFFs by year, and the registered files that cannot be read (ZIP without an unpacked GeoTIFF, no year)."""
    unpacked: dict[str, list[str]] = {}
    for entry in read_index("grids").values():
        if entry.get("source") == SOURCE and entry["path"].lower().endswith(".tif") \
                and (paths.data_dir() / entry["path"]).is_file():
            unpacked.setdefault(entry.get("from"), []).append(entry["path"])
    found: dict[int, list[str]] = {}
    unreadable: dict[int | None, list[str]] = {}
    for line in manifest_lines(sources={SOURCE}):
        year = _year(line)
        if line["format"] == "cog" and line["path"].lower().endswith((".tif", ".tiff")):
            tifs = [line["path"]]
        elif line["format"] == "zip":
            tifs = unpacked.get(line["path"], [])
        else:
            tifs = []
        if year is None or not tifs:
            unreadable.setdefault(year, []).append(line["path"])
        else:
            found.setdefault(year, []).extend(tifs)
    return {year: sorted(set(files)) for year, files in found.items()}, unreadable


def pixel_sums(raster, area) -> dict:
    """Sealed m², valid area, no-data area and pixels of `area` (raster CRS) inside one raster.

    Pixels wholly inside count in full, edge pixels by the share of their area inside. Raises ValueError on a value
    above 100 that is not no data.
    """
    sums = {"sealed_m2": 0.0, "valid_m2": 0.0, "nodata_m2": 0.0, "pixels": 0}
    area = area.intersection(box(*raster.bounds))
    if area.is_empty or area.area == 0:
        return sums
    try:
        window = geometry_window(raster, [area])
    except WindowError:
        return sums
    values = raster.read(1, window=window)
    affine = raster.window_transform(window)
    cell_area = abs(affine.a * affine.e)
    touched = geometry_mask([area], values.shape, affine, all_touched=True, invert=True)
    edge = rasterize([(area.boundary, 1)], values.shape, transform=affine, all_touched=True).astype(bool) & touched
    overlap = np.where(touched, cell_area, 0.0)
    rows, cols = np.nonzero(edge)
    xa, ya = affine.c + cols * affine.a, affine.f + rows * affine.e
    xb, yb = xa + affine.a, ya + affine.e
    cells = shapely.box(np.minimum(xa, xb), np.minimum(ya, yb), np.maximum(xa, xb), np.maximum(ya, yb))
    shapely.prepare(area)
    overlap[rows, cols] = shapely.area(shapely.intersection(cells, area))
    overlap[overlap <= cell_area * 1e-9] = 0.0  # floating-point slivers along pixel edges
    nodata = (values == OUTSIDE) | ((values == raster.nodata) if raster.nodata is not None else False)
    inside = overlap > 0
    if (values[inside & ~nodata] > 100).any():
        raise ValueError("value above 100 %")
    valid = inside & ~nodata
    return {"sealed_m2": float((values[valid] * overlap[valid]).sum() / 100),
            "valid_m2": float(overlap[valid].sum()), "nodata_m2": float(overlap[inside & nodata].sum()),
            "pixels": int(valid.sum())}


def _year_sums(files: list[str], geometry) -> dict | str:
    """Sums of one year over its files, or the reason it cannot be read: "outside", "projection" or "values"."""
    totals = {"sealed_m2": 0.0, "valid_m2": 0.0, "nodata_m2": 0.0, "pixels": 0}
    footprint, area = None, None
    for path in files:
        cancellation.checkpoint()
        with rasterio.open(paths.data_dir() / path) as raster:
            if not _equal_area(raster):
                return "projection"
            area = _project(geometry, raster.crs)
            frame = box(*raster.bounds)
            share = area if footprint is None else area.difference(footprint)  # overlapping files count once
            footprint = frame if footprint is None else footprint.union(frame)
            try:
                sums = pixel_sums(raster, share)
            except ValueError:
                return "values"
        for name in totals:
            totals[name] += sums[name]
    if area.difference(footprint).area > 1e-6 * area.area:
        return "outside"
    return {**totals, "zone_m2": area.area}


@register("1", "sealed_surface")
def read(variable: str, zone: dict, period: dict) -> dict:
    first, last = int(period["start"][:4]), int(period["end"][:4])
    files, unreadable = _files()
    years = sorted({*REFERENCE_YEARS, *files, *(year for year in unreadable if year)})
    wanted = [year for year in years if first <= year <= last]
    if not wanted:
        return absent("no_coverage", "Copernicus Imperviousness Density at 10 m has the reference years "
                      f"{', '.join(map(str, years))}; none falls in this period.",
                      "La impermeabilidad de Copernicus a 10 m tiene los años de referencia "
                      f"{', '.join(map(str, years))}; ninguno cae en este periodo.", _source(), origin="package")
    geometry = shapely.make_valid(shape(zone["geometry"])) if zone.get("geometry") else box(*zone["bbox"])
    rows, items, outside, failed = [], [], [], {}
    for year in wanted:
        if year not in files:
            continue
        sums = _year_sums(files[year], geometry)
        if sums == "outside":
            outside.append(year)
        elif isinstance(sums, str):
            failed[year] = sums
        elif sums["valid_m2"] <= 0:
            failed[year] = "nodata"
        else:
            rows.append({"t": f"{year}-01-01", "v": sums["sealed_m2"] / sums["valid_m2"],
                         "surface_m2": sums["sealed_m2"], "zone_area_m2": sums["zone_m2"],
                         "valid_area_m2": sums["valid_m2"], "n_valid": sums["pixels"]})
            items += [path.rsplit("/", 1)[-1].removesuffix(".tif") for path in files[year]]
    if not rows:
        if failed:
            year, why = next(iter(failed.items()))
            detail = {"projection": ("the grid is not in an equal-area, north-up projection",
                                     "la rejilla no está en una proyección de área equivalente orientada al norte"),
                      "values": ("the grid has values above 100 % that are not no data",
                                 "la rejilla tiene valores por encima del 100 % que no son sin dato"),
                      "nodata": ("the zone falls only on pixels without data (for example, open sea)",
                                 "la zona cae solo en píxeles sin dato (por ejemplo, mar abierto)")}[why]
            return absent("insufficient_qa", f"Imperviousness {year}: {detail[0]}.",
                          f"Impermeabilidad de {year}: {detail[1]}.", _source(), origin="package")
        if outside and not any(_year_sums(group, geometry) != "outside" for group in files.values()):
            return absent("no_coverage", "The zone is outside the imperviousness data prepared here (10 m, "
                          "Europe, downloaded for Sagunt, Valencia, l'Horta Sud and Alzira).",
                          "La zona queda fuera de los datos de impermeabilidad preparados aquí (10 m, Europa, "
                          "descargados para Sagunt, Valencia, l'Horta Sud y Alzira).", _source(), origin="package")
        zipped = [path for year in wanted for path in unreadable.get(year, [])]
        if zipped:
            return absent("needs_package", "Imperviousness files are registered for this period but no GeoTIFF "
                          f"could be read from them ({', '.join(zipped)}): run `python -m hup_data.prepare "
                          "population` to unpack them; if they hold no .tif, their layout must be checked first.",
                          "Hay archivos de impermeabilidad registrados para este periodo, pero no se ha podido leer "
                          f"ningún GeoTIFF de ellos ({', '.join(zipped)}): ejecuta `python -m hup_data.prepare "
                          "population` para desempaquetarlos; si no contienen ningún .tif, hay que revisar antes "
                          "su estructura.", _source(), origin="package")
        return absent("needs_package", "No Copernicus imperviousness file covering the zone is prepared for "
                      f"{', '.join(map(str, wanted))}.",
                      "No hay preparado ningún archivo de impermeabilidad de Copernicus que cubra la zona para "
                      f"{', '.join(map(str, wanted))}.", _source(), origin="package")
    table = pd.DataFrame(rows)
    zone_area = float(table["zone_area_m2"].iloc[0])
    valid_pct = round(100 * float(table["valid_area_m2"].min()) / zone_area, 2)
    present = [int(t[:4]) for t in table["t"]]
    limits = [
        {"en": "Share of the ground covered by sealed surfaces (buildings, roads, car parks, paved yards), from the "
               "percentage of each 10 m pixel estimated from Sentinel-2 images, averaged over the area with data. "
               "It does not tell one kind of sealing from another.",
         "es": "Fracción del suelo cubierta por superficies selladas (edificios, viales, aparcamientos, patios "
               "pavimentados), a partir del porcentaje de cada píxel de 10 m estimado con imágenes de Sentinel-2, "
               "promediado sobre el área con dato. No distingue un tipo de sellado de otro."},
        {"en": "One map per three-year campaign (2018 covers images of 2017 to 2019); it does not show what changed "
               "between campaigns.",
         "es": "Un mapa por campaña trienal (2018 reúne imágenes de 2017 a 2019); no muestra lo que cambió entre "
               "campañas."},
    ]
    if len(present) > 1:
        limits.append({
            "en": "CLMS warns that the difference between two status maps also carries technical change (method, "
                  "variability and noise of the images); its own change layers for 2018-2021 and 2021-2024 are at "
                  "20 m and 100 m.",
            "es": "CLMS advierte de que la diferencia entre dos mapas de estado también recoge cambio técnico "
                  "(método, variabilidad y ruido de las imágenes); sus capas de cambio 2018-2021 y 2021-2024 son "
                  "de 20 m y 100 m."})
    if any(year in (2021, 2024) for year in present):
        limits.append({"en": "CLMS lists the 2021 and 2024 maps as pending validation.",
                       "es": "CLMS da los mapas de 2021 y 2024 como pendientes de validación."})
    if valid_pct < 99.99:
        limits.append({"en": f"Only {valid_pct}% of the zone has data; pixels outside the mapped area, such as open "
                             "sea, are left out.",
                       "es": f"Solo el {valid_pct}% de la zona tiene dato; se dejan fuera los píxeles fuera del área "
                             "cartografiada, como el mar abierto."})
    skipped = [year for year in wanted if year not in present]
    if skipped:
        listed = ", ".join(map(str, skipped))
        limits.append({"en": f"Years without a file covering the zone on this computer: {listed}.",
                       "es": f"Años sin archivo que cubra la zona en este ordenador: {listed}."})
    return {"data": table, "absence": None, "origin": "package", "sources": [_source(sorted(set(items)), present)],
            "support": {"resolution_m": 10, "since": 2018, "cells": round(zone_area / 100, 2)},
            "coverage": {"valid_pct": valid_pct, "n_dates": len(table)},
            "unit": "1", "limits": limits, "input_fingerprint": None}
