"""Conservative decisions when a request crosses a boundary of the catalogue."""
import re
from datetime import date

from hup_api.assistant import question_article, templates, understand

_REPEAT = re.compile(
    r"\b(?:volvera|tornara|repetir[aas]?|repeat\w*|recur\w*|next|proxim\w*|probabil\w*|"
    r"frecuen\w*|frequen\w*|how often|cada cuanto|cada quant|how many danas|cuantas danas|quantes danes)\b"
)
_ANOTHER = re.compile(r"\b(?:when|cuando|quan)\b.*\b(?:another|otr[ao]|altre[sa]?)\b")
_DANA = re.compile(r"\b(?:dana[s]?|danes|gota fria|gota freda|cut[- ]off low)\b")
_HAZARD = re.compile(
    r"\b(?:flood\w*|riada[s]?|inund\w*|heat wave[s]?|heatwave[s]?|ola[s]? de calor|onada[s]? de calor|"
    r"earthquake[s]?|terremoto[s]?|terratremol[s]?|wildfire[s]?|incendio[s]? forestal\w*)\b"
)
_DATED_EVENT = re.compile(
    r"\b(?:when will|when could|cuando habra|quan hi haura|tomorrow|manana|dema|next week|"
    r"(?:next|proxim[ao])\s+(?:flood\w*|riada|inund\w*|heat\s?wave|ola de calor|earthquake|terremoto))\b"
)
_CONFLICT = re.compile(r"\b(?:war|wars|guerra[s]?|conflicto armado|conflicte armat|armed conflict)\b")
_EFFECT = re.compile(r"\b(?:affect\w*|afect\w*|effects?|efect[eo]s?|impact\w*|consecuencias?|consequences?)\b")
_MILESTONE = re.compile(
    r"\b(?:dana|gota (?:fria|freda)|fires?|incend\w*|refiner\w*|catastrof\w*|disaster\w*|"
    r"obra[s]?|construction|ley|llei|law|outage\w*|apagon\w*|suceso\w*|esdeveniment\w*|event\w*|"
    r"explosi\w*|derrame\w*|spill\w*|sismo\w*|huracan\w*|hurricane\w*)\b"
)
_AFTER_NAMED = re.compile(
    r"\b(?:(?:desde|des de|since|after|tras|despues de|despres de) (?:el|la|l|los|las|les|the|que) "
    r"(?!(?:ano|year|any|decade|decada|century|siglo|segle|\d+)\b)|"
    r"since [a-z]+ (?:started|began|happened))\b"
)
_CHANGE = re.compile(r"\b(?:chang\w*|cambi\w*|canvi\w*|pasado|passat|happened|happening|evolu\w*)\b")
_EXPLICIT_CONTRAST = re.compile(r"\b(?:before and after|antes y despues|abans i despres)\b")
_WORKS = re.compile(r"\b(?:fabrica|factory|planta|plant|obra|construction|gigafactoria|gigafactory)\b")
_LOCAL_HEAT = re.compile(r"\b(?:temperatura|temperature|calor|heat|lst)\b")
_SINCE_DATE = re.compile(r"\b(?:desde|since|des de) (?:el (?:ano )?)?(?:19|20)\d{2}\b")
_DIRECT_EFFECT = re.compile(r"\b(?:affect(?:ed|ing|s)?|afecta|afectara|on|sobre)\s+(?:a\s+)?"
                            r"([\w\-]+(?:[ ,]+[\w\-]+){0,3})(?=[.?!]|$)", re.I)
_FINAL_LOCATION = re.compile(r"\b(?:a|en|in|at)\s+(?:la provincia de\s+|the province of\s+)?"
                             r"([\w\-]+(?:[ ,]+[\w\-]+){0,3})(?=[.?!]|$)", re.I)
_PROVINCE = re.compile(r"\b(?:provincia|province)\b")

_OCTOBER = {"start": "2024-10-01", "end": "2024-10-31"}
_REFERENCE = {
    "name": "AEMET · DANA del 29 de octubre de 2024",
    "url": "https://repositorio.aemet.es/handle/20.500.11765/16360",
}
# Gazetteer bounds of the province discussed in the source. Only a contextual
# match for the comparison month, never an estimate of the footprint of the storm.
_REFERENCE_BOUNDS = (-1.528945, 38.686554, -0.024722, 40.211683)
_LIMIT = {
    "en": "Monthly rainfall puts the documented episode in context. It cannot date the next DANA or count DANAs. "
          "The local comparison does not describe the whole region.",
    "es": "La lluvia mensual pone el episodio documentado en contexto. No fecha la próxima DANA ni cuenta DANAs. "
          "La comparación local no describe toda la región.",
}


def signature(text: str) -> tuple[bool, bool]:
    words = understand.norm(text)
    # A past question about a documented episode continues through the ordinary
    # episode planner. The repeat/future wording is what makes this different.
    recurrence = bool(_DANA.search(words) and (_REPEAT.search(words) or _ANOTHER.search(words)))
    return recurrence, bool(_CONFLICT.search(words))


def asks_for_a_forecast(text: str) -> bool:
    words = understand.norm(text)
    return bool(_HAZARD.search(words) and _DATED_EVENT.search(words))


def subject_place(text: str) -> str | None:
    """Recover a lower-case or direct-object place in a recognised question family."""
    if found := _DIRECT_EFFECT.search(text.strip()):
        return found.group(1)
    if named := understand.place_words(text):
        return named
    if found := _FINAL_LOCATION.search(text.strip()):
        return found.group(1)
    return None


def matching_variables(text: str, catalog: dict, allowed: set[str] | None = None) -> list[str]:
    """Match whole expressions from the catalogue before proposing a partial reading."""
    words = f" {understand.norm(text)} "
    matches = []
    for item in catalog["variables"]:
        if allowed is not None and item["id"] not in allowed:
            continue
        labels = [*item.get("answers", []), *item["name"].values()]
        if any(len(key := understand.norm(label)) > 2 and f" {key} " in words for label in labels):
            matches.append(item["id"])
    return matches


def milestone_question(text: str) -> bool:
    """Route clear wording directly to the planner; the model also recognises other paraphrases."""
    words = understand.norm(text)
    named = _CONFLICT.search(words) or _MILESTONE.search(words) or _HAZARD.search(words)
    return not _EXPLICIT_CONTRAST.search(words) and bool(
        (named and _EFFECT.search(words)) or (_AFTER_NAMED.search(words) and _CHANGE.search(words)))


def dated_works(text):
    words = understand.norm(text)
    return not _EXPLICIT_CONTRAST.search(words) and bool(
        _WORKS.search(words) and _LOCAL_HEAT.search(words) and _SINCE_DATE.search(words))


def _in_reference(zone: dict) -> bool:
    west, south, east, north = zone["bbox"]
    lo, bottom, hi, top = _REFERENCE_BOUNDS
    return lo <= (west + east) / 2 <= hi and bottom <= (south + north) / 2 <= top


def _contains(outer: list, inner: list) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def contextual_zone(name: str, candidates: list[dict], text: str) -> dict | None:
    """The official reference can distinguish regions; nested same-name zones prefer the town."""
    initial = understand.norm(name.split(",")[0])
    local = [z for z in candidates if _in_reference(z)]
    exact = [z for z in local if understand.norm(z["name"].split(",")[0]) == initial]
    choices = exact or local
    province = bool(_PROVINCE.search(understand.norm(text)))
    for zone in choices:
        boxes = [other["bbox"] for other in choices if other is not zone]
        box = zone["bbox"]
        if all(other != box and (_contains(box, other) if province else _contains(other, box)) for other in boxes):
            return zone
    return None


def monthly_available(zone: dict, catalog: dict) -> bool:
    """Check the target and the required earlier Octobers before proposing the calculation."""
    series = next((v for v in catalog["variables"] if v["id"] == "precipitation"), None)
    method = next((op for op in catalog["operations"] if op["id"] == "rain_extreme"), None)
    if not series or not method or "event" not in series["designs"] or "imerg-monthly" not in series["sources"]:
        return False
    historical = {"start": f"{series['support']['since']}-01-01", "end": _OCTOBER["end"]}
    minimum = method["params"]["properties"]["min_years"]["default"]
    years = set()
    for row in templates._held(series["id"], zone, historical):
        period = row.get("period")
        if not period or row.get("source") != "imerg-monthly":
            continue
        start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
        years.update(year for year in range(start.year, end.year + 1)
                     if start <= date(year, 10, 1) and end >= date(year, 10, 31))
    return 2024 in years and sum(year < 2024 for year in years) >= minimum


def rain_context(zone: dict, catalog: dict) -> dict | None:
    return _OCTOBER.copy() if _in_reference(zone) and monthly_available(zone, catalog) else None


def recurrence_article(request: dict, zone: dict) -> dict:
    regional = _in_reference(zone)
    url = question_article.save(
        request,
        zone=zone["name"],
        headline={"en": "When another DANA might occur remains unknown",
                  "es": "La fecha de la próxima DANA sigue siendo desconocida"},
        lead={"en": "Delfos has no usable local record of DANA episodes on which to estimate their frequency.",
              "es": "Delfos carece de un registro local utilizable de episodios de DANA para estimar su frecuencia."},
        cannot_say=_LIMIT if regional else {
            "en": "A local event inventory would be needed to estimate historical frequency.",
            "es": "Para estimar la frecuencia histórica hace falta un inventario local de episodios.",
        },
        sources=[_REFERENCE] if regional else [],
    )
    return {"kind": "article", "article_url": url}


def recurrence_limit() -> dict:
    return _LIMIT.copy()
