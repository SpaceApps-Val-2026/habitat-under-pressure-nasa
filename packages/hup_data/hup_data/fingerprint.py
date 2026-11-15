"""Fingerprints: the keys of the plan cache and of the step cache.

The reference is docs/contratos/generate_examples.py: these functions must give exactly the values stored in
docs/contratos/examples/. A stray space or an escaped accent would change every fingerprint.
"""
import hashlib
import json

PLAN_KEYS = ("design", "period", "variables", "comparison", "scenarios", "catalog_version")
STEP_KEYS = ("operation", "version", "params", "depends_on")


def canonical(obj) -> str:
    """JSON with sorted keys, no spaces and no ASCII escaping. Two equal objects give the same text."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(obj) -> str:
    """Hexadecimal sha256 of the canonical form."""
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def zone_key(zone: dict) -> str:
    """What identifies a zone: its id when it has one; otherwise the hash of its geometry, or of its bbox."""
    if zone.get("id"):
        return zone["id"]
    return sha(zone.get("geometry") or zone["bbox"])


def plan_fingerprint(plan: dict) -> str:
    """Fingerprint of what is asked for, without the wording, the language, the ids or the time estimates.

    Two wordings of the same question must give the same value.
    """
    reduced = {key: plan[key] for key in PLAN_KEYS if key in plan}
    reduced["zone"] = zone_key(plan["zone"])
    reduced["steps"] = [{key: step[key] for key in STEP_KEYS} for step in plan["steps"]]
    return sha(reduced)


def step_fingerprint(operation: str, version: str, params: dict, zone_key: str, inputs: dict[str, str],
                     period: dict) -> str:
    """Fingerprint of one step. `inputs` maps a source to its version, or a step it depends on to its fingerprint.

    The period is part of it: every operation works on the period of its plan, so the same reading of the same
    place over another period is another result. Leaving it out made two analyses share a step by mistake.
    """
    return sha({"operation": operation, "version": version, "params": params, "zone": zone_key, "inputs": inputs,
                "period": period})
