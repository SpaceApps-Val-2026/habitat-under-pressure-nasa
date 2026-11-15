"""Deterministic copy for the optional weather hypotheses, outside editorial generation."""

from . import _phrases


def is_weather_result(result):
    params = result["method"].get("params", {})
    return bool(params.get("hypothesis_power_cell") or "weather_hypotheses" in params)


def blocks_for(results, catalog):
    if not any(item["id"] == "Hypotheses" for item in catalog.get("blocks", [])):
        return []
    blocks = []
    for result in results:
        table = result.get("table")
        if table is None:
            continue
        if "weather_hypotheses" not in result["method"].get("params", {}):
            continue
        text = {}
        for language in ("en", "es"):
            phrases = _phrases(language)["weather_hypotheses"]
            lines = []
            for pair, start, end, years, rho, _, _, _, _, status in table["rows"]:
                if status == "compatible":
                    key = "negative" if rho < 0 else "positive"
                    line = phrases["compatible"].format(hypothesis=phrases[pair][key])
                else:
                    line = phrases[status].format(pair=phrases[pair]["label"])
                if start is not None:
                    line += " " + phrases["period"].format(start=start, end=end, years=years)
                lines.append(line)
            lines.extend(limit.get(language, limit["en"]) for limit in result["limits"])
            text[language] = "\n".join(lines)
        blocks.append({"block": "Hypotheses", "results": [result["id"]], "text": text})
    return blocks
