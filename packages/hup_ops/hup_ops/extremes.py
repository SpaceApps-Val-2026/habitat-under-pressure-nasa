"""Return levels of an annual series of maxima: a GEV fitted by L-moments, with a parametric bootstrap."""
from copy import deepcopy

import numpy as np
from scipy.special import gamma

from ._series import absent, body, flatten, points
from .registry import Context, operation

# Defaults decided on 14 November 2026 (A11, delegated by Guillem; docs/decisiones.md and the plan note
# .dev/notes/2026-11-14-plan-lluvia-maxima-diaria.md).
# With 27 years, the simulations of that note give: L-moments nearly unbiased (Gumbel and a shape prior fall short
# in heavy tails), and a nominal 95 % parametric bootstrap that holds the true level 86-90 % of the times.
DEFAULTS = {"return_periods": [2, 5, 10, 25], "min_years": 20, "resamples": 999, "confidence": 0.95}
METHOD = "Parametric bootstrap, GEV by L-moments"
LIMITS = [
    {"en": "Generalised extreme value distribution fitted by L-moments to the annual maxima; it assumes the "
           "climate of the record does not change.",
     "es": "Distribución generalizada de valores extremos ajustada por L-momentos a los máximos anuales; supone "
           "que el clima del registro no cambia."},
    {"en": "A return level is exceeded on average once every so many years; it does not say when.",
     "es": "Un nivel de retorno se supera, de media, una vez cada tantos años; no dice cuándo."},
    {"en": "With about 30 years or fewer, the interval holds the true value less often than its level says "
           "(close to 87 % for a 95 % interval in simulations with 27 years).",
     "es": "Con unos 30 años o menos, el intervalo contiene el valor cierto menos veces de lo que dice su nivel "
           "(cerca del 87 % para uno del 95 % en simulaciones con 27 años)."},
]


def fit(values):
    """GEV by L-moments (Hosking, 1990). Each row is a sample; returns location, scale and k (k = -shape)."""
    x = np.sort(np.atleast_2d(np.asarray(values, dtype=float)), axis=1)
    n = x.shape[1]
    i = np.arange(n)
    b0 = x.mean(1)
    b1 = (i / (n - 1) * x).mean(1)
    b2 = (i * (i - 1) / ((n - 1) * (n - 2)) * x).mean(1)
    l1, l2, l3 = b0, 2 * b1 - b0, 6 * b2 - 6 * b1 + b0
    c = 2 / (3 + l3 / l2) - np.log(2) / np.log(3)
    k = 7.8590 * c + 2.9554 * c * c
    k = np.where(np.abs(k) < 1e-6, 1e-6, k)  # the Gumbel limit, without dividing by zero
    scale = l2 * k / ((1 - 2.0 ** -k) * gamma(1 + k))
    return l1 - scale * (1 - gamma(1 + k)) / k, scale, k


def level(u, a, k, years):
    """The value exceeded on average once every `years` years."""
    return u + a / k * (1 - (-np.log(1 - 1 / years)) ** k)


def period(x, u, a, k):
    """Return period of the value x; infinite above the upper end of a bounded fit."""
    z = np.maximum(1 - k * (x - u) / a, 0.0)
    with np.errstate(divide="ignore", over="ignore"):
        exceed = -np.expm1(-z ** (1 / k))  # 1 - F, without losing the small probabilities
        return np.where(exceed > 0, 1 / exceed, np.inf)


def _yearly(entry: dict) -> bool:
    dated = points(entry)
    return bool(dated) and all(len(t) == 4 and t.isdigit() for t in dated)


# 1.2: with event_year, a second input, the daily series of the episode, gives the value of the episode: its wettest
# day, set against the maxima of the whole years before it. Without it, the episode is the value of its year.
@operation("return_level", version="1.2")
def return_level(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {**DEFAULTS, **params}
    entries = flatten(inputs)
    event_year = settings.get("event_year")
    yearly = [entry for entry in entries if _yearly(entry)]
    episode = None
    if event_year and len(entries) == 2 and len(yearly) == 1:
        # The annual series first: it names the variable and the unit of the results.
        episode = next(entry for entry in entries if entry is not yearly[0])
        entries = [yearly[0], episode]
    base = body("return_level", "value", "E0", settings, entries, ctx)
    base["limits"] += deepcopy(LIMITS)
    base["diagnostics"] = {"n_years": 0}
    keys = [f"level_{years}" for years in settings["return_periods"]] + (["event"] if event_year else [])

    def all_absent(en, es):
        return {key: absent(deepcopy(base), en, es=es) for key in keys}

    if len(entries) != (2 if episode else 1):
        return all_absent("Exactly one annual series of maxima is required.",
                          "Se necesita una única serie anual de máximos.")
    dated = points(entries[0])
    by_year = {int(t): v for t, v in dated.items() if len(t) == 4 and t.isdigit()}
    if len(by_year) != len(dated):
        return all_absent("An annual series is required: one value per year.",
                          "Se necesita una serie anual: un valor por año.")
    # An episode is set against the years before it, as rain_extreme does: never against itself.
    history = [v for year, v in sorted(by_year.items()) if not event_year or year < event_year]
    n = len(history)
    base["diagnostics"]["n_years"] = n
    if n < settings["min_years"] or np.ptp(history) == 0:
        return all_absent(f"At least {settings['min_years']} years with different values are required.",
                          f"Se necesitan al menos {settings['min_years']} años con valores distintos.")
    u, a, k = (float(v[0]) for v in fit(history))
    base["diagnostics"].update({"location": u, "scale": a, "shape": -k, "max_observed": max(history)})
    uniform = np.random.default_rng(ctx.seed).random((settings["resamples"], n))
    boot = fit(u + a / k * (1 - (-np.log(uniform)) ** k))  # samples drawn from the fitted distribution
    tail = (1 - settings["confidence"]) / 2

    def interval(values):
        low, high = np.quantile(values, [tail, 1 - tail])
        if not np.isfinite(high):
            return None
        return {"level": settings["confidence"], "low": float(low), "high": float(high), "method": METHOD}

    results = {}
    for years in settings["return_periods"]:
        key = f"level_{years}"
        result = deepcopy(base)
        result["diagnostics"]["return_period_years"] = years
        if years > n:
            results[key] = absent(result, f"With {n} years, a return period longer than the record is not estimated.",
                                  "not_computed",
                                  es=f"Con {n} años no se estima un periodo de retorno más largo que el registro.")
            continue
        results[key] = {**result, "value": float(level(u, a, k, years))}
        spread = interval(level(*boot, years))
        if spread:  # always finite in practice; a result never carries an empty interval
            results[key]["interval"] = spread
    if event_year:
        result = deepcopy(base)
        result["unit"] = "years"
        if episode is None:
            x, day = by_year.get(event_year), None
        else:
            days = {t: v for t, v in points(episode).items() if len(t) == 10 and t[:4] == str(event_year)}
            day = max(days, key=days.get) if days else None
            x = days.get(day)
        if x is None:
            missing = ("The daily series of the episode has no value in {year}.",
                       "La serie diaria del episodio no tiene valores en {year}.") if episode else (
                       "{year} has no value in the series.", "{year} no tiene valor en la serie.")
            results["event"] = absent(result, missing[0].format(year=event_year),
                                      es=missing[1].format(year=event_year))
            return results
        estimate = float(period(x, u, a, k))
        result["diagnostics"].update({"event_year": event_year, "event_value": x,
                                      "record_level": float(level(u, a, k, n))})  # the level of n years
        if day:
            result["diagnostics"]["event_day"] = day
        with np.errstate(invalid="ignore"):  # infinite periods of a bounded fit: the top is then not finite
            low, high = np.quantile(period(x, *boot), [tail, 1 - tail])
        if low > n:
            # Rarer than the record can tell, the whole interval with it: say so, with the level it exceeds,
            # instead of an extrapolated figure.
            results["event"] = absent(
                result, f"Rarer than {n} years of record can estimate: above the {n}-year level.", "not_computed",
                es=f"Más raro de lo que permiten estimar {n} años de registro: supera el nivel de {n} años.")
            return results
        if estimate > n or not np.isfinite(high):
            # The estimate or the top of its interval leaves the record, the bottom does not: neither a figure
            # nor "rarer than the record" can be asserted.
            results["event"] = absent(
                result, f"At the edge of what {n} years of record can estimate: it cannot be said whether it is "
                        "rarer than the record.",
                es=f"En el límite de lo que permiten estimar {n} años de registro: no se puede afirmar si es más "
                   "raro que el registro.")
            return results
        result["value"] = estimate
        result["interval"] = {"level": settings["confidence"], "low": float(low), "high": float(high),
                              "method": METHOD}
        results["event"] = result
    return results
