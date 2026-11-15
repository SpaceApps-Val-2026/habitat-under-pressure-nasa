"""Landsat 8 and 9, Collection 2 Level-2, through Planetary Computer: greenness (NDVI) and surface temperature.

For each scene only the window of the zone is read. Clouds, shadows and fill are masked with the quality band
before anything is averaged, the product scale and offset are applied, and one scene is kept per date and
satellite (neighbouring scenes overlap and would count a date twice). Both variables come from the same pass.
Each year of a zone is computed once and kept in <data>/cache/landsat-c2-l2/ with its manifest line.

For the map, `scene_table`, `pixel_grid` and `composite` give the same usable scenes as per-pixel medians on a
grid in EPSG:4326, with the same mask and scale (hup_data.layers draws them).

Limits that travel with the catalog: surface temperature is the temperature of the ground at about 10:30, not
of the air. By default, water that the quality band of a scene misses enters the median on a coast. With
`land_only=True` (`read`, `scene_table`) or a `water` tile (`composite`), permanent water from JRC Global Surface
Water (occurrence of at least PERMANENT_WATER per cent) is also left out, through the same `_water` mask for the
zone medians and for the map pixels, when a downloaded tile covers the zone; without one they read as before and
say so (`land_mask`).

By default only Tier 1 scenes count (`tier1_only=True` in `read`, `scene_table` and `landsat_grid.read`; Guillem,
decision 1 of A10, 2026-11-14); `tier1_only=False` lets every tier of Collection 2 in (T1, T2 and RT). USGS keeps
in T1 the scenes "suitable for time-series analysis" and sends to T2 those that miss its geometry because of
"significant cloud cover, insufficient ground control". Over Valencia every T2 scene read so far is a cloud deck
that QA_PIXEL flags as snow, and snow is "clear" in that band (bit 6 only looks at the cloud bits), so it entered the
medians at -47 to +4 °C (.dev/notes/2026-11-14-traspaso-
landsat-tier2.md). The cache keeps every tier; the filter is applied on reading and declared in `limits`.
"""
import csv
import io
import math
import threading
import warnings
from contextlib import closing, contextmanager
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds, transform_geom
from rasterio.windows import from_bounds

from hup_data import cancellation, fingerprint, manifest, paths, settings, store
from hup_data.prepare.common import manifest_lines
from hup_data.readers import absent, register

SOURCE = "landsat-c2-l2"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
URL = f"{STAC}/collections/{SOURCE}"
LICENSE = "Public domain; courtesy of the U.S. Geological Survey"
FIRST_YEAR = 2013
MAX_SCENE_CLOUD = 40  # per cent of the whole scene; scenes above it are not even opened
MIN_CLEAR = 0.8  # share of the zone that must be clear for a scene to count
COLUMNS = ["t", "platform", "item", "ndvi", "lst", "clear_pct", "cloud_pct"]
UNITS = {"ndvi": "1", "lst": "°C"}
WATER_SOURCE = "jrc-gsw"
WATER_URL = "https://global-surface-water.appspot.com/download"
PERMANENT_WATER = 90  # JRC occurrence, per cent of the valid observations of 1984-2021 in which the pixel was water
RESOLUTION = {"ndvi": 30, "lst": 100}
GDAL = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "2", "GDAL_HTTP_RETRY_DELAY": "1",
        "GDAL_HTTP_TIMEOUT": "30", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".TIF,.tif"}
SEARCH_WORKERS = 3
SCENE_WORKERS = 8
STAC_TIMEOUT = 30


def tier(item_id: str) -> str:
    """The Collection 2 tier of a scene from its identifier: T1, T2 or RT (the last field of the id)."""
    return item_id.rsplit("_", 1)[-1]


def tier1_limit(left_out: int) -> dict:
    """What `tier1_only` leaves out, in en/es, for the `limits` of a result."""
    return {"en": f"Only Landsat Collection 2 Tier 1 scenes (USGS: suitable for time series); {left_out} Tier 2 or "
                  "Real-Time scene(s) of this zone and period left out.",
            "es": f"Solo escenas Landsat Colección 2 de nivel T1 (USGS: aptas para series temporales); {left_out} "
                  "escena(s) T2 o RT de esta zona y periodo fuera."}


def _source(n_items: int | None = None) -> dict:
    ref = {"source": SOURCE, "product": SOURCE, "version": "02", "url": URL, "license": LICENSE}
    return ref if n_items is None else {**ref, "n_items": n_items}


def _search(zone: dict, year: int, months: list[int] | None) -> list:
    """Scenes of one year that touch the zone. URLs are signed here and used at once: they expire."""
    import planetary_computer
    import pystac_client

    cancellation.checkpoint()
    catalog = pystac_client.Client.open(STAC, modifier=planetary_computer.sign_inplace, timeout=STAC_TIMEOUT)
    found = catalog.search(
        collections=[SOURCE], bbox=zone["bbox"], datetime=f"{year}-01-01/{year}-12-31",
        query={"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD}, "platform": {"in": ["landsat-8", "landsat-9"]}},
    )
    items = []
    for item in found.items():
        cancellation.checkpoint()
        if not months or item.datetime.month in months:
            items.append(item)
    return items


def water_tile(zone: dict) -> dict | None:
    """The manifest line of a downloaded JRC occurrence tile that holds the whole zone, or None.

    When no single tile holds it but several of the same version do together (a zone across 0° or 40° N), the
    answer is one mosaic of them: the first line with `tiles` (all of them), the items joined with "+" and a
    checksum of their checksums. `water_path` turns it into a virtual raster, so every reader takes it as one tile.
    """
    import shapely
    from shapely.geometry import box

    west, south, east, north = zone["bbox"]
    wanted, near = box(west, south, east, north), []
    for line in manifest_lines(sources={WATER_SOURCE}):
        cancellation.checkpoint()
        if line.get("product") != "occurrence":
            continue
        with rasterio.open(paths.data_dir() / line["path"]) as tile:
            left, bottom, right, top = tile.bounds
            grid = (str(tile.crs), tile.res)
        if left <= west and right >= east and bottom <= south and top >= north:
            return line
        if box(left, bottom, right, top).intersects(wanted):
            near.append((line, (left, bottom, right, top), grid))
    for version in sorted({line["version"] for line, _, _ in near}):
        same = [entry for entry in near if entry[0]["version"] == version]
        same = [entry for entry in same if entry[2] == same[0][2]]  # one grid: JRC tiles share it
        if len(same) > 1 and shapely.union_all([box(*bounds) for _, bounds, _ in same]).covers(wanted):
            lines = [line for line, _, _ in same]
            return {**lines[0], "item": "+".join(line["item"] for line in lines), "tiles": lines,
                    "sha256": fingerprint.sha([line["sha256"] for line in lines]), "path": None}
    return None


def _mosaic(tiles: list[dict]) -> str:
    """A GDAL virtual raster (its XML, which GDAL opens as a file name) joining JRC tiles on their common grid."""
    opened = []
    for line in tiles:
        path = paths.data_dir() / line["path"]
        with rasterio.open(path) as tile:
            opened.append((path, tile.bounds, tile.res, tile.width, tile.height, tile.crs))
    size_x, size_y = opened[0][2]
    west, north = min(item[1].left for item in opened), max(item[1].top for item in opened)
    width = round((max(item[1].right for item in opened) - west) / size_x)
    height = round((north - min(item[1].bottom for item in opened)) / size_y)
    sources = "".join(
        f'<SimpleSource><SourceFilename relativeToVRT="0">{path.resolve().as_posix()}</SourceFilename>'
        f'<SourceBand>1</SourceBand><SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>'
        f'<DstRect xOff="{round((bounds.left - west) / size_x)}" yOff="{round((north - bounds.top) / size_y)}" '
        f'xSize="{w}" ySize="{h}"/></SimpleSource>'
        for path, bounds, _, w, h, _ in opened)
    return (f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}"><SRS>{opened[0][5].to_wkt()}</SRS>'
            f"<GeoTransform>{west!r}, {size_x!r}, 0, {north!r}, 0, {-size_y!r}</GeoTransform>"
            f'<VRTRasterBand dataType="Byte" band="1">{sources}</VRTRasterBand></VRTDataset>')


def water_path(tile: dict | None) -> str | None:
    """The local file of a tile from `water_tile` (for a mosaic, its virtual raster), or None."""
    if tile and tile.get("tiles"):
        return _mosaic(tile["tiles"])
    return str(paths.data_dir() / tile["path"]) if tile else None


def water_source(tile: dict) -> dict:
    """The source entry of the JRC tile (or tiles) used as land mask."""
    items = [line["item"] for line in tile.get("tiles") or [tile]]
    url = WATER_URL if tile.get("tiles") else tile["ingest"].get("url", WATER_URL)
    return {"source": WATER_SOURCE, "product": "occurrence", "version": tile["version"], "items": items,
            "url": url, "license": tile["license"]}


def _water(path: str, crs, transform, shape: tuple[int, int]) -> np.ndarray:
    """Permanent water on a grid: the JRC tile taken to it by nearest neighbour. The one land mask of this module,
    used by the zone medians (`_window`) and by the map pixels (`scene_pixels`). Outside the tile: not water."""
    with rasterio.open(path) as tile, WarpedVRT(tile, crs=crs, transform=transform, width=shape[1],
                                                 height=shape[0], resampling=Resampling.nearest) as warped:
        occurrence = warped.read(1)
    return (occurrence >= PERMANENT_WATER) & (occurrence <= 100)  # 255 is no data


def _window(href: str, zone: dict, water: str | None = None):
    """The pixels of one band over the zone, and which of them fall inside its outline (and on land, with `water`)."""
    cancellation.checkpoint()
    with rasterio.open(href) as band:
        bounds = transform_bounds("EPSG:4326", band.crs, *zone["bbox"], densify_pts=21)
        window = from_bounds(*bounds, transform=band.transform).round_offsets().round_lengths()
        values = band.read(1, window=window, boundless=True, fill_value=0)
        if zone.get("geometry"):
            outline = transform_geom("EPSG:4326", band.crs, zone["geometry"])
            transform = band.window_transform(window)
            inside = geometry_mask([outline], values.shape, transform, invert=True, all_touched=True)
        else:
            inside = np.ones(values.shape, dtype=bool)
        if water:
            inside &= ~_water(water, band.crs, band.window_transform(window), values.shape)
    return values, inside


def _clear(quality: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """Pixels that can be used. Bit 6 set: clear. Bit 7 set: water. Values 0 and 1: no data or fill."""
    quality = quality.astype(np.uint16)
    return ((quality >> 6) & 1 == 1) & ((quality >> 7) & 1 == 0) & (quality > 1) & inside


def _physical(clear: np.ndarray, red, infrared, thermal) -> tuple[np.ndarray, np.ndarray]:
    """NDVI and surface temperature (°C) of each clear pixel, NaN elsewhere. Scale and offset of the product."""
    red_r, infrared_r = red * 2.75e-5 - 0.2, infrared * 2.75e-5 - 0.2
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(clear & (red > 0) & (infrared > 0), (infrared_r - red_r) / (infrared_r + red_r), np.nan)
    kelvin_to_celsius = 273.15
    lst = np.where(clear & (thermal > 0), thermal * 0.00341802 + 149.0 - kelvin_to_celsius, np.nan)
    return ndvi, lst


def _scene(item, zone: dict, water: str | None = None) -> dict | None:
    """One scene reduced to the zone: its two medians and how much of the zone was clear. None if unusable."""
    with rasterio.Env(**GDAL):
        quality, inside = _window(item.assets["qa_pixel"].href, zone, water)
        if not inside.any():
            return None
        clear = _clear(quality, inside)
        clear_share = float(clear.sum() / inside.sum())
        if clear_share < MIN_CLEAR:
            return None
        red, _ = _window(item.assets["red"].href, zone)
        infrared, _ = _window(item.assets["nir08"].href, zone)
        thermal, _ = _window(item.assets["lwir11"].href, zone)
    ndvi, lst = _physical(clear, red, infrared, thermal)
    if not np.isfinite(ndvi).any() or not np.isfinite(lst).any():
        return None
    return {"t": item.datetime.strftime("%Y-%m-%d"), "platform": item.properties["platform"], "item": item.id,
            "ndvi": round(float(np.nanmedian(ndvi)), 5), "lst": round(float(np.nanmedian(lst)), 3),
            "clear_pct": round(100 * clear_share, 1), "cloud_pct": round(float(item.properties["eo:cloud_cover"]), 1),
            "pixels": int(inside.sum())}


def _year_file(zone, year, months, water):
    key = fingerprint.zone_key(zone) if zone.get("id") else fingerprint.sha(zone.get("geometry") or zone["bbox"])[:16]
    suffix = "all" if not months else "m" + "-".join(f"{month:02d}" for month in sorted(months))
    suffix += "_land" if water else ""
    return paths.cache() / SOURCE / key / f"{year}_{suffix}.csv"


def _cached_year(zone, year, months, water):
    exact = _year_file(zone, year, months, water)
    if exact.is_file():
        return exact
    if months:
        full_year = _year_file(zone, year, None, water)
        if full_year.is_file():
            return full_year
    return None


_year_locks: dict = {}
_year_locks_guard = threading.Lock()


@contextmanager
def _year_lock(zone, year, months, water):
    """One reduction at a time per year file. NDVI and LST steps of one plan run at once and need the same file
    (both variables come from one pass): the second waits and then finds it cached instead of reading every scene
    again. The current year is never cached, so nothing is gained by waiting for it."""
    if year >= datetime.now(UTC).year:
        yield
        return
    with _year_locks_guard:
        lock = _year_locks.setdefault(_year_file(zone, year, months, water), threading.Lock())
    while not lock.acquire(timeout=0.2):
        cancellation.checkpoint()
    try:
        yield
    finally:
        lock.release()


def _year(zone: dict, year: int, months: list[int] | None, water: str | None, items: list | None):
    """Usable scenes of one year for the zone: cached, or reduced from the listed scenes and cached."""
    cancellation.checkpoint()
    with _year_lock(zone, year, months, water):
        return _year_unlocked(zone, year, months, water, items)


def _year_unlocked(zone, year, months, water, items):
    if cached := _cached_year(zone, year, months, water):
        table = pd.read_csv(cached, dtype={"t": str})
        if months:
            table = table[table["t"].str[5:7].isin({f"{month:02d}" for month in months})]
        return table, "cache", 0
    if items is None:
        return None, "cache", 0
    scenes = [scene for scene in cancellation.map_items(lambda item: _scene(item, zone, water), items,
                                                       workers=SCENE_WORKERS) if scene]
    # One scene per date and satellite: the clearest one.
    best: dict[tuple[str, str], dict] = {}
    for scene in scenes:
        slot = (scene["t"], scene["platform"])
        if slot not in best or scene["clear_pct"] > best[slot]["clear_pct"]:
            best[slot] = scene
    rows = sorted(best.values(), key=lambda scene: (scene["t"], scene["platform"]))
    pixels = rows[0]["pixels"] if rows else 0
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    if year < datetime.now(UTC).year:  # the current year is still growing: it is read again next time
        file = _year_file(zone, year, months, water)
        store.write_atomic(file, buffer.getvalue().encode("utf-8"))
        months_text = "all months" if not months else "months " + ", ".join(str(month) for month in sorted(months))
        key = file.parent.name
        manifest.record(file, source=SOURCE, item=f"{key}_{file.stem}", scope=key, fmt="csv", url=URL,
                        license=LICENSE, product=f"zone medians, {months_text}", version="02", tier="ready",
                        variables=["ndvi", "lst"], bbox=zone["bbox"],
                        period={"start": f"{year}-01-01", "end": f"{year}-12-31"})
    return pd.read_csv(io.StringIO(buffer.getvalue()), dtype={"t": str}), "remote", pixels


def _tables(zone, period, first, last, water):
    """Check the local cache first and overlap catalogue searches for missing years with scene reads."""
    months = period.get("months")
    years = range(first, last + 1)
    missing = set()
    if not settings.load().offline:
        for year in years:
            cancellation.checkpoint()
            if _cached_year(zone, year, months, water) is None:
                missing.add(year)
    tables, origins, pixels = [], set(), 0
    with closing(cancellation.map_items(lambda year: _search(zone, year, months), sorted(missing),
                                        workers=SEARCH_WORKERS)) as searches:
        for year in years:
            cancellation.checkpoint()
            try:
                items = next(searches) if year in missing else None
                table, origin, seen = _year(zone, year, months, water, items)
            except Exception:  # network, signing or a broken scene: the reason is the same for the person
                return absent("source_unreachable", f"Landsat could not be read for {year}.",
                              f"No se ha podido leer Landsat para {year}.", _source(), origin="remote")
            if table is None:
                return absent("source_unreachable", f"Offline, and {year} has not been read for this zone before.",
                              f"Sin red, y {year} no se había leído antes para esta zona.", _source())
            tables.append(table)
            origins.add(origin)
            pixels = pixels or seen
    return pd.concat(tables, ignore_index=True), "remote" if "remote" in origins else "cache", pixels


@register("2", "ndvi", "lst")  # 2: Tier 1 only by default (before, every tier)
def read(variable: str, zone: dict, period: dict, land_only: bool = False, tier1_only: bool = True) -> dict:
    """Zone medians by date. `land_only` leaves out permanent water where a JRC tile covers the zone (see above);
    the result says whether it did (`land_mask`), also when it is an absence. `tier1_only` (the default) keeps
    only Tier 1 scenes and says how many it left out (`limits`); with False every tier enters and no limit is added."""
    tile = water_tile(zone) if land_only else None
    result = _medians(variable, zone, period, water_path(tile), tier1_only)
    if land_only:
        result["land_mask"] = land_mask(tile)
        if tile:
            result["sources"].append(water_source(tile))
    return result


def _medians(variable: str, zone: dict, period: dict, water: str | None, tier1_only: bool = False) -> dict:
    first, last = max(int(period["start"][:4]), FIRST_YEAR), min(int(period["end"][:4]), datetime.now(UTC).year)
    if first > last:
        return absent("no_coverage", f"Landsat 8 and 9 start in {FIRST_YEAR}; there is nothing for this period.",
                      f"Landsat 8 y 9 empiezan en {FIRST_YEAR}; no hay nada para este periodo.", _source())
    got = _tables(zone, period, first, last, water)
    if isinstance(got, dict):
        return got
    scenes, origin, pixels = got
    scenes = scenes[(scenes.t >= period["start"]) & (scenes.t <= period["end"])].dropna(subset=[variable])
    left_out = 0
    if tier1_only:
        kept = scenes["item"].map(tier) == "T1"
        left_out, scenes = int((~kept).sum()), scenes[kept]
    if scenes.empty:
        return absent("insufficient_qa", "No scene was clear enough over this zone in this period.",
                      "Ninguna escena estaba lo bastante despejada sobre esta zona en este periodo.", _source(0))
    data = scenes.rename(columns={variable: "v", "clear_pct": "n_valid"})[["t", "v", "n_valid"]].reset_index(drop=True)
    support = {"resolution_m": RESOLUTION[variable], "since": FIRST_YEAR, "revisit_days": 8}
    if pixels:
        support["cells"] = round(pixels * 900 / RESOLUTION[variable] ** 2, 1)
    source = {**_source(len(data)), "items": scenes["item"].tolist()[:5]}
    result = {"data": data, "absence": None, "origin": origin,
              "sources": [source], "support": support,
              "coverage": {"valid_pct": round(float(data.n_valid.mean()), 1), "n_dates": len(data)},
              "unit": UNITS[variable], "input_fingerprint": None}
    if tier1_only:
        result["limits"] = [tier1_limit(left_out)]
    return result


def land_mask(tile: dict | None) -> dict:
    """Whether permanent water was left out, and why, in en/es. Only when it was asked for."""
    if tile:
        return {"applied": True, "detail": {
            "en": f"Permanent water left out: JRC Global Surface Water occurrence of {PERMANENT_WATER}% or more.",
            "es": f"Sin agua permanente: ocurrencia de JRC Global Surface Water del {PERMANENT_WATER} % o más."}}
    return {"applied": False, "detail": {
        "en": "No JRC Global Surface Water tile covers this zone here: water the scenes miss may enter the median.",
        "es": "Ninguna tesela de JRC Global Surface Water cubre esta zona aquí: el agua que las escenas no marcan "
              "puede entrar en la mediana."}}


# ---------------------------------------------------------------- pixels for the map (A11b)
# The figures above reduce each scene to zone medians. The map needs the pixels: the same usable scenes (the ones
# the cache above lists for the zone), the same quality mask and the same scale and offset, warped onto one grid
# in EPSG:4326 and reduced to a per-pixel median. Nothing here changes what `read` returns.
PIXEL_M = 30
MAX_PIXELS = 600_000  # about 775 x 775: a municipality and its surroundings at 30 m; larger zones are coarser
METRES_PER_DEGREE = 111_320


def scene_table(zone: dict, period: dict, land_only: bool = False, tier1_only: bool = True) -> dict:
    """The usable scenes of a zone and period, one per date and satellite, as the reader keeps them in its cache.

    Common reader shape: `data` is the cached table (t, platform, item, ndvi, lst, clear_pct, cloud_pct), or None
    with the same absences as `read` (before 2013, offline with a year never read, unreachable). With `land_only`,
    the scenes chosen with permanent water left out, as `read(..., land_only=True)` keeps them; with `tier1_only`
    (the default), only the Tier 1 ones, as `read` keeps them.
    """
    water = water_path(water_tile(zone)) if land_only else None
    first, last = max(int(period["start"][:4]), FIRST_YEAR), min(int(period["end"][:4]), datetime.now(UTC).year)
    if first > last:
        return absent("no_coverage", f"Landsat 8 and 9 start in {FIRST_YEAR}; there is nothing for this period.",
                      f"Landsat 8 y 9 empiezan en {FIRST_YEAR}; no hay nada para este periodo.", _source())
    got = _tables(zone, period, first, last, water)
    if isinstance(got, dict):
        return got
    scenes, origin, _ = got
    scenes = scenes[(scenes.t >= period["start"]) & (scenes.t <= period["end"])].reset_index(drop=True)
    if tier1_only:
        scenes = scenes[scenes["item"].map(tier) == "T1"].reset_index(drop=True)
    return {"data": scenes, "absence": None, "origin": origin,
            "sources": [_source(len(scenes))], "support": {}, "coverage": {}, "unit": None, "input_fingerprint": None}


def pixel_grid(bbox: list[float], margin_km: float) -> dict:
    """The output grid: the bbox plus `margin_km` on every side, 30 m pixels, coarser when it would be too large.

    Returns the affine `transform` (north-up, EPSG:4326), `width`, `height`, `bounds` and `pixel_m`, the size of a
    pixel in metres (30, or more when the zone is large).
    """
    west, south, east, north = bbox
    shrink = math.cos(math.radians((south + north) / 2))
    dlat = margin_km * 1000 / METRES_PER_DEGREE
    dlon = dlat / shrink
    west, south, east, north = (round(west - dlon, 6), round(south - dlat, 6), round(east + dlon, 6),
                                round(north + dlat, 6))
    factor = 1.0
    while True:
        ry = PIXEL_M * factor / METRES_PER_DEGREE
        rx = ry / shrink
        width, height = math.ceil((east - west) / rx), math.ceil((north - south) / ry)
        if width * height <= MAX_PIXELS:
            break
        factor *= max(1.01, math.sqrt(width * height / MAX_PIXELS))
    bounds = [west, round(north - height * ry, 6), round(west + width * rx, 6), north]
    return {"transform": from_origin(west, north, rx, ry), "width": width, "height": height, "bounds": bounds,
            "pixel_m": round(PIXEL_M * factor)}


def _items(ids: list[str]) -> list:
    """Scenes by identifier, signed now: the signed URLs expire and are never kept."""
    import planetary_computer
    import pystac_client

    cancellation.checkpoint()
    catalog = pystac_client.Client.open(STAC, modifier=planetary_computer.sign_inplace, timeout=STAC_TIMEOUT)
    found = {}
    for item in catalog.search(collections=[SOURCE], ids=ids).items():
        cancellation.checkpoint()
        found[item.id] = item
    return [found[name] for name in ids if name in found]


def _warped(href: str, grid: dict) -> np.ndarray:
    """One band on the output grid. Nearest neighbour: quality bits must never be averaged. Outside the scene: 0."""
    cancellation.checkpoint()
    with rasterio.open(href) as band, WarpedVRT(band, crs="EPSG:4326", transform=grid["transform"],
                                                width=grid["width"], height=grid["height"],
                                                resampling=Resampling.nearest, nodata=0) as vrt:
        return vrt.read(1)


def scene_pixels(item, grid: dict, water: str | None = None) -> dict | None:
    """One scene on the output grid: NDVI and surface temperature per pixel, NaN where it is not clear.

    The same mask and the same scale as the zone medians of `read`; with `water` (a JRC tile from `water_path`),
    also without permanent water, as `read(..., land_only=True)`. None when no pixel of the grid is clear.
    """
    with rasterio.Env(**GDAL):
        quality = _warped(item.assets["qa_pixel"].href, grid)
        land = np.ones(quality.shape, dtype=bool)
        if water:
            land &= ~_water(water, "EPSG:4326", grid["transform"], quality.shape)
        clear = _clear(quality, land)
        if not clear.any():
            return None
        red = _warped(item.assets["red"].href, grid)
        infrared = _warped(item.assets["nir08"].href, grid)
        thermal = _warped(item.assets["lwir11"].href, grid)
    ndvi, lst = _physical(clear, red, infrared, thermal)
    return {"ndvi": ndvi.astype("float32"), "lst": lst.astype("float32")}


def composite(ids: list[str], grid: dict, water: str | None = None) -> dict:
    """Per-pixel median of the clear observations of these scenes, for both variables in one pass.

    {"ndvi": array, "lst": array, "n_dates": scenes that contributed, "items": their ids, "failed": scenes that
    could not be read}.
    Arrays are NaN where no scene was clear. Signing the scenes may raise when the network is down.
    """
    items = _items(ids)

    def one(item):
        try:
            return scene_pixels(item, grid, water)
        except Exception:  # one broken or unreachable scene leaves the others
            return "failed"

    got = list(cancellation.map_items(one, items, workers=SCENE_WORKERS))
    failed = sum(1 for scene in got if scene == "failed") + len(ids) - len(items)
    scenes = [scene for scene in got if isinstance(scene, dict)]
    result = {"n_dates": len(scenes), "failed": failed,
              "items": [item.id for item, scene in zip(items, got, strict=True) if isinstance(scene, dict)]}
    for variable in UNITS:
        if not scenes:
            result[variable] = None
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels stay NaN
            result[variable] = np.nanmedian(np.stack([scene[variable] for scene in scenes]), axis=0)
    return result
