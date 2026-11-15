"""Anomalies and trend tests for pre-aggregated series."""
import numpy as np
from scipy.stats import theilslopes
from statsmodels.stats.multitest import multipletests

from ._mk import CORRECTION, c2s, sen_interval
from ._series import absent, body, estimate, flatten, points
from .registry import Context, operation
from .seasons import season_end, season_year

# Decided by Guillem on 14 November 2026: C2s is valid from 9 to 44 years (preparation note, section 10).
MIN_YEARS = 9
SHORT_SERIES_YEARS = 15


def _annual(entry, period):
    dated = points(entry)
    years = {int(date[:4]): value for date, value in dated.items()}
    end = period.get("end", "")
    months = period.get("months") or list(range(1, 13))
    if len(years) != len(dated):
        return [], [], False
    excluded = False
    if end:
        # The last season counts only if the period reaches its end; a season across the new year ends in its year.
        year = season_year(end, months)
        excluded = end < season_end(year, months) and year in years
    if excluded:
        del years[year]
    ordered = sorted(years)
    return ordered, [years[year] for year in ordered], excluded


def _trend(params, entry, ctx):
    settings = {"min_years": MIN_YEARS, "alpha": 0.05, **params}
    result = body("trend", "value", "E1", settings, [entry], ctx)
    result["unit"] += "/year"
    years, values, excluded = _annual(entry, entry.get("period") or ctx.period)
    diagnostics = result["diagnostics"] = {
        "n_years": len(years), "correction": CORRECTION,
        "autocorrelation_lag1": "unavailable", "interval_crosses_zero": "unavailable",
    }
    if excluded:
        result["limits"].append({
            "en": "Excluded the final incomplete year.", "es": "Se excluyó el último año por estar incompleto.",
        })
    if len(years) < settings["min_years"] or len(years) != years[-1] - years[0] + 1:
        return absent(result, "A contiguous series of complete years is required.",
                      es="Se necesita una serie continua de años completos.")
    alpha = settings["alpha"]
    slope, _, low, high = theilslopes(values, years, alpha=1 - alpha)
    detrended = np.asarray(values) - slope * np.arange(len(years))
    tested = c2s(values, alpha)
    if tested is None:
        # A constant series: no test is possible and the slope and its interval are zero.
        p, z = 1.0, 0.0
    else:
        p, z, var_s = tested
        low, high = sen_interval(values, var_s, 1 - alpha)
    if np.std(detrended[:-1]) > 0 and np.std(detrended[1:]) > 0:
        diagnostics["autocorrelation_lag1"] = float(np.corrcoef(detrended[:-1], detrended[1:])[0, 1])
    if tested is not None:
        # 1.2 (Guillem, 15 November 2026): always declared, not only when the estimated autocorrelation reaches 0.3.
        # A few decades hide the memory from that estimate, and the rate grows with it: about 7 % at 0.3 and 15 % at
        # 0.6 (preparation note of A5), 27 % at 0.8 (workshop simulation).
        result["limits"].append({
            "en": "If the series carries memory from one year to the next, this test reports a trend that is not "
                  "there more often than 5 times in 100: about 7 with moderate memory (0.3), about 15 with strong "
                  "memory (0.6) and more above that. A short series does not show how much memory it has.",
            "es": "Si la serie guarda memoria de un año a otro, esta prueba da por buena una tendencia que no existe "
                  "más de 5 veces de cada 100: unas 7 con memoria moderada (0,3), unas 15 con memoria alta (0,6) y "
                  "más por encima. Una serie corta no deja ver cuánta memoria tiene.",
        })
    if len(years) < SHORT_SERIES_YEARS:
        result["limits"].append({
            "en": "With fewer than about 15 years, only a trend that is large against year-to-year variation "
                  "can be asserted.",
            "es": "Con menos de unos 15 años, solo se puede afirmar una tendencia grande frente a la variación "
                  "de un año a otro.",
        })
    result["statistic"] = {"name": "Mann-Kendall (Yue-Wang lag 1, variance not below classic)",
                           "p": p, "value": z, "n": len(years)}
    return estimate(result, float(slope), float(low), float(high), 1 - alpha, "Sen slope, Gilbert interval")


@operation("trend", version="1.2")
def trend(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    entries = flatten(inputs)
    if len(entries) != 1:
        return {"trend": absent(body("trend", "value", "E1", params, entries, ctx),
                                "Exactly one annual or seasonal series is required.",
                                es="Se necesita una única serie anual o estacional.")}
    return {"trend": _trend(params, entries[0], ctx)}


@operation("scan_trends", version="1.2")
def scan_trends(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {"min_years": MIN_YEARS, "alpha": 0.05, **params}
    entries = flatten(inputs)
    summary = body("scan_trends", "table", "E1", settings, entries, ctx)
    summary["unit"] = "mixed"
    tested = []
    rows = []
    for index, entry in enumerate(entries):
        # The reference scan excludes constant series from the test family.
        if len(set(points(entry).values())) <= 1:
            continue
        result = _trend(settings, entry, ctx)
        if "statistic" in result:
            tested.append((index, entry, result))
    ps = [result["statistic"]["p"] for _, _, result in tested]
    alpha = settings["alpha"]
    adjusted = multipletests(ps, alpha=alpha, method="fdr_bh")[1] if ps else []
    results = {"scan": summary}
    for (index, entry, result), q in zip(tested, adjusted, strict=True):
        result["method"] = summary["method"]
        result["diagnostics"]["multiple_test_correction"] = "Benjamini-Hochberg"
        statistic = result["statistic"]
        statistic.pop("p")
        statistic["p_adjusted"] = float(q)
        result["direction_assertable"] = bool(result["direction_assertable"] and q < alpha)
        if not result["direction_assertable"] and "value" in result:
            del result["value"]
            result = absent(result, "The adjusted test does not support a direction.", "not_computed",
                            es="La prueba ajustada no permite determinar la dirección de la tendencia.")
        results[f"trend-{index}"] = result
        rows.append([entry.get("id"), result["variable"], result["unit"], result.get("value"),
                     float(q), bool(result["direction_assertable"])])
    summary["diagnostics"] = {
        "n_tests": len(ps), "n_significant_raw": sum(p < alpha for p in ps),
        "n_significant_adjusted": sum(float(q) < alpha for q in adjusted),
        "correction": "Benjamini-Hochberg",
    }
    if not rows:
        results["scan"] = absent(summary, "No series met the trend test requirements.",
                                 es="Ninguna serie cumplió los requisitos para evaluar la tendencia.")
    else:
        summary["table"] = {
            "columns": ["input", "variable", "unit", "slope", "p_adjusted", "direction_assertable"],
            "rows": rows,
        }
    return results


@operation("anomaly", version="1.0")
def anomaly(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {"min_baseline_dates": 1, **params}
    entries = flatten(inputs)
    result = body("anomaly", "series", "E0", settings, entries, ctx)
    if len(entries) != 1 or "baseline" not in params:
        return {"anomaly": absent(result, "A series and a baseline period are required.",
                                  es="Se necesitan una serie y un periodo de referencia.")}
    if entries[0].get("absence"):
        return {"anomaly": {**result, "absence": entries[0]["absence"]}}
    dates = sorted(points(entries[0]).items())
    baseline = params["baseline"]
    reference = [v for t, v in dates if baseline["start"][:len(t)] <= t <= baseline["end"][:len(t)]]
    if len(reference) < settings["min_baseline_dates"]:
        return {"anomaly": absent(result, "Insufficient observations in the baseline.",
                                  es="No hay suficientes observaciones en el periodo de referencia.")}
    mean = float(np.mean(reference))
    result["series"] = [{"t": t, "v": v - mean} for t, v in dates]
    result["diagnostics"] = {"n_baseline_dates": len(reference)}
    return {"anomaly": result}

