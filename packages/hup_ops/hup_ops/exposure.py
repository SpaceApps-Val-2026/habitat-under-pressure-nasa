"""Exposure and overlap for gridded inputs already aligned by the reader."""

from math import isfinite

from ._series import absent, body, flatten
from .registry import Context, operation


def _cells(entries, required):
    if len(entries) != 1 or "absence" in entries[0]:
        return []
    table = entries[0].get("table", {})
    if not set(required) <= set(table.get("columns", [])):
        return []
    indices = [table["columns"].index(column) for column in required]
    rows = table.get("rows", [])
    if any(not isinstance(row, list) or len(row) != len(table["columns"]) for row in rows):
        return []
    return [[row[index] for index in indices] for row in rows]


def _nonnegative(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


@operation("exposure", version="1.1")
def exposure(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {"min_cells": 1, **params}
    entries = flatten(inputs)
    categories = [entry.get("method", {}).get("params", {}).get("category") for entry in entries]
    if categories and all(categories) and len(set(categories)) == len(entries):
        return {name.replace("_", "-"): _exposure({**settings, "category": name}, [entry], ctx)
                for name, entry in zip(categories, entries, strict=True)}
    return {"exposure": _exposure(settings, entries, ctx)}


def _exposure(settings, entries, ctx):
    result = body("exposure", "value", "E0", settings, entries, ctx)
    result["method"]["version"] = "1.1"
    result["unit"] = "people"
    if len(entries) == 1 and entries[0].get("coverage"):
        result["coverage"] = entries[0]["coverage"]
    if len(entries) == 1 and "absence" in entries[0]:
        return {**result, "absence": entries[0]["absence"].copy()}
    cells = _cells(entries, ("population", "detected"))
    if len(cells) < settings["min_cells"] or any(
        not isinstance(mask, bool) or (mask and not _nonnegative(count)) for count, mask in cells
    ):
        return absent(result, "A complete population and mask table on a common grid is required.",
                      es="Se necesita una tabla completa de población y máscara en una misma cuadrícula.")
    result["diagnostics"] = {"n_cells": len(cells), "n_masked": sum(mask for _, mask in cells)}
    result["value"] = sum(count for count, mask in cells if mask)
    result["limits"].append({
        "en": "This count uses the supplied grid mask and represents spatial exposure.",
        "es": "Este recuento usa la máscara de la cuadrícula y representa exposición espacial.",
    })
    return result


@operation("validate_against", version="1.0")
def validate_against(params: dict, inputs: dict[str, list[dict]], ctx: Context) -> dict[str, dict]:
    settings = {"min_cells": 1, **params}
    entries = flatten(inputs)
    result = body("validate_against", "value", "E0", settings, entries, ctx)
    result["unit"] = "%"
    if len(entries) == 1 and "absence" in entries[0]:
        return {"overlap": {**result, "absence": entries[0]["absence"].copy()}}
    cells = _cells(entries, ("detected", "reference", "area_km2"))
    if len(cells) < settings["min_cells"] or any(
        not isinstance(detected, bool) or not isinstance(reference, bool) or not _nonnegative(area) or area == 0
        for detected, reference, area in cells
    ):
        return {"overlap": absent(result, "Complete masks and positive cell areas on a common grid are required.")}
    detected_area = sum(area for detected, _, area in cells if detected)
    reference_area = sum(area for _, reference, area in cells if reference)
    intersection = sum(area for detected, reference, area in cells if detected and reference)
    union = detected_area + reference_area - intersection
    result["diagnostics"] = {"n_cells": len(cells)}
    if union == 0:
        return {"overlap": absent(result, "Both masks are empty on the supplied grid.", "not_computed")}
    result["value"] = 100 * intersection / union
    result["table"] = {
        "columns": ["detected_km2", "reference_km2", "intersection_km2", "union_km2"],
        "rows": [[detected_area, reference_area, intersection, union]],
    }
    result["limits"].extend([
        {"en": "Overlap is the intersection divided by the union within the supplied masks.",
         "es": "El solapamiento es la intersección dividida por la unión de las máscaras."},
        {"en": "For radar water masks, terrain shadows and smooth ground can resemble water.",
         "es": "En las máscaras de radar, las sombras del relieve y las superficies lisas pueden parecer agua."},
    ])
    return {"overlap": result}
