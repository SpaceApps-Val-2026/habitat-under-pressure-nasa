"""GPM IMERG Final daily precipitation (0.1°, global, from 1998), asked for on the spot through OPeNDAP at GES DISC.

Each day is one granule. Only the block of 0.1° cells that touch the zone rectangle is asked for, as CSV: the
server prints the stored values, and the file is about 1 KB against 20-25 KB for the same block as NetCDF (the
NetCDF subset repeats the granule metadata). Every day is kept in <data>/cache/imerg-daily/<cell window>/ with its
manifest line, so a zone whose rectangle touches the same cells reuses it.
Trap, as in the monthly reader: the grid is stored as (time, lon, lat). Cell i has its centre at -179.95 + 0.1 i
of longitude, cell j at -89.95 + 0.1 j of latitude.

The zone value is the mean of the cells weighted by the area each one shares with the rectangle (overlap in
longitude × overlap in latitude × cos latitude). Cells with the fill value are left out.

Access needs an Earthdata account. The key is read at the moment of the request (EARTHDATA_TOKEN, or the file named
by EARTHDATA_TOKEN_FILE, or ~/.config/hup/earthdata-token) and is never logged or returned.
"""
import io
import math
import os
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import requests

from hup_data import cancellation, manifest, paths, settings, store
from hup_data.readers import absent, register

SOURCE = "imerg-daily"
DAILY, YEARLY_MAX = "precipitation_daily", "pr_max_1day_observed"
PRODUCT, VERSION = "GPM_3IMERGDF", "07"
URL = "https://doi.org/10.5067/GPM/IMERGDF/DAY/07"
OPENDAP = "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDF.07"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
COLLECTION = "C2723754864-GES_DISC"  # GPM_3IMERGDF v07 in CMR
LICENSE = ("Open without restriction under the NASA EOSDIS Data Use Guidance; cite Huffman et al. (2023), GPM IMERG "
           "Final Precipitation L3 1 day 0.1 degree x 0.1 degree V07, https://doi.org/10.5067/GPM/IMERGDF/DAY/07")
FIRST_DAY = date(1998, 1, 1)  # first granule of V07 in CMR (checked 2026-11-14)
CELL = 0.1
FILL = -9999.9
WORKERS = 4  # at most four requests at a time: GES DISC is a shared server
ATTEMPTS = 3
TIMEOUT = (10, 60)  # connect, read (seconds)
MAX_REMOTE_DAYS = 400  # more days than this are not asked for during an analysis: they are predownloaded
MIN_YEAR_SHARE = 0.95  # a yearly maximum needs this share of the days of its calendar year (of the months asked)
NO_ACCOUNT = ("The Earthdata account is not configured on this server.",
              "La cuenta de Earthdata no está configurada en este servidor.")


class Unreachable(Exception):
    """The source could not be read: no account, no network or a refusal. The message never holds the key."""


def _source(n_items: int | None = None) -> dict:
    ref = {"source": SOURCE, "product": PRODUCT, "version": VERSION, "url": URL}
    return ref if n_items is None else {**ref, "n_items": n_items}


def _token() -> str | None:
    """The Earthdata key, read now. Never logged, printed or returned to a client."""
    key = settings.load().secret("EARTHDATA_TOKEN")
    if key:
        return key.strip()
    file = Path(os.environ.get("EARTHDATA_TOKEN_FILE") or Path.home() / ".config" / "hup" / "earthdata-token")
    try:
        return file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class _Session(requests.Session):
    """Keeps the key on redirects inside Earthdata and GES DISC (the login goes through urs.earthdata.nasa.gov);
    drops it anywhere else."""

    def rebuild_auth(self, prepared, response):
        host = urlparse(prepared.url).hostname or ""
        if not host.endswith((".earthdata.nasa.gov", ".eosdis.nasa.gov")):
            prepared.headers.pop("Authorization", None)


def window(bbox: list[float]) -> tuple[int, int, int, int]:
    """First and last cell index (lon, lat) of the 0.1° cells that share some area with the rectangle."""
    west, south, east, north = bbox

    def span(low, high, origin, count):
        first = math.floor((low - origin) / CELL + 1e-9)
        last = math.ceil((high - origin) / CELL - 1e-9) - 1
        last = max(last, first)  # a point or a line still falls in one cell
        return max(first, 0), min(last, count - 1)

    i0, i1 = span(west, east, -180.0, 3600)
    j0, j1 = span(south, north, -90.0, 1800)
    return i0, i1, j0, j1


def weights(bbox: list[float], cells: tuple[int, int, int, int]) -> np.ndarray:
    """Area each cell shares with the rectangle, shape (lon, lat), as stored in the file."""
    west, south, east, north = bbox
    i0, i1, j0, j1 = cells
    lon_low = -180.0 + CELL * np.arange(i0, i1 + 1)
    lat_low = -90.0 + CELL * np.arange(j0, j1 + 1)
    dlon = np.clip(np.minimum(lon_low + CELL, east) - np.maximum(lon_low, west), 0, None)
    dlat = np.clip(np.minimum(lat_low + CELL, north) - np.maximum(lat_low, south), 0, None)
    if not dlon.any():  # the rectangle is a point or a vertical line: the cell that holds it
        dlon = np.ones_like(dlon)
    if not dlat.any():
        dlat = np.ones_like(dlat)
    return np.outer(dlon, dlat * np.cos(np.radians(lat_low + CELL / 2)))


def _folder(cells: tuple[int, int, int, int]) -> Path:
    i0, i1, j0, j1 = cells
    return paths.cache() / SOURCE / f"lon{i0}-{i1}_lat{j0}-{j1}"


def _file(cells, day: date) -> Path:
    return _folder(cells) / f"{day:%Y}" / f"{day:%Y%m%d}.csv"


def _holders(cells) -> list[tuple[int, int, int, int]]:
    """Cached windows that hold every cell of `cells`: its own first, then larger ones (a town inside a
    predownloaded rectangle reads it without the network)."""
    i0, i1, j0, j1 = cells
    found = []
    root = paths.cache() / SOURCE
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        match = re.fullmatch(r"lon(\d+)-(\d+)_lat(\d+)-(\d+)", folder.name)
        if match:
            a0, a1, b0, b1 = map(int, match.groups())
            if a0 <= i0 and i1 <= a1 and b0 <= j0 and j1 <= b1:
                found.append((a0, a1, b0, b1))
    return sorted(found, key=lambda held: held != cells)


def _cached(cells, day: date, holders) -> tuple[Path, tuple] | None:
    for held in holders:
        if _file(held, day).is_file():
            return _file(held, day), held
    return None


def parse(text: str, cells: tuple[int, int, int, int]) -> np.ndarray:
    """The block of an OPeNDAP CSV answer, shape (lon, lat), with NaN for the fill value."""
    i0, i1, j0, j1 = cells
    block = np.full((i1 - i0 + 1, j1 - j0 + 1), np.nan)
    rows = 0
    for line in io.StringIO(text):
        if not line.startswith("/precipitation["):
            continue
        head, *values = line.split(",")
        row = int(head.split("[")[2].rstrip("]"))
        block[row] = [float(value) for value in values]
        rows += 1
    if rows != block.shape[0]:
        raise ValueError(f"the answer holds {rows} rows of precipitation, not {block.shape[0]}")
    block[block <= FILL + 0.05] = np.nan
    return block


def _granules(first: date, last: date) -> dict[date, str]:
    """Granule name of every day that exists between two dates, from CMR. The sub-version (V07B...) is not
    guessed: it is the name CMR gives."""
    names, after = {}, None
    while True:
        headers = {"CMR-Search-After": after} if after else {}
        response = _get(requests, CMR, headers=headers, params={
            "concept_id": COLLECTION, "page_size": 2000, "sort_key": "start_date",
            "temporal": f"{first:%Y-%m-%d}T00:00:00Z,{last:%Y-%m-%d}T23:59:59Z"})
        entries = response.json()["feed"]["entry"]
        for entry in entries:
            name = entry["title"].split(":", 1)[-1]
            day = date.fromisoformat(entry["time_start"][:10])
            if first <= day <= last:
                names[day] = name
        after = response.headers.get("CMR-Search-After")
        if not entries or not after or len(entries) < 2000:
            return names


def _get(client, url: str, **options) -> requests.Response:
    """A GET with a timeout and a few attempts. 404 is returned; any other failure becomes Unreachable."""
    for attempt in range(ATTEMPTS):
        cancellation.checkpoint()
        try:
            response = client.get(url, timeout=TIMEOUT, **options)
        except requests.RequestException as error:
            failure = type(error).__name__
        else:
            if response.status_code in (401, 403):
                raise Unreachable(f"Earthdata did not accept the account (HTTP {response.status_code}).")
            if response.status_code == 404 or response.ok:
                return response
            failure = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                break
        if attempt + 1 < ATTEMPTS:
            signal = cancellation.signal.get()
            if signal is None:
                time.sleep(2 ** attempt)
            elif signal.wait(2 ** attempt):
                cancellation.checkpoint()
    raise Unreachable(f"{urlparse(url).hostname} did not answer ({failure}).")


def _url(day: date, name: str, cells) -> str:
    i0, i1, j0, j1 = cells
    return (f"{OPENDAP}/{day:%Y}/{day:%m}/{name}.dap.csv"
            f"?dap4.ce=/precipitation[0][{i0}:{i1}][{j0}:{j1}]")


def fetch(days: list[date], cells, key: str) -> None:
    """Ask GES DISC for the days, at most WORKERS at a time, and cache each one with its manifest line."""
    names = _granules(min(days), max(days))
    wanted = [day for day in days if day in names]
    for year in sorted({day.year for day in wanted}):
        _file(cells, date(year, 1, 1)).parent.mkdir(parents=True, exist_ok=True)
    local = threading.local()

    def one(day: date) -> None:
        if not hasattr(local, "session"):
            local.session = _Session()
            local.session.headers["Authorization"] = f"Bearer {key}"
        url = _url(day, names[day], cells)
        response = _get(local.session, url)
        if response.status_code == 404:
            return
        text = response.text
        if not text.startswith("Dataset:"):  # a login page instead of data
            raise Unreachable("GES DISC answered with something that is not data.")
        parse(text, cells)  # refuse to cache an answer that cannot be read
        file = _file(cells, day)
        store.write_atomic(file, response.content)
        i0, i1, j0, j1 = cells
        manifest.record(file, source=SOURCE, item=names[day], scope="global", fmt="csv", url=url, license=LICENSE,
                        product=PRODUCT, version=VERSION, tier="raw", variables=[DAILY, YEARLY_MAX],
                        bbox=[round(-180 + CELL * i0, 4), round(-90 + CELL * j0, 4),
                              round(-180 + CELL * (i1 + 1), 4), round(-90 + CELL * (j1 + 1), 4)],
                        period={"start": day.isoformat(), "end": day.isoformat()})

    for _ in cancellation.map_items(one, wanted, workers=WORKERS):
        pass  # each complete day stays in the cache


def days_of(period: dict) -> list[date]:
    """Every day of the period inside the record, only of period["months"] when given."""
    first = max(date.fromisoformat(period["start"][:10]), FIRST_DAY)
    last = date.fromisoformat(period["end"][:10])
    months = set(period.get("months") or range(1, 13))
    return [first + timedelta(n) for n in range((last - first).days + 1)
            if (first + timedelta(n)).month in months]


def _series(days, cells, bbox) -> pd.DataFrame:
    weight = weights(bbox, cells)
    holders = _holders(cells)
    i0, i1, j0, j1 = cells
    rows = []
    for day in days:
        cancellation.checkpoint()
        found = _cached(cells, day, holders)
        if found is None:
            continue
        file, held = found
        block = parse(file.read_text(encoding="utf-8"), held)
        block = block[i0 - held[0]:i1 - held[0] + 1, j0 - held[2]:j1 - held[2] + 1]
        valid = np.isfinite(block) & (weight > 0)
        if valid.any():
            rows.append((day.isoformat(), float(np.average(block[valid], weights=weight[valid]))))
    return pd.DataFrame(rows, columns=["t", "v"])


def calendar_days(year: int, months=None) -> int:
    """Days of a calendar year, only of `months` when given: what a whole year of the season asked for has."""
    months = set(months or range(1, 13))
    first = date(year, 1, 1)
    return sum((first + timedelta(n)).month in months for n in range((date(year + 1, 1, 1) - first).days))


def yearly_max(daily: pd.DataFrame, asked: list[date], months=None) -> pd.DataFrame:
    """The wettest day of each year asked for (of `months` when given), only for years with MIN_YEAR_SHARE of the
    days of their calendar year: a period that starts or ends within a year does not make a piece of it a year."""
    years = daily.t.str[:4].astype(int)
    grouped = daily.groupby(years)["v"]
    table = pd.DataFrame({"v": grouped.max(), "n": grouped.count()})
    table = table[table.index.isin({day.year for day in asked})]
    whole = np.array([calendar_days(year, months) for year in table.index])
    table = table[table.n.to_numpy() >= MIN_YEAR_SHARE * whole]
    return pd.DataFrame({"t": [str(year) for year in table.index], "v": table.v.to_numpy()})


@register("2", DAILY, YEARLY_MAX)
def read(variable: str, zone: dict, period: dict) -> dict:
    west, south, east, north = zone["bbox"]
    if south < -90 or north > 90 or south > north:
        return absent("no_coverage", "IMERG covers latitudes between 90° S and 90° N only.",
                      "IMERG solo cubre latitudes entre 90° S y 90° N.", _source())
    days = days_of(period)
    if not days:
        return absent("no_coverage", f"IMERG daily starts on {FIRST_DAY.isoformat()}; this period is before it.",
                      f"IMERG diario empieza el {FIRST_DAY:%d-%m-%Y}; este periodo es anterior.", _source())
    cells = window(zone["bbox"])
    holders = _holders(cells)
    missing = [day for day in days if _cached(cells, day, holders) is None]
    origin = "cache"
    if missing:
        if settings.load().offline:
            return absent("source_unreachable", "Offline, and these days of IMERG have not been read here before.",
                          "Sin red, y estos días de IMERG no se habían leído antes aquí.", _source())
        if len(missing) > MAX_REMOTE_DAYS:
            return absent("needs_package",
                          f"{len(missing)} days of IMERG would have to be downloaded; more than {MAX_REMOTE_DAYS} "
                          "are predownloaded, not asked for during an analysis.",
                          f"Habría que descargar {len(missing)} días de IMERG; más de {MAX_REMOTE_DAYS} se "
                          "predescargan, no se piden durante un análisis.", _source())
        key = _token()
        if not key:
            return absent("source_unreachable", *NO_ACCOUNT, _source(), origin="remote")
        try:
            fetch(missing, cells, key)
        except (Unreachable, ValueError, KeyError) as error:
            text = str(error) if isinstance(error, Unreachable) else "GES DISC gave an answer that cannot be read."
            return absent("source_unreachable", f"IMERG daily could not be read: {text}",
                          "No se ha podido leer IMERG diario de GES DISC.", _source(), origin="remote")
        origin = "remote"
    daily = _series(days, cells, zone["bbox"])
    if daily.empty:
        return absent("no_coverage", "IMERG Final has no day published for this period yet (it comes out about "
                      "3.5 months late).", "IMERG Final aún no tiene publicado ningún día de este periodo (sale con "
                      "unos 3,5 meses de retraso).", _source())
    support = {"resolution_m": 11100, "since": 1998, "revisit_days": 1,
               "cells": round((east - west) * (north - south) / CELL**2, 3)}
    if variable == YEARLY_MAX:
        table = yearly_max(daily, days, period.get("months"))
        if table.empty:
            return absent("insufficient_qa", f"No year has {MIN_YEAR_SHARE:.0%} of its days read from IMERG.",
                          f"Ningún año tiene el {MIN_YEAR_SHARE:.0%} de sus días leídos de IMERG.",
                          _source(len(daily)), origin=origin)
        asked_years = len({day.year for day in days})
        return {"data": table, "absence": None, "origin": origin, "sources": [_source(len(daily))],
                "support": support, "unit": "mm", "input_fingerprint": None,
                "coverage": {"valid_pct": round(100 * len(table) / asked_years, 1), "n_dates": int(len(table))}}
    return {"data": daily, "absence": None, "origin": origin, "sources": [_source(len(daily))], "support": support,
            "coverage": {"valid_pct": round(100 * len(daily) / len(days), 1), "n_dates": int(len(daily))},
            "unit": "mm/day", "input_fingerprint": None}

