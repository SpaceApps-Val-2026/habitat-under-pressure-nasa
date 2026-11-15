"""Provisional two-pair test, using annual residuals on a common POWER cell."""

import os

import numpy as np
from scipy.stats import rankdata
from scipy.stats import t as student

from ._series import body, flatten, points

MIN_YEARS = 20
BLOCK = 3
RESAMPLES = 999
ALPHA = .05
RESOLUTION = 55_000


def enabled():
    return os.environ.get("HUP_MILESTONE_HYPOTHESES") == "1"


def power_cell(zone):
    """Native 0.625° × 0.5° MERRA-2 cell around the centre of the zone."""
    west, south, east, north = zone["bbox"]
    lon = np.floor(((west + east) / 2 + 180) / .625 + .5) * .625 - 180
    lat = np.floor(((south + north) / 2 + 90) / .5 + .5) * .5 - 90
    if not -180 < lon < 180:
        # A split IMERG box would need area-weighted reads on both sides of the antimeridian.
        return None
    return [float(lon - .3125), float(max(-90, lat - .25)),
            float(lon + .3125), float(min(90, lat + .25))]


def detrend(values):
    time = np.arange(values.shape[-1])
    left, right = np.triu_indices(len(time), 1)
    slopes = (values[..., right] - values[..., left]) / (right - left)
    return values - np.median(slopes, axis=-1)[..., None] * time


def correlation(x, y):
    x = x - x.mean(axis=-1, keepdims=True)
    y = y - y.mean(axis=-1, keepdims=True)
    denominator = np.linalg.norm(x, axis=-1) * np.linalg.norm(y, axis=-1)
    return np.divide((x * y).sum(axis=-1), denominator, out=np.zeros_like(denominator), where=denominator > 0)


def rank_test(residuals, negative):
    """Residuals with shape (..., two series, years); one null per pair."""
    ranks = rankdata(residuals, axis=-1)
    rho = np.clip(correlation(ranks[..., 0, :], ranks[..., 1, :]), -1, 1)
    lag1 = correlation(ranks[..., :-1], ranks[..., 1:])
    product = np.clip(lag1[..., 0] * lag1[..., 1], 0, .999)
    effective = ranks.shape[-1] * (1 - product) / (1 + product)
    degrees = effective - 2
    statistic = rho * np.sqrt(np.maximum(degrees, 0) / np.maximum(1 - rho**2, np.finfo(float).eps))
    p = np.where(negative, student.cdf(statistic, degrees), 2 * student.sf(abs(statistic), degrees))
    eligible = (degrees > 0) & np.all(np.ptp(residuals, axis=-1) > 1e-10, axis=-1)
    return rho, np.where(eligible, p, 1.), effective, eligible


def holm(p):
    order = np.argsort(p, axis=-1)
    sorted_p = np.take_along_axis(p, order, axis=-1)
    adjusted = np.minimum(1, np.maximum.accumulate(sorted_p * [2, 1], axis=-1))
    return np.take_along_axis(adjusted, np.argsort(order, axis=-1), axis=-1)


def block_interval(residuals, rng):
    n = residuals.shape[-1]
    starts = rng.integers(0, n - BLOCK + 1, size=(RESAMPLES, (n + BLOCK - 1) // BLOCK))
    indices = (starts[..., None] + np.arange(BLOCK)).reshape(RESAMPLES, -1)[:, :n]
    ranks = rankdata(residuals[:, indices], axis=-1)
    return tuple(map(float, np.quantile(correlation(ranks[0], ranks[1]), [ALPHA / 2, 1 - ALPHA / 2])))


def supports(low, high, negative):
    return high < 0 or (not negative and low > 0)


def _annual(entry):
    dated = points(entry)
    if any(len(date) != 4 or not date.isdecimal() for date in dated):
        return {}
    return {int(date): value for date, value in dated.items()}


def _pair(pair, entries, cell):
    keys = [(pair["precipitation"], "sum", "imerg-monthly"), (pair["temperature"], "mean", "nasa-power")]
    selected = []
    for variable, statistic, source in keys:
        candidates = []
        for item in entries:
            method = item["method"]
            params = method["params"]
            if (item["variable"] == variable and "absence" not in item and cell is not None
                and method["operation"] == "read_series" and params.get("hypothesis_power_cell") is True
                and params.get("require_complete_months") is True and params.get("season") == pair["season"]
                and params.get("aggregate") == "year" and params.get("statistic") == statistic
                and item.get("support", {}).get("resolution_m") == RESOLUTION
                and any(ref["source"] == source for ref in item["sources"])):
                candidates.append(item)
        if len(candidates) != 1:
            return [], None
        selected.append(_annual(candidates[0]))
    years = sorted(selected[0].keys() & selected[1].keys())
    if len(years) < MIN_YEARS or years[-1] - years[0] + 1 != len(years):
        return years, None
    return years, detrend(np.array([[series[year] for year in years] for series in selected]))


def evaluate(params, inputs, ctx):
    entries = flatten(inputs)
    settings = {"weather_hypotheses": params["weather_hypotheses"], "min_years": MIN_YEARS, "lags": [0],
                "block_years": BLOCK, "resamples": RESAMPLES, "alpha": ALPHA}
    result = body("lagged_association", "table", "E0", settings, entries, ctx)
    result.update(unit="1", support={"resolution_m": RESOLUTION, "cells": 1, "since": 1998})
    result["limits"] = [{
        "en": "Provisional method agreed by Claude at Iban's request. The POWER cell (about 55 km) describes "
              "the wider area. Sen detrending does not control for other regional factors or link either series "
              "to an event.",
        "es": "Método provisional acordado por Claude a petición de Iban. La celda de POWER (unos 55 km) "
              "describe la comarca, no el municipio. Quitar la tendencia de Sen no controla otros factores "
              "regionales ni vincula las series con el suceso.",
    }]
    for entry in entries:
        detail = entry.get("absence", {}).get("detail")
        if detail and detail not in result["limits"]:
            result["limits"].append(detail)
    result.pop("period", None)  # each pair has its own full overlapping observation period
    pairs = settings["weather_hypotheses"]
    cell = power_cell(ctx.zone)
    samples = [_pair(pair, entries, cell) for pair in pairs]
    tests = [rank_test(sample, pair["negative"]) if sample is not None else (0., 1., None, False)
             for pair, (_, sample) in zip(pairs, samples, strict=True)]
    adjusted = holm(np.array([test[1] for test in tests]))
    rng = np.random.default_rng(ctx.seed)
    rows = []
    for pair, (years, sample), (rho, _, effective, eligible), q in zip(pairs, samples, tests, adjusted, strict=True):
        status = "inconclusive" if eligible else "not_testable"
        low = high = None
        if eligible and q < ALPHA:
            low, high = block_interval(sample, rng)
            if supports(low, high, pair["negative"]):
                status = "compatible"
                result["evidence_level"] = "E2"
        rows.append([pair["id"], years[0] if years else None, years[-1] if years else None, len(years),
                     float(rho) if eligible else None, float(effective) if eligible else None,
                     float(q) if eligible else None, low, high, status])
    result["table"] = {"columns": ["pair", "start", "end", "years", "rho", "n_effective", "p_adjusted",
                                   "low", "high", "status"], "rows": rows}
    return {"hypotheses": result}
