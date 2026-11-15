"""NASA POWER: monthly series at a point, from 1981, for anywhere on Earth. No account needed.

The grid is coarse (about 50 km): the zone is read at its centre, and `support.cells` says how much of a cell it is.
Each point and parameter is asked for once, whole, and kept in <data>/cache/nasa-power/ with its manifest line.
"""
import csv
import io
from datetime import UTC, datetime

import pandas as pd
import requests

from hup_data import cancellation, manifest, paths, settings, store
from hup_data.readers import absent, register

SOURCE = "nasa-power"
API = "https://power.larc.nasa.gov/api/temporal/monthly/point"
LICENSE = "Open; acknowledgement of the NASA Langley Research Center POWER Project required"
FIRST_YEAR = 1981
CELL_DEGREES = (0.5, 0.625)  # MERRA-2 latitude and longitude spacing
# variable → (POWER parameter, unit after conversion, factor per day of the month or None)
PARAMETERS = {
    "air_temperature": ("T2M", "°C", None),
    # Monthly T2M_MAX is the hottest hour of the month; T2M_MAX_AVG is the mean of the daily maxima (Guillem, 14 Nov)
    "air_temperature_max": ("T2M_MAX_AVG", "°C", None),
}


def _source(url: str = API) -> dict:
    return {"source": SOURCE, "product": "MERRA-2 monthly", "url": url, "license": LICENSE}


def _fetch(parameter: str, lat: float, lon: float, last_year: int) -> tuple[dict[str, float], str]:
    response = requests.get(API, timeout=60, params={
        "parameters": parameter, "community": "AG", "latitude": lat, "longitude": lon,
        "start": FIRST_YEAR, "end": last_year, "format": "JSON"})
    response.raise_for_status()
    return response.json()["properties"]["parameter"][parameter], response.url


def _table(variable: str, lat: float, lon: float) -> tuple[pd.DataFrame | None, str]:
    """Monthly values of the whole record for one point: from the cache, or asked for once and cached."""
    parameter = PARAMETERS[variable][0]
    folder = paths.cache() / SOURCE / f"{lat:+.2f}_{lon:+.2f}"
    file = folder / f"{parameter}.csv"
    if not file.is_file():
        if settings.load().offline:
            return None, "cache"
        last_year = datetime.now(UTC).year - 1  # the current year is incomplete: it is not asked for
        values, url = _fetch(parameter, lat, lon, last_year)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["t", "v"])
        for key, value in sorted(values.items()):
            cancellation.checkpoint()
            if key.endswith("13"):  # the API adds a thirteenth "month" with the annual value
                continue
            writer.writerow([f"{key[:4]}-{key[4:]}-01", "" if value <= -900 else value])  # -999 means no data
        store.write_atomic(file, buffer.getvalue().encode("utf-8"))
        half_lat, half_lon = CELL_DEGREES[0] / 2, CELL_DEGREES[1] / 2
        manifest.record(file, source=SOURCE, item=f"{parameter}_{lat:+.2f}_{lon:+.2f}", scope="global", fmt="csv",
                        url=url, license=LICENSE, product="MERRA-2 monthly", tier="raw", variables=[variable],
                        bbox=[lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat],
                        period={"start": f"{FIRST_YEAR}-01-01", "end": f"{last_year}-12-31"})
        return pd.read_csv(file), "remote"
    return pd.read_csv(file), "cache"


# 2: air_temperature_max from T2M_MAX_AVG; 3: only the months of period["months"]. Older saved series are not reused.
@register("4", *PARAMETERS)
def read(variable: str, zone: dict, period: dict) -> dict:
    west, south, east, north = zone["bbox"]
    lat, lon = round((south + north) / 2, 2), round((west + east) / 2, 2)
    try:
        table, origin = _table(variable, lat, lon)
    except (requests.RequestException, KeyError, ValueError):
        return absent("source_unreachable", "NASA POWER did not answer.", "NASA POWER no ha respondido.",
                      _source(), origin="remote")
    if table is None:
        return absent("source_unreachable", "Offline, and this point has not been read from NASA POWER before.",
                      "Sin red, y este punto no se había leído antes de NASA POWER.", _source())
    months = pd.period_range(period["start"][:7], period["end"][:7], freq="M")
    if period.get("months"):  # a season may cross the new year (12, 1, 2, 3): the month number is all that counts
        months = months[months.month.isin(period["months"])]
    asked = len(months)  # coverage is measured against the months asked for, not against the whole record
    table = table[(table.t >= period["start"][:7] + "-01") & (table.t <= period["end"])].dropna(subset=["v"])
    if period.get("months"):
        table = table[table.t.str[5:7].astype(int).isin(period["months"])]
    if table.empty:
        return absent("no_coverage", f"NASA POWER has no values for this period; its record starts in {FIRST_YEAR}.",
                      f"NASA POWER no tiene valores para este periodo; su registro empieza en {FIRST_YEAR}.", _source())
    _, unit, per = PARAMETERS[variable]
    table = table.reset_index(drop=True)
    if per == "days":
        table["v"] = table["v"] * pd.to_datetime(table["t"]).dt.days_in_month
    cells = (east - west) * (north - south) / (CELL_DEGREES[0] * CELL_DEGREES[1])
    return {"data": table[["t", "v"]], "absence": None, "origin": origin, "sources": [_source()],
            "support": {"resolution_m": 55000, "since": FIRST_YEAR, "cells": round(cells, 4)},
            "coverage": {"valid_pct": round(100 * len(table) / max(asked, 1), 1) if asked else 0.0,
                         "n_dates": int(len(table))},
            "unit": unit, "input_fingerprint": None}
