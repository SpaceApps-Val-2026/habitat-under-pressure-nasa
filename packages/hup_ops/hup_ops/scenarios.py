"""Climate deltas and independent projections of non-climate indicators."""

from collections import defaultdict

import numpy as np
from scipy.stats import t as student

from ._series import absent, body, flatten, least_direct, points
from .registry import Context, operation

WINDOWS = {"2030": [2025, 2034], "2040": [2035, 2044], "2050": [2045, 2054]}  # Guillem, 14 Nov: three ten-year windows
DEFAULTS = {
    "reference": [1995, 2014],
    "windows": WINDOWS,
    "scenario_pairs": {"ssp1": "ssp126", "ssp2": "ssp245", "ssp3": "ssp370", "ssp5": "ssp585"},
    "min_coverage": 1.0,
    "reference_min_years": 17,  # Guillem, 14 Nov: IMERG starts in 1998 and shares 17 of the 20 reference years
}
MODEL_COLUMNS = ["horizon", "scenario", "model", "delta"]


def _year(date):
    return int(str(date)[:4])


def _mean(values, bounds, coverage):
    start, end = bounds
    sample = [v for year, v in values.items() if start <= year <= end]
    if start <= end and sample and len(sample) / (end - start + 1) >= coverage:
        return float(np.mean(sample))
    return None


def _cube_records(entry):
    table = entry.get("table")
    if table is not None:
        return [
            dict(zip(table["columns"], row, strict=True)) if not isinstance(row, dict) else row for row in table["rows"]
        ]
    data = entry.get("data")
    if hasattr(data, "to_dict"):
        return data.to_dict("records")
    # Climate cubes carry (model, scenario); projected population (e.g. SEDAC SSP) carries scenario only.
    return [row for row in entry.get("series", []) if "scenario" in row and "t" in row and "v" in row]


def _historical_scenario(scenario):
    return scenario.lower() in {"historical", "hist"}


def _reference_unit_matches(entry, model_unit):
    if entry.get("unit") == model_unit:
        return True
    params = entry.get("method", {}).get("params", {})
    return (
        model_unit == "mm" and entry.get("unit") == "mm/year"
        and params.get("aggregate") == "year" and params.get("statistic") == "sum"
    )


def _reference_window(observed, settings):
    start, end = settings["reference"]
    minimum = settings["reference_min_years"]
    if minimum is None:
        return start, end
    need = min(minimum, end - start + 1)  # a reference shorter than the minimum has to be complete
    years = sorted(y for y in observed if start <= y <= end)
    if years and len(years) >= need and years[-1] - years[0] + 1 == len(years):
        return years[0], years[-1]
    return None


def _deltas(entries, settings):
    cubes = [(entry, records) for entry in entries if (records := _cube_records(entry))]
    observed_entries = [entry for entry in entries if entry.get("series") and not _cube_records(entry)]
    change_only = settings.get("output") == "change"
    if not cubes:
        return None, [], "projection"
    if not change_only and len(observed_entries) != 1:
        return None, [], "observation"
    cube = cubes[0][0]
    projection = cube.get("variable")
    unit = cube.get("unit")
    if any((entry.get("unit"), entry.get("variable")) != (unit, projection) for entry, _ in cubes):
        return None, [], "projection"
    coverage = settings["min_coverage"]
    reference_window = tuple(settings["reference"])
    reference_coverage = 1.0 if settings["reference_min_years"] is not None else coverage
    baseline = None
    if not change_only:
        observed_entry = observed_entries[0]
        observed_variable = settings.get("reference_variable", projection)
        if (observed_entry.get("variable") != observed_variable
                or not _reference_unit_matches(observed_entry, unit)):
            return None, [], "observation"
        dated = points(observed_entry)
        observed = {_year(date): v for date, v in dated.items()}
        if len(observed) != len(dated):
            return None, [], "observation"
        effective = _reference_window(observed, settings)
        if effective is None:
            return None, [], "observation_window"
        reference_window = effective
        baseline = _mean(observed, reference_window, reference_coverage)
        if baseline is None:
            return None, [], "observation_window"
    groups = defaultdict(dict)
    for row in (row for _, records in cubes for row in records):
        if "scenario" not in row or "t" not in row or row.get("v") is None or not np.isfinite(row["v"]):
            continue
        model = str(row.get("model", "projection"))
        key = (model, str(row["scenario"]))
        year = _year(row["t"])
        if year in groups[key] and groups[key][year] != row["v"]:
            return None, [], "projection"
        groups[key][year] = float(row["v"])
    historical = {model: values for (model, scenario), values in groups.items() if _historical_scenario(scenario)}
    selected = settings.get("population_scenarios")
    allowed = (
        None
        if selected is None
        else {settings["scenario_pairs"][ssp] for ssp in selected if ssp in settings["scenario_pairs"]}
    )
    rows = []
    for (model, scenario), values in sorted(groups.items()):
        if _historical_scenario(scenario) or (allowed is not None and scenario not in allowed):
            continue
        if change_only:
            # In change-only mode, use the model's own historical/reference window (or the series itself).
            ref_source = historical.get(model, values)
            ref_years = sorted(y for y in ref_source if reference_window[0] <= y <= reference_window[1])
            effective_ref = tuple(reference_window)
            if settings["reference_min_years"] is not None:
                need = min(settings["reference_min_years"], reference_window[1] - reference_window[0] + 1)
                if ref_years and len(ref_years) >= need and ref_years[-1] - ref_years[0] + 1 == len(ref_years):
                    effective_ref = (ref_years[0], ref_years[-1])
                elif len(ref_years) < need:
                    continue
            reference = _mean(ref_source, effective_ref, reference_coverage)
        else:
            reference = _mean(historical.get(model, values), reference_window, reference_coverage)
        if reference is None:
            continue
        for horizon, window in sorted(settings["windows"].items(), key=lambda item: int(item[0])):
            future = _mean(values, window, coverage)
            if future is not None:
                rows.append([int(horizon), scenario, model, future - reference])
    if change_only and rows:
        # Keep a single effective reference window for reporting from the configured one.
        return (None, tuple(reference_window)), rows, None
    return (baseline, reference_window), rows, None if rows else "model_windows"


def _metadata(result, rows):
    models = sorted({row[2] for row in rows})
    result["diagnostics"] = {"n_models": len(models), "models": ", ".join(models)}


def _scenario_result(name, kind, settings, entries, ctx):
    result = body(name, kind, "E0", settings, entries, ctx)
    result["data_class"] = least_direct((result["data_class"], "extrapolated"))
    return result


# Equilibrium climate sensitivity above the IPCC AR6 likely range of 2.5–4 °C: ACCESS-CM2 4.72 °C and EC-Earth3
# 4.26–4.3 °C (Zelinka et al. 2020; Meehl et al. 2020). Counted by ECS, Guillem's decision of 14 November 2026;
# sources in .dev/notes/2026-11-14-sensibilidad-modelos-a8.md.
HIGH_SENSITIVITY = ("ACCESS-CM2", "EC-Earth3")


@operation("climate_delta", version="1.4")
def climate_delta(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {**DEFAULTS, **params}
    change_only = settings.get("output") == "change"
    entries = flatten(inputs)
    result = _scenario_result("climate_delta", "series", settings, entries, ctx)
    reference, rows, missing = _deltas(entries, settings)
    cubes = [entry for entry in entries if _cube_records(entry)]
    if cubes and cubes[0].get("variable"):
        result["variable"] = cubes[0]["variable"]
        result["unit"] = cubes[0]["unit"]
    _metadata(result, rows)
    result["limits"].extend(
        [
            {
                "en": "The bounds describe model spread, not a confidence interval: with five models, the real "
                "value can fall outside them about one time in three. Short horizon windows are sensitive to "
                "year-to-year variation.",
                "es": "Los límites describen la dispersión entre modelos, no un intervalo de confianza: con cinco "
                "modelos, el valor real puede quedar fuera más o menos una vez de cada tres. Los periodos de "
                "proyección cortos son sensibles a las variaciones de un año a otro.",
            },
            {
                "en": (
                    "The model change uses the model's own reference period. The projection is reported at the "
                    "coarsest available spatial support."
                    if change_only else
                    "The model change and the annual reference use separate area averages. The projection "
                    "is reported at the coarsest available spatial support."
                ),
                "es": (
                    "El cambio modelado usa el propio periodo de referencia del modelo. La proyección se declara "
                    "con el soporte espacial más grueso disponible."
                    if change_only else
                    "El cambio modelado y la referencia anual usan promedios zonales separados. La proyección "
                    "se declara con el soporte espacial más grueso disponible."
                ),
            },
        ]
    )
    if change_only:
        result["limits"].append({
            "en": "This shows the modelled change relative to the reference period, "
            "not an observed value for the place.",
            "es": "Muestra el cambio según los modelos respecto al periodo de referencia, "
            "no un valor observado del lugar.",
        })
    if not rows:
        details = {
            "observation": {
                "en": "A compatible observed annual reference is missing.",
                "es": "Falta una referencia anual observada compatible.",
            },
            "observation_window": {
                "en": "The observed annual reference lacks sufficient coverage in the configured baseline window.",
                "es": "La referencia anual observada no tiene cobertura suficiente en el periodo base configurado.",
            },
            "projection": {
                "en": "A compatible projection series with scenario (and, when available, model) is missing.",
                "es": "Falta una serie de proyección compatible con escenario (y, si existe, modelo).",
            },
            "model_windows": {
                "en": "The model reference and projection windows lack sufficient coverage.",
                "es": "Falta cobertura suficiente en la referencia y las ventanas de proyección.",
            },
        }
        result["absence"] = {"reason": "insufficient_qa", "detail": details[missing]}
        return {"delta": result}
    baseline, effective = reference
    if not change_only and list(effective) != list(settings["reference"]):
        start, end = effective
        full_start, full_end = settings["reference"]
        result["limits"].append({
            "en": f"The observed and model reference use {start}–{end} instead of {full_start}–{full_end}.",
            "es": f"La referencia observada y modelada usa {start}–{end} en vez de {full_start}–{full_end}.",
        })
    if result.get("unit") == "mm" and not change_only:
        result["limits"].append({
            "en": "Each model's rainfall change is added in millimetres to the observed value; in dry areas "
            "this can give unreliable values.",
            "es": "El cambio de lluvia de cada modelo se suma en milímetros al valor observado; en zonas secas "
            "puede dar valores poco fiables.",
        })
    models = sorted({row[2] for row in rows})
    warm = [model for model in models if model in HIGH_SENSITIVITY]
    if warm:
        # 1.4 (Guillem, 14 and 15 Nov; figures verified by Nadia): said whenever one of them enters the fan.
        names = {"en": " and ".join(warm), "es": " y ".join(warm)}
        result["limits"].append({
            "en": f"{len(warm)} of the {len(models)} models ({names['en']}) warm more for the same CO2 than the "
                  "range the IPCC considers likely (an equilibrium climate sensitivity above the 2.5–4 °C range). "
                  "They count like the others in the median and the bounds. This is a project criterion based on "
                  "that IPCC threshold, not an official IPCC category.",
            "es": f"{len(warm)} de los {len(models)} modelos ({names['es']}) calientan más, con el mismo CO2, que "
                  "el rango que el IPCC considera probable (una sensibilidad climática de equilibrio por encima del "
                  "rango de 2,5 a 4 °C). Cuentan como los demás en la mediana y en la banda. Es un criterio del "
                  "proyecto basado en ese umbral del IPCC, no una categoría oficial del IPCC.",
        })
    grouped = defaultdict(list)
    for horizon, scenario, _, delta in rows:
        grouped[horizon, scenario].append(delta if change_only else delta + baseline)
    result["series"] = []
    for (horizon, scenario), values in sorted(grouped.items()):
        low, median, high = min(values), float(np.median(values)), max(values)  # Guillem, 14 Nov: full model range
        result["series"].append({"t": str(horizon), "scenario": scenario, "v": median, "low": low, "high": high})
    result["table"] = {"columns": MODEL_COLUMNS, "rows": rows}
    return {"delta": result}


@operation("uncertainty_split", version="1.1")
def uncertainty_split(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    entries = flatten(inputs)
    settings = {**DEFAULTS, **(entries[0].get("method", {}).get("params", {}) if entries else {}), **params}
    result = _scenario_result("uncertainty_split", "table", settings, entries, ctx)
    result["unit"] += "^2"
    rows = [
        row
        for entry in entries
        for row in entry.get("table", {}).get("rows", [])
        if entry["table"]["columns"] == MODEL_COLUMNS
    ]
    if not rows and len(entries) == 1 and entries[0].get("absence"):
        result["absence"] = entries[0]["absence"]
        return {"uncertainty": result}
    grouped = defaultdict(lambda: defaultdict(dict))
    for horizon, scenario, model, delta in rows:
        grouped[horizon][scenario][model] = delta
    output = []
    used = []
    for horizon, scenarios in sorted(grouped.items()):
        models = sorted(set.intersection(*(set(values) for values in scenarios.values())))
        if len(models) < 2 or len(scenarios) < 2:
            continue
        matrix = np.array([[scenarios[s][m] for m in models] for s in sorted(scenarios)])
        output.append(
            [horizon, float(matrix.var(axis=1).mean()), float(matrix.mean(axis=1).var()), models, sorted(scenarios)]
        )
        used.extend([horizon, None, model] for model in models)
    _metadata(result, used)
    result["limits"].append({
        "en": "Equal weighting over a common model set per horizon. Small ensembles provide indicative estimates.",
        "es": "En cada horizonte, los modelos comunes tienen el mismo peso. "
        "Con pocos modelos, las estimaciones son orientativas.",
    })
    if not output:
        return {
            "uncertainty": absent(result, "At least two common models and two scenarios are required.",
                                  "not_computed", es="Se necesitan al menos dos modelos comunes y dos escenarios.")
        }
    result["table"] = {
        "columns": ["horizon", "model_variance", "scenario_variance", "models", "scenarios"],
        "rows": output,
    }
    return {"uncertainty": result}


@operation("project_trend", version="1.0")
def project_trend(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {"horizons": [2030, 2040, 2050], "min_years": 10, "confidence": 0.95, **params}
    entries = flatten(inputs)
    result = body("project_trend", "series", "E0", settings, entries, ctx)
    result["data_class"] = least_direct((result["data_class"], "extrapolated"))
    result["limits"].append({
        "en": "The linear prediction interval assumes stable relationships and independent residuals.",
        "es": "El intervalo de predicción lineal presupone relaciones estables y residuos independientes.",
    })
    if (
        len(entries) != 1
        or settings.get("variable_kind") != "non_climate"
        or entries[0].get("data_class") not in (None, "measured", "illustrative")
    ):
        return {
            "projection": absent(result, "One observed, explicitly non-climate series is required.", "not_computed")
        }
    dated = sorted((_year(date), value) for date, value in points(entries[0]).items())
    if len(dated) < settings["min_years"] or len({year for year, _ in dated}) != len(dated):
        return {"projection": absent(result, "Insufficient annual observations.")}
    x, y = np.asarray(dated, dtype=float).T
    centered = x - x.mean()
    slope = float(centered @ (y - y.mean()) / (centered @ centered))
    residual = y - y.mean() - slope * centered
    variance = float(residual @ residual / (len(x) - 2))
    critical = float(student.ppf((1 + settings["confidence"]) / 2, len(x) - 2))
    result["series"] = []
    for horizon in sorted(set(settings["horizons"])):
        if horizon <= x[-1]:
            continue
        target = horizon - float(x.mean())
        estimate = float(y.mean()) + slope * target
        radius = critical * np.sqrt(variance * (1 + 1 / len(x) + target**2 / (centered @ centered)))
        result["series"].append(
            {"t": str(horizon), "v": estimate, "low": float(estimate - radius), "high": float(estimate + radius)}
        )
    if not result["series"]:
        del result["series"]
        return {"projection": absent(result, "No requested horizon falls after the observations.", "not_computed")}
    return {"projection": result}
