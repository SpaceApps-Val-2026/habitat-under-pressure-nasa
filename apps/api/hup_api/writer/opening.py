"""Evidence-based journalistic openings when the provider supplies no approved piece."""

from . import CONTRAST_LIMIT, REPORT_END, _phrases, finding_text, place_label, pretrend_limit
from .render import MONTHS, number, state_points

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
