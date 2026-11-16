"""Deterministic findings, report blocks and figure rendering."""

import math
import re
from functools import lru_cache
from pathlib import Path

import yaml

from .render import DIAGNOSTICS, format_field, resource_row, scenario_series, scenario_variances, state_points

SLOT = re.compile(r"\{([a-z0-9_-]+)\.([a-z_]+)\}")
STYLES = ("journalistic", "institutional", "scientific")
LEADS = {
    "trend": ("TrendChart",),
    "impact": ("BeforeAfterMap", "RingsDecay"),
    "scan": ("ScanMatrix",),
    "scenario": ("ScenarioFan", "UncertaintySplit"),
    "event": ("BeforeAfterMap", "ExposureCounter"),
    "exposure": ("ExposureCounter",),
}
FIELDS = {"value", "interval", "unit", "period", "method", "statistic", "diagnostics"}
METHOD_NAME_PREFIXES = ("método empleado fue", "method used was")
CONTRAST_LIMIT = {
    "en": "This comparison cannot identify the origin of the difference.",
    "es": "Esta comparación no identifica el origen de la diferencia.",
}


def place_label(zone: dict) -> str:
    name = zone["name"]
    if (zone.get("source") or "").startswith("OpenStreetMap (Nominatim),"):
        return name.partition(",")[0].strip() or name
    return name


@lru_cache
def _phrases(language):
    return yaml.safe_load((Path(__file__).parent / "phrases" / f"{language}.yaml").read_text(encoding="utf8"))


def rare_event(result):
    """An episode rarer than its record can estimate (return_level, A11). It is an answer, not a gap: the absence
    keeps its reason, and its finding says it with the length of the record."""
    diagnostics = result.get("diagnostics") or {}
    return (result.get("method", {}).get("operation") == "return_level"
            and result.get("absence", {}).get("reason") == "not_computed"
            and type(diagnostics.get("event_year")) is int and type(diagnostics.get("n_years")) is int)


def episode_day(result):
    """The episode is its wettest day, read from its daily series (return_level 1.2), not the value of its year."""
    diagnostics = result.get("diagnostics") or {}
    value = diagnostics.get("event_value")
    return (result.get("method", {}).get("operation") == "return_level"
            and isinstance(diagnostics.get("event_day"), str)
            and type(value) in (int, float) and math.isfinite(value))


def fill(text: str, results: list[dict], language: str) -> str:
    by_id = {result["id"]: result for result in results}

    def replace(match):
        rid, field = match.groups()
        result = by_id.get(rid, {})
        extra = report_fields(result)
        absent = "absence" in result and not (rare_event(result) and field in extra)
        if absent or (field not in FIELDS and field not in extra) or (field in FIELDS and field not in result):
            raise ValueError(f"Cannot fill {match[0]}")
        prefix = text[: match.start()].rstrip()
        method_name = field == "method" and prefix.endswith(METHOD_NAME_PREFIXES)
        return format_field(field, result, language, _phrases(language)["units"], method_name=method_name)

    rendered = SLOT.sub(replace, text)
    rendered = re.sub(r" {2,}", " ", rendered).replace(" .", ".")
    if "{" in rendered or "}" in rendered:
        raise ValueError("Unrecognized result placeholder")
    return rendered


def _display_label(value, default):
    # Catalog labels containing digits would become literals in the findings contract.
    if not value or re.search(r"[\d{}]", value):
        return default
    return value


def report_fields(result):
    operation = result.get("method", {}).get("operation")
    if operation == "read_series" and result.get("unit") and state_points(result):
        return {"series"}
    if operation == "climate_delta" and result.get("unit") and scenario_series(result):
        return {"series"}
    if operation == "uncertainty_split" and result.get("unit") and scenario_variances(result):
        return {"table"}
    if operation == "resource_estimate" and result.get("unit") and resource_row(result):
        return {"table"}
    if operation in {"rain_extreme", "same_date_anomaly"} and result.get("support", {}).get("resolution_m"):
        return {"support"}
    if operation == "return_level":
        diagnostics = result.get("diagnostics") or {}
        fields = {"support"} if result.get("support", {}).get("resolution_m") else set()
        if type(diagnostics.get("n_years")) is int:
            fields.add("record")
        if "absence" not in result and type(diagnostics.get("return_period_years")) is int:
            fields.add("return_period")
        if episode_day(result):
            fields.add("event_value")
        return fields
    return set()


def rain_limit(result, language):
    if result["method"]["operation"] == "return_level":
        if "support" not in report_fields(result):
            return ""
        return _phrases(language)["formats"]["return_level_limit"].format(support="{" + result["id"] + ".support}")
    if result["method"]["operation"] not in {"rain_extreme", "same_date_anomaly"}:
        return ""
    if result["method"]["operation"] == "same_date_anomaly" and result.get("unit") != "mm":
        return ""
    formats = _phrases(language)["formats"]
    if "support" in report_fields(result):
        return formats["rain_grid"].format(support="{" + result["id"] + ".support}")
    return formats["rain_monthly"]


def scenario_limit(result, language):
    op = result["method"]["operation"]
    if op == "climate_delta":
        return _phrases(language)["formats"]["scenario_limit"]
    if op == "uncertainty_split":
        return _phrases(language)["formats"]["variance_limit"]
    return ""


def pretrend_limit(result, language):
    if (result["method"]["operation"] == "baci_contrast"
            and result.get("diagnostics", {}).get("pre_trend_control") is True):
        return _phrases(language)["formats"]["pretrend_limit"]
    return ""


def finding_text(result, variable, level, plan=None, *, include_limits=True):
    """Return bilingual wording from one result, bounded by the selected level."""
    op = result["method"]["operation"]
    params = result["method"].get("params", {})
    neutral = result.get("direction_assertable") is False
    extra = report_fields(result)
    if op == "read_series" and (plan or {}).get("design") not in ("state", "whatif"):
        return None
    rare = rare_event(result)
    if not neutral and "value" not in result and not extra & {"series", "table"} and not rare:
        return None
    text = {}
    for language in ("en", "es"):
        phrases = _phrases(language)
        phrase_op = op
        if op == "climate_delta" and params.get("output") == "change":
            phrase_op = "climate_delta_change"
        if op == "return_level" and "event_year" in result.get("diagnostics", {}):
            phrase_op = "return_level_rare" if rare else "return_level_event"
            if episode_day(result):
                phrase_op += "_day"
        templates = phrases.get(phrase_op, phrases.get(op, {}))
        template = phrases["neutral"]["E0"] if neutral else templates.get(level, phrases["fallback"]["E0"])
        default = "The indicator" if language == "en" else "El indicador"
        label = _display_label(variable.get(language, variable.get("en")), default)
        label = phrases["labels"].get(result.get("variable"), label)
        if op == "exposure" and params.get("category") in phrases["exposure_labels"]:
            label = phrases["exposure_labels"][params["category"]]
        if op == "resource_estimate":  # what would be built, as the catalog names its archetype
            name = (params.get("archetype") or {}).get("name") or {}
            label = _display_label(name.get(language, name.get("en")), default)
            if not neutral and "table" in extra:  # one sentence per step of the answer ladder
                template = phrases["resource_estimate_basis"][resource_row(result)["basis"]]
        slots = {field: "{" + result["id"] + "." + field + "}" for field in FIELDS | extra
                 if field in extra or field in result}
        slots.setdefault("unit", "")
        plan_zone = (plan or {}).get("zone", {})
        zone_name = place_label(plan_zone) if plan_zone.get("name") else None
        zone = _display_label(zone_name, None)
        if zone is None and zone_name:
            zone = "la zona delimitada" if language == "es" else "the mapped site"
        values = {**slots, "variable": label, "zone": zone,
                  "year": (result.get("diagnostics") or {}).get("event_year")}
        if not neutral and zone and {"value", "interval"} <= slots.keys():
            upwards = result["value"] >= 0
            if op == "trend" and level == "E1" and "period" in slots:
                template = phrases["formats"]["trend"]
                values["verb"] = phrases["tendency"]["up" if upwards else "down"]
            if op == "baci_contrast" and level == "E2" and {"before", "after"} <= params.keys():
                template = phrases["formats"]["impact"]
                values["verb"] = phrases["impact"]["up" if upwards else "down"]
                values["limit"] = phrases["impact"]["limit"]
        rendered = template.format(**values).replace(" .", ".")
        if op in {"read_series", "trend", "scan_trends"} and "period" in slots and slots["period"] not in rendered:
            rendered += " " + phrases["formats"]["state_period"].format(period=slots["period"])
        if "interval" in slots and not neutral and slots["interval"] not in rendered:
            rendered = rendered.rstrip(".") + f" ({slots['interval']})."
        if not neutral and op in {"rain_extreme", "same_date_anomaly"} and "year" in params and params.get("months"):
            rendered += " " + phrases["formats"]["event_window"].format(method=slots["method"])
            if "period" in slots:
                rendered += " " + phrases["formats"]["reference_period"].format(period=slots["period"])
        if include_limits and not neutral:
            limit = rain_limit(result, language) or scenario_limit(result, language) or pretrend_limit(result, language)
            if limit:
                rendered += " " + limit
        text[language] = rendered
        if op == "baci_contrast" and phrases["impact"]["limit"] not in text[language]:
            if not neutral and {"before", "after"} <= params.keys():
                if slots["method"] not in rendered:
                    text[language] += " " + phrases["formats"]["comparison_windows"].format(method=slots["method"])
                if include_limits:
                    text[language] += " " + CONTRAST_LIMIT[language]
            elif level == "E2" and not neutral:
                text[language] += " " + phrases["impact"]["limit"]
    return text


def _scenario_opening(result, name):
    change = result.get("method", {}).get("params", {}).get("output") == "change"
    key = "scenario_opening_change" if change else "scenario_opening"
    return {
        lang: _phrases(lang)["formats"][key].format(
            variable=_display_label(name.get(lang, name.get("en")), _phrases(lang)["formats"]["indicator"]).lower(),
        )
        for lang in ("en", "es")
    }


def write_findings(plan: dict, results: list[dict], catalog: dict) -> list[dict]:
    from .llm import generate
    from .weather import is_weather_result

    results = [result for result in results if not is_weather_result(result)]
    generated = generate(plan, results, catalog)
    return _template_findings(plan, results, catalog) if generated is None else generated


def reading_order(result):
    """By result id, except that the return levels of one step go from the shortest period to the longest."""
    period = (result.get("diagnostics") or {}).get("return_period_years")
    level = type(period) is int
    return result["id"].rsplit("-", 1)[0], level, period if level else 0, result["id"]


def _template_findings(plan, results, catalog):
    names = {v["id"]: v["name"] for v in catalog.get("variables", [])}
    findings = []
    # whatif answers with its estimates: they go first (the report's headline), its context after them.
    first = "resource_estimate" if plan.get("design") == "whatif" else None
    for result in sorted(results, key=lambda item: (item["method"]["operation"] != first, reading_order(item))):
        if ("absence" in result and not rare_event(result)) or not result.get("sources"):
            continue
        level = result.get("evidence_level", "E0")
        text = finding_text(result, names.get(result["variable"], {}), level, plan)
        if text:
            name = result["method"]["operation"]
            if name == "climate_delta" and "series" in report_fields(result):
                text = _scenario_opening(result, names.get(result["variable"], {}))
            if result.get("direction_assertable") is False or level not in _phrases("en").get(name, {}):
                level = "E0"
            order = len(findings) + 1
            findings.append(
                {
                    "id": f"f-{result['id']}",
                    "order": order,
                    "text": text,
                    "results": [result["id"]],
                    "data_class": result["data_class"],
                    "evidence_level": level,
                    "review": {"status": "pending"},
                }
            )
    return findings


def _matches(result, needs):
    return (
        (not needs.get("result_types") or result["type"] in needs["result_types"])
        and all(field in result for field in needs.get("fields", []))
        and (not needs.get("operations") or result["method"]["operation"] in needs["operations"])
    )


def _block_order(design, style):
    lead = LEADS.get(design, ())
    start = ("KeyFigure", *lead) if style == "journalistic" else (*lead, "KeyFigure")
    return tuple(dict.fromkeys((*start, "TrendChart", "EvidenceBadge", "MethodBox", "LimitsBox", "SourcesTable")))


REPORT_END = ("LimitsBox", "SourcesTable")


def eligible_blocks(plan, results, catalog):
    """Match catalog blocks to the results independently of any authored prose."""
    blocks = {block["id"]: block for block in catalog.get("blocks", [])}
    data = sorted(results, key=reading_order)
    selections = {}
    for style in STYLES:
        selections[style] = eligible = {}
        for name in _block_order(plan.get("design"), style):
            if name not in blocks or style not in blocks[name]["styles"]:
                continue
            needs = blocks[name]["needs"]
            selected = [
                r for r in data
                if ("absence" not in r or name == "LimitsBox")
                and (name != "LimitsBox" or "absence" in r or r.get("limits"))
                and (name != "TrendChart" or not any("scenario" in p or "model" in p for p in r.get("series", [])))
                and (
                    _matches(r, needs)
                    or (name == "KeyFigure" and plan.get("design") == "whatif"
                        and r["type"] == "value" and resource_row(r))
                )
            ]
            if len(selected) >= needs.get("min_results", 1):
                eligible[name] = selected
    return selections


def compose_fallback(plan: dict, results: list[dict], catalog: dict, *, notice: dict[str, str] | None = None) -> dict:
    """Publish validated results without reusing drafts, cached prose, or an editorial provider."""
    from .opening import answer, fallback, milestone, recurrence
    from .weather import blocks_for, is_weather_result

    hypotheses = blocks_for(results, catalog)
    results = [result for result in results if not is_weather_result(result)]
    selections = eligible_blocks(plan, results, catalog)
    story = {}
    for style, eligible in selections.items():
        blocks = story[style] = []
        for name, selected in eligible.items():
            if name == REPORT_END[0]:
                blocks.extend(hypotheses)
            ids = [result["id"] for result in selected]
            if name == "KeyFigure":
                blocks.extend({"block": name, "results": [result_id]} for result_id in ids)
            else:
                blocks.append({"block": name, "results": ids})
        if REPORT_END[0] not in eligible:
            blocks.extend(hypotheses)
    eligible = selections["journalistic"]
    pieces = (recurrence(plan, results, eligible) or milestone(plan, results, [], catalog, eligible)
              or fallback(plan, results, [], catalog, eligible))
    if "journalistic" in pieces and (answered := answer(plan, results, pieces["journalistic"], catalog)):
        pieces["journalistic"] = answered
    for piece in pieces.values():
        piece.pop("blocks", None)
        if notice:
            piece["cannot_say"] = {
                language: f"{notice[language]} {limit}" if language in notice else limit
                for language, limit in piece["cannot_say"].items()
            }
    story["editorial"] = pieces
    return story


def opening_for_style(analysis: dict, results: list[dict], catalog: dict, style: str) -> dict | None:
    """Compose one opening on demand for an already finished report."""
    from .llm import short_opening
    from .opening import answer, fallback, milestone, recurrence
    from .weather import is_weather_result

    plan = analysis["plan"]
    results = [result for result in results if not is_weather_result(result)]
    eligible = eligible_blocks(plan, results, catalog)[style]
    findings = analysis.get("findings", [])
    basis = (recurrence(plan, results, eligible) or milestone(plan, results, findings, catalog, eligible)
             or fallback(plan, results, findings, catalog, eligible)).get("journalistic")
    if not basis:
        return None
    basis = (answer(plan, results, basis, catalog) if style == "journalistic" else None) or basis
    place = place_label(plan["zone"])
    piece = short_opening(plan, results, catalog, eligible, style, basis=basis)
    if piece is None:
        headline = (
            {"en": f"Environmental findings for {place}", "es": f"Resultados ambientales para {place}"}
            if style == "institutional" else
            {"en": f"Environmental evidence in {place}", "es": f"Evidencia ambiental en {place}"}
        )
        piece = {**basis, "headline": headline}
    return {field: value for field, value in piece.items() if field != "blocks"}


def compose(plan: dict, results: list[dict], findings: list[dict], catalog: dict) -> dict:
    from hup_api import contracts

    from .llm import cached_story
    from .opening import answer, fallback, milestone, recurrence
    from .weather import blocks_for, is_weather_result

    hypotheses = blocks_for(results, catalog)
    results = [result for result in results if not is_weather_result(result)]
    selections = eligible_blocks(plan, results, catalog)
    dynamic, editorial = cached_story(plan, results, findings, catalog, selections)
    if "editorial" not in contracts.validator("analysis").schema["properties"]["story"].get("properties", {}):
        editorial = {}
    else:
        eligible = selections["journalistic"]
        contextual = milestone(plan, results, findings, catalog, eligible)
        if contextual and "journalistic" in editorial:
            planned, written = contextual["journalistic"], editorial["journalistic"]
            for lang in ("en", "es"):
                written["lead"][lang] = f"{written['lead'][lang]} {planned['lead'][lang]}"
            written.update(meaning=planned["meaning"], cannot_say=planned["cannot_say"])
            written["blocks"] = list(dict.fromkeys([*written["blocks"], *planned["blocks"]]))
        editorial = recurrence(plan, results, eligible) or editorial or contextual
        if not editorial:
            editorial = fallback(plan, results, findings, catalog, selections["journalistic"])
        if "journalistic" in editorial and (answered := answer(plan, results, editorial["journalistic"], catalog)):
            editorial = {**editorial, "journalistic": answered}
    blocks = {block["id"]: block for block in catalog.get("blocks", [])}
    links = {
        rid: f["id"]
        for f in sorted(findings, key=lambda item: item["order"], reverse=True)
        if f["review"]["status"] != "rejected"
        for rid in f["results"]
    }
    finding_by_id = {finding["id"]: finding for finding in findings}
    variable_names = {v["id"]: v["name"] for v in catalog.get("variables", [])}
    projection_details = {
        f"f-{r['id']}": finding_text(r, variable_names.get(r["variable"], {}), "E0", plan)
        for r in results if r["method"]["operation"] == "climate_delta" and "series" in report_fields(r)
    }
    approved = sorted((f for f in findings if f["review"]["status"] == "approved"), key=lambda f: f["order"])
    reviewed_results = {rid for finding in approved for rid in finding["results"]}
    story = {}
    for style in STYLES:
        attached = set()
        eligible = selections[style]
        order = eligible
        if style in editorial:
            chosen = editorial[style]["blocks"]
            required = ["KeyFigure"] if plan.get("design") == "whatif" and "KeyFigure" in eligible else []
            order = list(dict.fromkeys([*required, *chosen, *(name for name in REPORT_END if name in eligible)]))
        current = story[style] = []
        for name in order:
            if name == REPORT_END[0]:
                current.extend(hypotheses)
            needs = blocks[name]["needs"]
            selected = eligible[name]
            if name == "EvidenceBadge" and any(r["id"] in reviewed_results for r in selected):
                evidence = [r for r in selected if r["id"] in reviewed_results
                            or r["type"] != "series" or r.get("evidence_level") != "E0"]
                if len(evidence) >= needs.get("min_results", 1):
                    selected = evidence
            if name == "KeyFigure" and needs.get("min_results", 1) == 1:
                for r in selected:
                    item = {"block": name, "results": [r["id"]]}
                    if r["id"] in links:
                        item["finding"] = links[r["id"]]
                    if r.get("direction_assertable") is not False and {"value", "interval"} <= r.keys():
                        rid = r["id"]
                        finding = finding_by_id.get(item.get("finding"))
                        item["text"] = {}
                        for lang in ("en", "es"):
                            phrases = _phrases(lang)
                            if style == "journalistic":
                                first = (
                                    finding["text"][lang] if finding and finding["results"] == item["results"] else ""
                                )
                                item["text"][lang] = first or phrases["report"]["institutional"].format(
                                    value="{" + rid + ".value}", interval="{" + rid + ".interval}"
                                )
                                continue
                            sentence = (
                                finding["text"][lang]
                                if finding and finding["results"] == item["results"]
                                else phrases["report"][style].format(
                                    value="{" + rid + ".value}", interval="{" + rid + ".interval}"
                                )
                            )
                            if style == "institutional":
                                sentence += " " + phrases["formats"]["institution"].format(
                                    method="{" + rid + ".method}"
                                )
                            if style == "scientific":
                                for field in ("statistic", "diagnostics"):
                                    if r.get(field) and (
                                        field != "diagnostics" or DIAGNOSTICS.keys() & r[field].keys()
                                    ):
                                        sentence += " " + phrases["formats"][
                                            field if field == "diagnostics" else style
                                        ].format(**{field: "{" + rid + "." + field + "}"})
                                if sentence == (finding or {}).get("text", {}).get(lang):
                                    sentence += " " + phrases["report"][style].format(
                                        value="{" + rid + ".value}", interval="{" + rid + ".interval}"
                                    )
                            item["text"][lang] = sentence
                    fid = item.get("finding")
                    if style in dynamic.get(fid, {}) and finding_by_id[fid]["results"] == item["results"]:
                        item["text"] = dynamic[fid][style]
                    if fid and item.get("text") and finding_by_id[fid]["results"] == item["results"]:
                        attached.add(fid)
                    current.append(item)
            else:
                ids = [r["id"] for r in selected]
                item = {"block": name, "results": ids}
                if name not in {"SourcesTable", "LimitsBox", "MethodBox"}:
                    linked = [
                        {"block": name, "results": f["results"], "finding": f["id"],
                         "text": dynamic.get(f["id"], {}).get(style, projection_details.get(f["id"], f["text"]))}
                        for f in approved
                        if f["id"] not in attached
                        and set(f["results"]) <= set(ids)
                        and len(f["results"]) >= needs.get("min_results", 1)
                    ]
                    if linked:
                        attached.update(block["finding"] for block in linked)
                        current.extend(linked)
                        covered = {rid for block in linked for rid in block["results"]}
                        remaining = [rid for rid in ids if rid not in covered]
                        if len(remaining) >= needs.get("min_results", 1):
                            current.append({**item, "results": remaining})
                        elif remaining:
                            current.append(item)
                        continue
                current.append(item)
        if REPORT_END[0] not in order:
            current.extend(hypotheses)
    if editorial:
        story["editorial"] = {
            style: {field: value for field, value in piece.items() if field != "blocks"}
            for style, piece in editorial.items()
        }
    return story
