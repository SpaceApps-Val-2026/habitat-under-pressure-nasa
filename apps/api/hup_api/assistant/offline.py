"""The floor of the answer ladder: a plan read from the question, with no language model.

The person says what they want to know. The design, the variables, the period and the windows are
decided here from the catalogue (AGENTS.md, 6); nothing is asked except the place, and only when the
words do not name one. Never a form: this is what the assistant does when no model answers
(AGENTS.md, 7). A word that the catalogue does not know never becomes a made-up number.
"""

import re
import time
import unicodedata

from hup_data import places

from . import grounding, question_article, service, understand


def _plain(value: str) -> str:
    """Lower case, without accents: «precipitación» and «precipitacion» are the same word."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


# What the words ask to see. The design is a choice from this catalogue, never an invented one.
DESIGN_WORDS = {
    "event": ("episodio", "dana", "gota fria", "tormenta", "inund", "riada", "extrem", "event", "storm", "flood"),
    "exposure": ("exposic", "person", "poblac", "habit", "exposure", "people", "population"),
    "impact": ("antes", "despues", "impacto", "impact", "obra", "gigafactoria", "proyecto", "construc", "cambi",
               "actividad", "before", "after", "change"),
    "scenario": ("escenario", "proyecc", "futuro", "ssp", "scenario", "projection", "future", "2050", "2100"),
    "scan": ("escaneo", "barrido", "que cambia", "todo", "scan", "overview", "all variables"),
    "state": ("estado", "actual", "ahora", "hoy", "state", "current", "now"),
    "trend": ("tendencia", "evolu", "aument", "subid", "bajad", "calent", "trend", "evolution", "increase",
              "decrease", "warming"),
}
# When two designs match the same words, the more specific one reads the question.
PRIORITY = ("exposure", "impact", "event", "scenario", "scan", "state", "trend")

# One variable per design and subject; all of them are ids the catalogue declares with that design.
SUBJECTS = {
    "temperature": (
        ("temperatura", "temperature", "calor", "calentamiento", "heat", "hotter", "warm", "grados", "degrees"),
        {"trend": "air_temperature", "state": "air_temperature", "scan": "air_temperature",
         "impact": "lst", "event": "lst", "scenario": "tas_mean"},
    ),
    "rain": (
        ("lluvia", "llueve", "precipitacion", "precipitation", "rain", "pluja", "dana", "storm", "tormenta",
         "seco", "sequia", "drought"),
        {"trend": "precipitation", "state": "precipitation", "scan": "precipitation",
         "event": "precipitation", "scenario": "pr_total"},
    ),
    "green": (
        ("verde", "ndvi", "vegetacion", "green", "vegetation"),
        {"trend": "ndvi", "state": "ndvi", "scan": "ndvi", "impact": "ndvi"},
    ),
    "hot_cells": (
        ("celdas calientes", "hot cells"),
        {"state": "population_in_hot_cells"},
    ),
    "people": (
        ("personas", "poblacion", "habitantes", "people", "population"),
        {"exposure": "population_in_flood_area"},
    ),
}

# The variable each design falls back to when the words name no subject.
FIRST_VARIABLE = {
    "trend": "air_temperature", "state": "air_temperature", "scan": "air_temperature",
    "impact": "lst", "event": "precipitation", "scenario": "tas_mean", "exposure": "population_in_flood_area",
}

PREPOSITIONS = ("en", "de", "del", "para", "por", "sobre", "cerca de", "around", "near", "in", "of", "at", "for")
YEARS = re.compile(r"\b(19\d{2}|20\d{2}|2100)\b")
UPPER = r"[A-ZÁÉÍÓÚÜÑ]"
WORD = r"[\w'áéíóúüñç-]+"
PROPER = re.compile(UPPER + WORD + r"(?:\s+(?:de|del|la|el|los|las)?\s*" + UPPER + WORD + r")*")
AFTER_PREPOSITION = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in PREPOSITIONS) + r")\s+(" + WORD
    + r"(?:\s+(?:de|del|la|el)?\s*" + WORD + r"){0,2})"
)
# Words that frame a place but are not part of its name.
STOPWORDS = {
    "desde", "hasta", "en", "de", "del", "la", "el", "los", "las", "y", "que", "como", "para", "por", "con",
    "around", "since", "from", "to", "in", "the", "of", "and", "how", "hot", "will", "be",
    "cuanto", "cuanta", "cuantos", "cuantas", "quiero", "saber", "pasa", "pasado", "hola",
}


def _trim(candidate: str) -> str:
    words = [word for word in candidate.split() if not re.fullmatch(r"\d{4}", word)]
    while words and _plain(words[-1]) in STOPWORDS:
        words.pop()
    while words and _plain(words[0]) in STOPWORDS:
        words.pop(0)
    return " ".join(words)


def _years(text: str) -> list[int]:
    return sorted({int(year) for year in YEARS.findall(text)})


def _period(design: str, years: list[int], last_year: int) -> dict:
    start, end = (years[0], years[-1]) if len(years) >= 2 else (None, None)
    if design == "scenario":
        return {"start": f"{start or 1995}-01-01", "end": f"{end or 2054}-12-31"}
    if years and len(years) == 1:
        year = years[0]
        return {"start": f"{year}-01-01", "end": f"{year}-12-31"}
    if not years and design in ("event", "exposure"):
        return {"start": f"{last_year}-01-01", "end": f"{last_year}-12-31"}
    if start is None:
        start = 1981
        end = last_year
    if design in ("trend", "scan", "impact"):
        end = max(start, min(end, last_year))
    return {"start": f"{start}-01-01", "end": f"{end}-12-31"}


def _windows(design: str, years: list[int], last_year: int) -> tuple[list[int] | None, list[int] | None]:
    """The change happened at the last year named, or at the middle of the period; the plan shows it."""
    if design != "impact":
        return None, None
    change = years[-1] if years else None
    if change is None:
        end = last_year
        return [end - 12, end - 4], [end - 3, end]
    before = max(1981, change - 8)
    return [before, change - 1], [change, min(change + 2, last_year)]


def _design(words: str) -> str:
    scores = {design: sum(1 for word in vocabulary if word in words) for design, vocabulary in DESIGN_WORDS.items()}
    ranked = [design for design in PRIORITY if scores[design] > 0]
    if not ranked:
        return "trend"
    return max(ranked, key=lambda design: (scores[design], -PRIORITY.index(design)))


def _variable(words: str, design: str, catalog: dict) -> tuple[str | None, str]:
    """The variable and the design that reads it: a subject with one design only («hot cells») brings its own."""
    declared = {variable["id"]: variable for variable in catalog["variables"]}
    allowed = {vid for vid, variable in declared.items() if design in (variable.get("designs") or [])}
    for _subject, (vocabulary, per_design) in SUBJECTS.items():
        if any(word in words for word in vocabulary):
            candidate = per_design.get(design) or next(iter(per_design.values()))
            if candidate in allowed:
                return candidate, design
            # The words are the catalogue's own name for a variable of one design: that design reads them.
            found = declared.get(candidate, {})
            own = next(iter(per_design))
            answers = {_plain(answer) for answer in found.get("answers") or []}
            if len(per_design) == 1 and own in (found.get("designs") or []) and answers & {
                    word for word in vocabulary if word in words}:
                return candidate, own
    declared_matches = grounding.matching_variables(words, catalog, allowed)
    if declared_matches:
        return declared_matches[0], design
    if not re.search(r"\b(anali[sz]\w*|overview|scan|barrido|gigafactor\w*|gigafactory|mi pueblo|my town)\b", words):
        return None, design
    fallback = FIRST_VARIABLE.get(design)
    return (fallback if fallback in allowed else (sorted(allowed)[0] if allowed else None)), design


def _place_candidates(text: str) -> list[str]:
    """Proper nouns, the words after a place preposition, and their last word; longest first."""
    plain = _plain(text)
    pieces = PROPER.findall(text) + [found.group(1) for found in AFTER_PREPOSITION.finditer(plain)]
    candidates: list[str] = []
    for piece in pieces:
        trimmed = _trim(piece)
        if not trimmed:
            continue
        candidates.append(trimmed)
        # «la gigafactoría de Sagunt»: the name is the tail, not the whole phrase.
        if " de " in _plain(trimmed):
            candidates.append(trimmed.split(" de ")[-1])
        last = trimmed.split()[-1]
        if last != trimmed:
            candidates.append(last)
    seen, ordered = set(), []
    # A word that starts with a capital is the better name; phrases read from lower case come later.
    for candidate in sorted(candidates, key=lambda item: (0 if item[:1].isupper() else 1, -len(item))):
        key = _plain(candidate).strip()
        if len(key) < 3 or key in seen or key in PREPOSITIONS or key in STOPWORDS:
            continue
        seen.add(key)
        ordered.append(candidate.strip())
    return ordered


def from_text(text: str, language: str, catalog: dict, *, resolve=places.resolve, last_year: int | None = None,
              zone: dict | None = None) -> dict:
    """A plan or a question, from the words alone. Same shapes as the assistant's answer.

    `zone` is the place the person chose from the candidates: the words are read again, not asked again.
    """
    started = time.monotonic()
    if not text or not text.strip():
        return service.clarification("request")
    words = _plain(text)
    year_now = time.gmtime().tm_year - 1
    last = last_year if last_year is not None else year_now
    # What something uses or would use (A9): the whatif recipe with the archetypes the words name, from the catalog.
    # A «what if» that names no archetype keeps the reading below; a consumption question that names none still
    # answers with the state of the place and says no archetype covers it.
    understood = understand.words(text, language, last)
    consumption = understand.asks_consumption(text, understood)
    matched = understand.archetype_matches(text, catalog) if consumption or "whatif" in understood["intents"] else []
    by_archetype = bool(matched) or consumption or bool(service.asked_resources(text, understood, catalog))
    request = {"text": text, "language": language, "origin": "assistant"}
    design = _design(words)
    variable, design = _variable(words, design, catalog)
    since_year = re.search(r"\b(?:desde|des de|since|from|a partir de) \d{4}\b", words)
    if variable is None and design == "impact" and since_year:
        # A bare year is a time range for an explicitly named series, with no intervention to compare.
        trend_ids = {entry["id"] for entry in catalog["variables"] if "trend" in entry.get("designs", [])}
        matches = grounding.matching_variables(words, catalog, trend_ids)
        design, variable = "trend", matches[0] if matches else None
    if variable is None and design == "trend":
        state_ids = {entry["id"] for entry in catalog["variables"] if "state" in entry.get("designs", [])}
        matches = grounding.matching_variables(words, catalog, state_ids)
        if matches:
            design, variable = "state", matches[0]
    if variable is None and not by_archetype:
        name = zone["name"] if zone else understand.place_words(text)
        return question_article.out_of_scope(request, zone=name)
    if zone is None and variable == "terrestrial_water_storage_anomaly" and re.search(
        r"\b(?:a escala (?:mundial|global)|worldwide|across the world|globalment|globally)\b", words
    ):
        zone = {"id": "world", "name": "World", "bbox": [-180, -90, 180, 90]}
    if zone is not None:
        candidates = []
    else:
        candidates = _place_candidates(text)
    chosen, asked = zone, None
    for candidate in candidates:
        try:
            found = resolve(candidate, language)
        except Exception:  # noqa: BLE001 - a gazetteer hiccup is a candidate that did not answer
            continue
        if found.get("status") == "found":
            chosen = found["zone"]
            break
        if found.get("status") == "ambiguous" and asked is None:
            asked = found.get("candidates")
    if chosen is None:
        reply = service.clarification("place", asked) if asked else service.clarification("place_not_found")
        return question_article.clarification(request, reply)
    if by_archetype:
        return service.archetype_answer(text, understood, matched, chosen, request, catalog, started)
    years = _years(text)
    open_range = re.search(r"\b(desde|des de|since|from|a partir de)\b", words)
    if len(years) == 1 and design in ("trend", "scan", "state") and open_range:
        # «desde 1981» is a range to the last complete year, not a single year.
        years = sorted({years[0], last})
    before, after = _windows(design, years, last)
    period = _period(design, years, last)
    if variable == "nighttime_radiance":
        month = next((item["start"] for item in understood.get("times", [])
                      if re.fullmatch(r"\d{4}-\d{2}", item.get("start") or "")), None)
        if month:
            design, before, after = "state", None, None
            period = {"start": month, "end": month}
    return service.plan_answer(
        design=design,
        place=chosen,
        variables=[variable],
        period=period,
        before=before,
        after=after,
        request=request,
        catalog=catalog,
        started=started,
    )
