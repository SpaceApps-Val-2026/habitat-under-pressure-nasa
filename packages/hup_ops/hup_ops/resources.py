"""Resource and stress calculations for supplied archetypes and index parameters."""

import re
from collections import Counter
from math import isfinite

import numpy as np
from SALib.analyze import sobol
from SALib.sample import sobol as sobol_sample

from ._series import absent, body, flatten, least_direct, points
from .registry import Context, operation


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _result(name, kind, params, entries, ctx):
    result = body(name, kind, "E0", params, entries, ctx)
    result["data_class"] = least_direct((result["data_class"], "approximated"))
    return result


def _archetype_source(archetype, ref=None, suffix=""):
    """A published reference as a source: its first link, or a stable name when it has none."""
    ref = archetype.get("ref") if ref is None else ref
    name = (archetype.get("id") or "archetype") + suffix
    link = re.search(r"https?://[^\s,;)]+", ref) if isinstance(ref, str) else None
    source = {"source": name, "url": link[0].rstrip(".") if link else f"urn:hup:archetype:{name}"}
    if isinstance(ref, str) and ref:
        source["product"] = ref
    return source


def typical_size(archetype):
    """(low, central, high, ref) of the archetype's typical size in its `per` unit, or None when it has none
    complete with a published reference. A missing central is the midpoint, as for the coefficients."""
    typical = archetype.get("typical")
    if not isinstance(typical, dict) or not isinstance(typical.get("ref"), str) or not typical["ref"]:
        return None
    low, high, central = (typical.get(key) for key in ("low", "high", "central"))
    if not _number(low) or not _number(high) or not 0 < low <= high:
        return None
    central = low + (high - low) / 2 if central is None else central
    if not _number(central) or not low <= central <= high:
        return None
    return low, central, high, typical["ref"]


def basis(params: dict, has_inputs: bool = False) -> str:
    """Which step of the answer ladder an estimate uses: input (a quantity computed by a previous step), assumed
    (a size stated in the question), typical (the archetype's typical size, catalog data) or per_unit."""
    if has_inputs:
        return "input"
    if params.get("quantity") is not None:
        return "assumed"
    return "typical" if typical_size(params.get("archetype") or {}) else "per_unit"


LADDER = {
    "assumed": ("The size is the one stated in the question, an assumption, not a measurement.",
                "El tamaño es el que dice la pregunta, un supuesto, no una medida."),
    "typical": ("Estimate for an example size published with a reference. The range is that of the unit "
                "coefficients at that size; it has no probability level.",
                "Estimación para un tamaño de ejemplo publicado con su referencia. El rango es el de los coeficientes "
                "unitarios a ese tamaño; no tiene nivel de probabilidad."),
    "per_unit": ("The catalog has no published example size for this archetype: the answer is given per "
                 "unit of reference.",
                 "El catálogo no tiene un tamaño de ejemplo publicado para este arquetipo: la respuesta se da por "
                 "unidad de referencia."),
}


def annual(unit, values):
    """A per-day unit read as a year: (unit, values) with x365 and litres as m³, or None for any other unit."""
    if not isinstance(unit, str) or not unit.endswith("/day"):
        return None
    base = unit.removesuffix("/day")
    factor = 365 / 1000 if base == "L" else 365
    return ("m3" if base == "L" else base) + "/year", tuple(value * factor for value in values)


# Units of the range sentence; a cubic hectometre is a million m³, the usual word for a municipality's water.
UNIT_WORDS = {"L/day": ("L/day", "L/día"), "m3/year": ("m³ a year", "m³ al año"),
              "hm3/year": ("hm³ a year", "hm³ al año"), "MWh/year": ("MWh a year", "MWh al año")}


def _figure(value, language):
    rounded = float(f"{value:.4g}" if abs(value) >= 1000 else f"{value:.3g}")
    text = f"{rounded:,}".removesuffix(".0")
    return text.translate(str.maketrans(",.", ".,")) if language == "es" else text


def _range_text(unit, values, language):
    if unit == "m3/year" and min(values) >= 1e6:
        unit, values = "hm3/year", tuple(value / 1e6 for value in values)
    word = UNIT_WORDS.get(unit, (unit, unit))[language == "es"]
    central, low, high = (_figure(value, language) for value in values)
    if language == "es":
        return f"{central} {word} (rango: de {low} a {high})"
    return f"{central} {word} (range: {low} to {high})"


@operation("resource_estimate", version="1.3")
def resource_estimate(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    """Size times the archetype's unit coefficients (low, central, high), by a ladder of answers (see `basis`).

    input: one input result in the archetype's `per` unit, as before. assumed: `params.quantity` ({value, unit}),
    the size the question states; the result is illustrative. typical: the archetype's `typical` size (catalog data
    with its reference): its central size times the three coefficients, its smallest and largest size said apart
    (table and a limit), never multiplied into the range. per_unit: one unit of `per`. Without
    coefficients and a reference, an absence not_computed.
    """
    entries = flatten(inputs)
    archetype = params.get("archetype", {})
    declared = params.get("quantity")
    step = basis(params, bool(entries))
    result = _result("resource_estimate", "value", params, entries, ctx)
    if not isinstance(archetype, dict):  # the plan carries the catalog entry, as evaluate_chain carries its chain
        detail = "The plan has to carry the catalog archetype, not only its id."
        es = "El plan tiene que llevar el arquetipo del catálogo, no solo su id."
        return {"estimate": absent(result, detail, "not_computed", es=es)}
    result["variable"] = archetype.get("resource") or result["variable"]
    result["unit"] = archetype.get("unit") or result["unit"]
    for limit in archetype.get("limits", []):
        if limit not in result["limits"]:
            result["limits"].append(limit)
    if step != "input":
        result.pop("period", None)  # the size of an installation refers to no period of the place
        result["sources"] = [_archetype_source(archetype)]
    elif archetype.get("ref"):
        result["sources"].append(_archetype_source(archetype))
    if step == "assumed":
        result["data_class"] = "illustrative"  # the person's assumption, not a measurement
    low, high, central = (archetype.get(key) for key in ("low", "high", "central"))
    ref, archetype_id = archetype.get("ref"), archetype.get("id")
    if not all((ref, archetype_id, archetype.get("per"), archetype.get("unit"))):
        return {"estimate": absent(result, "The archetype requires units and a published reference.", "not_computed",
                                   es="El arquetipo necesita sus unidades y una referencia publicada.")}
    if not _number(low) or not _number(high) or not 0 <= low <= high:
        return {"estimate": absent(result, "The archetype requires a complete coefficient range.", "not_computed",
                                   es="El arquetipo necesita un rango de coeficientes completo.")}
    if central is None:
        central = low + (high - low) / 2
    if not _number(central) or not low <= central <= high:
        return {"estimate": absent(result, "The central coefficient is outside its range.", "not_computed",
                                   es="El coeficiente central está fuera de su rango.")}
    size_ref, year = None, None
    if step == "input":
        if len(entries) != 1 or entries[0].get("unit") != archetype["per"]:
            return {"estimate": absent(result, "One input quantity in the archetype denominator unit is required.",
                                       es="Hace falta una cantidad en la unidad de referencia del arquetipo.")}
        quantity = entries[0].get("value")
        if "series" in entries[0]:  # Guillem, A9: a series counts with its last year with a value
            known = points(entries[0])
            if not known:
                return {"estimate": absent(result, "The input series has no value to use.",
                                           es="La serie de entrada no tiene ningún valor que usar.")}
            year = max(known)
            quantity = known[year]
            year = str(year)[:4]
        sizes = (quantity,) * 3
    elif step == "assumed":
        if not isinstance(declared, dict) or declared.get("unit") != archetype["per"]:
            return {"estimate": absent(result, "The stated size must be counted in the archetype unit.",
                                       es="El tamaño dicho debe contarse en la unidad del arquetipo.")}
        sizes = (declared.get("value"),) * 3
    elif step == "typical":
        size_low, size_central, size_high, size_ref = typical_size(archetype)
        # Guillem, 14 November (A9, «si a les recomanacions»): the range is the coefficients' at the example size;
        # the smallest and largest published sizes are said apart, never multiplied into the range.
        sizes = (size_central,) * 3
        if size_ref != ref:
            result["sources"].append(_archetype_source(archetype, size_ref, "-typical"))
    else:
        sizes = (1, 1, 1)
    if not all(_number(size) and size >= 0 for size in sizes):
        return {"estimate": absent(result, "The input quantity is unavailable or invalid.",
                                   es="La cantidad de entrada no está disponible o no es válida.")}
    lower, estimate, upper = (size * coefficient
                              for size, coefficient in zip(sizes, (low, central, high), strict=True))
    if not all(map(isfinite, (estimate, lower, upper))):
        return {"estimate": absent(result, "The estimate exceeds the supported numeric range.", "not_computed",
                                   es="La estimación supera el rango numérico admitido.")}
    cited = ref.rstrip().removesuffix(".")
    result["limits"].append({
        "en": f"Archetype {archetype_id}. Reference: {cited}. Range from the unit coefficients.",
        "es": f"Arquetipo {archetype_id}. Referencia: {cited}. El rango procede de los coeficientes unitarios.",
    })
    # The figure and its range in words, with a per-day figure also read as a year: findings carry placeholders
    # only (analysis.schema.json), so this is where a person reads the range whatever the screen can fill.
    yearly = annual(result["unit"], (estimate, lower, upper))
    said = {language: _range_text(result["unit"], (estimate, lower, upper), language) for language in ("en", "es")}
    if yearly:
        said = {language: text + (", that is " if language == "en" else ", es decir, ")
                + _range_text(*yearly, language) for language, text in said.items()}
    result["limits"].append({"en": f"Estimate: {said['en']}. The range has no probability level.",
                             "es": f"Estimación: {said['es']}. El rango no tiene nivel de probabilidad."})
    if step in LADDER:
        en, es = LADDER[step]
        result["limits"].append({"en": en, "es": es})
    result["value"] = estimate
    # The supplied range has no probability level. Preserve it as endpoints in a structured breakdown.
    result["table"] = {
        "columns": ["resource", "unit", "low", "high", "archetype", "reference", "quantity", "per", "basis",
                    "quantity_low", "quantity_high", "quantity_reference"],
        "rows": [[result["variable"], result["unit"], lower, upper, archetype_id, ref, sizes[1], archetype["per"],
                  step, size_low if step == "typical" else sizes[0], size_high if step == "typical" else sizes[2],
                  size_ref]],
    }
    if yearly:  # version 1.3: a per-day estimate also as a year total, for whoever reads the table
        result["table"]["columns"] += ["annual_unit", "annual", "annual_low", "annual_high"]
        result["table"]["rows"][0] += [yearly[0], *yearly[1]]
    if step == "typical":
        result["limits"].append({
            "en": f"The published example sizes go from {size_low:g} to {size_high:g} {archetype['per']}; they are "
                  "said apart and are not multiplied into the range.",
            "es": f"Los tamaños de ejemplo publicados van de {size_low:g} a {size_high:g} {archetype['per']}; se dicen "
                  "aparte y no se multiplican en el rango.",
        })
    if year is not None:
        name = entries[0].get("variable", "quantity")
        result["limits"].append({
            "en": f"Input {name} of {year}, the last year with a value; the coefficient year is that of its reference.",
            "es": f"Entrada {name} de {year}, el último año con valor; el año del coeficiente es el de su referencia.",
        })
        result["table"]["columns"].append("quantity_year")
        result["table"]["rows"][0].append(year)
    return {"estimate": result}


def _range(entry):
    table = entry.get("table", {})
    if entry.get("method", {}).get("operation") == "resource_estimate":
        row = dict(zip(table["columns"], table["rows"][0], strict=True))
        return row["low"], row["high"]
    interval = entry.get("interval", {})
    return interval.get("low"), interval.get("high")


def _index(values, thresholds, weights):
    with np.errstate(divide="ignore"):
        return np.exp((np.log(values) - np.log(thresholds)) @ weights)


def stress_index(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {"sample_power": 10, "confidence": 0.95, **params}
    entries = flatten(inputs)
    result = _result("stress_index", "value", settings, entries, ctx)
    result.update(variable="stress_index", unit="1")
    components = settings.get("components", {})
    power, confidence = settings["sample_power"], settings["confidence"]
    if not components or not isinstance(power, int) or not 5 <= power <= 14 or not 0 < confidence < 1:
        return {"index": absent(result, "Components and valid sampling parameters are required.", "not_computed")}
    names = sorted(components)
    entries_by_variable = {entry.get("variable"): entry for entry in entries}
    counts = Counter(entry.get("variable") for entry in entries)
    values, bounds, thresholds, weights = [], [], [], []
    for name in names:
        entry = entries_by_variable.get(name, {})
        central = entry.get("value")
        if counts[name] != 1 or "absence" in entry or not _number(central):
            return {"index": absent(result, f"Missing or ambiguous component: {name}.", "not_computed")}
        low, high = _range(entry)
        if not _number(low) or not _number(high) or not 0 <= low <= central <= high:
            return {"index": absent(result, f"Missing or invalid range for {name}.", "not_computed")}
        threshold, weight = (components[name].get(key) for key in ("threshold", "weight"))
        if not _number(threshold) or not _number(weight) or min(threshold, weight) <= 0:
            return {"index": absent(result, f"Invalid threshold or weight for {name}.", "not_computed")}
        values.append(central)
        bounds.append([low, high])
        thresholds.append(threshold)
        weights.append(weight)

    weights = np.array(weights) / max(weights)
    weights /= weights.sum()
    low, high = np.array(bounds).T
    rng = np.random.default_rng(ctx.seed)
    draws = rng.uniform(low, high, size=(2**power, len(names)))
    tail = (1 - confidence) / 2
    lower, upper = map(float, np.quantile(_index(draws, thresholds, weights), [tail, 1 - tail]))
    central = float(_index(np.array(values), thresholds, weights))
    if not all(map(isfinite, (central, lower, upper))):
        return {"index": absent(result, "The index exceeds the supported numeric range.", "not_computed")}
    result.update(
        value=central,
        interval={
            "level": confidence,
            "low": lower,
            "high": upper,
            "method": "Monte Carlo with independent uniform inputs",
        },
    )
    result["limits"].append({
        "en": "Provisional weighted geometric mean of component-to-threshold ratios.",
        "es": "Media geométrica ponderada provisional de los cocientes entre cada componente y su umbral.",
    })
    result["table"] = {
        "columns": ["component", "value", "low", "high", "threshold", "normalized_weight"],
        "rows": [
            [name, value, *band, threshold, float(weight)]
            for name, value, band, threshold, weight in zip(names, values, bounds, thresholds, weights, strict=True)
        ],
    }

    varying = np.flatnonzero(high > low)
    indices = {}
    if len(varying):
        problem = {
            "num_vars": len(varying),
            "names": [names[i] for i in varying],
            "bounds": [bounds[i] for i in varying],
        }
        seed = int(rng.integers(1, 2**32))
        design = sobol_sample.sample(problem, 2**power, calc_second_order=False, seed=seed)
        samples = np.broadcast_to(low, (len(design), len(low))).copy()
        samples[:, varying] = design
        scores = _index(samples, thresholds, weights)
        if np.ptp(scores) > 0:
            effects = sobol.analyze(problem, scores, calc_second_order=False, seed=seed)
            indices = {
                int(i): [float(first), float(total)]
                for i, first, total in zip(varying, effects["S1"], effects["ST"], strict=True)
            }
    sensitivity = _result("stress_index", "table", settings, entries, ctx)
    sensitivity.update(variable="stress_index", unit="1")
    sensitivity["table"] = {
        "columns": ["component", "first_order", "total_order"],
        "rows": [[name, *indices.get(i, [0.0, 0.0])] for i, name in enumerate(names)],
    }
    return {"index": result, "sensitivity": sensitivity}
