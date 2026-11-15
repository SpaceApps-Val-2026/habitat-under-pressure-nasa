"""Understand the question before planning it: what the person asked, written down and nothing else.

    understand(text, language, complete=None) -> understanding

An understanding is {"language", "place", "times", "intents", "subjects", "magnitudes", "by"} (UNDERSTANDING_SCHEMA
plus "by": "llm" or "words"). It holds only what is in the text: the place as written, the time references, the
kinds of answer in the project's own terms (the designs), the subjects and every stated size with its unit.

Two ways, one shape:
- words(text, language): regular expressions and a small bilingual vocabulary. No network, never fails. The floor.
- with HUP_UNDERSTAND=llm, one small typed call (adapter task "understand", the small model, strict JSON schema,
  prompts/understand.md). Its answer is checked against the text: a place, a time or a size whose words are not in
  the question is dropped, so the model cannot add what the person did not say. Any failure gives words().

The vocabulary is generic (heat, rain, flood...), never tied to a place or an episode: the variables of each subject
are found in the catalog's own `answers` and names by the prefilter (prefilter.py).
"""
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path

from hup_api.assistant import templates
from hup_api.llm.adapter import LLMUnavailable
from hup_data import settings

PROMPT_FILE = Path(__file__).parent / "prompts" / "understand.md"
PROMPT = PROMPT_FILE.read_text(encoding="utf-8")
# Bump the number when the schema or the checks change; the hash follows every edit of the prompt file.
PROMPT_VERSION = "understand-1-" + hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:10]

LANGUAGES = ("en", "es", "ca", "other")
TIME_KINDS = ("point", "since", "until", "range", "future", "episode", "recent", "unspecified")

# Subject → words that name it, in English, Spanish and Valencian, written without accents (see norm()).
SUBJECTS = {
    "heat": ["heat", "hot", "hotter", "warm", "warming", "temperature", "calor", "caluros", "temperatura",
             "calentamiento", "calor extremo", "ola de calor", "heatwave"],
    "rain": ["rain", "rainfall", "precipitation", "storm", "lluvia", "llueve", "precipitacion", "tormenta",
             "pluja", "dana", "gota fria"],
    "flood": ["flood", "flooded", "flooding", "inundacion", "inundable", "inundad", "riada", "anegad",
              "inundacio", "dana", "gota fria"],
    "drought": ["drought", "dry", "sequia", "seco", "seca", "sequera"],
    "vegetation": ["vegetation", "green", "greenness", "tree", "trees", "verde", "vegetacion", "arbolado",
                   "arboles", "parques", "vegetacio"],
    "water": ["water", "agua", "aigua", "hidric", "consumo de agua"],
    "sea": ["sea", "ocean", "coast", "mar", "costa", "marina"],
    "population": ["population", "people", "inhabitants", "residents", "poblacion", "habitantes", "gente",
                   "personas", "vecinos", "poblacio", "habitants"],
    "building": ["built", "building", "buildings", "urban", "housing", "homes", "construido", "construida",
                 "edificad", "edificacion", "urbano", "viviendas", "vivendes", "sealed", "impervious", "sellado",
                 "impermeab"],
    "groundwater": ["groundwater", "aquifer", "water table", "acuifero", "freatico", "subterranea", "piezometr"],
    "energy": ["energy", "electricity", "power", "energia", "electricidad", "mw", "gw"],
    "land": ["land", "suelo", "terreno", "hectareas", "hectares", "sol"],
}

# Design → words that ask for it. Only used to order the floor's intents; the planner still chooses the design.
INTENT_CUES = {
    "whatif": ["what if", "if we build", "if they build", "were built", "que pasa si", "que pasaria si",
               "si se construy", "si construy", "si se instala", "si s instal", "data centre", "data center",
               "centro de datos", "would need", "necesitaria"],
    "scenario": ["will", "future", "futuro", "futura", "hara", "proyecc", "projection", "scenario", "escenario"],
    "trend": ["rising", "falling", "trend", "tendencia", "subiendo", "bajando", "aumentado", "increase",
              "changed", "cambiado", "getting", "cada vez"],
    "impact": ["before and after", "antes y despues", "abans i despres"],
    "event": ["episode", "episodio", "exceptional", "excepcional", "storm", "tormenta", "dana", "heatwave",
              "ola de calor"],
    "exposure": ["how many people", "cuanta gente", "cuantas personas", "flood-prone", "flood prone",
                 "zona inundable", "zonas inundables", "exposed", "expuest"],
    "scan": ["overview", "general", "everything", "todo sobre", "analiza"],
}

UNITS = {  # stated size units the floor recognises, written as norm() leaves them
    "homes": ["viviendas", "vivendes", "homes", "houses", "dwellings", "pisos"],
    "people": ["habitantes", "inhabitants", "people", "personas", "residents", "residentes", "vecinos"],
    "MW": ["mw"], "GW": ["gw"], "ha": ["hectareas", "hectares", "ha"],
}


def norm(text: str | None) -> str:
    """Lower case, no accents, no punctuation, single spaces: how every comparison here reads text."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _has(normalized: str, word: str) -> bool:
    """The word (or the phrase) starts a word of the text: «inundad» finds «inundada», «mw» does not find «mwh»
    only when it is a whole cue of two letters."""
    if len(word) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z])", normalized) is not None
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}", normalized) is not None


def _cued(normalized: str, table: dict[str, list[str]]) -> list[str]:
    return [key for key, words in table.items() if any(_has(normalized, w) for w in words)]


# what a resource question asks for (A9, assistant front) --------------------------------------------------------

# Words that ask how much of a resource is used, whatever the resource: consume, spend, need (en, es, ca). The
# resource and what uses it are read from the catalog archetypes (archetype_matches), never from this list.
CONSUMPTION_CUES = ["consum", "gastan", "gasten", "necesitan", "necessiten"]
CHANGE_INTENTS = ("trend", "scenario", "impact", "event")  # a change over time is not a current consumption
STEM, MIN_STEM, MIN_SHARED = 5, 4, 2


def _stems(text: str) -> set[str]:
    return {word[:STEM] for word in norm(text).split() if len(word) >= MIN_STEM and not word.isdigit()}


def archetype_matches(text: str, catalog: dict) -> list[dict]:
    """The catalog archetypes a question names: those whose name (in any language the catalog gives) and resource
    share the most word stems with the question, two at least. Several with the same score are all returned, so a
    caller says so; none is a question no archetype covers. A rule on catalog data only: a new archetype can be
    asked about without changing this code."""
    asked = _stems(text)
    cues = _stems(" ".join(CONSUMPTION_CUES))
    scored = []
    for archetype in catalog.get("archetypes") or []:
        names = archetype.get("name") if isinstance(archetype.get("name"), dict) else {}
        resource = _stems(archetype.get("resource") or "")
        shared = asked & set().union(*(_stems(name) for name in names.values() if isinstance(name, str)), resource)
        # «consume» and the resource alone say what is asked, not who uses it: one more word of the name is needed.
        # The verb counts to reach two shared words but not to rank: it is in every «consumption» name.
        if len(shared) >= MIN_SHARED and shared - cues - resource:
            scored.append((len(shared - cues), archetype))
    best = max((score for score, _ in scored), default=0)
    return [archetype for score, archetype in scored if score == best]


def asks_consumption(text: str, understood: dict | None = None) -> bool:
    """A question about how much something uses now («¿Cuánta agua consumen los hogares de Sagunt?»), not about a
    change over time («¿ha aumentado el consumo…?», which goes to its own design)."""
    normalized = norm(text)
    intents = (understood or {}).get("intents") or _cued(normalized, INTENT_CUES)
    if any(intent in CHANGE_INTENTS for intent in intents):
        return False
    if _has(normalized, "how much"):
        return re.search(r"\bhow much\b.*\b(use|uses|used|need|needs|consume|consumes)\b", normalized) is not None
    return any(_has(normalized, cue) for cue in CONSUMPTION_CUES)


# the schema of an understanding ---------------------------------------------------------------------------------


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


UNDERSTANDING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["language", "place", "times", "intents", "subjects", "magnitudes"],
    "properties": {
        "language": {"type": "string", "enum": list(LANGUAGES)},
        "place": _nullable({"type": "string"}),
        "times": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "kind", "start", "end"],
            "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": list(TIME_KINDS)},
                           "start": _nullable({"type": "string"}), "end": _nullable({"type": "string"})}}},
        "intents": {"type": "array", "items": {"type": "string", "enum": list(templates.ALL_DESIGNS)}},
        "subjects": {"type": "array", "items": {"type": "string", "enum": list(SUBJECTS)}},
        "magnitudes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "value", "unit", "what"],
            "properties": {"text": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"},
                           "what": _nullable({"type": "string"})}}},
    },
}


# the floor: words only ------------------------------------------------------------------------------------------

_YEAR = r"(1[89]\d{2}|20\d{2}|21\d{2})"
_TIME_PATTERNS = [  # (kind, pattern over the normalised text); the first that covers a year wins it
    ("range", rf"\b(?:entre|between|from|de|desde|del)\s+(?:el\s+|la\s+)?(?:ano\s+|year\s+)?{_YEAR}\s+"
              rf"(?:y|and|to|a|al|hasta|until|i)\s+(?:el\s+)?{_YEAR}\b"),
    ("range", rf"\b{_YEAR}\s+(?:a|al|to)\s+{_YEAR}\b"),
    ("since", rf"\b(?:desde|since|des de|a partir de)\s+(?:(?:el|la|les|the)\s+)?(?:[a-z]+\s+){{0,3}}?"
              rf"(?:de\s+|del\s+|of\s+)?{_YEAR}\b"),
    ("until", rf"\b(?:hasta|until|fins)\s+(?:el\s+)?{_YEAR}\b"),
    ("point", rf"\b{_YEAR}\b"),
]
_NUMBER = r"(\d{1,3}(?:[.,\s]\d{3})+|\d+(?:[.,]\d+)?)"


def _number(raw: str, language: str) -> float | None:
    """«5.000» is 5000 in Spanish and Valencian, «5,000» in English; «2,5» and «2.5» are both two and a half."""
    raw = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,])\d{3}(\1\d{3})*", raw):
        return float(re.sub(r"[.,]", "", raw))
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


MONTHS = {  # month words in English, Spanish and Valencian, as norm() leaves them → month number
    **{m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                   "september", "october", "november", "december"), 1)},
    **{m: i for i, m in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                   "septiembre", "octubre", "noviembre", "diciembre"), 1)},
    **{m: i for i, m in enumerate(("gener", "febrer", "marc", "abril", "maig", "juny", "juliol", "agost",
                                   "setembre", "octubre", "novembre", "desembre"), 1)},
    "setiembre": 9,
}
_MONTH = re.compile(rf"\b({'|'.join(sorted(MONTHS, key=len, reverse=True))})\s+(?:de\s+|del\s+|of\s+|d\s+)?{_YEAR}\b")


def _times(normalized: str, last_year: int) -> list[dict]:
    found, used = [], set()
    since_years = {m.group(1) for m in re.finditer(rf"\b(?:desde|since|des de)\s+(?:\w+\s+){{0,3}}?{_YEAR}\b",
                                                    normalized)}
    for match in _MONTH.finditer(normalized):  # «octubre de 2024»: one month, unless it starts a «since»
        month, year = MONTHS[match.group(1)], match.group(2)
        if year in since_years or year in used:
            continue
        used.add(year)
        found.append({"text": match.group(0), "kind": "point" if int(year) <= last_year + 1 else "future",
                      "start": f"{year}-{month:02d}", "end": f"{year}-{month:02d}"})
    for kind, pattern in _TIME_PATTERNS:
        for match in re.finditer(pattern, normalized):
            years = [g for g in match.groups() if g]
            if any(y in used for y in years):
                continue
            used.update(years)
            start, end = years[0], years[-1] if kind == "range" else None
            if kind == "point":
                kind, end = ("future" if int(start) > last_year + 1 else "point"), start
            if kind == "until":
                start, end = None, years[0]
            found.append({"text": match.group(0), "kind": kind, "start": start, "end": end})
    if not any(t["kind"] == "since" for t in found):  # «desde la DANA»: a named moment, never dated here
        match = re.search(r"\b(?:desde|since|des de)\s+(?:(?:la|el|les|the|que)\s+)?[a-z]+", normalized)
        if match:
            found.append({"text": match.group(0), "kind": "since", "start": None, "end": None})
    return found


def _magnitudes(normalized_digits: str, language: str) -> list[dict]:
    """Sizes with a unit of UNITS, read from text where only accents and case were removed (the thousands
    separator must survive)."""
    out = []
    for unit, words in UNITS.items():
        for word in words:
            for match in re.finditer(rf"{_NUMBER}\s*{re.escape(word)}(?![a-z])", normalized_digits):
                value = _number(match.group(1), language)
                if value is not None and value > 0 and not re.fullmatch(_YEAR, match.group(1)):
                    out.append({"text": match.group(0), "value": value, "unit": unit, "what": None})
    return out


def _lower_keep_digits(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def words(text: str, language: str, last_year: int | None = None) -> dict:
    """The floor: what regular expressions and the vocabulary find. The place is left to the planner (null)."""
    last_year = last_year if last_year is not None else time.gmtime().tm_year - 1
    normalized = norm(text)
    intents = _cued(normalized, INTENT_CUES)
    times = _times(normalized, last_year)
    if any(t["kind"] == "future" for t in times) and "scenario" not in intents:
        intents.append("scenario")
    return {"language": language if language in LANGUAGES else "other", "place": None, "times": times,
            "intents": intents, "subjects": _cued(normalized, SUBJECTS),
            "magnitudes": _magnitudes(_lower_keep_digits(text), language), "by": "words"}


# the typed call -------------------------------------------------------------------------------------------------


def mode() -> str:
    """HUP_UNDERSTAND: "llm" to make the small typed call, anything else (the default) for words only."""
    settings.load()  # loads a local .env first, as every other setting
    return "llm" if os.environ.get("HUP_UNDERSTAND", "").strip().lower() == "llm" else "words"


def _context(last_year: int) -> str:
    designs = ", ".join(templates.ALL_DESIGNS)
    return (f"CONTEXT\nDesigns (intents): {designs}\nSubjects: {', '.join(SUBJECTS)}\n"
            f"TODAY: {time.strftime('%Y-%m-%d', time.gmtime())}; last complete calendar year: {last_year}")


def request_block(text: str, language: str) -> str:
    clean = re.sub(r"(?i)</?\s*request\s*>", " ", text)  # the markers cannot be closed or reopened from inside
    return f"Language selected in the interface: {language}\n<request>\n{clean}\n</request>"


def _in_text(fragment, normalized: str) -> bool:
    return isinstance(fragment, str) and bool(norm(fragment)) and norm(fragment) in normalized


def _years_written(value, normalized: str) -> bool:
    years = re.findall(r"\d{4}", value or "")
    return all(re.search(rf"\b{y}\b", normalized) for y in years)


def checked(output: dict, text: str, language: str) -> dict:
    """The model's answer with everything it could not have read in the text removed."""
    normalized = norm(text)
    place = output.get("place") if _in_text(output.get("place"), normalized) else None
    times = []
    for item in output.get("times") or []:
        if not _in_text(item.get("text"), normalized):
            continue
        relative = not re.search(r"\d", item["text"])  # «last October»: dates from TODAY are allowed
        if not relative and not (_years_written(item.get("start"), normalized)
                                 and _years_written(item.get("end"), normalized)):
            item = {**item, "start": None, "end": None}
        times.append(item)
    digits = _lower_keep_digits(text)
    magnitudes = [m for m in output.get("magnitudes") or []
                  if _in_text(m.get("text"), normalized) and re.search(r"\d", m.get("text") or "")
                  and isinstance(m.get("value"), int | float) and m["value"] > 0
                  and any(_number(n, language) == m["value"] for n in re.findall(_NUMBER, digits))]
    return {"language": output.get("language") or language, "place": place, "times": times,
            "intents": list(dict.fromkeys(output.get("intents") or [])),
            "subjects": list(dict.fromkeys(output.get("subjects") or [])), "magnitudes": magnitudes, "by": "llm"}


def understand(text: str, language: str, complete=None, *, last_year: int | None = None) -> tuple[dict, list]:
    """(understanding, llm calls). Never raises: without HUP_UNDERSTAND=llm, without a model or on any failure,
    the floor of words() (with no call, or with the calls the failed attempt cost)."""
    last_year = last_year if last_year is not None else time.gmtime().tm_year - 1
    floor = words(text, language, last_year)
    if not (text or "").strip() or mode() != "llm":
        return floor, []
    if complete is None:
        from hup_api.llm.adapter import complete
    data = {"instructions": PROMPT, "context": _context(last_year), "request": request_block(text, language),
            "schema": UNDERSTANDING_SCHEMA, "name": "understanding", "max_tokens": 800, "timeout": 12.0}
    try:
        got = complete("understand", data)
    except LLMUnavailable as error:
        return floor, list(error.calls)
    except Exception:  # noqa: BLE001 - understanding is an aid; the planner still runs without it
        return floor, []
    calls = got.get("calls") or [got.get("call")]
    output = got.get("output")
    from jsonschema import Draft202012Validator

    if not isinstance(output, dict) or any(True for _ in Draft202012Validator(UNDERSTANDING_SCHEMA)
                                           .iter_errors(output)):
        return floor, [c for c in calls if c]
    understood = checked(output, text, language)
    # The floor's sizes and times are read from the text by rule: keep any the model missed.
    seen = {norm(m["text"]) for m in understood["magnitudes"]}
    understood["magnitudes"] += [m for m in floor["magnitudes"] if norm(m["text"]) not in seen]
    if not understood["times"]:
        understood["times"] = floor["times"]
    return understood, [c for c in calls if c]


# a size in another unit: an explicit, illustrative assumption ----------------------------------------------------

CONVERT_PROMPT = (Path(__file__).parent / "prompts" / "convert.md").read_text(encoding="utf-8")
CONVERT_VERSION = "convert-1-" + hashlib.sha256(CONVERT_PROMPT.encode("utf-8")).hexdigest()[:10]
_NUMBER_OR_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_TEXT_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
CONVERSION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["possible", "factor", "low", "high", "basis_en", "basis_es", "scale_en", "scale_es"],
    "properties": {"possible": {"type": "boolean"}, "factor": _NUMBER_OR_NULL, "low": _NUMBER_OR_NULL,
                   "high": _NUMBER_OR_NULL, "basis_en": {"type": "string"}, "basis_es": {"type": "string"},
                   "scale_en": _TEXT_OR_NULL, "scale_es": _TEXT_OR_NULL},
}


def convert(magnitude: dict, to_unit: str, what: str, question: str, complete=None) -> tuple[dict | None, list]:
    """(conversion, llm calls): the small model's factor from the stated unit to `to_unit`, with a range and a
    one-line basis, or None when it is unavailable, declines or answers something unusable. Never raises."""
    if complete is None:
        from hup_api.llm.adapter import complete
    clean = re.sub(r"(?i)</?\s*request\s*>", " ", question or "")
    data = {"instructions": CONVERT_PROMPT,
            "context": f"FROM: {magnitude.get('unit')} (as written: «{magnitude.get('text')}»)\nTO: {to_unit}\n"
                       f"WHAT IS ESTIMATED: {what}",
            "request": f"<request>\n{clean}\n</request>", "schema": CONVERSION_SCHEMA, "name": "conversion",
            "max_tokens": 400, "timeout": 12.0}
    try:
        got = complete("understand", data)
    except LLMUnavailable as error:
        return None, list(error.calls)
    except Exception:  # noqa: BLE001 - the estimate is an aid; without it the size is named, not converted
        return None, []
    calls = [c for c in (got.get("calls") or [got.get("call")]) if c]
    out = got.get("output")
    from jsonschema import Draft202012Validator

    if not isinstance(out, dict) or any(True for _ in Draft202012Validator(CONVERSION_SCHEMA).iter_errors(out)):
        return None, calls
    factor, low, high = out["factor"], out["low"], out["high"]
    if not out["possible"] or not all(isinstance(x, int | float) and x > 0 for x in (factor, low, high)) \
            or not low <= factor <= high or not out["basis_en"].strip() or not out["basis_es"].strip():
        return None, calls
    return out, calls


# typed decisions (Jev) ------------------------------------------------------------------------------------------

DESIGN_CRITERIA = {
    "state": "How a place is now, or was in a given period (a value, a map).",
    "trend": "Whether something has been rising or falling over the years.",
    "impact": "What changed before and after a known change, a works or an event, compared with surroundings.",
    "scan": "A general overview of a place, many variables at once.",
    "event": "How exceptional one episode was: a storm, a heatwave, a flood, a given month or season.",
    "scenario": "How something will be in the future (a future year or decade) under climate scenarios.",
    "exposure": "How many people or homes are inside an area at risk (flood zone, heat).",
    "whatif": "What something would need (water, energy, land) if it were built or people moved in.",
}
TIME_CRITERIA = {
    "none": "The question gives no time at all.",
    "point": "One past date, year, month or season.",
    "since": "Since a date or since an event until now.",
    "range": "From one past date to another.",
    "future": "A future year, decade or 'the end of the century'.",
    "recent": "Lately, this year, this summer, now.",
}
SUBJECT_LABELS = {
    "heat": "heat or temperature", "rain": "rain or precipitation", "flood": "floods or flood zones",
    "drought": "drought or dry soil", "vegetation": "vegetation, trees or greenness", "water": "water use or supply",
    "sea": "the sea or the coast", "population": "population or people", "building": "buildings, housing, built-up "
    "or sealed land", "groundwater": "groundwater or aquifers", "energy": "energy or electricity",
    "land": "land or soil use",
}


def typed_questions(catalog: dict) -> dict:
    """Every typed question of one Jev request: kind of answer, scope, place, time, subjects, variables."""
    questions = {
        "design": {"type": "choice", "instructions": "Which kind of answer does this question ask for?",
                   "criteria": DESIGN_CRITERIA},
        "in_scope": {"type": "noul", "instructions": "Could maps, satellites and climate or population data about a "
                     "place on Earth answer this question?", "criteria": None},
        "place": {"type": "noul", "instructions": "Does the question name a specific place (a town, district, "
                  "region, country or address)?", "criteria": None},
        "time": {"type": "choice", "instructions": "What time does the question refer to?", "criteria": TIME_CRITERIA},
    }
    for subject, label in SUBJECT_LABELS.items():
        questions[f"s_{subject}"] = {"type": "noul", "instructions": f"Is this question about {label}?",
                                     "criteria": None}
    for variable in catalog["variables"]:
        name = variable.get("name") or {}
        label = name.get("en") if isinstance(name, dict) else str(name)
        answers = "; ".join((variable.get("answers") or [])[:6])
        questions[f"v_{variable['id']}"] = {
            "type": "noul", "instructions": f"Does this question ask about {label} (also said: {answers})?",
            "criteria": None}
    return questions


def typed(text: str, catalog: dict, timeout: float | None = None, transport=None) -> dict:
    """Jev's typed reading of the question: {"design": {"choice", "confidence", "probabilities"}, "in_scope": p,
    "place": p, "time": {...}, "subjects": {s: p}, "variables": {id: p}, "seconds", "usage", "model"}.
    Raises jev.JevUnavailable on any failure; the caller then keeps the words."""
    from hup_api.llm import jev

    started = time.monotonic()
    got = jev.ask(text, typed_questions(catalog), timeout=timeout or jev.TIMEOUT, transport=transport)
    answers = got["answers"]

    def choice(key, options):
        answer = answers.get(key) or {}
        picked = answer.get("choice")
        if picked not in options:
            raise jev.JevUnavailable(f"malformed {key}")
        confidence = answer.get("confidence")
        return {"choice": picked, "confidence": float(confidence) if isinstance(confidence, int | float) else 0.0,
                "probabilities": jev.probabilities(answer)}

    def p(key):
        value = jev.noul(answers.get(key) or {})
        if value is None:
            raise jev.JevUnavailable(f"malformed {key}")
        return value

    return {"design": choice("design", DESIGN_CRITERIA), "time": choice("time", TIME_CRITERIA),
            "in_scope": p("in_scope"), "place": p("place"),
            "subjects": {s: p(f"s_{s}") for s in SUBJECT_LABELS},
            "variables": {v["id"]: p(f"v_{v['id']}") for v in catalog["variables"]},
            "seconds": round(time.monotonic() - started, 3), "usage": got.get("usage") or {},
            "model": got.get("model")}


SUBJECT_MIN = 0.5  # a subject Jev finds at least this likely joins the understanding (they only add)


def with_typed(understanding: dict, reading: dict | None) -> dict:
    """The understanding with Jev's subjects added and its design put first. The words' times, sizes and place
    stay: Jev answers questions, it does not read dates or numbers."""
    if not reading:
        return understanding
    out = dict(understanding)
    out["subjects"] = list(dict.fromkeys([*(understanding.get("subjects") or []),
                                          *[s for s, v in reading["subjects"].items() if v >= SUBJECT_MIN]]))
    out["intents"] = list(dict.fromkeys([reading["design"]["choice"], *(understanding.get("intents") or [])]))
    out["typed"] = {k: reading[k] for k in ("design", "time", "in_scope", "place")}
    return out


# the place, by words: only for the fast path; the planner LLM reads it otherwise --------------------------------

_LOCATIVE = r"en|in|a|at|al|near|cerca de|around|(?:por|per)(?!\s+(?:(?:la|el)\s+)?(?i:dana|navidad|nadal)\b)"
_AREA_NAME = r"gulf|golfo|golf|sea|mar|bay|bah[ií]a|badia|capital|delta|coast|costa"
_GENERIC_AREA = re.compile(rf"(?:(?:the|el|la)\s+)?(?:{_AREA_NAME})", re.I)
_AREA_MENTION = re.compile(rf"\b(?:{_LOCATIVE})\s+(?:(?:the|el|la)\s+)?(?:{_AREA_NAME})\b", re.I)
_PLACE = re.compile(
    rf"\b(?P<prep>{_LOCATIVE}|de|del|of)\s+(?:la\s+costa\s+de\s+|the\s+|el\s+|la\s+)?"
    rf"(?P<name>(?:(?i:{_AREA_NAME})\s+)?[A-ZÁÉÍÓÚÀÈÒÏÜÑÇ][\w'’·-]*"
    r"(?:[ ,]+(?:(?:de|del|la|el|les|d'|l'|i|y)\s+)?[A-ZÁÉÍÓÚÀÈÒÏÜÑÇ][\w'’·-]*)*)")


def place_words(text: str) -> str | None:
    """Prefer a named location to a name attached to an event or a person."""
    text = text or ""
    found = [(m["prep"], m["name"].strip(" ,")) for m in _PLACE.finditer(text)]
    found = [(prep, name) for prep, name in found
             if norm(name).split()[0] not in MONTHS and not _GENERIC_AREA.fullmatch(name)]
    locative = [name for prep, name in found if prep not in {"de", "del", "of"}]
    if locative:
        return locative[-1]
    if _AREA_MENTION.search(text):
        return None  # a generic location needs the rest of the question, not a name after "of"
    return found[-1][1] if found else None


def summary(understanding: dict) -> str:
    """One line for logs and tests; never the question text itself."""
    keys = ("intents", "subjects")
    return json.dumps({k: understanding.get(k) for k in keys} | {
        "times": [t.get("kind") for t in understanding.get("times") or []],
        "magnitudes": [m.get("unit") for m in understanding.get("magnitudes") or []],
        "by": understanding.get("by")})
