"""Evidence-based journalistic openings when the provider supplies no approved piece."""

from . import CONTRAST_LIMIT, REPORT_END, SLOT, _phrases, finding_text, place_label, pretrend_limit
from .render import MONTHS, number, resource_row, state_points

_MILESTONE_VARIABLES = {
    "precipitation": {"en": "monthly rainfall", "es": "lluvia mensual"},
    "air_temperature": {"en": "mean air temperature", "es": "temperatura media del aire"},
    "air_temperature_max": {"en": "mean daily maximum temperature", "es": "media de las máximas diarias"},
}
_SCAN_VARIABLES = {
    "air_temperature": {"en": "air temperature", "es": "temperatura del aire"},
    "air_temperature_max": {"en": "daily maximum temperatures", "es": "temperaturas máximas diarias"},
    "precipitation": {"en": "rainfall", "es": "lluvia"},
    "ndvi": {"en": "vegetation greenness", "es": "verdor de la vegetación"},
    "lst": {"en": "land surface temperature", "es": "temperatura de superficie"},
}


def _scan(results):
    for result in results:
        if result["method"]["operation"] != "scan_trends" or result["type"] != "table" or not result.get("sources"):
            continue
        table = result.get("table", {})
        columns = table.get("columns", [])
        if "direction_assertable" in columns:
            index = columns.index("direction_assertable")
            directions = [row[index] for row in table.get("rows", [])
                          if isinstance(row, list) and len(row) > index and type(row[index]) is bool]
            if directions and not any(directions):
                return result, "scan_inconclusive"
            if any(directions) and result.get("evidence_level", "E0") >= "E1":
                return result, "scan_detected"
        if result.get("absence"):
            return result, "scan_unavailable"
    return None


def _impact(results, approved):
    contrasts = [r for r in results if r["method"]["operation"] == "baci_contrast" and r.get("sources")]
    if measured := next((r for r in contrasts if r["id"] in approved and "absence" not in r
                         and r.get("direction_assertable") is not False and "value" in r), None):
        pretrend = measured.get("diagnostics", {}).get("pre_trend_control") is True
        return measured, "impact_pretrend" if pretrend else "impact_detected"
    if neutral := next((r for r in contrasts if r.get("direction_assertable") is False and r.get("interval")), None):
        return neutral, "impact_inconclusive"
    if missing := next((r for r in contrasts if r.get("absence")), None):
        return missing, "impact_unavailable"
    return None


def _piece(headline, lead, meaning, cannot_say, results, eligible):
    return {"journalistic": {
        "headline": headline, "lead": lead, "meaning": meaning, "cannot_say": cannot_say,
        "results": results, "blocks": [name for name in eligible if name not in REPORT_END],
    }}


def scan_opening(plan, results, catalog, eligible):
    """Make a short lead from the reviewed scan table, including its inconclusive rows."""
    table = next((r for r in results if r["method"]["operation"] == "scan_trends"
                  and r["type"] == "table" and r.get("sources")), None)
    if table is None:
        return None
    grid = table.get("table", {})
    columns = grid.get("columns", [])
    if not {"variable", "slope", "p_adjusted", "direction_assertable"} <= set(columns):
        return None
    labels = {v["id"]: v["name"] for v in catalog.get("variables", [])}
    labels.update(_SCAN_VARIABLES)
    rows = [dict(zip(columns, row, strict=True)) for row in grid.get("rows", []) if len(row) == len(columns)]
    place = milestone_place(plan)
    groups = {"up": [], "down": [], "unclear": []}
    best = None
    scoped = {r["variable"]: r for r in results if r["method"]["operation"] == "scan_trends"
              and r["type"] == "value" and r["id"].rsplit("-trend-", 1)[0] == table["id"].rsplit("-scan", 1)[0]}
    comparable = [row for row in rows if row["p_adjusted"] is None or type(row["p_adjusted"]) in (int, float)]
    for row in sorted(comparable, key=lambda r: (r["p_adjusted"] is None, r["p_adjusted"] or 0)):
        variable, slope = row["variable"], row["slope"]
        if variable not in labels or type(row["direction_assertable"]) is not bool:
            continue
        result = scoped.get(variable)
        supported = (row["direction_assertable"] and table.get("evidence_level", "E0") >= "E1"
                     and type(slope) in (int, float) and slope != 0
                     and (result is None or result.get("direction_assertable") is not False
                          and result.get("evidence_level", "E0") >= "E1" and not result.get("absence")))
        direction = ("up" if slope > 0 else "down") if supported else "unclear"
        groups[direction].append((labels[variable], result))
        if best is None and supported:
            best = direction
    if not any(groups.values()):
        return None
    best = best or "unclear"
    # The summary table can be cited on its own. A numeric slot needs the linked scalar result.
    main, scalar = groups[best][0]
    figure = scalar if best != "unclear" and scalar and {"value", "unit"} <= scalar.keys() else None
    suffix = "{" + table["id"] + ".period}" if table.get("period") else None
    facts, focus, headline, lead, meaning, limits = {}, {}, {}, {}, {}, {}
    for lang in ("en", "es"):
        en = lang == "en"
        patterns = (
            {"up": "rising {}", "down": "falling {}", "unclear": "no clear direction in {}"} if en else
            {"up": "{} al alza", "down": "{} a la baja", "unclear": "{} sin una dirección clara"}
        )
        facts[lang] = [patterns[direction].format(label[lang].lower())
                       for direction, items in groups.items() for label, _ in items]
        focus[lang] = patterns[best].format(main[lang].lower())
        headline[lang] = f"{focus[lang][0].upper() + focus[lang][1:]} {'in' if en else 'en'} {place}"
        wording = ({"up": "The clearest increases were in", "down": "Clear declines appeared in",
                    "unclear": "There was no clear direction for"} if en else
                   {"up": "Las subidas más claras se observaron en", "down": "Se detectaron descensos en",
                    "unclear": "No se encontró una dirección clara para"})
        period = f", {suffix}" if suffix else ""
        lines = [f"Delfos {'examined indicators for' if en else 'examinó indicadores de'} {place}{period}."]
        for direction, items in groups.items():
            names = [label[lang].lower() for label, _ in items]
            if names:
                names = (" and " if en else " y ").join(filter(None, (", ".join(names[:-1]), names[-1])))
                lines.append(f"{wording[direction]} {names}.")
        if figure:
            label = main[lang].lower()
            slots = "{" + figure["id"] + ".value} {" + figure["id"] + ".unit}"
            lines.append(f"The estimated slope for {label} was {slots}." if en else
                         f"La pendiente estimada de {label} fue de {slots}.")
        lead[lang] = " ".join(lines)
        phrases = _phrases(lang)["design_opening"]
        meaning[lang] = phrases["scan_inconclusive" if best == "unclear" else "scan_detected"]["meaning"]
        limits[lang] = phrases["scan_limit"]
    ids = [table["id"]]
    if figure:
        ids.append(figure["id"])
    piece = _piece(headline, lead, meaning, limits, ids, eligible)["journalistic"]
    if "ScanMatrix" in eligible:
        piece["featured"] = table["id"]
    return piece, {"place": place, "headline_focus": focus, "verified_phrases": facts}


def recurrence(plan, results, eligible):
    if not any(item["field"] == "recurrence" for item in plan.get("answer", {}).get("decided", [])):
        return {}
    from hup_api.assistant.grounding import recurrence_limit

    extreme = next((r for r in results if r["method"]["operation"] == "rain_extreme"), None)
    if extreme and "value" in extreme and extreme.get("sources") and extreme.get("unit") == "percentile":
        figure = "{" + extreme["id"] + ".value}"
        lead = {
            "en": f"The reference month's rainfall ranked at the {figure} percentile compared with earlier Octobers.",
            "es": f"La lluvia del mes de referencia quedó en el percentil {figure} frente a los octubres anteriores.",
        }
    else:
        lead = {
            "en": "The local rainfall record did not yield a comparable set of complete months.",
            "es": "El registro de lluvia local no dio una serie comparable de meses completos.",
        }
    return _piece(
        {"en": "When another DANA might occur remains unknown",
         "es": "La fecha de la próxima DANA sigue siendo desconocida"},
        lead,
        {"en": "The comparison uses satellite rainfall estimates over the area shown in the report.",
         "es": "La comparación usa estimaciones satelitales de lluvia sobre la zona que muestra el informe."},
        recurrence_limit(), [extreme["id"]] if extreme else [], eligible,
    )


def milestone_place(plan):
    from hup_api.assistant.milestone import place_in_question

    zone = plan["zone"]
    return place_in_question(plan.get("request", {}).get("text", ""), zone, place_label(zone))


def milestone_point(plan, result):
    if result["method"]["operation"] != "anomaly" or result.get("absence") or not result.get("sources"):
        return None
    event_year = int(result["method"]["params"]["baseline"]["end"][:4]) + 1
    first_after = event_year if event_year == int(plan["period"]["end"][:4]) else event_year + 1
    return next(((t, v) for t, v in reversed(state_points(result)) if int(t[:4]) >= first_after), None)


def milestone(plan, results, findings, catalog, eligible):
    from hup_api.assistant import grounding
    from hup_api.assistant import milestone as context

    decisions = context.decisions_of(plan)
    if context.FIELD not in decisions:
        return {}
    piece = (fallback(plan, results, findings, catalog, eligible)
             or _general(plan, results, findings, catalog, eligible))["journalistic"]
    names = {v["id"]: v["name"] for v in catalog["variables"]}
    place = milestone_place(plan)
    event = decisions.get("event")
    if event:
        event = context.event_display(event)
    else:  # Earlier saved plans and offline planning have no structured event name.
        legacy = context.event_name(plan.get("request", {}).get("text", ""), place)
        event = ({"en": f"“{legacy}”", "es": f"«{legacy}»"} if legacy else
                 {"en": "the event in the question", "es": "el suceso consultado"})
    evidence = [(r, *point) for r in results if (point := milestone_point(plan, r)) is not None]
    if evidence:
        result, _, change = evidence[0]
        subject = _MILESTONE_VARIABLES.get(result["variable"], names[result["variable"]])
        en, es = ("above its reference", "por encima de la referencia") if change > 0 else (
            ("below its reference", "por debajo de la referencia") if change < 0 else
            ("at its reference level", "al nivel de la referencia"))
        conflict = grounding.signature(plan.get("request", {}).get("text", ""))[1]
        since_en, since_es = ("Since the start of", "Desde el inicio de") if conflict else ("After", "Tras")
        piece["headline"] = {
            "en": f"{since_en} {event['en']}, {subject['en'].lower()} was {en} in {place}",
            "es": f"{since_es} {event['es']}, {subject['es'].lower()} {es} en {place}",
        }
        piece["results"] = [result["id"] for result, _, _ in evidence]
        if "TrendChart" in eligible and "TrendChart" not in piece["blocks"]:
            piece["blocks"].append("TrendChart")
        lead = {"en": [], "es": []}
        for result, year, value in evidence:
            label = names[result["variable"]]
            if result["variable"] == "precipitation":
                label = {"en": "Mean monthly rainfall over the matched months",
                         "es": "Lluvia mensual media en los meses comparados"}
            unit = result.get("unit", "")
            for lang in lead:
                figure = f"{number(value, lang, 2)} {_phrases(lang)['units'].get(unit, unit)}".strip()
                lead[lang].append(f"{label['en']} in {year[:4]} differed by {figure} from the reference."
                                  if lang == "en" else
                                  f"{label['es']} en {year[:4]} mostró una diferencia de {figure} frente a la media.")
        piece["lead"] = {language: " ".join(lines) for language, lines in lead.items()}
        piece["lead"]["en"] += " The comparison describes a difference from earlier years at the same place."
        piece["lead"]["es"] += " La comparación describe una diferencia frente a años anteriores en el mismo lugar."
    else:
        piece["headline"] = {
            "en": f"The data cannot establish whether {event['en']} affected {place}",
            "es": f"Los datos no permiten afirmar que {event['es']} haya afectado a {place}",
        }
        observed = [r for r in results if r["method"]["operation"] == "read_series" and r.get("sources")
                    and not r.get("absence") and state_points(r)]
        if observed:
            points = [t for r in observed for t, _ in state_points(r)]
            a, b = min(points)[:4], max(points)[:4]
            topics = {}
            for lang, conjunction in (("en", " and "), ("es", " y ")):
                words = [_MILESTONE_VARIABLES.get(r["variable"], names[r["variable"]])[lang].lower() for r in observed]
                topics[lang] = conjunction.join(filter(None, (", ".join(words[:-1]), words[-1])))
            piece["lead"] = {
                "en": f"Delfos examined {topics['en']} in {place} from {a} to {b}. {piece['lead']['en']}",
                "es": f"Delfos examinó {topics['es']} en {place} de {a} a {b}. {piece['lead']['es']}",
            }
        if decisions[context.FIELD] == context.UNLOCATED:
            piece["lead"]["en"] += f" Delfos could not date {event['en']}."
            piece["lead"]["es"] += f" Delfos no pudo fechar {event['es']}."
        elif "baseline" in decisions and any(result["method"]["operation"] == "anomaly" for result in results):
            decisions["baseline"] = {
                "en": "The available observations did not yield a comparable series before and after the event.",
                "es": "Las observaciones disponibles no dieron una serie comparable antes y después del suceso.",
            }
    piece["meaning"] = {
        lang: " ".join(decisions[key][lang] for key in (context.FIELD, "baseline") if key in decisions)
        for lang in ("en", "es")
    }
    months = plan["period"].get("months")
    if evidence and months:
        for lang, prefix in (("en", "The comparison covers"), ("es", "La comparación cubre")):
            labels = ", ".join(MONTHS[lang][month - 1] for month in months)
            piece["meaning"][lang] += f" {prefix} {labels}."
    limits = plan["answer"].get("limits", [])
    piece["cannot_say"] = {lang: " ".join(item[lang] for item in limits[:3] if lang in item) for lang in ("en", "es")}
    return {"journalistic": piece}


def _general(plan, results, findings, catalog, eligible):
    """Give the opening a subject even when there is no approved language-model text."""
    approved = min((f for f in findings if f["review"]["status"] == "approved"),
                   key=lambda f: f["order"], default=None)
    results = sorted(results, key=lambda r: r["id"])
    present = [r for r in results if "absence" not in r and r.get("sources")]
    primary = next((r for r in present if "value" in r), None) or next(iter(present), None)
    limits = plan.get("answer", {}).get("limits") or []
    neutral = next((r for r in results if r.get("direction_assertable") is False and r.get("interval")), None)
    if neutral:
        return _piece(
            {"en": "The data show no clear direction", "es": "Los datos no muestran una dirección clara"},
            {"en": "The range of plausible trends includes a flat trend.",
             "es": "El intervalo de tendencias plausibles incluye una tendencia nula."},
            {"en": "The available series can still be examined in the report.",
             "es": "La serie disponible se puede consultar en el informe."},
            {"en": "The observations do not establish whether the measure increased or decreased.",
             "es": "Las observaciones no establecen si la variable subió o bajó."},
            [neutral["id"]], eligible,
        )
    if primary is None:
        missing = next((r for r in results if "absence" in r), None)
        detail = (missing or {}).get("absence", {}).get("detail")
        cannot_say = (detail if isinstance(detail, dict) and {"es", "en"} <= detail.keys() else None) or (
            limits[0] if limits else
            {"en": "No usable results are available for the requested place and period.",
             "es": "No hay resultados utilizables para el lugar y periodo consultados."}
        )
        return _piece(
            {"en": "The available data leave this question open",
             "es": "Los datos disponibles dejan abierta la pregunta"},
            {"en": "The analysis yielded no result that could support a finding.",
             "es": "El análisis no produjo un resultado que sustente un hallazgo."},
            {"en": "The report records where the evidence ran out.",
             "es": "El informe indica dónde termina la evidencia disponible."},
            cannot_say, [missing["id"]] if missing else [], eligible,
        )

    labels = next((v["name"] for v in catalog.get("variables", []) if v["id"] == primary["variable"]), {})
    en = labels.get("en", "this question")
    es = labels.get("es", "esta pregunta")
    headline = {"en": f"What the data show about {en}", "es": f"Qué dicen los datos sobre {es}"}
    if approved:
        lead = approved["text"]
        cited = approved["results"]
    else:
        lead = {"en": "The report brings together the evidence available for this question.",
                "es": "El informe reúne la evidencia disponible para esta pregunta."}
        cited = [primary["id"]]
    cannot_say = next(iter(limits or primary.get("limits", [])), None) or {
        "en": "The results apply to the geographic area, period and method documented below.",
        "es": "Los resultados se refieren a la zona, al periodo y al método que se documentan abajo.",
    }
    return _piece(
        headline, lead,
        {"en": "The period, resolution and sources describe where the result applies.",
         "es": "El periodo, la resolución y las fuentes delimitan el alcance del resultado."},
        cannot_say, cited, eligible,
    )


def fallback(plan, results, findings, catalog, eligible):
    from hup_api.reviewer.text import validate_text

    design = plan.get("design")
    if design == "scan" and (scan := scan_opening(plan, results, catalog, eligible)):
        return {"journalistic": scan[0]}
    if design not in {"scan", "impact"}:
        return _general(plan, results, findings, catalog, eligible)
    approved = {f["results"][0]: f["evidence_level"] for f in findings
                if f["review"]["status"] == "approved" and len(f["results"]) == 1}
    selected = _scan(results) if design == "scan" else _impact(results, approved)
    if selected is None:
        return _general(plan, results, findings, catalog, eligible)
    primary, variant = selected
    level = min(primary.get("evidence_level", "E0"), approved.get(primary["id"], "E5"))
    lead = None
    if variant in {"impact_detected", "impact_pretrend"}:
        names = next((v["name"] for v in catalog.get("variables", []) if v["id"] == primary["variable"]), {})
        lead = finding_text(primary, names, level, include_limits=False)

    opening = {}
    for lang in ("en", "es"):
        phrases = _phrases(lang)["design_opening"]
        wording = phrases[variant]
        limits = phrases[f"{design}_limit"]
        if design == "impact" and variant != "impact_unavailable":
            limits = " ".join(filter(None, (pretrend_limit(primary, lang), CONTRAST_LIMIT[lang])))
        opening[lang] = {**wording, "lead": lead[lang] if lead else wording["lead"], "cannot_say": limits}

    piece = {field: {lang: wording[field] for lang, wording in opening.items()} for field in opening["en"]}
    if design == "scan" and variant == "scan_unavailable":
        place = milestone_place(plan)
        names = next((v["name"] for v in catalog.get("variables", []) if v["id"] == primary["variable"]), {})
        label = _SCAN_VARIABLES.get(primary["variable"], names)
        if label:
            piece["headline"] = {
                "en": f"Too few comparable records for {label['en'].lower()} in {place}",
                "es": f"Registros insuficientes para evaluar {label['es'].lower()} en {place}",
            }
    if any(validate_text({"text": text, "evidence_level": level}, [primary]) for text in piece.values()):
        return {}
    piece["results"] = [primary["id"]]
    piece["blocks"] = [name for name in eligible if name not in REPORT_END]
    if any(r["id"] == primary["id"] for r in eligible.get("ScanMatrix", ())):
        piece["featured"] = primary["id"]
    return {"journalistic": piece}


def state_answer(plan, results, piece):
    """Answer the question in the first sentence for the population living in hot cells: the latest summer, the
    average and the peak with its year, all read from the series, plus its range as a limit (not a trend, no cause).
    Returns a copy of the journalistic `piece` with its lead, meaning and limits replaced, or None when it does not
    apply (another variable or design, an absence, or fewer than two yearly values)."""
    if plan.get("design") != "state":
        return None
    result = next((r for r in sorted(results, key=lambda r: r["id"])
                   if r.get("variable") == "population_in_hot_cells" and r["method"]["operation"] == "read_series"
                   and r.get("sources") and "absence" not in r), None)
    points = state_points(result) if result else []
    if len(points) < 2:
        return None
    from statistics import fmean

    peak_year, peak = max(points, key=lambda point: (point[1], -int(point[0][:4])))
    low_year, low = min(points, key=lambda point: (point[1], int(point[0][:4])))
    values = {"latest_year": points[-1][0][:4], "first_year": points[0][0][:4], "last_year": points[-1][0][:4],
              "peak_year": peak_year[:4], "high_year": peak_year[:4], "low_year": low_year[:4],
              "place": place_label(plan["zone"])}
    threshold = (result.get("diagnostics") or {}).get("threshold_c")
    answered = dict(piece, results=[result["id"], *(rid for rid in piece.get("results", []) if rid != result["id"])])
    for field in ("lead", "meaning", "cannot_say"):
        answered[field] = dict(piece.get(field) or {})
    for lang in ("en", "es"):
        phrases = _phrases(lang)["state_answer"]["population_in_hot_cells"]
        figures = {"latest": number(points[-1][1], lang), "mean": number(fmean(v for _, v in points), lang),
                   "peak": number(peak, lang), "low": number(low, lang), "high": number(peak, lang)}
        slots = {**values, **figures, "threshold": number(threshold, lang, 2) if type(threshold) in (int, float)
                 else None}
        answered["lead"][lang] = phrases["lead"].format(**slots)
        answered["meaning"][lang] = phrases["meaning" if slots["threshold"] else "meaning_no_threshold"].format(**slots)
        limit = phrases["limit"].format(**slots)
        kept = (piece.get("cannot_say") or {}).get(lang, "")
        answered["cannot_say"][lang] = f"{kept} {limit}".strip()
    return answered


# The opening answers the question in its first sentence, for every design (phase 1). Each design reads its main
# result; if it is absent, the opening says what is missing, why, and what the report does show. Wording comes
# from `answer` in the phrase files. A figure that is a result field goes in as a placeholder; a figure the
# contract has no placeholder for (a point of a series, a model median, the range of an estimate) is written as
# `state_answer` does, read from the result, and left out of the copy that `validate_text` checks.

_PRIMARY = {
    "whatif": {"resource_estimate"}, "state": {"read_series"}, "trend": {"trend"},
    "event": {"return_level", "rain_extreme", "same_date_anomaly"}, "scenario": {"climate_delta"},
    "impact": {"baci_contrast"}, "exposure": {"exposure"}, "scan": {"scan_trends"},
}
_GENERIC = {  # what _general() says when it has nothing to say: the answer replaces it
    "headline": ("What the data show about", "Qué dicen los datos sobre", "The available data leave this question",
                 "Los datos disponibles dejan abierta", "The data show no clear direction",
                 "Los datos no muestran una dirección clara"),
    "lead": ("The report brings together the evidence", "El informe reúne la evidencia",
             "The analysis yielded no result", "El análisis no produjo un resultado",
             "The range of plausible trends includes", "El intervalo de tendencias plausibles incluye"),
    "meaning": ("The period, resolution and sources describe", "El periodo, la resolución y las fuentes delimitan",
                "The report records where the evidence", "El informe indica dónde termina",
                "The available series can still be examined", "La serie disponible se puede consultar"),
}


def _generic(field, text):
    return not text or text.startswith(_GENERIC[field])


def _present(result):
    return bool(result.get("sources")) and "absence" not in result


def _slot(result, field):
    return "{" + result["id"] + "." + field + "}"


def _lower(label):
    word = label.split(" ", 1)[0]
    return label if len(word) > 1 and word.isupper() else label[:1].lower() + label[1:]


def _capital(text):
    return text[:1].upper() + text[1:]


def _label(result, names, lang):
    phrases = _phrases(lang)
    params = result.get("method", {}).get("params", {})
    if result["method"]["operation"] == "exposure" and params.get("category") in phrases["exposure_labels"]:
        return _lower(phrases["exposure_labels"][params["category"]])
    if result["method"]["operation"] == "resource_estimate" and (params.get("archetype") or {}).get("name"):
        return _lower(params["archetype"]["name"].get(lang, params["archetype"]["name"].get("en", "")))
    if result.get("variable") in phrases["labels"]:
        return phrases["labels"][result["variable"]]
    name = names.get(result.get("variable"), {})
    return _lower(name.get(lang, name.get("en", ""))) or phrases["formats"]["indicator"].lower()


def _unit(unit, lang):
    return _phrases(lang)["units"].get(unit, unit or "")


def _when(t, lang, *, yearly=False, monthly=False):
    """A date of a series as a reader says it: the year, the month or the day."""
    parts = [int(x) for x in t[:10].split("-")]
    year, month, day = parts[0], (parts[1:2] or [1])[0], (parts[2:3] or [1])[0]
    if yearly:
        return str(year)
    if monthly:
        return f"{MONTHS[lang][month - 1]} de {year}" if lang == "es" else f"{MONTHS[lang][month - 1]} {year}"
    name = MONTHS[lang][month - 1]
    return f"{day} de {name} de {year}" if lang == "es" else f"{day} {name} {year}"


def _say(lang, key, slots=(), **values):
    """One sentence of `answer.<key>`. Values named in `slots` are result placeholders and stay in the checked copy;
    the rest (place, labels, figures read from a result) are left out of it."""
    template = _phrases(lang)["answer"]
    for part in key.split("."):
        template = template[part]
    check = template.format(**{name: value if name in slots else "" for name, value in values.items()})
    return template.format(**values), check


def _by_order(results, operations):
    from . import reading_order

    return [r for r in sorted(results, key=reading_order) if r["method"]["operation"] in operations]


def _whatif(plan, results, names, place):
    estimates = sorted((r for r in _by_order(results, {"resource_estimate"}) if r.get("sources")),
                       key=lambda r: not _present(r))  # what was estimated first, what was not after
    first = next((r for r in estimates if _present(r) and resource_row(r) and "value" in r), None)
    if first is None:
        return None
    lead = {}
    for lang in ("en", "es"):
        lines = []
        for result in estimates:
            label = _label(result, names, lang)
            if not _present(result):
                lines.append(_say(lang, "whatif.absent", label=_capital(label), reason=_reason(result, lang)))
                continue
            row = resource_row(result)
            if not row or "value" not in result:
                continue
            unit = _unit(result["unit"], lang)
            values = {"label": _capital(label), "place": place, "value": _slot(result, "value"),
                      "unit": _slot(result, "unit"),
                      "low": number(row["low"], lang), "high": number(row["high"], lang), "range_unit": unit,
                      "quantity": number(row["quantity"], lang), "per": _unit(row["per"], lang)}
            key = f"whatif.{row['basis']}" if result is first else "whatif.more"
            lines.append(_say(lang, key, ("value", "unit"), **values))
        lead[lang] = lines
    ids = [r["id"] for r in estimates]
    return {"ids": ids, "level": first.get("evidence_level", "E0"), "lead": lead, "subject": first}


def _state(plan, results, names, place):
    wanted = (plan.get("variables") or [None])[0]
    series = [r for r in _by_order(results, {"read_series"}) if _present(r) and state_points(r) and r.get("unit")]
    result = next((r for r in series if r["variable"] == wanted), None) or next(iter(series), None)
    if result is None:
        return None
    points = state_points(result)
    params = result["method"].get("params", {})
    monthly = result["unit"].endswith("/month") or params.get("aggregate") == "month"
    yearly = params.get("aggregate") == "year" or (len(points) > 1 and all(t[5:10] == "01-01" for t, _ in points))
    lead = {}
    for lang in ("en", "es"):
        label = _capital(_label(result, names, lang))
        t, v = points[-1]
        latest = {"when": _when(t, lang, yearly=yearly, monthly=monthly), "value": number(v, lang),
                  "unit": _unit(result["unit"].removesuffix("/month") if monthly else result["unit"], lang)}
        if len(points) == 1:
            lead[lang] = [_say(lang, "state.one", place=place, label=label, **latest)]
            continue
        key = "state.series" if "period" in result else "state.series_no_period"
        lead[lang] = [_say(lang, key, ("series", "period"), place=place, label=label, series=_slot(result, "series"),
                           period=_slot(result, "period") if "period" in result else ""),
                      _say(lang, "state.latest", **latest)]
    return {"ids": [result["id"]], "level": result.get("evidence_level", "E0"), "lead": lead, "subject": result}


def _trend(plan, results, names, place):
    result = next((r for r in _by_order(results, {"trend"}) if _present(r) and {"value", "interval"} <= r.keys()), None)
    if result is None:
        return _trend_unclear(results, names, place)
    level = result.get("evidence_level", "E0")
    detected = result.get("direction_assertable") is True and level >= "E1" and result["value"] != 0
    crosses = (result.get("diagnostics") or {}).get("interval_crosses_zero") is True or (
        result["interval"]["low"] <= 0 <= result["interval"]["high"])
    key = "trend.detected" if detected else ("trend.unclear" if crosses else "trend.estimate")
    slots = ("value", "unit", "interval", "period", "statistic")
    lead = {}
    for lang in ("en", "es"):
        values = {"place": place, "label": _capital(_label(result, names, lang)), "value": _slot(result, "value"),
                  "unit": _slot(result, "unit"), "interval": _slot(result, "interval"),
                  "period": _slot(result, "period") if "period" in result else "",
                  "verb": _phrases(lang)["tendency"]["up" if result["value"] > 0 else "down"]}
        lines = [_say(lang, key, slots, **values)]
        if result.get("statistic") and detected:
            lines.append(_say(lang, "trend.statistic", slots, statistic=_slot(result, "statistic")))
        lead[lang] = lines
    return {"ids": [result["id"]], "level": level, "lead": lead, "subject": result, "variant": key}


def _trend_unclear(results, names, place):
    """The trend was computed but its interval includes zero, so it arrives without a value: the data are there and
    give no direction. Says that, without a figure, instead of the "missing" opening (which means no data)."""
    def crosses(r):
        interval = r.get("interval") or {}
        return (r.get("diagnostics") or {}).get("interval_crosses_zero") is True or (
            {"low", "high"} <= interval.keys() and interval["low"] <= 0 <= interval["high"])

    result = next((r for r in _by_order(results, {"trend"}) if "absence" in r and crosses(r)), None)
    if result is None:
        return None
    lead = {lang: [_say(lang, "trend.unclear_no_value", place=place, label=_capital(_label(result, names, lang)))]
            for lang in ("en", "es")}
    return {"ids": [result["id"]], "level": "E0", "lead": lead, "subject": result, "variant": "trend.unclear",
            "headline": "trend_unclear"}


def _event(plan, results, names, place):
    from . import episode_day, rare_event

    slots = ("value", "interval", "record", "event_value", "window", "anomaly", "unit")
    episode = next((r for r in _by_order(results, {"return_level"}) if r.get("sources")
                    and "event_year" in (r.get("diagnostics") or {})
                    and "return_period_years" not in r["diagnostics"]), None)
    lead, ids, subject = {"en": [], "es": []}, [], None
    if episode is not None:
        diagnostics, rare, day = episode["diagnostics"], rare_event(episode), episode_day(episode)
        complete = rare or (_present(episode) and {"value", "interval"} <= episode.keys()
                            and type(diagnostics.get("n_years")) is int)
        if complete:
            key = "event." + ("rare" if rare else "usual") + ("_day" if day else "_year")
            for lang in lead:
                values = {"place": place, "year": str(diagnostics["event_year"]), "record": _slot(episode, "record"),
                          "day": _when(diagnostics["event_day"], lang) if day else "",
                          "event_value": _slot(episode, "event_value") if day else ""}
                if not rare:
                    values.update(value=_slot(episode, "value"), interval=_slot(episode, "interval"))
                lead[lang].append(_say(lang, key, slots, **values))
            ids, subject = [episode["id"]], episode
    extreme = next((r for r in _by_order(results, {"rain_extreme"}) if _present(r) and "value" in r), None)
    if subject is None and extreme is not None:
        params = extreme["method"].get("params", {})
        window = _slot(extreme, "method") if params.get("months") and "year" in params else (
            _slot(extreme, "period") if "period" in extreme else "")
        anomaly = next((r for r in _by_order(results, {"same_date_anomaly"}) if _present(r) and "value" in r
                        and r.get("unit")), None)
        for lang in lead:
            lead[lang].append(_say(lang, "event.percentile", slots, place=place, window=window,
                                   value=_slot(extreme, "value")))
            if anomaly:
                lead[lang].append(_say(lang, "event.anomaly", slots, anomaly=_slot(anomaly, "value"),
                                       unit=_slot(anomaly, "unit")))
        ids, subject = [extreme["id"], *([anomaly["id"]] if anomaly else [])], extreme
    if subject is None:
        return None
    return {"ids": ids, "level": subject.get("evidence_level", "E0"), "lead": lead, "subject": subject}


def _ssp(name):
    import re

    found = re.fullmatch(r"ssp(\d)(\d)(\d)", name)
    return f"SSP{found[1]}-{found[2]}.{found[3]}" if found else name


def _scenario(plan, results, names, place):
    from .render import _window, scenario_series

    result = next((r for r in _by_order(results, {"climate_delta"}) if _present(r) and scenario_series(r)
                   and r.get("unit")), None)
    if result is None:
        return None
    points = scenario_series(result)
    horizon = max({p["t"] for p in points}, key=lambda t: (len(t), t))
    at = sorted((p for p in points if p["t"] == horizon), key=lambda p: p["v"])
    params = result["method"].get("params", {})
    change = params.get("output") == "change"
    window = params.get("windows", {}).get(horizon)
    reference = params.get("reference", [1995, 2014])
    lead = {}
    for lang in ("en", "es"):
        def figure(value, lang=lang):
            rendered = number(value, lang)
            return "+" + rendered if change and value > 0 else rendered

        values = {"place": place, "label": _label(result, names, lang), "horizon": horizon,
                  "window": _window(window, lang) if window else horizon, "unit": _unit(result["unit"], lang),
                  "low": figure(min(p["low"] for p in at)), "high": figure(max(p["high"] for p in at)),
                  "low_v": figure(at[0]["v"]), "high_v": figure(at[-1]["v"]),
                  "low_s": _ssp(at[0]["scenario"]), "high_s": _ssp(at[-1]["scenario"]),
                  "reference": f"{reference[0]}–{reference[1]}"}
        key = "scenario." + ("change" if change else "level") + ("_one" if len(at) == 1 else "_range")
        lead[lang] = [_say(lang, key, **values)]
    return {"ids": [result["id"]], "level": result.get("evidence_level", "E0"), "lead": lead, "subject": result,
            "horizon": horizon}


def _impact_answer(plan, results, names, place):
    contrasts = [r for r in _by_order(results, {"baci_contrast"}) if _present(r) and {"value", "interval"} <= r.keys()]
    result = next((r for r in contrasts if r.get("direction_assertable") is True), None)
    key = "impact.detected"
    if result is None:
        result, key = next(iter(contrasts), None), "impact.unclear"
    if result is None:
        return None
    params = result["method"].get("params", {})
    windows = {"before", "after"} <= params.keys()
    slots = ("value", "unit", "interval", "method")
    lead = {}
    for lang in ("en", "es"):
        values = {"place": place, "label": _label(result, names, lang), "value": _slot(result, "value"),
                  "unit": _slot(result, "unit"), "interval": _slot(result, "interval"),
                  "method": _slot(result, "method") if windows else _slot(result, "period"),
                  "verb": _phrases(lang)["impact"]["up" if result["value"] >= 0 else "down"]}
        lead[lang] = [_say(lang, key, slots, **values)]
    return {"ids": [result["id"]], "level": result.get("evidence_level", "E0"), "lead": lead, "subject": result}


def _exposure(plan, results, names, place):
    counted = [r for r in _by_order(results, {"exposure"}) if _present(r) and "value" in r and r.get("unit")]
    if not counted:
        return None
    lead = {}
    for lang in ("en", "es"):
        lead[lang] = [_say(lang, "exposure.first" if result is counted[0] else "exposure.more", ("value", "unit"),
                           place=place, label=_label(result, names, lang), value=_slot(result, "value"),
                           unit=_slot(result, "unit")) for result in counted]
    return {"ids": [r["id"] for r in counted], "level": min(r.get("evidence_level", "E0") for r in counted),
            "lead": lead, "subject": counted[0]}


def _scan_answer(plan, results, names, place, catalog):
    made = scan_opening(plan, results, catalog, {})
    if made is None:
        return None
    piece = made[0]
    lead = {lang: [(piece["lead"][lang], piece["lead"][lang])] for lang in ("en", "es")}
    table = next(r for r in results if r["id"] == piece["results"][0])
    return {"ids": piece["results"], "level": table.get("evidence_level", "E0"), "lead": lead, "subject": table,
            "limit": {lang: _phrases(lang)["design_opening"]["scan_limit"] for lang in ("en", "es")}}


def _reason(result, lang):
    absence = result.get("absence") or {}
    detail = absence.get("detail")
    text = detail.get(lang) if isinstance(detail, dict) else None
    text = text or _phrases(lang)["answer"]["reasons"].get(absence.get("reason"), _phrases(lang)["answer"]["reasons"]
                                                           ["not_computed"])
    return _lower(text.strip().rstrip("."))


def _missing(plan, results, names, place):
    """The main result is absent: say what is missing, why, and what the report does show."""
    from .render import _window

    design = plan.get("design")
    operations = _PRIMARY.get(design, set())
    absent = [r for r in _by_order(results, operations) if "absence" in r] or [
        r for r in _by_order(results, {r["method"]["operation"] for r in results}) if "absence" in r]
    if not absent:
        return None
    gap = absent[0]
    shown = [r for r in _by_order(results, {"read_series"}) if _present(r) and state_points(r) and r.get("unit")
             and r["method"]["operation"] not in operations][:2]
    if design == "state":
        shown = [r for r in shown if r["variable"] != gap.get("variable")]
    lead = {}
    for lang in ("en", "es"):
        what = _say(lang, f"missing.what.{design}" if design in _phrases(lang)["answer"]["missing"]["what"]
                    else "missing.what.other", label=_label(gap, names, lang))[0]
        lines = [_say(lang, "missing.lead", place=place, what=what, reason=_reason(gap, lang))]
        if shown:
            items = [_say(lang, "missing.item", label=_label(r, names, lang), period=_window(
                (state_points(r)[0][0][:4], state_points(r)[-1][0][:4]), lang)) for r in shown]
            joined = [(" " + _phrases(lang)["answer"]["and"] + " ").join(item[i] for item in items) for i in (0, 1)]
            lines.append((_say(lang, "missing.shown", ("items",), items=joined[0])[0],
                          _say(lang, "missing.shown", ("items",), items=joined[1])[1]))
        else:
            lines.append(_say(lang, "missing.nothing"))
        lead[lang] = lines
    return {"ids": [gap["id"], *(r["id"] for r in shown)], "level": "E0", "lead": lead, "subject": gap,
            "variant": "missing"}


_ANSWERS = {"whatif": _whatif, "state": _state, "trend": _trend, "event": _event, "scenario": _scenario,
            "impact": _impact_answer, "exposure": _exposure}


def _limit(design, made, lang):
    from . import CONTRAST_LIMIT, rain_limit, scenario_limit

    subject = made["subject"]
    phrases = _phrases(lang)["answer"]
    if made.get("variant") == "missing":
        return phrases["missing"]["limit"]
    if "limit" in made:
        return made["limit"][lang]
    if design == "event":
        return rain_limit(subject, lang) or phrases["event"]["limit"]
    if design == "scenario":
        return " ".join(filter(None, (phrases["scenario"]["limit"], scenario_limit(subject, lang))))
    if design == "impact":
        return " ".join(filter(None, (pretrend_limit(subject, lang), CONTRAST_LIMIT[lang])))
    if design == "trend":
        return phrases["trend"]["limit" if made.get("variant") == "trend.detected" else "limit_unclear"]
    return phrases.get(design, {}).get("limit", "")


def answer(plan, results, piece, catalog):
    """The opening's first sentence answers the question, in every design: the main figure with its place, period
    and unit read from the results, or what is missing and why. Builds on `state_answer` (population in hot cells),
    which goes first. Returns a copy of `piece` with its lead and limits (and a generic headline or meaning)
    replaced, or None when the wording does not pass `validate_text` or there is nothing to answer with."""
    from hup_api.reviewer.text import validate_text

    if "zone" not in plan:
        return None  # nothing to say "where" with
    if answered := state_answer(plan, results, piece):
        for lang in ("en", "es"):  # state_answer keeps the headline; a generic one names the subject instead
            if _generic("headline", answered.get("headline", {}).get(lang, "")):
                label = _phrases(lang)["state_answer"]["population_in_hot_cells"]["headline"]
                answered["headline"] = {**answered["headline"], lang: label.format(place=place_label(plan["zone"]))}
        return answered
    decided = {item.get("field") for item in plan.get("answer", {}).get("decided", [])}
    from hup_api.assistant import milestone as context

    if "recurrence" in decided or context.FIELD in context.decisions_of(plan) or plan.get("design") == "scan":
        return None  # recurrence(), milestone() and scan_opening() already answer with their own figures
    design = plan.get("design")
    names = {v["id"]: v["name"] for v in catalog.get("variables", [])}
    place = milestone_place(plan).partition(",")[0].strip()
    if design == "scan":
        made = _scan_answer(plan, results, names, place, catalog)
    else:
        made = _ANSWERS[design](plan, results, names, place) if design in _ANSWERS else None
    made = made or _missing(plan, results, names, place)
    if made is None:
        return None
    phrases = {lang: _phrases(lang)["answer"] for lang in ("en", "es")}
    limit = {lang: _limit(design, made, lang) for lang in ("en", "es")}
    checked = {lang: " ".join([*(check for _, check in made["lead"][lang]), limit[lang]]) for lang in ("en", "es")}
    if validate_text({"text": checked, "evidence_level": made["level"]}, results):
        return None
    answered = dict(piece)
    for field in ("headline", "lead", "meaning", "cannot_say"):
        answered[field] = dict(piece.get(field) or {})
    ids = set(made["ids"])
    for lang in ("en", "es"):
        old = (piece.get("lead") or {}).get(lang, "")
        cited = {match[1] for match in SLOT.finditer(old)}
        kept = "" if _generic("lead", old) or cited & ids else old
        answered["lead"][lang] = " ".join(filter(None, (*(text for text, _ in made["lead"][lang]), kept)))
        if _generic("headline", answered["headline"].get(lang, "")):
            key = "missing" if made.get("variant") == "missing" else made.get("headline", design)
            answered["headline"][lang] = phrases[lang]["headline"].get(key, phrases[lang]["headline"]["other"]).format(
                place=place, label=_capital(_label(made["subject"], names, lang)))
        if _generic("meaning", answered["meaning"].get(lang, "")):
            answered["meaning"][lang] = phrases[lang]["meaning"].get(design, phrases[lang]["meaning"]["other"])
        before = answered["cannot_say"].get(lang, "")
        if limit[lang] and limit[lang] not in before:
            answered["cannot_say"][lang] = f"{before} {limit[lang]}".strip()
    answered["results"] = list(dict.fromkeys([*made["ids"], *piece.get("results", [])]))
    return answered
