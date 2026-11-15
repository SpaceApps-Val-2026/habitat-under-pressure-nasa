"""Result metadata and common series handling."""
from copy import deepcopy

import numpy as np

DATA_CLASSES = ("measured", "reanalysis", "extrapolated", "approximated", "illustrative")


def least_direct(classes):
    return max(classes, key=DATA_CLASSES.index, default="measured")


def flatten(inputs):
    return [result for step in inputs.values() for result in step]


def points(result):
    rows = result.get("series", [])
    if any("model" in row or "scenario" in row for row in rows):
        return {}
    if len({row["t"] for row in rows}) != len(rows):
        return {}
    return {
        row["t"]: float(row["v"])
        for row in rows
        if row.get("v") is not None and np.isfinite(row["v"])
    }


def _version(name):
    """The version the operation is registered with: a result says which code computed it."""
    from .registry import REGISTRY

    return REGISTRY[name].version if name in REGISTRY else "1.0"


def body(name, kind, level, params, entries, ctx):
    first = entries[0] if entries else {}
    sources = []
    limits = []
    for entry in entries:
        for item in entry.get("sources", []):
            if item not in sources:
                sources.append(item)
        for item in entry.get("limits", []):
            if item not in limits:
                limits.append(item)
    support = max(
        (entry.get("support", {}) for entry in entries),
        key=lambda item: item.get("resolution_m", 0),
        default={},
    )
    if support.get("cells", float("inf")) <= 1:
        limit = {
            "en": "The grid cell is at least as large as the area.",
            "es": "La celda de la rejilla es al menos tan grande como la zona analizada.",
        }
        if limit not in limits:
            limits.append(limit)
    result = {
        "type": kind, "variable": params.get("variable", first.get("variable", "unknown")),
        "data_class": least_direct(entry.get("data_class", "measured") for entry in entries),
        "unit": first.get("unit", "1"),
        "sources": sources or [{"source": "inputs", "url": "urn:hup:inputs"}],
        "method": {"operation": name, "version": _version(name), "params": params},
        "support": support, "limits": limits, "evidence_level": level,
    }
    if ctx.period:
        result["period"] = ctx.period
    if "id" in ctx.zone:
        result["zone"] = ctx.zone["id"]
    return deepcopy(result)


def absent(result, detail, reason="insufficient_qa", *, es=None):
    text = {"en": detail, "es": es}
    return {**result, "absence": {"reason": reason, "detail": {k: v for k, v in text.items() if v is not None}}}


def estimate(result, value, low, high, confidence, method):
    crosses = low <= 0 <= high
    result["diagnostics"]["interval_crosses_zero"] = crosses
    result["interval"] = {"level": confidence, "low": low, "high": high, "method": method}
    result["direction_assertable"] = not crosses
    if crosses:
        return absent(result, "The interval includes zero.", "not_computed", es="El intervalo incluye el cero.")
    return {**result, "value": value}

