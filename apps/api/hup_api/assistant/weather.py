"""Add the two provisional comparisons without augmenting the ordinary results."""

import re

from hup_ops.weather_hypotheses import MIN_YEARS, enabled, power_cell

from . import templates, understand

RELATION = re.compile(r"\b(?:relacion\w*|relacio|relationship\w*|relate[ds]?|correla\w*|associa\w*)\b")
TOGETHER = re.compile(r"\b(?:what (?:moves|changes) together|que (?:se mueve|canvia|cambia) junt\w*)\b")
RAIN = re.compile(r"\b(?:rain\w*|precip\w*|lluv\w*|pluja|plou)\b")
TEMPERATURE = re.compile(r"\b(?:temperatur\w*|calor|heat|warm\w*)\b")


def relationship_question(text):
    question = understand.norm(text)
    return bool(TOGETHER.search(question)
                or (RELATION.search(question) and RAIN.search(question) and TEMPERATURE.search(question)))


def add(plan, catalog):
    if not enabled() or plan["answer"]["level"] == "not_possible" or plan["design"] == "whatif":
        return plan
    cell = power_cell(plan["zone"])
    if cell is None:
        return plan
    operations = {item["id"]: item for item in catalog["operations"]}
    variables = {item["id"]: item for item in catalog["variables"]}
    association = operations["lagged_association"]
    pairs = association["params"]["properties"]["weather_hypotheses"]["const"]
    reader = operations["read_series"]
    period = {"start": "1998-01-01", "end": f"{templates.LAST_COMPLETE_YEAR}-12-31"}
    zone = {**plan["zone"], "bbox": cell}
    weather = []
    for pair in pairs:
        season = pair["season"]
        for role, statistic in (("precipitation", "sum"), ("temperature", "mean")):
            variable = pair[role]
            sources = variables[variable]["sources"]
            params = {"variable": variable, "read_period": period, "aggregate": "year",
                      "min_count": 4 if season else 12, "require_complete_months": True,
                      "statistic": statistic, "hypothesis_power_cell": True}
            if season:
                params["season"] = season
            step = {"id": f"hypothesis-{role}-{'summer' if season else 'annual'}",
                    "type": reader["type"], "operation": reader["id"], "version": reader["version"],
                    "params": params, "sources": sources, "depends_on": []}
            origin, _ = templates.read_origin(step, {**plan, "zone": zone, "period": period}, catalog)
            step["data"] = {"origin": origin}
            weather.append(step)
    compute = {"id": "weather-hypotheses", "type": association["type"], "operation": association["id"],
               "version": association["version"], "params": {"weather_hypotheses": pairs, "min_years": MIN_YEARS},
               "depends_on": [step["id"] for step in weather]}
    earlier = [step for step in plan["steps"] if step["type"] not in {"write", "review"}]
    editorial = [step for step in plan["steps"] if step["type"] in {"write", "review"}]
    for step in editorial:
        if step["type"] == "write":
            step["depends_on"].append(compute["id"])
    plan["steps"] = [*earlier, *weather, compute, *editorial]
    return plan
