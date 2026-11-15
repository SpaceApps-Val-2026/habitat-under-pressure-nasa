"""Render contract fields without changing the stored result."""

import re
from datetime import date
from itertools import groupby
from math import floor, isfinite, log10
from statistics import fmean
from warnings import warn

VARIANCE_FIELDS = ("horizon", "model_variance", "scenario_variance")
MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "es": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(),
}

DIAGNOSTICS = {
    "n_dates": ("dates", "fechas"),
    "n_years": ("years", "años"),
    "n_effective": ("effective sample", "muestra efectiva"),
    "correction": ("correction", "corrección"),
    "autocorrelation_lag1": ("lag-one autocorrelation", "autocorrelación de orden uno"),
    "threshold_c": ("hot-cell threshold (°C, surface)", "umbral de celda caliente (°C, superficie)"),
    "threshold_percentile": ("threshold percentile", "percentil del umbral"),
    "threshold_base_start": ("threshold base from", "base del umbral desde"),
    "threshold_base_end": ("threshold base to", "base del umbral hasta"),
    "threshold_n_values": ("pixel-years in the base", "píxeles-año en la base"),
}

_METHOD_NAMES_ES = {
    "Sen slope": "pendiente de Sen",
    "Sen slope, Gilbert interval": "pendiente de Sen, intervalo de Gilbert",
}


def _method_label(name, language):
    return _METHOD_NAMES_ES.get(name, name) if language == "es" else name


def scale(result):
    return (
        10
        if (
            result.get("method", {}).get("operation") == "trend"
            and result.get("unit", "").endswith("/year")
            and 0 < abs(result.get("value", 0)) < 0.1
        )
        else 1
    )


def number(value, language, decimals=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("The placeholder requires a finite number")
    if decimals is None:
        rounded = float(f"{value:.3g}")
        rendered = f"{rounded:,}".removesuffix(".0")
    else:
        rendered = f"{round(value, decimals):,.{max(decimals, 0)}f}"
    return rendered.translate(str.maketrans(",.", ".,")) if language == "es" else rendered


def precision(result):
    interval = result.get("interval")
    if interval and interval["high"] > interval["low"]:
        magnitude = max(abs(interval["low"]), abs(interval["high"])) * scale(result)
        cap = max(2, 2 - floor(log10(magnitude))) if magnitude < 0.01 else 2
        return min(cap, -floor(log10((interval["high"] - interval["low"]) * scale(result) / 4)))
    return None


def scenario_series(result):
    points = result.get("series", [])
    if points and all(
        isinstance(p.get("t"), str) and isinstance(p.get("scenario"), str)
        and all(type(p.get(field)) in (int, float) and isfinite(p[field]) for field in ("v", "low", "high"))
        and p["low"] <= p["v"] <= p["high"]
        for p in points
    ):
        return points
    return []


def state_points(result):
    times = set()
    points = []
    for p in result.get("series", []):
        t, v = p["t"], p.get("v")
        if t in times or "scenario" in p or "model" in p:
            return []
        times.add(t)
        if type(v) in (int, float) and isfinite(v):
            points.append((t, v))
    return sorted(points)


def _month(year, month, language):
    separator = " de " if language == "es" else " "
    return MONTHS[language][month - 1] + separator + str(year)


def _state_text(result, language, units):
    points = state_points(result)
    unit = result["unit"]
    monthly_totals = unit.endswith("/month")
    unit = unit.removesuffix("/month") if monthly_totals else unit
    unit = units.get(unit, unit)
    total = sum(v for _, v in points) if monthly_totals else fmean(v for _, v in points)
    peak = max(v for _, v in points)
    peak_times = []
    # A series of one value a year (every t on the 1st of January) says the year, not the date.
    yearly = not monthly_totals and all(re.fullmatch(r"\d{4}-01-01", t) for t, _ in points)
    for t, v in points:
        if v == peak:
            monthly_date = re.fullmatch(r"(\d{4})-(\d{2})(?:-01)?", t)
            peak_times.append(t[:4] if yearly else _month(*(int(x) for x in monthly_date.groups()), language)
                              if monthly_totals and monthly_date else t)
    suffix = f" {unit}" if unit else ""
    value = f"{number(total, language)}{suffix}"
    top = f"{number(peak, language)}{suffix}"
    when = ", ".join(peak_times)
    if language == "es":
        return f"{'total' if monthly_totals else 'media'} de los datos disponibles {value}, máximo {top} ({when})"
    return f"{'total' if monthly_totals else 'mean'} of available observations {value}, maximum {top} ({when})"


def scenario_variances(result):
    table = result.get("table", {})
    columns = table.get("columns", [])
    if any(columns.count(field) != 1 for field in VARIANCE_FIELDS):
        return []
    indices = [columns.index(field) for field in VARIANCE_FIELDS]
    rows = table.get("rows", [])
    if not all(isinstance(row, list) and len(row) == len(columns) for row in rows):
        return []
    values = [tuple(row[i] for i in indices) for row in rows]
    if all(type(year) is int and all(type(x) in (int, float) and isfinite(x) and x >= 0 for x in (a, b))
           for year, a, b in values):
        return values
    return []


RESOURCE_FIELDS = ("low", "high", "reference", "quantity", "per")


def resource_row(result):
    """The one row of a resource_estimate: its range, reference, size and the ladder step (`basis`, assumed when
    an older result has none). None if incomplete."""
    if result.get("method", {}).get("operation") != "resource_estimate":
        return None
    table = result.get("table", {})
    columns, rows = table.get("columns", []), table.get("rows", [])
    if any(columns.count(field) != 1 for field in RESOURCE_FIELDS) or len(rows) != 1:
        return None
    if not isinstance(rows[0], list) or len(rows[0]) != len(columns):
        return None
    row = {"basis": "assumed", **dict(zip(columns, rows[0], strict=True))}
    numbers = all(type(row[k]) in (int, float) and isfinite(row[k]) for k in ("low", "high", "quantity"))
    texts = all(isinstance(row[k], str) and row[k] for k in ("reference", "per"))
    if row["basis"] == "typical":
        numbers = numbers and all(type(row.get(k)) in (int, float) and isfinite(row[k])
                                  for k in ("quantity_low", "quantity_high"))
        texts = texts and isinstance(row.get("quantity_reference"), str) and bool(row["quantity_reference"])
    known = row["basis"] in ("assumed", "input", "typical", "per_unit")
    return row if known and numbers and texts and row["low"] <= row["high"] else None


def _resource_text(result, language, units):
    row = resource_row(result)
    unit = units.get(result["unit"], result["unit"])
    low, high, quantity = (number(row[key], language) for key in ("low", "high", "quantity"))
    per, ref, basis = row["per"], row["reference"], row["basis"]
    if per == "people":
        person = "persona" if language == "es" else "person"
        per = person if basis == "per_unit" or row["quantity"] == 1 else units.get(per, per)
    if basis == "typical":
        small, large = (number(row[key], language) for key in ("quantity_low", "quantity_high"))
        if language == "es":
            return (f"tamaño de ejemplo publicado: {quantity} {per}, referencia: {row['quantity_reference']}; "
                    f"rango a ese tamaño: de {low} a {high} {unit}; los tamaños de ejemplo publicados van de {small} a "
                    f"{large} {per}, sin multiplicarse en el rango; coeficientes: {ref}")
        return (f"published example size: {quantity} {per}, reference: {row['quantity_reference']}; range at that "
                f"size: {low} to {high} {unit}; the published example sizes go from {small} to {large} {per}, not "
                f"multiplied into the range; coefficients: {ref}")
    if basis == "per_unit":
        if language == "es":
            return f"por {per}; rango: de {low} a {high} {unit}; referencia: {ref}"
        return f"per {per}; range: {low} to {high} {unit}; reference: {ref}"
    labels = {"assumed": ("assumed", "supuesto"), "input": ("quantity", "cantidad")}[basis]
    if language == "es":
        return f"{labels[1]}: {quantity} {per}; rango: de {low} a {high} {unit}; referencia: {ref}"
    return f"{labels[0]}: {quantity} {per}; range: {low} to {high} {unit}; reference: {ref}"


def format_field(field, result, language, units, *, method_name=False):
    value = result.get(field)
    factor = scale(result)
    if field == "value":
        return number(value * factor, language, precision(result))
    if field == "interval":
        if not isinstance(value, dict) or not {"low", "high"} <= value.keys():
            raise ValueError("Invalid interval")
        low, high = (number(value[key] * factor, language, precision(result)) for key in ("low", "high"))
        if "level" not in value:
            return f"{low}–{high}"
        level = number(value["level"] * 100, language)
        return (
            f"intervalo del {level} %: de {low} a {high}"
            if language == "es"
            else (f"{level}% interval: {low} to {high}")
        )
    if field == "unit":
        if factor == 10:
            value = value.removesuffix("/year") + "/decade"
        if value not in units:
            warn(f"Untranslated result unit: {value}", stacklevel=3)
        return units.get(value, value)
    if field == "period":
        return _window((value["start"], value["end"]), language)
    if field == "support":
        return f"{number(value['resolution_m'] / 1000, language)} km"
    if field == "return_period":
        return number(result["diagnostics"]["return_period_years"], language)
    if field == "record":
        return number(result["diagnostics"]["n_years"], language)
    if field == "event_value":
        return number(result["diagnostics"]["event_value"], language)
    if field == "series" and result["method"]["operation"] == "read_series":
        return _state_text(result, language, units)
    if field == "series" and result["method"]["operation"] == "climate_delta":
        unit = units.get(result["unit"], result["unit"])
        separator = " a " if language == "es" else " to "
        points = scenario_series(result)
        change_only = result["method"].get("params", {}).get("output") == "change"

        def signed(value):
            rendered = number(value, language)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return "+" + rendered
            return rendered

        if change_only:
            ref = result["method"].get("params", {}).get("reference", [1995, 2014])
            ref_text = f"{ref[0]}–{ref[1]}"
            rel = f" respecto a {ref_text}" if language == "es" else f" relative to {ref_text}"
            reported = "; ".join(
                f"{p['t']} ({p['scenario']}): {signed(p['v'])} {unit} "
                f"({signed(p['low'])}{separator}{signed(p['high'])} {unit}){rel}"
                for p in points
            )
        else:
            reported = "; ".join(
                f"{p['t']} ({p['scenario']}): {number(p['v'], language)} {unit} "
                f"({number(p['low'], language)}{separator}{number(p['high'], language)} {unit})"
                for p in points
            )
        windows = result["method"].get("params", {}).get("windows", {})
        horizons = sorted({p["t"] for p in points} & windows.keys())
        if horizons:
            title = "Ventanas de proyección" if language == "es" else "Projection windows"
            rows = [f"{horizon}: {_window(windows[horizon], language)}" for horizon in horizons]
            reported += f". {title}: " + ", ".join(rows)
        return reported
    if field == "table" and result["method"]["operation"] == "resource_estimate":
        return _resource_text(result, language, units)
    if field == "table" and result["method"]["operation"] == "uncertainty_split":
        labels = {"en": ("models", "scenarios"), "es": ("modelos", "escenarios")}[language]
        return "; ".join(
            f"{horizon}: {labels[0]} {number(model, language)}, {labels[1]} {number(scenario, language)}"
            for horizon, model, scenario in scenario_variances(result)
        ) + f" {result['unit']}"
    if field == "method":
        params = value.get("params", {})
        if not method_name and "before" in params and "after" in params:
            return " / ".join(_window(params[k], language) for k in ("before", "after"))
        if (
            not method_name and value["operation"] in {"rain_extreme", "same_date_anomaly"}
            and params.get("months") and "year" in params
        ):
            year = params["year"]
            windows = []
            for _, run in groupby(enumerate(sorted(set(params["months"]))), lambda pair: pair[1] - pair[0]):
                months = [month for _, month in run]
                first, last = months[0], months[-1]
                date(year, last, 1)  # Reject invalid years or months before formatting.
                if first == last:
                    windows.append(_month(year, first, language))
                else:
                    join = " a " if language == "es" else " to "
                    windows.append(MONTHS[language][first - 1] + join + _month(year, last, language))
            return " / ".join(windows)
        if result.get("interval", {}).get("method"):
            return _method_label(result["interval"]["method"], language)
        return value["operation"].replace("_", " ")
    if field == "statistic":
        details = [_method_label(value["name"], language)]
        if "value" in value:
            details.append(number(value["value"], language))
        for key, label in (("p_adjusted", "p ajustada" if language == "es" else "adjusted p"), ("p", "p"), ("n", "n")):
            if key in value and not (key == "p" and "p_adjusted" in value):
                details.append(f"{label} = {number(value[key], language)}")
        return ", ".join(details)
    if field == "diagnostics":
        details = []
        for key, labels in DIAGNOSTICS.items():
            if key in value:
                datum = value[key]
                displayed = number(datum, language) if type(datum) in (int, float) else datum
                details.append(f"{labels[language == 'es']}: {displayed}")
        return ", ".join(details)
    raise ValueError(f"Unsupported result field: {field}")


def _window(bounds, language):
    start, end = map(str, bounds)
    if re.fullmatch(r"\d{4}-01-01", start) and re.fullmatch(r"\d{4}-12-31", end):
        start, end = start[:4], end[:4]
    return f"de {start} a {end}" if language == "es" else f"{start} to {end}"
