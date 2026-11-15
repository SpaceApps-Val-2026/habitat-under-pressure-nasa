"""MUR SST v4.1: sea surface temperature, daily, about 1 km, read from its public Zarr on AWS Open Data (no account).

A land zone holds no sea, so the series is read over the sea next to it: the open-sea cells of the product's own
mask whose centre lies within STRIP_KM of the zone outline. STRIP_KM is the width of the territorial sea (12 nautical
miles, UNCLOS art. 3), measured here from the zone outline, not from the legal baselines. A zone with no open-sea
cell within COAST_KM of its outline does not reach the sea: absence `no_coverage`. Lakes (mask 5 and 13) never count.

The Zarr is chunked for time series: one file holds every day of the record for 100 x 100 cells (1 degree). Each
file is fetched once, kept as is in <data>/cache/mur-sst/zarr/ with its manifest line, and decoded here. This copy
of the record ends on 2020-01-20 (6443 days from 2002-06-01). The reader returns the monthly mean of the daily
means of the strip, only for months whose days are all in the record.
"""
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests
import shapely

from hup_data import cancellation, manifest, paths, settings, store
from hup_data.masks import _local
from hup_data.readers import absent, register
from hup_data.readers.exposure import _project
from hup_data.readers.population import zone_geometry

SOURCE = "mur-sst"
VARIABLE = "sea_surface_temperature"
STORE = "https://mur-sst.s3.us-west-2.amazonaws.com/zarr"
DOI = "https://doi.org/10.5067/GHGMR-4FJ04"
PRODUCT = "MUR-JPL-L4-GLOB-v4.1"
LICENSE = "EOSDIS data policy (open, no restriction); cite DOI 10.5067/GHGMR-4FJ04"
FIRST_DAY, N_DAYS = date(2002, 6, 1), 6443  # time is "days since 2002-06-01 09:00", one step per day, no gaps
CELL, FIRST_LAT, FIRST_LON, CHUNK, N_LAT, N_LON = 0.01, -89.99, -179.99, 100, 17999, 36000
SCALE, OFFSET, FILL = 0.001, 298.15 - 273.15, -32768  # analysed_sst is int16 in kelvin; returned in °C
STRIP_KM = 22.224  # 12 nautical miles
COAST_KM = 2.0  # two cells: the product's coastline may sit a cell away from the zone outline


def last_day() -> date:
    return FIRST_DAY + timedelta(days=N_DAYS - 1)


def _source(n_items: int | None = None) -> dict:
    ref = {"source": SOURCE, "product": PRODUCT, "version": "4.1", "url": DOI, "license": LICENSE}
    return ref if n_items is None else {**ref, "n_items": n_items}


def _download(url: str) -> bytes:
    """One request, one retry: a 28 MB chunk may be cut on a slow line."""
    for attempt in (0, 1):
        cancellation.checkpoint()
        try:
            response = requests.get(url, timeout=300)
            response.raise_for_status()
            return response.content
        except requests.RequestException:
            if attempt:
                raise
    raise AssertionError("unreachable")


def _chunk(name: str, row: int, col: int) -> tuple[np.ndarray | None, str]:
    """All days of one 100 x 100 chunk of `name` (analysed_sst or mask): from the cache, or fetched once and kept."""
    from numcodecs import Blosc

    cancellation.checkpoint()
    key = f"{name}/0.{row}.{col}"
    file = paths.cache() / SOURCE / "zarr" / key
    origin = "cache"
    if not file.is_file():
        if settings.load().offline:
            return None, "cache"
        url = f"{STORE}/{key}"
        store.write_atomic(file, _download(url))
        south, west = FIRST_LAT + row * CHUNK * CELL, FIRST_LON + col * CHUNK * CELL
        manifest.record(file, source=SOURCE, item=key, scope="global", fmt="zarr", url=url, license=LICENSE,
                        product=PRODUCT, version="4.1", tier="raw",
                        variables=[VARIABLE] if name == "analysed_sst" else None,
                        bbox=[round(west - CELL / 2, 3), round(south - CELL / 2, 3),
                              round(west + (CHUNK - 0.5) * CELL, 3), round(south + (CHUNK - 0.5) * CELL, 3)],
                        period={"start": FIRST_DAY.isoformat(), "end": last_day().isoformat()})
        origin = "remote"
    dtype = "<i2" if name == "analysed_sst" else "i1"
    return np.frombuffer(Blosc().decode(file.read_bytes()), dtype).reshape(-1, CHUNK, CHUNK), origin


def _index(value: float, first: float, size: int) -> int:
    return int(min(max(round((value - first) / CELL), 0), size - 1))


def strip(zone: dict) -> tuple[dict[tuple[int, int], np.ndarray] | None, bool, set[str]]:
    """The sea next to the zone: per chunk, which cells are open sea within STRIP_KM of the outline.

    Returns ({(row, col): boolean 100 x 100}, reaches_sea, origins), or (None, False, origins) offline with a mask
    chunk missing. Only chunks with at least one cell of the strip are kept.
    """
    outline = zone_geometry(zone)
    crs = _local(outline)
    local = _project(outline, crs)
    wide, near = local.buffer(STRIP_KM * 1000), local.buffer(COAST_KM * 1000)
    shapely.prepare(wide)
    shapely.prepare(near)
    west, south, east, north = outline.bounds
    margin_lat = STRIP_KM / 111.0 + CELL
    margin_lon = margin_lat / max(np.cos(np.radians(max(abs(south), abs(north)) + margin_lat)), 0.05)
    rows = range(_index(south - margin_lat, FIRST_LAT, N_LAT) // CHUNK, _index(north + margin_lat, FIRST_LAT, N_LAT)
                 // CHUNK + 1)
    cols = range(_index(west - margin_lon, FIRST_LON, N_LON) // CHUNK, _index(east + margin_lon, FIRST_LON, N_LON)
                 // CHUNK + 1)
    from pyproj import Transformer

    projector = Transformer.from_crs(4326, crs, always_xy=True)
    cells, reaches, origins = {}, False, set()
    for row in rows:
        for col in cols:
            mask, origin = _chunk("mask", row, col)
            if mask is None:
                return None, False, origins
            origins.add(origin)
            flags = mask[0]  # the land and lake flags do not change with time; only the ice bit does
            sea = ((flags & 1) == 1) & ((flags & 2) == 0) & ((flags & 4) == 0)
            if not sea.any():
                continue
            lats = FIRST_LAT + (row * CHUNK + np.arange(CHUNK)) * CELL
            lons = FIRST_LON + (col * CHUNK + np.arange(CHUNK)) * CELL
            lon, lat = np.meshgrid(lons, lats)
            x, y = projector.transform(lon[sea], lat[sea])
            inside = np.zeros_like(sea)
            inside[sea] = shapely.contains_xy(wide, x, y)
            reaches = reaches or bool(shapely.contains_xy(near, x, y).any())
            if inside.any():
                cells[(row, col)] = inside
    return cells, reaches, origins


def _months_in(period: dict) -> list[str]:
    """First days of the months the period asks for, with its `months` filter if it has one."""
    start, end = pd.Timestamp(period["start"][:7] + "-01"), pd.Timestamp(period["end"])
    months = period.get("months")
    return [stamp.strftime("%Y-%m-01") for stamp in pd.date_range(start, end, freq="MS")
            if not months or stamp.month in months]


@register("1", VARIABLE)
def read(variable: str, zone: dict, period: dict) -> dict:
    first, last = FIRST_DAY.isoformat(), last_day().isoformat()
    if period["end"] < first or period["start"] > last:
        return absent("no_coverage", f"This copy of MUR SST runs from {first} to {last}; nothing for this period.",
                      f"Esta copia de MUR SST va del {first} al {last}; no hay nada para este periodo.", _source())
    try:
        cells, reaches, origins = strip(zone)
        if cells is None:
            return absent("source_unreachable", "Offline, and the sea next to this zone has not been read before.",
                          "Sin red, y el mar junto a esta zona no se había leído antes.", _source())
        if not reaches or not cells:
            return absent("no_coverage",
                          f"The zone does not reach the sea: no open-sea cell of MUR SST within {COAST_KM:g} km.",
                          f"La zona no llega al mar: ninguna celda de mar abierto de MUR SST a menos de "
                          f"{COAST_KM:g} km.", _source())
        blocks = []
        for (row, col), inside in cells.items():
            values, origin = _chunk("analysed_sst", row, col)
            if values is None:
                return absent("source_unreachable", "Offline, and the sea next to this zone has not been read before.",
                              "Sin red, y el mar junto a esta zona no se había leído antes.", _source())
            origins.add(origin)
            blocks.append(values[:, inside])
    except requests.RequestException:
        return absent("source_unreachable", "The MUR SST store on AWS Open Data did not answer.",
                      "El almacén de MUR SST en AWS Open Data no ha respondido.", _source(), origin="remote")
    raw = np.concatenate(blocks, axis=1)[:N_DAYS]
    celsius = np.where(raw == FILL, np.nan, raw * SCALE + OFFSET)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a day with every cell missing is NaN, not an error
        daily = np.nanmean(celsius, axis=1)
    days = pd.Series(daily, index=pd.date_range(FIRST_DAY, periods=len(daily), freq="D"))
    in_record = days.groupby(days.index.to_period("M")).size()
    whole = in_record.index[in_record.to_numpy() == in_record.index.days_in_month]
    valid = days.dropna()
    monthly = valid.groupby(valid.index.to_period("M")).agg(["mean", "size"])
    monthly = monthly[monthly.index.isin(whole)]
    table = pd.DataFrame({"t": monthly.index.strftime("%Y-%m-01"), "v": monthly["mean"].round(3).to_numpy(),
                          "n_valid": monthly["size"].to_numpy()})
    table = table[table.t.isin(_months_in(period))].reset_index(drop=True)
    asked = len(_months_in(period))
    n_cells = int(sum(int(inside.sum()) for inside in cells.values()))
    if table.empty:
        return absent("insufficient_qa", "MUR SST has no valid values over the sea next to this zone in this period.",
                      "MUR SST no tiene valores válidos sobre el mar junto a esta zona en este periodo.",
                      _source(len(cells)), origin="remote" if "remote" in origins else "cache")
    return {"data": table, "absence": None, "origin": "remote" if "remote" in origins else "cache",
            "sources": [_source(len(cells))],
            "support": {"resolution_m": 1100, "since": 2002, "revisit_days": 1, "cells": n_cells},
            "coverage": {"valid_pct": round(100 * len(table) / max(asked, 1), 1), "n_dates": int(len(table))},
            "unit": "°C", "input_fingerprint": None}
