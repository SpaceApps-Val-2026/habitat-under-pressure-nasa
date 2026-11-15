"""GPM IMERG monthly precipitation, read straight from the downloaded files (0.1°, global, from 1998).

Trap: inside each file the grid is stored as (time, lon, lat), not (time, lat, lon). The files hold a rate in
mm/hour; the reader returns the month total in mm.
"""
import calendar

import h5py
import numpy as np
import pandas as pd

from hup_data import cancellation, coverage, paths
from hup_data.readers import absent, register

SOURCE = "imerg-monthly"
VARIABLE = "precipitation"
URL = "https://doi.org/10.5067/GPM/IMERG/3B-MONTH/07"
CELL = 0.1


def _source(n_items: int | None = None) -> dict:
    ref = {"source": SOURCE, "product": "GPM_3IMERGM", "version": "07", "url": URL}
    return ref if n_items is None else {**ref, "n_items": n_items}


def _window(axis: np.ndarray, low: float, high: float) -> slice:
    """Cells whose centre falls inside [low, high]; if none does, the cell that holds the middle."""
    inside = np.flatnonzero((axis >= low) & (axis <= high))
    if inside.size == 0:
        inside = np.array([np.abs(axis - (low + high) / 2).argmin()])
    return slice(int(inside[0]), int(inside[-1]) + 1)


@register("2", VARIABLE)  # 2: only the months of period["months"]; saved series of version 1 are not reused
def read(variable: str, zone: dict, period: dict) -> dict:
    rows = [row for row in coverage.query(VARIABLE, zone["bbox"], period) if row["source"] == SOURCE
            and (not period.get("months") or int(row["period"]["start"][5:7]) in period["months"])]
    if not rows:
        return absent("needs_package", "Monthly precipitation (IMERG) is not downloaded for this period.",
                      "La precipitación mensual (IMERG) no está descargada para este periodo.", _source())
    west, south, east, north = zone["bbox"]
    dates, values, lons, lats = [], [], None, None
    for row in rows:
        cancellation.checkpoint()
        with h5py.File(paths.data_dir() / row["path"], "r") as file:
            grid = file["Grid"]
            if lons is None:
                lons, lats = _window(grid["lon"][:], west, east), _window(grid["lat"][:], south, north)
            block = grid["precipitation"][0, lons, lats].astype("float64")  # (lon, lat): see the module note
            fill = float(np.ravel(grid["precipitation"].attrs["_FillValue"])[0])  # scalar or one-item array
        block[block == fill] = np.nan
        start = row["period"]["start"]
        hours = 24 * calendar.monthrange(int(start[:4]), int(start[5:7]))[1]
        dates.append(start)
        values.append(float(np.nanmean(block) * hours) if np.isfinite(block).any() else np.nan)
    table = pd.DataFrame({"t": dates, "v": values}).dropna(subset=["v"]).sort_values("t").reset_index(drop=True)
    if table.empty:
        return absent("insufficient_qa", "IMERG has no valid values here for this period.",
                      "IMERG no tiene valores válidos aquí para este periodo.", _source(len(rows)), origin="package")
    return {"data": table, "absence": None, "origin": "package", "sources": [_source(len(rows))],
            "support": {"resolution_m": 11100, "since": 1998,
                        "cells": round((east - west) * (north - south) / CELL**2, 3)},
            "coverage": {"valid_pct": round(100 * len(table) / len(rows), 1), "n_dates": int(len(table))},
            "unit": "mm/month", "input_fingerprint": None}
