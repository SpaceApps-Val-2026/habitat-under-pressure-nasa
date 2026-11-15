"""Related EU legislation (B9): acts in force on the subject of a variable, from Cellar, by EuroVoc concept.

    related(variable, language, cards) -> {"items": [{"celex", "title", "date", "url", "visibility",
                                                      "explained"?, "under"?}],
                                           "language", "retrieved", "note", "disclaimer"?, "general_options"?,
                                           "absence"?}

One SPARQL query to the public Cellar endpoint of the EU Publications Office, filtered to directives and
regulations in force adopted by the European Parliament or the Council, with the title in the language asked for
(English when Cellar has no such language). The answer is cached in the store with its date. A short timeout: if
Cellar does not answer, the list is an absence `source_unreachable`, never an empty list that looks like "no
legislation". A variable without EuroVoc concepts is `not_computed`.

`cards` is the `legislation` section of the catalog (catalog/legislation.yaml, written by the legal front). Each
act is joined with its plain-language card by `celex` or `also_for` and gets a `visibility`: `card`, `title` or
`hidden`. An act without a reviewed card is title-only: nothing is written about it that the card does not say.
The disclaimer and the general routes are returned even when Cellar does not answer. It lists legislation
related to a subject; it never says whether anything meets it.
"""
import json
import logging
import re
from datetime import UTC, datetime, timedelta

import requests

from hup_data import fingerprint, paths, settings, store

log = logging.getLogger("hup_api.legislation")

ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
TIMEOUT = 5.0
MAX_AGE = timedelta(days=30)
LIMIT = 50
USER_AGENT = "habitat-under-pressure/0.1 (open-data analysis tool)"
TYPES = ("DIR", "REG")  # directives and regulations; implementing and delegated acts are left out
AUTHORS = ("EP", "CONSIL")  # adopted by the European Parliament or the Council
# ISO 639-1 → the EU language authority code Cellar uses. The 24 official languages of the EU.
LANGUAGES = {"bg": "BUL", "cs": "CES", "da": "DAN", "de": "DEU", "el": "ELL", "en": "ENG", "es": "SPA", "et": "EST",
             "fi": "FIN", "fr": "FRA", "ga": "GLE", "hr": "HRV", "hu": "HUN", "it": "ITA", "lt": "LIT", "lv": "LAV",
             "mt": "MLT", "nl": "NLD", "pl": "POL", "pt": "POR", "ro": "RON", "sk": "SLK", "sl": "SLV", "sv": "SWE"}
_CONCEPT = re.compile(r"^(c_)?[0-9a-f]+$")
NOTE = {"en": "EU legislation in force on the subject of this variable, listed for reference with its official title.",
        "es": "Legislación de la UE en vigor sobre la materia de esta variable, listada como referencia con su "
              "título oficial."}


class Unreachable(Exception):
    """Cellar could not be asked, or its answer could not be read."""


def query_text(concepts: list[str], language: str) -> str:
    """The SPARQL query. Concepts are checked against the catalog pattern before they get here."""
    values = " ".join(f"<http://eurovoc.europa.eu/{c}>" for c in concepts)
    types = " ".join(f"<http://publications.europa.eu/resource/authority/resource-type/{t}>" for t in TYPES)
    authors = " ".join(f"<http://publications.europa.eu/resource/authority/corporate-body/{a}>" for a in AUTHORS)
    return f"""PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT DISTINCT ?celex ?title ?date WHERE {{
  VALUES ?concept {{ {values} }}
  VALUES ?type {{ {types} }}
  VALUES ?author {{ {authors} }}
  ?work cdm:work_is_about_concept_eurovoc ?concept ;
        cdm:resource_legal_id_celex ?celex ;
        cdm:work_has_resource-type ?type ;
        cdm:work_created_by_agent ?author ;
        cdm:resource_legal_in-force "true"^^xsd:boolean ;
        cdm:work_date_document ?date .
  ?expr cdm:expression_belongs_to_work ?work ;
        cdm:expression_title ?title ;
        cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/{LANGUAGES[language]}> .
}}
ORDER BY DESC(?date)
LIMIT {LIMIT}
"""


def _ask(query: str) -> dict:
    """One request to Cellar. The only function that touches the network."""
    response = requests.get(ENDPOINT, params={"query": query}, timeout=TIMEOUT,
                            headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def _items(answer, language: str) -> list[dict]:
    try:
        bindings = answer["results"]["bindings"]
        rows = [(b["celex"]["value"], b["title"]["value"], b.get("date", {}).get("value")) for b in bindings]
    except (KeyError, TypeError) as error:
        raise Unreachable("not a SPARQL JSON answer") from error
    items, seen = [], set()
    for celex, title, date in rows:
        if celex in seen:  # one act, one line, whatever the number of its expressions
            continue
        seen.add(celex)
        items.append({"celex": celex, "title": " ".join(title.split()), "date": date,
                      "url": f"https://eur-lex.europa.eu/legal-content/{language.upper()}/TXT/?uri=CELEX:{celex}"})
    return sorted(items, key=lambda item: (item["date"] or "", item["celex"]), reverse=True)


def _absence(reason: str, en: str, es: str) -> dict:
    return {"reason": reason, "detail": {"en": en, "es": es}}


def _cache_file(concepts: list[str], language: str):
    key = fingerprint.sha({"cellar": sorted(concepts), "language": language, "types": TYPES, "authors": AUTHORS,
                              "limit": LIMIT})
    return paths.cache() / "cellar" / "legislation" / f"{key}.json"


def _text(text: dict | None, language: str) -> str | None:
    """A card text in the language asked for; English when the card does not have it."""
    return None if text is None else text.get(language) or text["en"]


def _route(route: dict, labels: dict, language: str) -> dict:
    out = {"action": _text(route["action"], language), "how": _text(route["how"], language), "who": route["who"],
           "who_label": _text(labels.get(route["who"]), language), "legal_basis": route["legal_basis"],
           "url": route["url"]}
    if "id" in route:
        out = {"id": route["id"], **out}
    if "status" in route:
        out["status"] = _text(route["status"], language)
    return out


def _explained(card: dict, labels: dict, language: str) -> dict:
    return {"celex": card["celex"], "short_name": _text(card["short_name"], language),
            "plain": _text(card["plain"], language), "in_spain": _text(card["in_spain"], language),
            "citizen_options": [_route(r, labels, language) for r in card["citizen_options"]],
            "general": card["general"], "reviewed_by": card["reviewed_by"]}


def _join(items: list[dict], cards: dict | None, language: str) -> tuple[list[dict], list[dict]]:
    """Each act with its visibility and, when it has a reviewed card, the card. Returns (items, cards shown)."""
    if not cards:
        return [{**item, "visibility": "title"} for item in items], []
    labels = cards.get("who_labels", {})
    reviewed = {card["celex"]: card for card in cards.get("acts", []) if card.get("reviewed_by")}
    alias = {}
    for card in reviewed.values():
        for celex in card.get("also_for", []):
            alias.setdefault(celex, card)
    visibility = cards.get("visibility", {})
    titles = {t["celex"]: t.get("under", []) for t in visibility.get("title", [])}
    hidden = set(visibility.get("hidden", []))
    listed = {item["celex"] for item in items}
    joined, shown = [], []
    for item in items:
        celex = item["celex"]
        card = reviewed.get(celex) or alias.get(celex)
        if card and (card in shown or (celex not in reviewed and card["celex"] in listed)):
            # the card goes once, on its own act when that act is listed too
            joined.append({**item, "visibility": "hidden", "under": [card["celex"]]})
        elif card:
            shown.append(card)
            joined.append({**item, "visibility": "card", "explained": _explained(card, labels, language)})
        elif celex in hidden:
            joined.append({**item, "visibility": "hidden"})
        else:
            under = titles.get(celex)
            joined.append({**item, "visibility": "title", **({"under": under} if under else {})})
    return joined, shown


def _extras(cards: dict | None, shown: list[dict], language: str, everything: bool) -> dict:
    """Disclaimer and general routes: those linked by the cards shown, or all of them when there is no list."""
    if not cards:
        return {}
    wanted = {x for card in shown for x in card["general"]}
    labels = cards.get("who_labels", {})
    routes = [_route(r, labels, language) for r in cards.get("general_options", []) if everything or r["id"] in wanted]
    return {"disclaimer": _text(cards["disclaimer"], language), "general_options": routes}


def related(variable: dict, language: str = "en", cards: dict | None = None) -> dict:
    """Related legislation for a catalog variable. Never raises for a Cellar that does not answer."""
    language = language if language in LANGUAGES else "en"
    base = {"items": [], "language": language, "retrieved": None, "note": NOTE}

    def answer(items: list[dict], retrieved: str | None) -> dict:
        joined, shown = _join(items, cards, language)
        return {**base, "items": joined, "retrieved": retrieved, **_extras(cards, shown, language, False)}

    def absent(absence: dict, concepts: tuple[str, ...] = ()) -> dict:
        """No live list. The cards we hold for these concepts still come, each as its own act, with the absence
        saying that the list of related acts could not be checked."""
        held = [card for card in (cards or {}).get("acts", [])
                if card.get("reviewed_by") and set(card.get("eurovoc") or []) & set(concepts)]
        labels = (cards or {}).get("who_labels", {})
        items = [{"celex": card["celex"], "title": _text(card["short_name"], language), "date": None,
                  "url": f"https://eur-lex.europa.eu/legal-content/{language.upper()}/TXT/?uri=CELEX:{card['celex']}",
                  "visibility": "card", "explained": _explained(card, labels, language)} for card in held]
        return {**base, "items": items, **_extras(cards, [], language, True), "absence": absence}

    concepts = [c for c in variable.get("eurovoc") or [] if isinstance(c, str) and _CONCEPT.match(c)]
    if not concepts:
        return absent(_absence("not_computed", "This variable has no EuroVoc subject in the catalog.",
                               "Esta variable no tiene materia EuroVoc en el catálogo."))
    file = _cache_file(concepts, language)
    cached = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
    fresh = cached and datetime.now(UTC) - datetime.fromisoformat(cached["retrieved"]) < MAX_AGE
    if fresh:
        return answer(cached["items"], cached["retrieved"])
    unreachable = _absence("source_unreachable", "The EU legislation service could not be reached, so the list of "
                           "related acts is missing; the general routes apply to any law.",
                           "No se ha podido consultar el servicio de legislación de la UE, así que falta la lista de "
                           "normas relacionadas; las vías generales valen para cualquier norma.")
    if settings.load().offline:
        return answer(cached["items"], cached["retrieved"]) if cached else absent(unreachable, tuple(concepts))
    try:
        items = _items(_ask(query_text(concepts, language)), language)
    except (requests.RequestException, ValueError, Unreachable):
        log.warning("legislation: Cellar did not answer for %s", variable.get("id"))
        if cached:  # an old answer with its date is better than none
            return answer(cached["items"], cached["retrieved"])
        return absent(unreachable, tuple(concepts))
    retrieved = datetime.now(UTC).isoformat(timespec="seconds")
    store.write_atomic(file, json.dumps({"retrieved": retrieved, "items": items}, ensure_ascii=False).encode("utf-8"))
    return answer(items, retrieved)
