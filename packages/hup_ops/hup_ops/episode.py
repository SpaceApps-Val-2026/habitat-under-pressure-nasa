"""Compare a complete calendar-month window against equivalent windows."""

from collections import defaultdict
from statistics import mean

from ._series import absent, body, flatten, points
from .registry import Context, operation


def _missing_windows(result):
    return absent(result, "Insufficient complete comparable monthly windows.",
                  es="No hay suficientes periodos mensuales completos y comparables.")


def _episode(name, params, inputs, ctx):
    settings = {"min_years": 20, **params}
    entries = flatten(inputs)
    result = body(name, "value", "E0", settings, entries, ctx)
    result["diagnostics"] = {"n_years": 0}
    months = set(settings.get("months", []))
    year = settings.get("year")
    if len(entries) != 1 or not months or year is None:
        return result, None, []
    rows = entries[0].get("series", [])
    if any("model" in row or "scenario" in row for row in rows):
        return result, None, []
    if len({row["t"] for row in rows}) != len(rows):
        return result, None, []
    if name == "rain_extreme" and result["unit"] != "mm/month":
        return result, None, []
    by_year = defaultdict(dict)
    for date, value in points(entries[0]).items():
        if len(date) < 7 or int(date[5:7]) not in months:
            continue
        month = int(date[5:7])
        values = by_year[int(date[:4])]
        if month in values:
            return result, None, []
        values[month] = value
    total = result["unit"].endswith("/month")
    if total:
        result["unit"] = result["unit"].removesuffix("/month")
    combine = sum if total else mean
    complete = {y: combine(values.values()) for y, values in by_year.items() if values.keys() == months}
    target = complete.get(year)
    history = [value for y, value in complete.items() if y < year or (name == "same_date_anomaly" and y != year)]
    result["diagnostics"]["n_years"] = len(history) + int(target is not None)
    if len(history) < settings["min_years"]:
        history = []
    return result, target, history


@operation("rain_extreme", version="1.0")
def rain_extreme(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    result, target, history = _episode("rain_extreme", params, inputs, ctx)
    result["unit"] = "percentile"
    result["limits"].append({
        "en": "Empirical rank among equivalent prior windows. Equal totals share rank. Return period is unavailable.",
        "es": "Posición empírica entre periodos anteriores equivalentes. Los totales iguales comparten posición. "
        "El periodo de retorno no está disponible.",
    })
    if target is None or not history:
        return {"extreme": _missing_windows(result)}
    result["value"] = 100 * sum(value <= target for value in history) / len(history)
    result["diagnostics"]["rank"] = 1 + sum(value > target for value in history)
    return {"extreme": result}


@operation("same_date_anomaly", version="1.0")
def same_date_anomaly(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    result, target, history = _episode("same_date_anomaly", params, inputs, ctx)
    result["limits"].append({
        "en": "The reference uses the same months from other complete years.",
        "es": "La referencia usa los mismos meses de otros años con datos completos para esos meses.",
    })
    if target is None or not history:
        return {"anomaly": _missing_windows(result)}
    result["value"] = target - mean(history)
    return {"anomaly": result}
