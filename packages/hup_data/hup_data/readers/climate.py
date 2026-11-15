"""Reader of the climate cube: yearly indicators by model and scenario for a zone.

Returns the common reader shape (docs/paquetes/interfaces.md, section 4). The data is a table with one row per
model, scenario and year. It never raises for missing data: it returns an absence with its reason.
"""
import numpy as np
import pandas as pd
import xarray as xr

from hup_data import fingerprint
from hup_data.prepare import climate

URL = "https://www.nccs.nasa.gov/services/data-collections/land-based-products/nex-gddp-cmip6"
METRES_PER_DEGREE = 111_320
UNITS = {"degC": "°C"}  # the files spell the unit their way; results use the catalog spelling


def _absent(reason: str, en: str, es: str, meta: dict | None) -> dict:
    return {"data": None, "absence": {"reason": reason, "detail": {"en": en, "es": es}}, "origin": "cache",
            "sources": [_source(meta)], "support": {}, "coverage": {}, "unit": None, "input_fingerprint": None}


def _source(meta: dict | None) -> dict:
    ref = {"source": climate.SOURCE, "product": "yearly-indicators", "url": URL}
    if meta:
        ref.update(n_items=meta["n_files"], items=meta["models"])
        ref.update({key: meta[key] for key in ("version", "license") if meta.get(key)})
    return ref


def variables() -> dict[str, str]:
    """Indicator → unit, for the cube on this computer. Empty when it has not been built."""
    meta = climate.read_meta()
    return meta["variables"] if meta else {}


def climate_series(variable: str, zone: dict, period: dict | None = None) -> dict:
    """Yearly values of one indicator for a zone: the mean of the grid cells whose centre falls inside its bbox.

    A zone smaller than a cell takes the cell that contains its centre, and `support.cells` says so (below 1).
    """
    meta = climate.read_meta()
    if meta is None:
        return _absent("needs_package", "The climate cube has not been built on this computer.",
                       "El cubo del clima no está construido en este ordenador.", None)
    if variable not in meta["variables"]:
        return _absent("not_computed", f"The climate cube has no indicator named {variable}.",
                       f"El cubo del clima no tiene un indicador llamado {variable}.", meta)
    west, south, east, north = zone["bbox"]
    with xr.open_zarr(climate.cube_path(), consolidated=False, chunks=None) as cube:
        lat, lon = cube.lat.values, cube.lon.values
        half = meta["resolution_deg"] / 2
        if north < lat.min() - half or south > lat.max() + half:
            return _absent("no_coverage", "This source does not cover latitudes south of 60° S.",
                           "Esta fuente no cubre latitudes al sur de 60° S.", meta)
        inside_lat = np.flatnonzero((lat >= south) & (lat <= north))
        inside_lon = np.flatnonzero((lon >= west) & (lon <= east))
        cells_in_zone = (east - west) * (north - south) / meta["resolution_deg"] ** 2
        if inside_lat.size == 0:
            inside_lat = np.array([np.abs(lat - (south + north) / 2).argmin()])
        if inside_lon.size == 0:
            inside_lon = np.array([np.abs(lon - (west + east) / 2).argmin()])
        block = cube[variable].isel(lat=inside_lat, lon=inside_lon)
        if period:
            block = block.sel(year=slice(int(period["start"][:4]), int(period["end"][:4])))
        valid = int(block.notnull().any(dim=["model", "scenario", "year"]).sum())
        mean = block.mean(dim=["lat", "lon"], skipna=True).load()
    table = mean.to_dataframe(name="v").reset_index().dropna(subset=["v"])
    table = table.rename(columns={"year": "t"})[["t", "v", "model", "scenario"]].reset_index(drop=True)
    table["v"] = table["v"].astype(float)
    if table.empty:
        return _absent("no_coverage", "This source has no values here: it only covers land.",
                       "Esta fuente no tiene valores aquí: solo cubre tierra firme.", meta)
    used = int(inside_lat.size * inside_lon.size)
    return {
        "data": pd.DataFrame(table),
        "absence": None,
        "origin": "cache",
        "sources": [_source(meta)],
        "support": {"resolution_m": round(meta["resolution_deg"] * METRES_PER_DEGREE),
                    "cells": round(cells_in_zone, 3)},
        "coverage": {"valid_pct": round(100 * valid / used, 1), "n_dates": int(table["t"].nunique())},
        "unit": UNITS.get(meta["variables"][variable], meta["variables"][variable]),
        "input_fingerprint": fingerprint.sha({"cube": meta["input_fingerprint"], "variable": variable,
                                              "bbox": zone["bbox"], "period": period}),
    }


def _register() -> None:
    """The indicators of the cube answer through the common door too, under their own names."""
    from hup_data.readers import register

    names = ("tas_mean", "tasmax_mean", "tasmax_max", "days_above_35c", "pr_total", "pr_max_1day")
    register("1", *names)(climate_series)


_register()
