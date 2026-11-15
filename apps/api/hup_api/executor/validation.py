"""Is a plan runnable against this catalog? Schema, operation, type, version, params and an acyclic step graph."""
from jsonschema import Draft202012Validator

from hup_api import contracts
from hup_ops.weather_hypotheses import enabled as hypotheses_enabled


def order(steps: list[dict]) -> list[dict] | None:
    """The steps with every step after the ones it depends on, or None when the dependencies make a cycle."""
    pending = {s["id"]: set(s["depends_on"]) for s in steps}
    by_id = {s["id"]: s for s in steps}
    ordered = []
    while pending:
        ready = [i for i, deps in pending.items() if not deps & pending.keys()]
        if not ready:
            return None
        for i in ready:
            ordered.append(by_id[i])
            del pending[i]
    return ordered


def validate_plan(plan: dict, catalog: dict) -> list[str]:
    """Every reason the plan cannot run, one sentence each; empty when it can."""
    errors = [f"plan: {e}" for e in contracts.errors("plan", plan)]
    if errors:
        return errors
    operations = {o["id"]: o for o in catalog["operations"]}
    sources = {s["id"] for s in catalog["sources"]}
    variables = {v["id"] for v in catalog["variables"]}
    archetypes = {a["id"]: a for a in catalog.get("archetypes") or []}
    ids = [s["id"] for s in plan["steps"]]
    errors += [f"plan: duplicate step id: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    errors += [f"plan: variable outside the catalog: {v}" for v in plan["variables"] if v not in variables]
    for step in plan["steps"]:
        name = f"step {step['id']}"
        op = operations.get(step["operation"])
        if op is None:
            errors.append(f"{name}: operation outside the catalog: {step['operation']}")
        else:
            if not hypotheses_enabled() and {"hypothesis_power_cell", "weather_hypotheses"} & step["params"].keys():
                errors.append(f"{name}: weather hypotheses are disabled")
            if step["type"] != op["type"]:
                errors.append(f"{name}: type {step['type']} does not match {op['id']} ({op['type']})")
            if step["version"] != op["version"]:
                errors.append(f"{name}: version {step['version']} does not match {op['id']} ({op['version']})")
            for e in Draft202012Validator(op["params"]).iter_errors(step["params"]):
                errors.append(f"{name}: params: {e.message[:200]}")
            if step["operation"] == "read_exposure":
                properties = op["params"].get("properties", {})
                for key in ("grid", "flood_layers"):
                    if step["params"].get(key) != properties.get(key, {}).get("default"):
                        errors.append(f"{name}: {key} must match the catalogued exposure inputs")
            if step["operation"] == "resource_estimate":  # Guillem, A9 D1-a: the whole archetype, as catalogued
                archetype = step["params"].get("archetype")
                key = archetype.get("id") if isinstance(archetype, dict) else None
                if archetype != archetypes.get(key):
                    errors.append(f"{name}: archetype must match the catalogued archetype {key}")
        errors += [f"{name}: depends on a missing step: {d}" for d in step["depends_on"] if d not in ids]
        errors += [f"{name}: source outside the catalog: {x}" for x in step.get("sources", []) if x not in sources]
    if not errors and order(plan["steps"]) is None:
        errors.append("plan: the step dependencies make a cycle")
    return errors
