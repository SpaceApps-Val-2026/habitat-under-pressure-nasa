"""Ingest: the only operations that read data. They go through ctx.data (hup_data.readers), never to a file or URL."""
import numpy as np
import pandas as pd

from ._series import absent, body, flatten
from .registry import operation
from .seasons import bounds, season_months, season_year
from .weather_hypotheses import RESOLUTION, enabled, power_cell

STATISTICS = {"mean": "mean", "sum": "sum", "median": "median"}
# Satellite scenes arrive unevenly by month (more in clear months, twice as many since Landsat 9 in 2022), so the
# year of a satellite series weighs every month the same. Decided by Guillem on 14 November 2026.
BALANCED = "month_balanced"
READ_SERIES_VERSION = "1.2"  # 1.2: Landsat reads Tier 1 only by default (A10); steps saved with T2 are not reused


@operation("zone_layer", version="1")
def zone_layer(params: dict, inputs: dict[str, list[dict]], ctx) -> dict[str, dict]:
    variable = params["variable"]
    if variable not in {"ndvi", "lst"}:
        return ctx.data.read_zone_layer(variable, ctx.zone, params.get("period", ctx.period), params.get("scenario"))
    entries = flatten(inputs)
    contrast = next((result["method"]["params"] for result in entries
                     if result["variable"] == variable and result["method"]["operation"] == "baci_contrast"), {})
    windows = _layer_windows(contrast, ctx.period)
    if windows is None:
        result = body("zone_layer", "layer", "E0", params, entries, ctx)
        result["method"]["version"] = "1"
        del result["evidence_level"]
        return {"missing": absent(
            result, "Landsat impact maps need two comparable windows supported by the map reader.", "not_computed",
            es="Los mapas de impacto de Landsat necesitan dos ventanas comparables que admita el lector de mapas.",
        )}
    return ctx.data.read_zone_layer(variable, ctx.zone, ctx.period, params.get("scenario"),
                                    windows=windows, margin_km=contrast.get("reference_km"))


def _layer_windows(contrast, period):
    windows = {}
    for name in ("before", "after"):
        if name not in contrast or len(contrast[name]) != 2:
            return None
        low, high = map(str, contrast[name])
        if len(low) != len(high):
            return None
        first, last = low[:4], high[:4]
        if len(first) != 4 or len(last) != 4 or not (first.isdecimal() and last.isdecimal()) or first > last:
            return None
        start, end = f"{first}-01-01", f"{last}-12-31"
        # The map reader accepts year bounds and clips them at the plan edges.
        if len(low) > 4 and max(low, period["start"]) != max(start, period["start"]):
            return None
        if len(high) > 4 and min(high, period["end"]) != min(end, period["end"]):
            return None
        windows[name] = [int(first), int(last)]
    return windows if windows["before"][1] < windows["after"][0] else None


@operation("read_exposure", version="1.1")
def read_exposure(params: dict, inputs: dict[str, list[dict]], ctx) -> dict[str, dict]:
    got = ctx.data.read_exposure(params, ctx.zone, ctx.period)
    categories = got.get("categories")
    if categories:
        return {name.replace("_", "-"): _exposure_table({**params, "category": name}, ctx, data)
                for name, data in categories.items()}
    return {"cells": _exposure_table(params, ctx, got)}


def _exposure_table(params, ctx, got):
    result = {
        "type": "table", "variable": params["variable"], "unit": "people", "data_class": "approximated",
        "sources": got["sources"], "evidence_level": "E0", "period": ctx.period,
        "method": {"operation": "read_exposure", "version": "1.1", "params": params},
        **{key: got[key] for key in ("coverage", "limits", "support", "diagnostics") if got.get(key)},
    }
    if ctx.zone.get("id"):
        result["zone"] = ctx.zone["id"]
    if got["absence"]:
        result["absence"] = got["absence"]
    else:
        table = got["data"]
        result["table"] = {"columns": table.columns.tolist(), "rows": table.to_numpy().tolist()}
    return result


def _yearly(table: pd.DataFrame, statistic: str, min_count: int, months=None, *,
            require_complete_months=False, weight_days=False) -> pd.DataFrame:
    """One value per year (and per model and scenario, if the series has them). Short years are dropped, not padded.

    A season that crosses the new year counts in the year it ends."""
    groups = [name for name in ("model", "scenario") if name in table.columns]
    if months:
        requested = {f"{month:02d}" for month in months}
        table = table[table["t"].astype(str).str[5:7].isin(requested)]
    table = table.assign(year=[str(season_year(t, months)) for t in table["t"]])
    if require_complete_months:
        table = table[np.isfinite(pd.to_numeric(table["v"], errors="coerce"))]
        observed = table["t"].astype(str).str[5:7]
        observed = observed.where(observed.str.fullmatch(r"0[1-9]|1[0-2]"))
        month_count = observed.groupby([table[key] for key in (*groups, "year")]).transform("nunique")
        table = table[month_count == len(set(months or range(1, 13)))]
    if statistic == BALANCED:
        return _balanced(table, groups, min_count)
    grouped = table.groupby([*groups, "year"], sort=True)["v"]
    yearly = grouped.agg(STATISTICS[statistic]).to_frame("v")
    if weight_days:
        weights = pd.to_datetime(table["t"]).dt.days_in_month
        weighted = table.assign(days=weights, value_days=table["v"] * weights)
        totals = weighted.groupby([*groups, "year"])[["days", "value_days"]].sum()
        yearly["v"] = totals["value_days"] / totals["days"]
    yearly["n"] = grouped.count()
    yearly = yearly[yearly["n"] >= min_count].reset_index().rename(columns={"year": "t"})
    return yearly[[*groups, "t", "v"]]


def _balanced(table: pd.DataFrame, groups: list[str], min_count: int) -> pd.DataFrame:
    """Every month weighs the same in its year.

    The value of a year is the mean of the usual values of the months (the median of each month over every year read)
    plus the mean anomaly of the months it has. With every month present this is the mean of the monthly medians; a
    month with more scenes counts once, and a missing month is neither counted as zero nor lets the others weigh more.
    """
    table = table.assign(month=table["t"].astype(str).str[5:7])
    parts = table.groupby(groups, sort=True) if groups else [((), table)]
    out = []
    for keys, part in parts:
        normal = part.groupby("month")["v"].median()
        monthly = part.groupby(["year", "month"])["v"].median().rename("m").reset_index()
        monthly["anomaly"] = monthly["m"] - monthly["month"].map(normal)
        value = monthly.groupby("year")["anomaly"].mean() + normal.mean()
        count = part.groupby("year")["v"].count().reindex(value.index)
        frame = pd.DataFrame({"t": value.index, "v": value.to_numpy(), "n": count.to_numpy()})
        for name, key in zip(groups, keys if isinstance(keys, tuple) else (keys,), strict=True):
            frame[name] = key
        out.append(frame[frame["n"] >= min_count])
    if not out:
        return pd.DataFrame(columns=[*groups, "t", "v"])
    return pd.concat(out, ignore_index=True)[[*groups, "t", "v"]].sort_values([*groups, "t"], ignore_index=True)


@operation("read_series", version=READ_SERIES_VERSION)
def read_series(params: dict, inputs: dict[str, list[dict]], ctx) -> dict[str, dict]:
    """A series of one variable for the zone and period of the plan, as measured or aggregated by year."""
    area = params.get("area", "zone")
    base = ctx.zone.get("id") or ctx.zone["name"]
    settings = {**params, "area": area}
    if params.get("hypothesis_power_cell"):
        if not enabled():
            raise ValueError("Weather hypotheses are disabled.")
        bbox = power_cell(ctx.zone)
        if bbox is None:
            result = body("read_series", "series", "E0", settings, [], ctx)
            return {"series": absent(result, "The POWER cell spans the antimeridian.", "not_computed",
                                     es="La celda de POWER cruza el antimeridiano.")}
        zone = {**ctx.zone, "bbox": bbox, "geometry": None}
        series = _read(settings, zone, f"{base}-power-cell", ctx)
        if "absence" not in series:
            series["support"] = {**series.get("support", {}), "resolution_m": RESOLUTION, "cells": 1}
            if params["variable"] != "precipitation":
                series["data_class"] = "reanalysis"
        return {"series": series}
    if area == "reference":
        km = settings.setdefault("reference_km", 3)
        if settings.get("reference_inner_km"):
            return {"series": _outer_reference(settings, km, base, ctx)}
        zone = ctx.data.reference_zone(ctx.zone, km)
        return {"series": _read(settings, zone, f"{base}-reference-{km:g}km", ctx)}
    if area == "ring":
        edges = sorted(set(settings.setdefault("ring_km", [1, 2, 3])))
        wanted = settings.get("ring_index")
        if wanted is not None and not 0 <= wanted < len(edges):
            result = body("read_series", "series", "E0", settings, [], ctx)
            return {"ring": absent(result, "The selected ring index is outside the declared edges.",
                                   "not_computed", es="El índice del anillo no corresponde a los radios declarados.")}
        zone = _outlined(ctx.zone) if wanted is not None else ctx.zone
        zones = ctx.data.ring_zones(zone, edges)
        return {
            f"ring-{index}": _read(
                {**settings, "ring_from_km": start, "ring_to_km": end},
                zone, f"{base}-ring-{start:g}-{end:g}km", ctx,
            )
            for index, (zone, start, end) in enumerate(zip(zones, [0, *edges[:-1]], edges, strict=True))
            if wanted is None or index == wanted
        }
    return {"series": _read(settings, ctx.zone, base, ctx)}


def _outlined(zone):
    """A zone with a geometry: its own, or the outline of its bbox."""
    if zone.get("geometry"):
        return zone
    west, south, east, north = zone["bbox"]
    outline = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    return {**zone, "geometry": {"type": "Polygon", "coordinates": [outline]}}


def _outer_reference(settings, km, base, ctx):
    """The reference as an outer ring: from `reference_inner_km` to `reference_km` from the edge of the zone.

    The same annulus the chain's regional read takes (`area: ring`, `ring_km: [inner, km]`, `ring_index: 1`), and only
    it: the inner disc is not read. Tagged `area: reference`, so baci_contrast takes it as its reference."""
    inner = settings["reference_inner_km"]
    if not 0 < inner < km:
        result = body("read_series", "series", "E0", settings, [], ctx)
        return absent(result, "The inner edge of the reference ring must be between 0 and reference_km.",
                      "not_computed",
                      es="El borde interior del anillo de referencia debe estar entre 0 y reference_km.")
    zone = ctx.data.ring_zones(_outlined(ctx.zone), [inner, km])[1]
    limit = {"en": f"Reference: ring from {inner:g} to {km:g} km from the edge of the zone.",
             "es": f"Referencia: anillo de {inner:g} a {km:g} km desde el borde de la zona."}
    return _read(settings, zone, f"{base}-reference-{inner:g}-{km:g}km", ctx, [limit])


def _threshold(threshold: dict) -> dict:
    base = threshold.get("base_period") or {}
    named = {"threshold_c": threshold.get("value_c"), "threshold_percentile": threshold.get("percentile"),
             "threshold_base_start": base.get("start"), "threshold_base_end": base.get("end"),
             "threshold_n_values": threshold.get("n_values")}
    return {name: value for name, value in named.items() if value is not None}


def _read(params, zone, zone_id, ctx, notes=()):
    variable = params["variable"]
    aggregate, statistic = params.get("aggregate", "none"), params.get("statistic", "mean")
    min_count = int(params.get("min_count", 1))
    period = params.get("read_period", ctx.period)
    if params.get("season") and period and not period.get("months"):
        months = season_months(params["season"], ctx.zone)
        period = {**period, "months": months}
        first, last = bounds(months)
        if first > last and len(period.get("start", "")) >= 7 and int(period["start"][5:7]) <= last:
            # A season across the new year starts in the previous one: read from there so the first year is whole.
            period["start"] = f"{int(period['start'][:4]) - 1}-{first:02d}-01"
    grid = params.get("grid_resolution_m")
    options = {"grid_resolution_m": grid, "grid_min_years": params.get("grid_min_years", 1)} if grid else {}
    if params.get("land_only"):
        options["land_only"] = True  # absent by default: the reader is called exactly as before
    if "tier1_only" in params:  # absent, the reader's default (Tier 1 only, decision 1 of A10) and its limit apply
        options["tier1_only"] = bool(params["tier1_only"])
    for name in ("percentile", "base_period"):  # absent, the call is the one of before
        if name in params:
            options[name] = params[name]
    got = ctx.data.read_series(variable, zone, period, **options)
    body = {
        "type": "series", "variable": variable, "data_class": "measured",
        "sources": got["sources"], "evidence_level": "E0",
        "method": {"operation": "read_series", "version": READ_SERIES_VERSION, "params": params},
    }
    if period:
        body["period"] = {key: period[key] for key in ("start", "end", "months") if key in period}
    body["zone"] = zone_id
    if got.get("threshold"):  # the threshold the reader used, as declared diagnostics of read_series
        body["diagnostics"] = _threshold(got["threshold"])
    if got.get("limits"):  # what the reader says about its reading, in every read (decision A9, Guillem)
        body["limits"] = got["limits"]
    mask = got.get("land_mask") if params.get("land_only") else None
    notes = [*notes, *([mask["detail"]] if mask else [])]
    if notes:
        limits = list(body.get("limits", []))
        body["limits"] = limits + [note for note in notes if note not in limits]
    if got["absence"] is not None:
        return {**body, "absence": got["absence"]}

    table = got["data"]
    if aggregate == "year":
        complete_months = params.get("require_complete_months", False)
        weight_days = params.get("hypothesis_power_cell", False) and variable != "precipitation"
        table = _yearly(table, statistic, min_count, (period or {}).get("months"),
                        require_complete_months=complete_months, weight_days=weight_days)
        if table.empty:
            detail = {"en": f"No year has at least {min_count} values of {variable} in this period.",
                      "es": f"Ningún año tiene al menos {min_count} valores de {variable} en este periodo."}
            if complete_months:
                detail = {"en": "No season has a valid observation in every required month and meets the "
                                "minimum observation count.",
                          "es": "Ninguna temporada tiene una observación válida en cada mes requerido y alcanza "
                                "el mínimo de observaciones."}
            return {**body, "absence": {"reason": "insufficient_qa", "detail": detail}}
    keep = [name for name in ("t", "v", "model", "scenario") if name in table.columns]
    rows = [{**{name: (str(row[name]) if name != "v" else float(row[name])) for name in keep}}
            for row in table[keep].to_dict(orient="records")]
    unit = got["unit"]
    if aggregate == "year" and statistic == "sum" and unit.endswith("/month"):
        unit = unit.removesuffix("/month") + "/year"  # twelve monthly totals added up are a yearly total
        if params.get("hypothesis_power_cell") and params.get("season"):
            unit = unit.removesuffix("/year")
    body.update(unit=unit, series=rows)
    if got["support"]:
        body["support"] = got["support"]
    if got["coverage"]:
        body["coverage"] = {"valid_pct": float(got["coverage"]["valid_pct"]), "n_dates": int(len(rows))}
    return body
