"""Regional terrestrial water storage anomalies from the downloaded JPL GRACE/GRACE-FO CRI product."""
from datetime import date, timedelta

import h5py
import numpy as np
import pandas as pd
from shapely import intersects_xy, make_valid
from shapely.geometry import shape

from hup_data import cancellation, paths
from hup_data.prepare.common import manifest_lines
from hup_data.readers import absent, register

SOURCE = "grace-fo"
VARIABLE = "terrestrial_water_storage_anomaly"
REF = {"source": SOURCE, "version": "RL06.3Mv04", "product": "JPL GRACE/GRACE-FO Mascon CRI",
       "url": "https://doi.org/10.5067/TEMSC-3JC634"}
BASE = date(2002, 1, 1)
RESOLUTION_M = 330_000


def _dates(values):
    return [(BASE + timedelta(days=float(value))).isoformat() for value in values]


def _land_support(file, zone):
    lon, lat = (file["lon"][:] + 180) % 360 - 180, file["lat"][:]
    xx, yy = np.meshgrid(lon, lat)
    west, south, east, north = zone["bbox"]
    land, mascons = file["land_mask"][:] == 1, file["mascon_ID"][:]
    inside = (xx >= west) & (xx <= east) & (yy >= south) & (yy <= north)
    if zone.get("geometry"):
        inside &= intersects_xy(make_valid(shape(zone["geometry"])), xx, yy)
    chosen = land & inside
    if not chosen.any():
        # Only grid cells touching the requested bounds can identify nearby terrestrial support on the coast.
        near = land & (xx >= west - .25) & (xx <= east + .25) & (yy >= south - .25) & (yy <= north + .25)
        if not near.any():
            return None
        distance = (xx - (west + east) / 2) ** 2 + (yy - (south + north) / 2) ** 2
        chosen.flat[int(np.argmin(np.where(near, distance, np.inf)))] = True
    ids = np.unique(mascons[chosen])
    # A small footprint inside one native estimate inherits the land part of that estimate, including its label.
    if len(ids) == 1:
        chosen = land & (mascons == ids[0])
    return chosen, lat, len(ids)


def _series(field, indices, selected, lat):
    rows, cols = np.where(selected)
    y, x = slice(min(rows), max(rows) + 1), slice(min(cols), max(cols) + 1)
    mask = selected[y, x]
    weights = np.broadcast_to(np.cos(np.deg2rad(lat[y]))[:, None], mask.shape)[mask]
    fill = np.ravel(field.attrs["_FillValue"])[0]
    chunk = field.chunks[0] if field.chunks else 16
    values = []
    for start in range(0, field.shape[0], chunk):
        cancellation.checkpoint()
        wanted = indices[(indices >= start) & (indices < start + chunk)]
        if not wanted.size:
            continue
        block = field[wanted.tolist(), y, x][:, mask]
        good = np.isfinite(block) & (block != fill)
        weights_sum = good @ weights
        weighted = np.where(good, block, 0) @ weights
        values.extend(np.divide(weighted, weights_sum, out=np.full(len(wanted), np.nan), where=weights_sum > 0))
    return values


@register("1", VARIABLE)
def read(variable: str, zone: dict, period: dict) -> dict:
    files = [paths.data_dir() / item["path"] for item in manifest_lines(sources={SOURCE})
             if item["path"].endswith("CRI.nc")]
    if not files:
        return absent("needs_package", "The JPL GRACE and GRACE-FO CRI package is missing.",
                      "Falta el paquete CRI de GRACE y GRACE-FO de JPL.", REF, origin="package")
    with h5py.File(max(files), "r") as file:
        dates = _dates(file["time"][:])
        months = period.get("months")
        indices = np.array([i for i, t in enumerate(dates) if period["start"] <= t <= period["end"]
                            and (not months or int(t[5:7]) in months)], dtype=int)
        support = _land_support(file, zone)
        if not len(indices) or support is None:
            return absent("no_coverage", "No terrestrial GRACE solution covers this location and period.",
                          "No hay soluciones terrestres de GRACE para este lugar y periodo.", REF, origin="package")
        selected, lat, n_mascons = support
        spans = file["time_bounds"][indices.tolist()]
        data = pd.DataFrame({"t": [dates[i] for i in indices], "window_start": _dates(spans[:, 0]),
                             "window_end": _dates(spans[:, 1]),
                             "v": _series(file["lwe_thickness"], indices, selected, lat)}).dropna(subset=["v"])
    if data.empty:
        return absent("insufficient_qa", "No usable terrestrial GRACE values in these observation windows.",
                      "No hay valores terrestres de GRACE utilizables en estas ventanas.", REF, origin="package")
    asked = pd.period_range(period["start"][:7], period["end"][:7], freq="M")
    asked = len(asked[asked.month.isin(months)]) if months else len(asked)
    limits = [
        {"en": "Regional total terrestrial water storage anomaly relative to 2004–2009. Its native support spans "
               "hundreds of kilometres. A municipal location selects regional context.",
         "es": "Anomalía regional del agua terrestre total almacenada frente a 2004–2009. El soporte nativo "
               "abarca cientos de kilómetros. Un municipio selecciona contexto regional."},
        {"en": "Dates are centres of the original observation windows. Gaps and overlapping windows are kept. "
               "Consumption, groundwater levels and available supply need their own evidence.",
         "es": "Las fechas son los centros de las ventanas originales. Se conservan sus huecos y solapamientos. "
               "El consumo, el nivel freático y el suministro disponible requieren fuentes propias."},
    ]
    return {"data": data, "absence": None, "origin": "package", "sources": [{**REF, "n_items": len(data)}],
            "support": {"resolution_m": RESOLUTION_M, "since": 2002, "revisit_days": 30, "cells": n_mascons},
            "coverage": {"n_dates": len(data), "valid_pct": 100 * data["t"].str[:7].nunique() / max(asked, 1)},
            "unit": "cm", "input_fingerprint": None, "limits": limits}
