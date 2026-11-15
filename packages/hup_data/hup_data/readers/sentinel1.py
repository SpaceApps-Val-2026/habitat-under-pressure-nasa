"""Sentinel-1 radar through Planetary Computer (RTC product, VV gamma naught in dB) and the water it shows.

`read_radar_pair(zone, before, after)` returns the zone on one metric grid at two passes of the same relative
orbit: backscatter changes with the viewing geometry, so two orbits cannot be compared. Each pass is mosaicked from
the scenes of that orbit on that day and kept as a COG in <data>/cache/sentinel-1-rtc/ with its manifest line;
later reads, also offline, use the file.

`radar_water_extent` (through `read_series`) is the share of the zone where VV backscatter falls below a threshold
on each of the two passes: a series of two rows. Calm water reflects the radar away from the satellite and reads
dark. The threshold is either given (`threshold_db`) or, by default, calibrated on known water: Otsu's (1979)
threshold between the pixels of the box that JRC Global Surface Water marks as permanent water and those it never saw
under water, both passes together and each class weighted the same (see `calibrate`). The same threshold serves both
passes, so that they can be compared. Otsu's separability (eta, between 0 and 1) travels with the result; nothing is
decided with it.

Limits that travel with the catalog: radar shadow behind relief and smooth dry surfaces (tarmac, bare fields) read
as dark as water; cities and vegetation hide water under them; flooded rice fields are water. Only the day of each
pass is seen. The grid is in a UTM zone, so `read_radar_pair` carries its CRS in `attrs["crs"]`: areas need metres.
"""
import json
from datetime import date as _date
from datetime import timedelta

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import Resampling, transform_geom

from hup_data import cancellation, fingerprint, manifest, paths, settings, store
from hup_data.readers import absent, landsat, register

SOURCE = "sentinel-1-rtc"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
URL = f"{STAC}/collections/{SOURCE}"
LICENSE = ("Contains modified Copernicus Sentinel data; Sentinel-1 RTC: Microsoft Planetary Computer, Catalyst, "
           "CC BY 4.0")
FIRST_YEAR = 2014  # Sentinel-1A, first scenes of the collection in October 2014
REPEAT_DAYS = 12  # repeat cycle of one Sentinel-1 satellite (ESA); with one satellite, the gap between passes
RESOLUTION = 20  # metres: what the exploration used; the product is 10 m, and averaging four pixels lowers speckle
HISTOGRAM_BINS = 256  # the grey levels of Otsu's paper
NODATA = -32768.0  # what the collection declares for its VV asset, used when a file declares nothing
GDAL = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "2", "GDAL_HTTP_RETRY_DELAY": "1",
        "GDAL_HTTP_TIMEOUT": "30", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tiff,.tif,.TIF"}  # RTC files end in .tiff
METHOD = {"en": "Otsu (1979) threshold between JRC permanent water and dry ground, both passes, equal weights",
          "es": "umbral de Otsu (1979) entre agua permanente y suelo seco de JRC, dos pasadas, mismo peso"}


def _source(items: list[str] | None = None, product: str = "Sentinel-1 RTC, VV gamma naught") -> dict:
    ref = {"source": SOURCE, "product": product, "url": URL, "license": LICENSE}
    return ref if items is None else {**ref, "items": items, "n_items": len(items)}


def grid_for(zone: dict, resolution: float = RESOLUTION) -> tuple[str, "rasterio.Affine", tuple[int, int]]:
    """A metric grid (UTM zone of the centre of the box) that covers the box of the zone, aligned to the resolution."""
    west, south, east, north = zone["bbox"]
    centre_lon, centre_lat = (west + east) / 2, (south + north) / 2
    crs = f"EPSG:{(326 if centre_lat >= 0 else 327) * 100 + int((centre_lon + 180) // 6) + 1}"
    to_grid = Transformer.from_crs(4326, crs, always_xy=True).transform
    xs, ys = zip(*[to_grid(x, y) for x in np.linspace(west, east, 11) for y in np.linspace(south, north, 11)],
                 strict=True)
    left, right = np.floor(min(xs) / resolution) * resolution, np.ceil(max(xs) / resolution) * resolution
    bottom, top = np.floor(min(ys) / resolution) * resolution, np.ceil(max(ys) / resolution) * resolution
    shape_ = (int(round((top - bottom) / resolution)), int(round((right - left) / resolution)))
    return crs, from_origin(float(left), float(top), resolution, resolution), shape_


def _passes(zone: dict, first: str, last: str) -> dict[int, dict[str, list]]:
    """Scenes with VV that touch the zone between two ISO days, by relative orbit and day (UTC).

    URLs are signed here, without an account, and used at once: they expire."""
    import planetary_computer
    import pystac_client

    cancellation.checkpoint()
    catalog = pystac_client.Client.open(STAC, modifier=planetary_computer.sign_inplace, timeout=30)
    found: dict[int, dict[str, list]] = {}
    for item in catalog.search(collections=[SOURCE], bbox=zone["bbox"], datetime=f"{first}/{last}").items():
        cancellation.checkpoint()
        if "vv" in item.assets:
            day = item.datetime.strftime("%Y-%m-%d")
            found.setdefault(int(item.properties["sat:relative_orbit"]), {}).setdefault(day, []).append(item)
    return found


def _mosaic(items: list, crs: str, transform, shape_: tuple[int, int]) -> np.ndarray:
    """VV backscatter of the scenes on the grid, in dB; NaN where no scene has a valid value. First scene wins."""
    from rasterio.vrt import WarpedVRT

    out = np.full(shape_, np.nan, dtype="float32")
    with rasterio.Env(**GDAL):
        for item in items:
            cancellation.checkpoint()
            with rasterio.open(item.assets["vv"].href) as source:
                nodata = source.nodata if source.nodata is not None else NODATA
                with WarpedVRT(source, crs=crs, transform=transform, width=shape_[1], height=shape_[0],
                               resampling=Resampling.average, src_nodata=nodata, nodata=nodata) as vrt:
                    band = vrt.read(1).astype("float32")
            band[(band == nodata) | ~(band > 0)] = np.nan  # gamma naught is a power: zero or less is no value
            out = np.where(np.isnan(out), band, out)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (10 * np.log10(out)).astype("float32")


def _stem(zone: dict) -> str:
    return fingerprint.zone_key(zone) if zone.get("id") else fingerprint.sha(zone.get("geometry") or zone["bbox"])[:16]


def _file(zone: dict, day: str, orbit: int | str, resolution: float):
    box_id = fingerprint.sha(zone["bbox"])[:8]  # the same id with another box is another crop
    return paths.cache() / SOURCE / _stem(zone) / f"{day}_o{orbit}_b{box_id}_r{resolution:g}.tif"


def _cached(zone: dict, resolution: float) -> dict[int, set[str]]:
    """Passes of this zone, box and resolution already in the cache: relative orbit → days."""
    pattern = _file(zone, "*", "*", resolution)
    found: dict[int, set[str]] = {}
    for file in pattern.parent.glob(pattern.name) if pattern.parent.is_dir() else []:
        day, orbit = file.name.split("_")[:2]
        found.setdefault(int(orbit[1:]), set()).add(day)
    return found


def _save(zone: dict, day: str, orbit: int, resolution: float, array: np.ndarray, crs: str, transform,
          items: list[str]) -> None:
    file = _file(zone, day, orbit, resolution)
    with MemoryFile() as memory:
        with memory.open(driver="COG", height=array.shape[0], width=array.shape[1], count=1, dtype="float32",
                         crs=crs, transform=transform, nodata=np.nan, BLOCKSIZE=512, COMPRESS="DEFLATE",
                         OVERVIEW_RESAMPLING="AVERAGE") as out:
            out.write(array, 1)
            out.update_tags(items=json.dumps(items), orbit=str(orbit), date=day, unit="dB")
        store.write_atomic(file, memory.read())
    valid = round(float(100 * np.isfinite(array).mean()), 1)
    manifest.record(file, source=SOURCE, item=f"{_stem(zone)}_{day}_o{orbit}", scope=_stem(zone), fmt="cog",
                    url=URL, license=LICENSE, tier="ready", bbox=zone["bbox"], period={"start": day, "end": day},
                    product=f"VV gamma naught in dB, {resolution:g} m, relative orbit {orbit}",
                    variables=["radar_water_extent"], quality={"valid_pct": valid})


def _load(zone: dict, day: str, orbit: int, resolution: float) -> tuple[np.ndarray, list[str]]:
    cancellation.checkpoint()
    with rasterio.open(_file(zone, day, orbit, resolution)) as source:
        return source.read(1), json.loads(source.tags()["items"])


def _choose(by_orbit: dict[int, dict[str, int]], windows, orbit: int | None) -> tuple[int, str, str] | None:
    """The pair to use: an orbit with a day in each window; the shortest gap between them, then the most scenes,
    then the lowest orbit number. `by_orbit` is orbit → day → number of scenes. None when no orbit has both."""
    options = []
    for number, days in by_orbit.items():
        if orbit is not None and number != orbit:
            continue
        before = sorted(day for day in days if windows[0][0] <= day <= windows[0][1])
        after = sorted(day for day in days if windows[1][0] <= day <= windows[1][1])
        if before and after and before[-1] < after[0]:
            gap = (_date.fromisoformat(after[0]) - _date.fromisoformat(before[-1])).days
            options.append((gap, -(days[before[-1]] + days[after[0]]), number, before[-1], after[0]))
    if not options:
        return None
    _, _, number, before, after = min(options)
    return number, before, after


def _windows(before: str, after: str, slack_days: int) -> tuple[tuple[str, str], tuple[str, str]]:
    first, last = _date.fromisoformat(before), _date.fromisoformat(after)
    return ((first - timedelta(days=slack_days)).isoformat(), before), (after, (last + timedelta(days=slack_days))
                                                                        .isoformat())


def read_radar_pair(zone: dict, before: str, after: str, orbit: int | None = None,
                    resolution: float = RESOLUTION, slack_days: int = 0) -> dict:
    """Radar over the zone at two passes of the same relative orbit, as a DataArray (phase, y, x) in dB.

    The 'before' pass is the last one on `before` or up to `slack_days` earlier; the 'after' pass is the first one on
    `after` or up to `slack_days` later (0: those very days). `orbit` fixes the relative orbit; without it, the
    orbit with the shortest gap between the two passes. Common reader shape: `data` is None with an `absence` when
    it cannot be read. Raises ValueError for a date that is not an ISO day.
    """
    first, last = _date.fromisoformat(before), _date.fromisoformat(after)
    if first >= last:
        return absent("not_computed", "The 'before' date must come earlier than the 'after' date.",
                      "La fecha «antes» debe ser anterior a la fecha «después».", _source())
    if last.year < FIRST_YEAR:
        return absent("no_coverage", f"Sentinel-1 starts in {FIRST_YEAR}; there is nothing for these dates.",
                      f"Sentinel-1 empieza en {FIRST_YEAR}; no hay nada para estas fechas.", _source())
    crs, transform, shape_ = grid_for(zone, resolution)
    windows = _windows(before, after, slack_days)
    asked = (f" (orbit {orbit} requested)." if orbit is not None else ".",
             f" (órbita {orbit} pedida)." if orbit is not None else ".")
    remote = False

    cached = _choose({number: dict.fromkeys(days, 0) for number, days in _cached(zone, resolution).items()},
                     windows, orbit)
    if cached:
        chosen, *days = cached
        loaded = [_load(zone, day, chosen, resolution) for day in days]
    else:
        if settings.load().offline:
            return absent("source_unreachable", "Offline, and this zone has not been read for these dates before.",
                          "Sin red, y esta zona no se había leído antes para estas fechas.", _source())
        try:
            found = _passes(zone, windows[0][0], windows[1][1])
        except Exception:  # network or signing: the reason is the same for the person
            return absent("source_unreachable", "The radar catalogue could not be reached.",
                          "No se ha podido consultar el catálogo del radar.", _source(), origin="remote")
        pick = _choose({number: {day: len(items) for day, items in days.items()} for number, days in found.items()},
                       windows, orbit)
        if pick is None:
            return absent("no_coverage", "No relative orbit has radar scenes over this zone at both dates" + asked[0],
                          "Ninguna órbita relativa tiene escenas de radar sobre esta zona en las dos fechas" + asked[1],
                          _source(), origin="remote")
        chosen, *days = pick
        try:
            arrays = list(cancellation.map_items(lambda day: _mosaic(found[chosen][day], crs, transform, shape_),
                                                 days, workers=2))
        except Exception:
            return absent("source_unreachable", "The radar scenes could not be read.",
                          "No se han podido leer las escenas de radar.", _source(), origin="remote")
        names = [[item.id for item in found[chosen][day]] for day in days]
        if any(not np.isfinite(array).any() for array in arrays):
            return absent("insufficient_qa", "The scenes hold no valid radar value over this zone.",
                          "Las escenas no tienen ningún valor de radar válido sobre esta zona.",
                          _source(names[0] + names[1]), origin="remote")
        for day, array, ids in zip(days, arrays, names, strict=True):
            _save(zone, day, chosen, resolution, array, crs, transform, ids)
        loaded = list(zip(arrays, names, strict=True))
        remote = True

    stack = np.stack([array for array, _ in loaded])
    ids = loaded[0][1] + loaded[1][1]
    rows = transform.f + transform.e * (np.arange(shape_[0]) + 0.5)
    cols = transform.c + transform.a * (np.arange(shape_[1]) + 0.5)
    data = xr.DataArray(stack, dims=("phase", "y", "x"), coords={"phase": ["before", "after"], "y": rows, "x": cols},
                        attrs={"orbit": chosen, "crs": crs, "transform": tuple(transform)[:6], "dates": list(days),
                               "polarisation": "VV", "unit": "dB"})
    both = np.isfinite(stack).all(axis=0)
    return {"data": data, "absence": None, "origin": "remote" if remote else "cache", "sources": [_source(ids)],
            "support": {"resolution_m": resolution, "since": FIRST_YEAR, "revisit_days": REPEAT_DAYS},
            "coverage": {"valid_pct": round(float(100 * both.mean()), 1), "n_dates": 2}, "unit": "dB",
            "input_fingerprint": fingerprint.sha({"source": SOURCE, "zone": _stem(zone), "bbox": zone["bbox"],
                                                  "orbit": chosen, "dates": list(days), "resolution": resolution})}


# ---------------------------------------------------------------- water below a threshold
def otsu(values: np.ndarray, bins: int = HISTOGRAM_BINS, weights: np.ndarray | None = None) -> tuple[float, float]:
    """Otsu's threshold of the finite values and his separability eta (between-class over total variance, 0 to 1).

    `weights` (one per value) let two samples of different size count the same. Otsu, N. (1979), A threshold
    selection method from gray-level histograms, IEEE Transactions on Systems, Man, and Cybernetics 9(1), 62-66,
    https://doi.org/10.1109/TSMC.1979.4310076. Raises ValueError with fewer than two different values.
    """
    keep = np.isfinite(values)
    values = values[keep]
    if values.size < 2 or values.min() == values.max():
        raise ValueError("Otsu's threshold needs at least two different values")
    counts, edges = np.histogram(values, bins=bins, weights=None if weights is None else weights[keep])
    p = counts / counts.sum()
    centres = (edges[:-1] + edges[1:]) / 2
    omega = np.cumsum(p)
    mu = np.cumsum(p * centres)
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu_t * omega - mu) ** 2 / (omega * (1 - omega))
    between[~np.isfinite(between)] = 0
    k = int(np.argmax(between))
    total = float(np.sum(p * (centres - mu_t) ** 2))
    return float(edges[k + 1]), (float(between[k] / total) if total > 0 else 0.0)


def zone_pixels(zone: dict, crs: str, transform, shape_: tuple[int, int]) -> np.ndarray:
    """Pixels of the grid whose centre falls inside the outline of the zone (its box when it has none)."""
    if not zone.get("geometry"):
        west, south, east, north = zone["bbox"]
        outline = {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north], [west, north],
                                                        [west, south]]]}
    else:
        outline = zone["geometry"]
    return geometry_mask([transform_geom("EPSG:4326", crs, outline)], shape_, transform, invert=True)


def _occurrence(path: str, crs: str, transform, shape_: tuple[int, int]) -> np.ndarray:
    """JRC water occurrence (per cent, 255 no data) on the radar grid, by nearest neighbour."""
    from rasterio.vrt import WarpedVRT

    with rasterio.open(path) as tile, WarpedVRT(tile, crs=crs, transform=transform, width=shape_[1],
                                                 height=shape_[0], resampling=Resampling.nearest) as warped:
        return warped.read(1)


def calibrate(values: np.ndarray, occurrence: np.ndarray) -> dict:
    """Otsu's threshold of two known classes in the box, weighted to count the same: permanent water (JRC occurrence
    of PERMANENT_WATER per cent or more) and ground never seen under water (occurrence 0), both passes together.

    Without the weights the threshold follows the larger class: over l'Horta Sud, where water is about 2 % of the
    pixels, Otsu over the whole zone splits bright towns from fields (-5.9 dB) and calls 80 % of it water.
    Raises ValueError when either class has fewer pixels than the histogram has bins.
    """
    water = (occurrence >= landsat.PERMANENT_WATER) & (occurrence <= 100)
    dry = occurrence == 0
    samples = [values[:, mask].ravel() for mask in (water, dry)]
    samples = [sample[np.isfinite(sample)] for sample in samples]
    if min(sample.size for sample in samples) < HISTOGRAM_BINS:
        raise ValueError("too few pixels of permanent water or of dry ground in the box")
    weights = np.concatenate([np.full(sample.size, 1 / sample.size) for sample in samples])
    threshold, eta = otsu(np.concatenate(samples), weights=weights)
    return {"threshold_db": threshold, "eta": eta, "water_pixels": int(samples[0].size),
            "dry_pixels": int(samples[1].size)}


def detect_water(zone: dict, before: str, after: str, threshold_db: float | None = None,
                 orbit: int | None = None, resolution: float = RESOLUTION, slack_days: int = 0) -> dict:
    """Water at the two passes of `read_radar_pair`: pixels inside the zone with VV below the threshold.

    Without `threshold_db`, the threshold comes from `calibrate` over the box of the zone, which needs a downloaded
    JRC occurrence tile that holds the zone. Common reader shape, with `data` a boolean DataArray (phase, y, x) that is
    False outside the zone and where the radar has no value, plus `detection`: threshold_db, method, eta and the
    pixels it was calibrated on (only when calibrated), orbit, dates, pixels inside the zone, valid pixels and water
    pixels at each pass, new water (after, not before) and pixel area in km².
    """
    radar = read_radar_pair(zone, before, after, orbit, resolution, slack_days)
    if radar["absence"]:
        return {**radar, "detection": None}
    backscatter = radar["data"]
    sources = [radar["sources"][0]]
    crs, transform, shape_ = grid_for(zone, resolution)
    inside = zone_pixels(zone, crs, transform, shape_)
    values = backscatter.values
    valid = np.isfinite(values) & inside
    if not valid.any(axis=(1, 2)).all():
        return {**absent("insufficient_qa", "The radar has no valid value inside the outline of the zone.",
                         "El radar no tiene ningún valor válido dentro del contorno de la zona.", sources[0],
                         radar["origin"]), "detection": None}
    calibration = {"eta": None}
    if threshold_db is None:
        tile = landsat.water_tile(zone)
        if tile is None:
            return {**absent("needs_package", "The water threshold is calibrated on JRC Global Surface Water, and no "
                             "downloaded tile holds this zone.", "El umbral del agua se calibra con JRC Global Surface "
                             "Water, y ninguna tesela descargada contiene esta zona.", sources[0], radar["origin"]),
                    "detection": None}
        sources.append(landsat.water_source(tile))
        try:
            calibration = calibrate(values, _occurrence(landsat.water_path(tile), crs, transform, shape_))
        except ValueError:
            return {**absent("not_computed", "The box of the zone holds too little permanent water or dry ground to "
                             "calibrate the threshold; give one.", "La caja de la zona tiene muy poca agua permanente "
                             "o suelo seco para calibrar el umbral; hay que darlo.", sources[0], radar["origin"]),
                    "detection": None}
        threshold_db = calibration.pop("threshold_db")
        method = METHOD
    else:
        method = {"en": "fixed threshold given with the request", "es": "umbral fijo dado en la petición"}
    water = valid & (values < threshold_db)
    pixel_km2 = resolution ** 2 / 1e6
    detection = {"threshold_db": round(float(threshold_db), 2), "method": method, **calibration,
                 "orbit": backscatter.attrs["orbit"], "dates": backscatter.attrs["dates"], "crs": crs,
                 "pixel_km2": pixel_km2, "inside_pixels": int(inside.sum()),
                 "valid_pixels": [int(n) for n in valid.sum(axis=(1, 2))],
                 "water_pixels": [int(n) for n in water.sum(axis=(1, 2))],
                 "new_water_pixels": int((water[1] & ~water[0] & valid[0]).sum())}
    if detection["eta"] is not None:
        detection["eta"] = round(detection["eta"], 3)
    product = (f"Sentinel-1 RTC, VV gamma naught; water below {detection['threshold_db']} dB "
               f"({method['en']}), relative orbit {detection['orbit']}")
    data = xr.DataArray(water, dims=backscatter.dims, coords=backscatter.coords,
                        attrs={**backscatter.attrs, "unit": "1", "threshold_db": detection["threshold_db"]})
    return {**radar, "data": data, "sources": [{**sources[0], "product": product}, *sources[1:]],
            "coverage": {"valid_pct": round(float(100 * valid.all(axis=0).sum() / max(inside.sum(), 1)), 1),
                         "n_dates": 2}, "unit": "1", "detection": detection}


@register("1", "radar_water_extent")
def read(variable: str, zone: dict, period: dict, threshold_db: float | None = None) -> dict:
    """Share of the zone with water at the pass on or before `period.start` and the one on or after `period.end`.

    Each pass may be up to one repeat cycle minus a day away from its date, so that any period finds the passes
    around an event. Rows: t (day of the pass), v (water pixels over valid pixels inside the zone), n_valid (valid
    pixels over pixels inside the zone, per cent). The threshold, its method and the orbit go in the source product.
    """
    found = detect_water(zone, period["start"], period["end"], threshold_db, slack_days=REPEAT_DAYS - 1)
    if found["absence"]:
        found.pop("detection", None)
        return found
    detection = found.pop("detection")
    rows = [{"t": day, "v": round(water / valid, 5), "n_valid": round(100 * valid / detection["inside_pixels"], 1)}
            for day, water, valid in zip(detection["dates"], detection["water_pixels"], detection["valid_pixels"],
                                         strict=True)]
    cells = detection["inside_pixels"]
    return {**found, "data": pd.DataFrame(rows, columns=["t", "v", "n_valid"]),
            "support": {**found["support"], "cells": cells}, "detection": detection}
