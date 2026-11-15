"""Partial rank associations on aligned annual anomalies."""

import re

import numpy as np
from scipy.stats import rankdata
from scipy.stats import t as student
from statsmodels.stats.multitest import multipletests

from ._series import absent, body, flatten, points
from .registry import Context, operation
from .scenarios import _year
from .seasons import SEASONS
from .weather_hypotheses import enabled as hypotheses_enabled
from .weather_hypotheses import evaluate as evaluate_hypotheses

DEFAULTS = {"min_years": 15, "block_years": 3, "resamples": 999, "alpha": 0.05, "lags": [0]}
GAP_MIN_YEARS = 10
GAP_MAX_YEARS = 2
GAP_MAX_FRACTION = 0.2
PRADES_GIL_DOI = re.compile(r"(?<![\w.-])10\.1016/j\.ecmx\.2024\.100706(?![\w/-]|\.[\w.])", re.IGNORECASE)


def _resolve(inputs, role, selector=None):
    entries = flatten(inputs)
    if selector is not None:
        matches = [entry for entry in entries if selector in (entry.get("id"), entry.get("variable"))]
    else:
        matches = inputs.get(role, [])
    return matches[0] if len(matches) == 1 else None


def _annual(entry):
    dated = points(entry)
    annual = {_year(date): value for date, value in dated.items()}
    return annual if len(annual) == len(dated) else {}


def _residuals(values, design):
    return values - design @ np.linalg.lstsq(design, values, rcond=None)[0]


def _partial(data):
    ranks = rankdata(data, axis=0)
    design = np.column_stack([np.ones(len(ranks)), ranks[:, 2:]])
    residual = _residuals(ranks[:, :2], design)
    x, y = residual.T
    norms = np.linalg.norm(residual, axis=0)
    if min(norms) <= 1e-10:
        return None, residual
    return float(np.clip((x @ y) / np.prod(norms), -1, 1)), residual


def _lag1(series, adjacent):
    first, second = series[:-1][adjacent], series[1:][adjacent]
    if min(np.std(first), np.std(second)) == 0:
        return 0.0
    return float(np.corrcoef(first, second)[0, 1])


def _minimum_years(settings, lag):
    controls = 1 if lag == 0 else 2
    return max(settings["min_years"], 2 * settings["block_years"], controls + 3)


def _complete_summers(entries):
    months = {frozenset(entry.get("period", {}).get("months", ())) for entry in entries}
    if len(months) != 1 or next(iter(months)) not in map(frozenset, SEASONS["summer"].values()):
        return False
    methods = [entry.get("method", {}).get("params", {}) for entry in entries]
    return all(method.get("aggregate") == "year" and method.get("require_complete_months") is True
               for method in methods)


def _segments(years, annual, lag, settings):
    if len(years) < _minimum_years(settings, lag):
        return []
    span = range(years[0], years[-1] + 1)
    missing = len(span) - len(years)
    if not missing:
        return [np.arange(len(years))]
    x, y, _ = annual
    if (lag != 0 or not settings.get("allow_regional_gaps") or len(years) < GAP_MIN_YEARS
            or missing > GAP_MAX_YEARS or missing > GAP_MAX_FRACTION * len(span)
            or any(settings[key] != DEFAULTS[key] for key in ("block_years", "resamples", "alpha"))
            or not set(span) <= x.keys() & y.keys()):
        return []
    runs = np.split(np.arange(len(years)), np.flatnonzero(np.diff(years) != 1) + 1)
    return runs if len(runs) == 2 and all(len(run) >= settings["block_years"] for run in runs) else []


def _bootstrap_indices(segments, block, resamples, rng):
    # Bound the allocation for contiguous samples with a caller-supplied resample count.
    for start in range(0, resamples, 1024):
        size = min(resamples - start, 1024)
        indices = []
        for segment in segments:
            n = len(segment)
            n_blocks = (n + block - 1) // block
            starts = rng.integers(0, n - block + 1, size=(size, n_blocks))
            offsets = (starts[:, :, None] + np.arange(block)).reshape(size, -1)[:, :n]
            indices.append(segment[offsets])
        yield from np.concatenate(indices, axis=1)


def _gap_limit(years):
    span = range(years[0], years[-1] + 1)
    missing = ", ".join(str(year) for year in sorted(set(span) - set(years)))
    return {
        "en": f"Evaluated using {len(years)} of {len(span)} complete summers in the paired period. "
              f"The regional control is missing complete summers for {missing}.",
        "es": f"Evaluado con {len(years)} de {len(span)} veranos completos del periodo emparejado. "
              f"Al control regional le faltan veranos completos de {missing}.",
    }


def _sample(x, y, regional, lag, settings, rng):
    years = _paired_years(x, y, regional, lag)
    n = len(years)
    block = settings["block_years"]
    controls = 1 if lag == 0 else 2
    segments = _segments(years, (x, y, regional), lag, settings)
    if not segments:
        return None
    values = np.column_stack(
        [
            [x[year] for year in years],
            [y[year + lag] for year in years],
            [regional[year] for year in years],
        ]
    )
    if lag:
        values = np.column_stack([values, [regional[year + lag] for year in years]])
    design = np.column_stack([np.ones(n), np.asarray(years) - years[0]])
    anomalies = _residuals(values, design)
    if np.any(np.std(anomalies, axis=0) <= 1e-10):
        return None
    rho, residual = _partial(anomalies)
    if rho is None:
        return None
    adjacent = np.diff(years) == 1
    product = np.clip(_lag1(residual[:, 0], adjacent) * _lag1(residual[:, 1], adjacent), 0, 0.999)
    effective = float(n * (1 - product) / (1 + product))
    degrees = effective - controls - 2
    if degrees <= 0:
        return None
    p = 0.0 if abs(rho) == 1 else float(2 * student.sf(abs(rho) * np.sqrt(degrees / (1 - rho**2)), degrees))
    draws = []
    for indices in _bootstrap_indices(segments, block, settings["resamples"], rng):
        draw, _ = _partial(anomalies[indices])
        if draw is not None:
            draws.append(draw)
    if len(draws) < max(2, settings["resamples"] // 2):
        return None
    alpha = settings["alpha"]
    low, high = map(float, np.quantile(draws, [alpha / 2, 1 - alpha / 2]))
    return rho, low, high, p, effective, n


def _paired_years(x, y, regional, lag):
    return sorted(year for year in x if year + lag in y and year in regional and year + lag in regional)


def _comparable(entries):
    return len({entry.get("support", {}).get("resolution_m") for entry in entries}) == 1


@operation("lagged_association", version="1.2")
def lagged_association(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    if "weather_hypotheses" in params:
        if not hypotheses_enabled():
            raise ValueError("Weather hypotheses are disabled.")
        return evaluate_hypotheses(params, inputs, ctx)
    settings = {**DEFAULTS, **params, "effective_sample": "lag-1 residual autocorrelation (provisional)"}
    selected = [_resolve(inputs, role, params.get(role)) for role in ("from", "to", "regional")]
    entries = [entry for entry in selected if entry is not None]
    lags = sorted(set(settings["lags"]))
    if settings.get("allow_regional_gaps"):
        settings["allow_regional_gaps"] = (
            lags == [0] and [entry.get("variable") for entry in entries] == ["ndvi", "lst", "lst"]
            and _complete_summers(entries)
        )
    summary = body("lagged_association", "table", "E3", settings, entries or flatten(inputs), ctx)
    summary["unit"] = "1"
    summary["limits"].append({
        "en": "Effective sample size is provisional. Inputs must use a common spatial resolution.",
        "es": "El tamaño efectivo de la muestra es provisional. Las entradas deben tener la misma resolución espacial.",
    })
    if len(entries) != 3 or not _comparable(entries):
        return {"association": absent(summary, "Three annual series at comparable resolution are required.")}
    x, y, regional = map(_annual, entries)
    rng = np.random.default_rng(ctx.seed)
    tested = [(lag, result) for lag in lags if (result := _sample(x, y, regional, lag, settings, rng))]
    if not tested:
        if settings.get("allow_regional_gaps"):
            en, es = _untestable_reason(entries, (x, y, regional), {0: _paired_years(x, y, regional, 0)}, settings)
            return {"association": absent(summary, en, es=es)}
        return {"association": absent(summary, "Insufficient contiguous years or residual variation.")}
    if settings.get("allow_regional_gaps"):
        years = _paired_years(x, y, regional, 0)
        if years[-1] - years[0] + 1 != len(years):
            summary["limits"].append(_gap_limit(years))
    alpha = settings["alpha"]
    qs = multipletests([values[3] for _, values in tested], method="holm")[1]
    rows = []
    results = {"association": summary}
    by_lag = dict(zip((lag for lag, _ in tested), zip((v for _, v in tested), qs, strict=True), strict=True))
    for lag in lags:
        if lag not in by_lag:
            rows.append([lag, None, None, None, None, "not_testable"])
            continue
        (rho, low, high, p, effective, n), q = by_lag[lag]
        direction = bool(q < alpha and not low <= 0 <= high)
        result = body("lagged_association", "value", "E3", settings, entries, ctx)
        result.update(unit="1", limits=summary["limits"], direction_assertable=direction)
        result["diagnostics"] = {"n_effective": effective, "lags_tested": len(tested), "detrended": True}
        statistic = result["statistic"] = {"name": "Partial Spearman", "n": n}
        if len(lags) > 1:
            statistic["p_adjusted"] = float(q)
            result["diagnostics"]["p_adjusted"] = float(q)
        else:
            statistic["p"] = p
        result["interval"] = {"level": 1 - alpha, "low": low, "high": high, "method": "Paired moving-block bootstrap"}
        if direction:
            result["value"] = rho
        else:
            result = absent(result, "The test does not establish a direction.", "not_computed")
        results[f"lag-{lag}"] = result
        rows.append([lag, rho if direction else None, low, high, float(q), "tested"])
    summary["diagnostics"] = {"lags_tested": len(tested), "detrended": True}
    summary["table"] = {"columns": ["lag_years", "rho", "low", "high", "p_adjusted", "status"], "rows": rows}
    return results


@operation("evaluate_chain", version="1.4")
def evaluate_chain(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    entries = flatten(inputs)
    chain = params.get("chain", {})
    result = body("evaluate_chain", "table", "E0", params, entries, ctx)
    result["unit"] = "1"
    links = chain.get("links", [])
    if not links:
        return {"chain": absent(result, "A declared chain is required.", "not_computed",
                                es="Se necesita una cadena declarada.")}
    rows = []
    ring = params.get("regional_ring")
    local = inputs
    if ring:
        local = {"site": [r for r in entries if r.get("method", {}).get("params", {}).get("area") == "zone"]}
        controls = []
        for entry in entries:
            method = entry.get("method", {}).get("params", {})
            if ([method.get("ring_from_km"), method.get("ring_to_km")] == ring
                    and entry.get("variable") == params.get("regional")):
                controls.append(entry)
        regional = controls[0] if len(controls) == 1 else None
        result["limits"].append({"en": f"The regional control uses the {ring[0]} to {ring[1]} km outer ring. "
                                       "The control does not exclude other construction works inside the ring.",
                                 "es": f"El control regional usa el anillo exterior de {ring[0]} a {ring[1]} km. "
                                       "El control no excluye otras obras dentro del anillo."})
    else:
        regional = _resolve(inputs, "regional", params.get("regional"))
    for link in links:
        source, target = link["from"], link["to"]
        left, right = _resolve(local, "from", source), _resolve(local, "to", target)
        status, en, es = _link_status(link, [left, right, regional], params, ctx, result["limits"])
        rows.append([source, target, status, en, link.get("ref"), es])
        if en and {"en": en, "es": es} not in result["limits"]:
            result["limits"].append({"en": en, "es": es})
    if all(row[2] == "met" for row in rows):
        result["evidence_level"] = "E5"
    result["table"] = {"columns": ["from", "to", "status", "reason", "reference", "reason_es"], "rows": rows}
    return {"chain": result}


def _link_status(link, entries, params, ctx, limits):
    reasons = []
    reference = link.get("ref")
    if (link["from"], link["to"], link["sign"]) == ("ndvi", "lst", "-") and PRADES_GIL_DOI.search(reference or ""):
        limits.append({
            "en": "The published reference supports the sign of the relation by comparing sites within the same "
                  "summer month. On its own it does not support comparing years at the same place.",
            "es": "La referencia publicada sostiene el signo de la relación comparando sitios dentro de un mismo "
                  "mes de verano. Por sí sola no sostiene la comparación entre años en un mismo lugar.",
        })
    if not reference:
        reasons.append(("Missing published reference.", "Falta una referencia publicada."))
    settings = {**DEFAULTS, **{key: params[key] for key in DEFAULTS if key in params}}
    lo, hi = link.get("lag_years", [0, 0])
    lags = list(range(lo, hi + 1))
    greenness_heat = (
        params["chain"].get("id") == "greenness_heat"
        and (link["from"], link["to"], link["sign"]) == ("ndvi", "lst", "-")
        and lags == [0]
    )
    if any(r is None for r in entries):
        reasons.append(("Missing or ambiguous series.", "Faltan series o hay más de una candidata."))
    elif any(r.get("absence") for r in entries):
        for entry in entries:
            if detail := entry.get("absence", {}).get("detail"):
                pair = (detail["en"], detail.get("es") or "Faltan datos para una de las series.")
                if pair not in reasons:
                    reasons.append(pair)
    elif not _comparable(entries):
        reasons.append(("Incompatible spatial resolution.", "Resoluciones espaciales incompatibles."))
    elif greenness_heat and not _complete_summers(entries):
        reasons.append(("The test needs readings with a valid observation for every summer month.",
                        "La prueba necesita lecturas con una observación válida en cada mes del verano."))
    else:
        annual = [_annual(entry) for entry in entries]
        years_by_lag = {lag: _paired_years(*annual, lag) for lag in lags}
        n = max(map(len, years_by_lag.values()), default=0)
        if n < settings["min_years"]:
            minimum = settings["min_years"]
            reasons.append((f"Insufficient paired years. {n} are available and the test requires {minimum}.",
                            f"Años coincidentes insuficientes. Hay {n} y la prueba requiere {minimum}."))
    if reasons:
        return "not_testable", " ".join(en for en, _ in reasons), " ".join(es for _, es in reasons)
    left, right, regional = entries
    paired = {"from": [left], "to": [right], "regional": [regional]}
    if greenness_heat and regional["variable"] == "lst":
        settings["allow_regional_gaps"] = True
    tested = lagged_association({**settings, "lags": lags}, paired, ctx)
    trials = tested["association"].get("table", {}).get("rows", [])
    if not any(row[-1] == "tested" for row in trials):
        return ("not_testable", *_untestable_reason(entries, annual, years_by_lag, settings))
    if params["chain"].get("id") == "greenness_heat" and lags == [0]:
        n = tested["lag-0"]["statistic"]["n"]
        years = years_by_lag[0]
        if years[-1] - years[0] + 1 != len(years):
            limits.append(_gap_limit(years))
            limits.append({
                "en": "The test with gaps was checked in simulations with one or two missing summers. "
                      "An inconclusive result leaves the expected direction open.",
                "es": "La prueba con huecos se comprobó en simulaciones con uno o dos veranos ausentes. "
                      "Un resultado no concluyente deja abierta la dirección esperada.",
            })
        elif n < DEFAULTS["min_years"]:
            limits.append({
                "en": f"With {n} years and no lag, the test reports a spurious relation about 5 to 7 times in 100 "
                      "and only detects strong relations: “not met” does not show that there is no relation.",
                "es": f"Con {n} años y solo el mismo año (sin desfase), la prueba da una relación falsa entre 5 y 7 "
                      "de cada 100 veces y solo distingue relaciones fuertes: un “no cumplido” no demuestra que no "
                      "haya relación.",
            })
    sign = 1 if link["sign"] == "+" else -1
    if any(row[1] is not None and row[1] * sign > 0 for row in trials):
        return "met", "", ""
    return "not_met", "Expected direction not established.", "No se ha establecido la dirección esperada."


def _untestable_reason(entries, annual, years_by_lag, settings):
    eligible = [(lag, years) for lag, years in years_by_lag.items() if len(years) >= _minimum_years(settings, lag)]
    if not eligible:
        lag, years = max(years_by_lag.items(), key=lambda item: len(item[1]) - _minimum_years(settings, item[0]))
        minimum = _minimum_years(settings, lag)
        return (f"Insufficient paired years. {len(years)} are available and the test requires {minimum}.",
                f"Años coincidentes insuficientes. Hay {len(years)} y la prueba requiere {minimum}.")
    if any(years[-1] - years[0] + 1 == len(years) for _, years in eligible):
        return ("There are enough consecutive years, but the residual variation or effective sample is insufficient.",
                "Hay suficientes años consecutivos, pero la variación residual o la muestra efectiva es insuficiente.")

    lag, years = max(eligible, key=lambda item: len(item[1]))
    minimum = _minimum_years(settings, lag)
    period = set(range(years[0], years[-1] + 1))
    shifted = {year + lag for year in period}
    variables = [entry["variable"].upper() if entry["variable"] in {"ndvi", "lst"} else entry["variable"]
                 for entry in entries]
    if _segments(years, annual, lag, settings):
        en = ["There are enough paired summers, but the residual variation or effective sample is insufficient."]
        es = ["Hay suficientes veranos emparejados, pero la variación residual o la muestra efectiva es insuficiente."]
    elif settings.get("allow_regional_gaps"):
        en = ["With a gap, the test needs at least 10 paired summers, one gap of at most two missing summers "
              "covering at most 20% of the paired period, complete local series, runs of at least three "
              "consecutive years and the default test settings."]
        es = ["Con un hueco, la prueba exige diez veranos emparejados como mínimo, un solo hueco de dos veranos "
              "ausentes como máximo y hasta el 20 % del periodo emparejado, series locales completas, tramos "
              "de al menos tres años seguidos y los ajustes de prueba predeterminados."]
    else:
        en = [f"The test requires a series without gaps covering at least {minimum} consecutive years."]
        es = [f"La prueba requiere una serie sin huecos de al menos {minimum} años consecutivos."]
    for variable, values, expected, (en_role, es_role) in zip(
        variables, annual, (period, shifted, period | shifted),
        (("source series", "la serie de origen"), ("target series", "la serie de destino"),
         ("regional control", "la serie del control regional")), strict=True,
    ):
        if missing := sorted(expected - values.keys()):
            dates = ", ".join(map(str, missing))
            en.append(f"The {en_role} ({variable}) lacks data for {dates}.")
            es.append(f"Faltan datos de {dates} en {es_role} ({variable}).")
    return " ".join(en), " ".join(es)
