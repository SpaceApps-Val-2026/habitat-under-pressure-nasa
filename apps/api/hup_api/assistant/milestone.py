"""A descriptive local comparison around a stated or assumed event date."""
import re
from datetime import date

from hup_api.assistant import grounding, templates, understand

FIELD = "milestone"
FAST_SOURCES = {"nasa-power", "imerg-monthly"}
CLIMATE = {"air_temperature", "air_temperature_max", "precipitation"}
_MONTHS = "|".join(sorted(understand.MONTHS, key=len, reverse=True))
_MONTH_DATE = re.compile(rf"\b(?:(\d{{1,2}}) (?:de )?)?({_MONTHS}) (?:de |del |of |d )?(\d{{4}})\b")
_MONTH_FIRST = re.compile(rf"\b({_MONTHS}) (\d{{1,2}})(?:st|nd|rd|th)? (\d{{4}})\b")
_NUMERIC_DATE = re.compile(r"\b(?:\d{4}-\d{2}(?:-\d{2})?|\d{1,2}/\d{1,2}/\d{4})\b")
_YEAR = re.compile(r"\b(?:1[89]\d{2}|20\d{2})\b")
_RAIN = re.compile(r"\b(?:dana|gota (?:fria|freda)|flood\w*|inund\w*|riada\w*|pluja|lluvia|rain\w*)\b")
_SURFACE = re.compile(r"\b(?:incend\w*|fire\w*|refiner\w*|surface temperature|temperatura de superficie)\b")
_LAND = re.compile(r"\b(?:built up|built surface|suelo|sol|land use|superficie construida|edific\w*)\b")
_HEALTH = re.compile(r"\b(?:salud|salut|health|humo|fum|smoke|calidad del aire|qualitat de l aire|air quality)\b")
_ES_EVENT_ARTICLE = {
    "guerra": "la", "invasión": "la", "dana": "la", "gota": "la", "inundación": "la", "tormenta": "la",
    "catástrofe": "la", "incendio": "el", "terremoto": "el", "conflicto": "el",
}
_EVENT_REFERENCES = ({
    "aliases": (
        "war in ukraine", "ukraine war", "war against ukraine", "russian ukrainian war", "russo ukrainian war",
        "invasion of ukraine", "guerra de ucrania", "guerra en ucrania",
        "guerra a ucrania", "guerra rusia ucrania", "guerra entre rusia y ucrania", "guerra russo ucraïnesa",
        "invasión rusa de ucrania", "invasión de ucrania", "guerra a Ucraïna", "guerra d'Ucraïna",
        "invasió russa d'Ucraïna", "invasió d'Ucraïna",
    ),
    "date": "2022-02-24",
    "name": {"en": "the full-scale Russian invasion of Ukraine",
             "es": "la invasión rusa a gran escala de Ucrania"},
    "source": "https://www.un.org/pga/wp-content/uploads/sites/108/2024/08/"
              "Assembly-for-Peace_GA-Digital-Handbook-on-Peace-and-Security.pdf",
},)

UNATTRIBUTED = templates._t(
    "These observations cannot attribute the changes to the event. Temporal overlap alone cannot establish that link.",
    "Estos datos no permiten atribuir los cambios al suceso. Coincidir en el tiempo no establece ese vínculo.",
)
UNLOCATED = templates._t(
    "Delfos could not date the event. The scan describes recent trends in the area.",
    "No se ha podido fechar el suceso. El escaneo describe las tendencias recientes de la zona.",
)


def _iso(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        try:
            parts = value.split("-")
            date(*map(int, parts + ["01"] * (3 - len(parts))))
        except ValueError:
            return None
        return value
    return None


def _written_dates(text):
    precise = []
    for item in _NUMERIC_DATE.findall(text):
        if "/" in item:
            day, month, year = item.split("/")
            item = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        precise.append(item)
    words = understand.norm(text)
    for day, month, year in _MONTH_DATE.findall(words):
        precise.append(f"{year}-{understand.MONTHS[month]:02d}" + (f"-{int(day):02d}" if day else ""))
    for month, day, year in _MONTH_FIRST.findall(words):
        precise.append(f"{year}-{understand.MONTHS[month]:02d}-{int(day):02d}")
    # Keep the highest written precision for each year.
    found = [d for item in precise if (d := _iso(item))]
    return set(found + [y for y in _YEAR.findall(text) if not any(d.startswith(y) for d in precise)])


def dating(text, proposed):
    dates = _written_dates(text)
    if len(dates) == 1:
        return next(iter(dates)), "stated"
    proposed = proposed or {}
    value = _iso(proposed.get("date"))
    if value in dates:
        return value, "stated"
    if value and not dates and proposed.get("basis") == "assumed" and value <= date.today().isoformat():
        return value, "assumed"
    return None, None


def event_reference(text, proposed):
    """Pick the documented phase for an undated event, independently of the affected place."""
    if _written_dates(text):
        return None
    names = event_label(proposed) or {}
    fields = [f" {understand.norm(wording)} " for wording in (text, *names.values())]
    return next((ref for ref in _EVENT_REFERENCES if any(
        f" {understand.norm(alias)} " in field for field in fields for alias in ref["aliases"]
    )), None)


def _topics(text, proposed, catalog):
    words = understand.norm(text)
    topics = set(proposed) | set(grounding.matching_variables(text, catalog))
    if _RAIN.search(words):
        topics.add("precipitation")
    if _LAND.search(words):
        topics.add("built_up_surface")
    if _SURFACE.search(words):
        topics.update(("lst", "ndvi"))
    if _HEALTH.search(words) or re.search(r"\b(?:clima|climate)\b", words):
        topics.update(CLIMATE)
    return topics


def decisions_of(plan):
    """The contextual plan records which date it used and where the comparison starts."""
    return {item["field"]: item["text"] for item in plan.get("answer", {}).get("decided", [])}


def place_in_question(text, zone, fallback):
    """Prefer a gazetteer alias the person used, when the same place has several names."""
    if not (zone.get("source") or "").startswith("OpenStreetMap (Nominatim),"):
        return fallback
    question = understand.norm(text)
    for item in zone["name"].split(","):
        alias = understand.norm(item)
        if alias and re.search(rf"\b{re.escape(alias)}\b", question):
            return item.strip()
    return fallback


def event_name(text, place):
    """Keep the event as named in the question, without importing the effect or destination clause."""
    tokens = [token for token in re.finditer(r"\w+", text) if understand.norm(token[0])]
    words = " ".join(understand.norm(token[0]) for token in tokens)
    since = grounding._AFTER_NAMED.search(words)
    if since:
        if since[0].split()[-1] in {"started", "began", "happened"}:
            return tokens[len(words[:since.start()].split()) + 1][0]
        first = len(words[:since.end()].split())
        if words.split()[first - 1] in {"el", "la", "l", "los", "las", "les", "the"}:
            first -= 1
    else:
        event = next((m for pattern in (grounding._CONFLICT, grounding._HAZARD, grounding._MILESTONE)
                      if (m := pattern.search(words))), None)
        if event is None:
            return ""
        first = len(words[:event.start()].split())
        if first and words.split()[first - 1] in {"el", "la", "l", "los", "las", "les", "the"}:
            first -= 1
    if first >= len(tokens):
        return ""
    tail = " ".join(words.split()[first:])
    target = re.escape(understand.norm(place))
    stop = re.search(rf"\b(?:afect\w*|affect\w*|ha tenido|had on|(?:a|en|in|at|on|sobre) {target}\b)\b", tail)
    last = first + len(tail[:stop.start()].split()) if stop else len(tokens)
    if first >= last:
        return ""
    name = text[tokens[first].start():tokens[last - 1].end()]
    return name if len(name) <= 96 else ""


def event_label(proposed):
    names = (proposed or {}).get("event")
    if not isinstance(names, dict):
        return None
    if not all(isinstance(names.get(lang), str) and 0 < len(names[lang].strip()) <= 100
               and not re.search(r"[\n\r<>{}]|https?://", names[lang]) for lang in ("en", "es")):
        return None
    return event_display({lang: names[lang].strip().rstrip(".?!") for lang in ("en", "es")})


def event_display(names):
    """Format event names as inline names, including labels saved before articles were required."""
    en, es = names["en"], names["es"]
    article = re.match(r"^(el|la|los|las)\s+", es, re.I)
    body = es[article.end():] if article else es
    first, _, _ = body.partition(" ")
    if (expected := _ES_EVENT_ARTICLE.get(first.casefold())) is not None:
        body = ("DANA" if first.casefold() == "dana" else first.lower()) + body[len(first):]
        es = f"{article[1].lower() if article else expected} {body}"
    elif article:
        es = f"{article[1].lower()} {body}"
    if re.match(r"^the\s+", en, re.I):
        en = "the" + en[3:]
    elif re.search(r"\b(?:war|invasion|storm|flood|fire|earthquake|conflict)\b", en, re.I):
        if re.match(r"^(war|invasion|storm|flood|fire|earthquake|conflict)\b", en, re.I):
            en = en[0].lower() + en[1:]
        en = f"the {en}"
    return {"en": en, "es": es}


def _matching_months(zone, period, end):
    """Respect the packaged rainfall record's last year, including when it has only some months."""
    rows = templates._held("precipitation", zone, period)
    months = {int(row["period"]["start"][5:7]) for row in rows
              if row.get("source") == "imerg-monthly" and row["period"]["start"].startswith(str(end))}
    return sorted(months) or list(range(1, 13))


def _available(variable, zone, period, offline):
    if not templates._overlap_years(period, variable)[-1] or not templates._spatial_limit(variable, zone)[0]:
        return False
    if not offline and "nasa-power" in variable["sources"]:
        return True
    return any(row["source"] in variable["sources"] for row in templates._held(variable["id"], zone, period))


def build(zone, catalog, request, proposed, variables):
    """Use scan for recent context and the existing anomaly operation for dated comparisons."""
    text = request["text"]
    reference = event_reference(text, proposed)
    if reference:
        proposed = {"date": reference["date"], "basis": "assumed", "event": reference["name"]}
    event_date, basis = dating(text, proposed)
    end = templates.LAST_COMPLETE_YEAR
    event_year = int(event_date[:4]) if event_date else None
    # Keep a recent trend and, for a dated event within the source era, enough years to form its reference.
    start = min(end - 11, event_year - 3) if event_year and 1981 < event_year <= end else end - 11
    period = {"start": f"{start}-01-01", "end": f"{end}-12-31"}
    topics = _topics(text, variables, catalog)
    offline = templates._offline()
    climate = [v for v in catalog["variables"] if "scan" in v["designs"] and v["sources"]
               and set(v["sources"]) <= FAST_SOURCES and _available(v, zone, period, offline)]
    focus = [v for v in climate if v["id"] in topics]
    land = next((v for v in catalog["variables"] if v["id"] == "built_up_surface" and v["id"] in topics), None)
    # Catalogued GHSL epochs after 2018 are carried forward. They cannot describe a change since a recent event.
    land_period = {"start": min(period["start"], "2000-01-01"), "end": "2018-12-31"}
    has_land = land and (event_year is None or event_year < 2015) and _available(land, zone, land_period, offline)
    if has_land:
        focus.append(land)
    chosen = focus or climate
    if not chosen:
        return None
    ids = [v["id"] for v in chosen]
    operations = {op["id"]: op for op in catalog["operations"]}
    months = _matching_months(zone, period, end) if "precipitation" in ids else list(range(1, 13))
    after = event_year + 1 if event_year else None
    # A date with month precision allows using only the months after the event in its own year.
    if event_year == end and len(event_date) >= 7:
        subsequent = [m for m in months if m > int(event_date[5:7])]
        if subsequent:
            months, after = subsequent, event_year
    if len(months) != 12:
        period["months"] = months
    if has_land:
        period["start"] = land_period["start"]
    plan = templates.template("scan", zone, period, ids, catalog, request=request, offline=offline)
    if plan["answer"]["level"] == "not_possible":
        return None

    decisions = []
    if not event_date:
        decision = UNLOCATED
    elif basis == "stated":
        decision = templates._t(f"Delfos uses the date stated in the question: {event_date}.",
                                f"Delfos usa la fecha indicada en la pregunta: {event_date}.")
    elif reference:
        event = reference["name"]
        decision = templates._t(
            f"Delfos uses {event_date}, the start of {event['en']}. Choosing this phase is an assumption.",
            f"Delfos toma {event_date}, inicio de {event['es']}. Elegir esta fase es un supuesto.")
    else:
        decision = templates._t(
            f"Delfos assumes the event began in {event_date}. This date is an assumption.",
            f"Delfos toma {event_date} como inicio del suceso. La fecha es un supuesto.")
    decisions.append({"field": FIELD, "text": decision})
    if reference:
        decisions.append({"field": "milestone_source", "text": dict.fromkeys(("en", "es"), reference["source"])})
    if label := event_label(proposed):
        decisions.append({"field": "event", "text": label})
    if event_year and start < event_year and after <= int(period["end"][:4]) and "anomaly" in operations:
        before = max(start, event_year - 3)
        _compare(plan, operations["anomaly"], before, event_year)
        en = f"Delfos compares the same calendar months from {after} with the reference {before}–{event_year - 1}."
        es = f"Delfos compara los mismos meses desde {after} con el periodo de referencia {before}–{event_year - 1}."
        decisions.append(templates._decision("baseline", en, es))
    elif event_year:
        decisions.append(templates._decision(
            "baseline", "The record lacks full years on both sides. The scan provides historical context.",
            "El registro disponible no abarca años completos a ambos lados. El escaneo aporta contexto histórico."))

    for step in plan["steps"]:
        if step["operation"] == "read_series":
            variable = step["params"]["variable"]
            if variable == "precipitation":
                step["params"]["statistic"] = "mean"  # keep mm/month for a mean of the matched monthly totals
            if variable == "built_up_surface":
                step["params"].update(min_count=1, read_period=land_period)
    limits = [UNATTRIBUTED]
    if _HEALTH.search(understand.norm(text)) or grounding.signature(text)[1]:
        limits.append(templates._t(
            "To assess effects on health or air quality, local pollution, exposure and health records would be needed.",
            "Para evaluar efectos sobre la salud o la calidad del aire harían falta mediciones locales de "
            "contaminación, exposición y datos sanitarios."))
    if _RAIN.search(understand.norm(text)):
        limits.append(templates._t(
            "To assess local flood impacts, compare inundation maps, water levels and records of damage or recovery.",
            "Para evaluar efectos locales de una inundación harían falta mapas de extensión, niveles de agua "
            "y registros de daños o recuperación."))
    if {"lst", "ndvi"} & topics:
        limits.append(templates._t(
            "A follow-up with Landsat could examine surface temperature and greenness near the site.",
            "Un análisis posterior con Landsat podría examinar la temperatura de superficie y el verde en la zona."))
    if land and not has_land:
        limits.append(templates._t(
            "A built-up surface comparison needs observed building footprints on both sides of the event.",
            "La comparación de superficie construida requiere huellas de edificios observadas a ambos lados del hito."))
    if not focus:
        limits.append(templates._t("The regional climate series provide background for the place.",
                                   "Las series de clima regional aportan contexto sobre el lugar."))
    reads = [step for step in plan["steps"] if step["operation"] == "read_series"]
    runnable = False
    for step in reads:
        origin, missing = templates.read_origin(step, plan, catalog, offline)
        step["data"]["origin"] = origin
        runnable = runnable or not missing
    if not runnable:
        return None
    plan["answer"].update(level="with_limits", decided=decisions, limits=limits + plan["answer"].get("limits", []))
    return plan


def _compare(plan, operation, before, event_year):
    """A fixed pre-event mean for every annual point, with no spatial control or causal claim."""
    steps = plan["steps"]
    reads = [step for step in steps if step["operation"] == "read_series"]
    editorial = [step for step in steps if step["type"] in {"write", "review"}]
    comparison = []
    for read in reads:
        sparse = read["params"]["variable"] == "built_up_surface"
        baseline_start, minimum = (event_year - 5, 1) if sparse else (before, event_year - before)
        comparison.append({"id": f"anomaly-{read['id']}", "type": operation["type"],
                           "operation": operation["id"], "version": operation["version"],
                           "params": {"baseline": {"start": f"{baseline_start}-01-01",
                                                   "end": f"{event_year - 1}-12-31"},
                                      "min_baseline_dates": minimum},
                           "depends_on": [read["id"]]})
    for step in editorial:
        if step["type"] == "write":
            step["depends_on"].extend(s["id"] for s in comparison)
    plan["steps"] = [step for step in steps if step not in editorial] + comparison + editorial
