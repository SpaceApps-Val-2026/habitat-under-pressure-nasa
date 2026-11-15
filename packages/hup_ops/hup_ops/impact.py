"""Paired-date impact contrasts and exploratory change diagnostics."""
import numpy as np

from ._mk import c2s
from ._series import absent, body, estimate, flatten, points
from .registry import Context, operation

DEFAULTS = {"min_dates": 3, "confidence": 0.95, "resamples": 2000, "alpha": 0.05}


def _difference(site, reference):
    if (site.get("unit"), site.get("variable")) != (reference.get("unit"), reference.get("variable")):
        return {}
    if site.get("support", {}).get("resolution_m") != reference.get("support", {}).get("resolution_m"):
        return {}
    a, b = points(site), points(reference)
    return {date: a[date] - b[date] for date in sorted(a.keys() & b.keys())}


def _windows(dates, settings):
    windows = []
    for key in ("before", "after"):
        bounds = settings.get(key, ())
        if len(bounds) != 2:
            return None
        low, high = map(str, bounds)
        windows.append((low, high))
    if windows[0][1] >= windows[1][0]:
        return None
    return [
        np.array([v for t, v in dates.items() if low <= t[:len(high)] <= high])
        for low, high in windows
    ]


def _contrast(windows):
    before, after = windows
    return float(np.median(after) - np.median(before))


def _area(entry):
    return entry.get("method", {}).get("params", {}).get("area")


def _role(inputs, area, legacy):
    tagged = [entry for entry in flatten(inputs) if _area(entry) == area]
    return tagged or [entry for entry in inputs.get(legacy, []) if _area(entry) is None]


def _paired(inputs):
    site = _role(inputs, "zone", "site")
    reference = _role(inputs, "reference", "reference")
    return site + reference if len(site) == len(reference) == 1 else []


def _interval(samples, confidence):
    tail = (1 - confidence) / 2
    return tuple(float(v) for v in np.quantile(samples, [tail, 1 - tail]))


def _baci(settings, entries, ctx, operation_id):
    result = body(operation_id, "value", "E2", settings, entries, ctx)
    result["diagnostics"] = {
        "n_dates": 0, "pre_trend_control": "unavailable", "interval_crosses_zero": "unavailable",
    }
    if len(entries) != 2:
        return absent(result, "A site series and a reference series are required.")
    labels = (("Analysis area", "Zona de análisis"), ("Reference", "Referencia"))
    for entry, (en, es) in zip(entries, labels, strict=True):
        if missing := entry.get("absence"):
            detail = missing.get("detail", {})
            return absent(result, f"{en}: " + detail.get("en", missing["reason"]), missing["reason"],
                          es=f"{es}: " + detail.get("es", detail.get("en", missing["reason"])))
    windows = _windows(_difference(*entries), settings)
    if windows is None or min(map(len, windows)) < settings["min_dates"]:
        return absent(result, "Insufficient paired dates in comparable before and after windows.",
                      es="No hay bastantes fechas emparejadas en ventanas comparables antes y después.")
    before, after = windows
    result["diagnostics"]["n_dates"] = len(before) + len(after)
    if np.ptp(before) == 0:
        pre_trend = False
    else:
        # Same trend test as A5 (C2s); np.ptp(before) > 0 here, so the test always has variation.
        p = c2s(before, settings["alpha"])[0]
        pre_trend = bool(p < settings["alpha"]) if np.isfinite(p) else "unavailable"
    result["diagnostics"]["pre_trend_control"] = pre_trend
    if pre_trend is not False:
        result["evidence_level"] = "E1"
        result["limits"].append({
            "en": "A stable pre-event difference could not be established.",
            "es": "No se pudo establecer una diferencia estable antes del hito.",
        })
    rng = np.random.default_rng(ctx.seed)
    # Each resampled item is a paired scene date, stratified by window.
    samples = [
        _contrast([rng.choice(before, len(before)), rng.choice(after, len(after))])
        for _ in range(settings["resamples"])
    ]
    low, high = _interval(samples, settings["confidence"])
    return estimate(result, _contrast(windows), low, high, settings["confidence"], "Paired-date bootstrap")


@operation("baci_contrast", version="1.1")
def baci_contrast(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {**DEFAULTS, **params}
    entries = _paired(inputs)
    if not entries:
        result = body("baci_contrast", "value", "E2", settings, flatten(inputs), ctx)
        return {"contrast": absent(result, "Exactly one site and one reference series are required.")}
    return {"contrast": _baci(settings, entries, ctx, "baci_contrast")}


@operation("distance_decay", version="1.1")
def distance_decay(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    rings = _role(inputs, "ring", "rings")
    reference = _role(inputs, "reference", "reference")
    settings = {**DEFAULTS, **params}
    summary = body("distance_decay", "table", "E2", settings, rings + reference, ctx)
    bands = params.get("bands_m", [])
    if rings and all(_area(ring) == "ring" for ring in rings):
        rings.sort(key=lambda ring: ring["method"]["params"]["ring_to_km"])
        bands = [[ring["method"]["params"][key] * 1000 for key in ("ring_from_km", "ring_to_km")]
                 for ring in rings]
    if len(reference) != 1 or not rings or len(bands) != len(rings):
        return {"decay": absent(summary, "One reference and one distance band per ring are required.")}
    results = {"decay": summary}
    rows = []
    for index, (ring, band) in enumerate(zip(rings, bands, strict=True)):
        result = _baci(settings, [ring, reference[0]], ctx, "distance_decay")
        results[f"ring-{index}"] = result
        if result["evidence_level"] == "E1":
            summary["evidence_level"] = "E1"
        interval = result.get("interval", {})
        rows.append([*band, result.get("value"), interval.get("low"), interval.get("high"),
                     result.get("absence", {}).get("reason"), result["evidence_level"]])
    summary["table"] = {"columns": ["from_m", "to_m", "contrast", "low", "high", "absence", "evidence_level"],
                        "rows": rows}
    return results


def _split(values, candidates):
    def loss(i):
        return sum(float(np.sum((part - part.mean()) ** 2)) for part in (values[:i], values[i:]))

    return min(candidates, key=loss)


@operation("break_year", version="1.0")
def break_year(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {**DEFAULTS, "min_dates": 4, **params}
    entries = flatten(inputs)
    result = body("break_year", "value", "E2", settings, entries, ctx)
    result["unit"] = "year"
    if len(entries) != 1:
        return {"break": absent(result, "Exactly one annual series is required.")}
    dates = sorted(points(entries[0]).items())
    years = [int(date[:4]) for date, _ in dates]
    values = np.array([v for _, v in dates])
    minimum = settings["min_dates"]
    candidates = range(minimum, len(values) - minimum + 1)
    if not candidates or len(set(years)) != len(years) or np.ptp(values) == 0:
        return {"break": absent(result, "Insufficient annual observations for a level change.")}
    split = _split(values, candidates)
    fitted = np.concatenate([np.full(len(v), v.mean()) for v in (values[:split], values[split:])])
    rng = np.random.default_rng(ctx.seed)
    residuals = values - fitted
    years_sampled = [
        years[_split(fitted + rng.choice(residuals, len(values)), candidates)]
        for _ in range(settings["resamples"])
    ]
    low, high = _interval(years_sampled, settings["confidence"])
    result["interval"] = {"level": settings["confidence"], "low": low, "high": high,
                          "method": "Residual bootstrap around a single level change"}
    result["diagnostics"] = {"n_years": len(years)}
    result["limits"].append({
        "en": "The change year is exploratory and assumes independent annual residuals.",
        "es": "El año de cambio es exploratorio y presupone residuos anuales independientes.",
    })
    result["value"] = years[split]
    return {"break": result}


def _shift(bound, years):
    return bound - years if isinstance(bound, int) else f"{int(bound[:4]) - years}{bound[4:]}"


def _false_events(dates, settings):
    """The case's windows moved back year by year while the series has dates.

    The later window of each false event ends at the latest in the last year of `before`, so it all precedes the event.
    """
    before, after = settings.get("before", ()), settings.get("after", ())
    if not dates or len(before) != 2 or len(after) != 2:
        return []
    first, last = int(min(dates)[:4]), int(str(before[1])[:4])
    events = []
    for years in range(1, int(str(before[0])[:4]) - first + 1):
        event = {"before": [_shift(b, years) for b in before], "after": [_shift(a, years) for a in after]}
        if int(str(event["after"][1])[:4]) <= last:
            events.append(event)
    return events[::-1]


@operation("placebo_test", version="1.1")
def placebo_test(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {**DEFAULTS, "min_controls": 20, **params}
    site, reference = inputs.get("site", []), inputs.get("reference", [])
    controls = inputs.get("controls", [])
    if "false_events" not in params and len(site) == len(reference) == 1:
        settings["false_events"] = _false_events(_difference(site[0], reference[0]), settings)
    result = body("placebo_test", "value", "E2", settings, site + reference + controls, ctx)
    result["unit"] = "percentile"
    criteria = settings.get("control_criteria", "")
    result["diagnostics"] = {"n_controls": 0, "control_criteria": criteria, "n_false_events": 0}
    result["limits"].append({
        "en": "Placebo calibration awaits validation on real observations.",
        "es": "La calibración del placebo está pendiente de validación con observaciones reales.",
    })
    if len(site) != 1 or len(reference) != 1 or not criteria:
        return {"placebo": absent(result, "A site, reference and documented control criteria are required.")}

    def contrast(dates, window_settings):
        windows = _windows(dates, window_settings)
        if windows is not None and min(map(len, windows)) >= settings["min_dates"]:
            return abs(_contrast(windows))
        return None

    case_dates = _difference(site[0], reference[0])
    observed = contrast(case_dates, settings)
    spatial = [contrast(_difference(control, reference[0]), settings) for control in controls]
    spatial = [v for v in spatial if v is not None]
    temporal = [
        contrast(case_dates, event) for event in settings.get("false_events", [])
    ]
    temporal = [v for v in temporal if v is not None]
    result["diagnostics"].update(n_controls=len(spatial), n_false_events=len(temporal))
    if observed is None or len(spatial) < settings["min_controls"] or not spatial + temporal:
        return {"placebo": absent(result, "Insufficient paired dates or usable placebo controls.",
                                  es="No hay bastantes fechas emparejadas ni controles de placebo utilizables.")}
    if not spatial:
        n = len(temporal)
        result["limits"].append({
            "en": f"Placebo from {n} temporal false events only, with no spatial controls.",
            "es": f"Placebo solo con {n} hitos falsos temporales, sin controles espaciales.",
        })
    null = np.array(spatial + temporal)
    ranks = (null < observed).astype(float) + 0.5 * (null == observed)
    result["value"] = float(100 * ranks.mean())
    rng = np.random.default_rng(ctx.seed)
    draws = [100 * rng.choice(ranks, len(ranks)).mean() for _ in range(settings["resamples"])]
    low, high = _interval(draws, settings["confidence"])
    result["interval"] = {
        "level": settings["confidence"], "low": low, "high": high,
        "method": "Bootstrap of empirical placebo ranks conditional on the case",
    }
    return {"placebo": result}

