"""Send the planner only the catalog it needs for this question (cost per question).

    select(catalog, text, understanding) -> {"variables": [ids], "by": str}
    prompt_catalog(catalog, text, understanding) -> (catalog for the prompt, selection)

What the planner reads to choose design, place, variables and period:
- the selected variables, with the fields that help to choose (name, unit, frame, designs, answers, what it is
  not, support, sources, baseline) and without the ones that do not (the limit text, which the code adds itself,
  thesaurus codes, links to references);
- the sources of those variables, with how they are reached and since when (no licence, citation or URL);
- the archetypes, all of them and short (what is built and in which unit it is counted), so a what-if always
  works;
- every other variable as a compact entry (id, both names, `answers`, frame, designs, since): nothing is ever
  removed from what the model can choose, only the detail of what the question does not name (independent audit,
  .dev/notes/2026-11-14-auditoria-plan-frente-a-construido.md: robustness before savings).
Legislation, norms, blocks, chains and the operations are left out: the planner never chooses them.

Selection, in this order (HUP_PREFILTER):
- "jev" (the default when TYPESAFE_API_KEY is set): Jev's typed reading (understand.typed, one request
  per question, asked by the service) answers one yes/no question per variable; a variable it finds at least
  JEV_THRESHOLD likely is added. Jev only adds: the words are the floor.
- "lexical" (the default without the key): the words of the question and of the understanding against each
  variable's own catalog `answers`, names and id, in both languages, plus the variables of every subject the
  question names.
- "off": no prefilter; the service sends the catalog as before.
Never empty: with nothing found, the variables of the designs the question asks for; with none, all of them.
"""
import os

from hup_api.assistant.understand import SUBJECTS, _has, norm
from hup_data import settings

VERSION = "prefilter-1"
STOP = {"de", "del", "la", "el", "los", "las", "en", "a", "al", "y", "o", "un", "una", "por", "con", "the", "of",
        "in", "an", "and", "or", "to", "per", "at", "on", "is", "es", "i", "que"}
VARIABLE_FIELDS = ("id", "name", "unit", "data_class", "frame", "designs", "answers", "sources", "support",
                   "baseline")
COMPACT_FIELDS = ("id", "name", "answers", "frame", "designs")
SOURCE_FIELDS =("id", "name", "since", "resolution")
ARCHETYPE_FIELDS = ("id", "name", "resource", "unit", "per")
JEV_THRESHOLD = 0.5


def mode() -> str:
    """An explicit HUP_PREFILTER wins; unset, "jev" when TYPESAFE_API_KEY is set and "lexical" otherwise."""
    current = settings.load()  # a local .env first, as every other setting
    value = os.environ.get("HUP_PREFILTER", "").strip().lower()
    if value in ("jev", "off", "lexical"):
        return value
    return "jev" if current.secret("TYPESAFE_API_KEY") else "lexical"


def _stem(token: str) -> str:
    return token[:-2] if len(token) >= 6 else token


def _phrases(variable: dict) -> list[str]:
    name = variable.get("name") or {}
    names = [name.get("en"), name.get("es")] if isinstance(name, dict) else [name]
    return [norm(p) for p in [*(variable.get("answers") or []), *names, variable["id"].replace("_", " ")] if p]


def _phrase_in(phrase: str, question: str) -> bool:
    tokens = [t for t in phrase.split() if t not in STOP and len(t) >= 3]
    return bool(tokens) and all(_has(question, _stem(t)) for t in tokens)


def subject_variables(catalog: dict, subjects: list[str]) -> list[str]:
    """The variables whose own catalog words name one of these subjects."""
    out = []
    for variable in catalog["variables"]:
        text = " ".join(_phrases(variable))
        if any(_has(text, word) for s in subjects for word in SUBJECTS.get(s, [])):
            out.append(variable["id"])
    return out


def _designed(catalog: dict, intents) -> list[str]:
    intents = set(intents or [])
    intents |= {"state"} if "whatif" in intents else set()
    return [v["id"] for v in catalog["variables"] if intents & set(v.get("designs") or [])]


def lexical(catalog: dict, text: str, understanding: dict | None) -> list[str]:
    """Variables named by their own catalog words, then those of the subjects; when no variable is named by its
    own words (a vague «what has changed?»), also every variable of the kinds of answer asked for."""
    question = norm(text)
    named = [v["id"] for v in catalog["variables"] if any(_phrase_in(p, question) for p in _phrases(v))]
    found = named + subject_variables(catalog, list((understanding or {}).get("subjects") or []))
    if not named:
        found += _designed(catalog, (understanding or {}).get("intents"))
    return list(dict.fromkeys(found))


def select(catalog: dict, text: str, understanding: dict | None = None, how: str | None = None,
           reading: dict | None = None) -> dict:
    """{"variables": [ids in catalog order], "by": "lexical" | "jev+lexical" | "designs" | "all"}.

    In "jev" mode, `reading` is Jev's typed reading (understand.typed); without one it is asked here. Jev only adds
    the variables it finds at least JEV_THRESHOLD likely; any failure keeps the words."""
    how = how or mode()
    chosen = lexical(catalog, text, understanding)
    by = "lexical"
    if how == "jev":
        from hup_api.llm import jev

        if reading is None and jev.configured():
            from hup_api.assistant.understand import typed

            try:
                reading = typed(text, catalog)
            except jev.JevUnavailable:
                reading = None
        if reading:
            added = [v for v, p in reading["variables"].items() if p >= JEV_THRESHOLD]
            chosen += [v for v in added if v not in chosen]
            by = "jev+lexical"
    if not chosen:
        chosen = _designed(catalog, (understanding or {}).get("intents"))
        by = "designs"
    if not chosen:
        chosen = [v["id"] for v in catalog["variables"]]
        by = "all"
    order = [v["id"] for v in catalog["variables"] if v["id"] in set(chosen)]
    return {"variables": order, "by": by}


def _pick(item: dict, fields) -> dict:
    return {k: item[k] for k in fields if k in item}


def _variable(variable: dict) -> dict:
    out = _pick(variable, VARIABLE_FIELDS)
    if isinstance(variable.get("not"), dict) and variable["not"].get("en"):
        out["not"] = variable["not"]["en"]
    extent = variable.get("extent")
    if isinstance(extent, dict) and extent.get("kind"):
        out["extent"] = {k: extent[k] for k in ("kind", "bbox") if k in extent}
    return out


def _source(source: dict) -> dict:
    out = _pick(source, SOURCE_FIELDS)
    access = source.get("access")
    if isinstance(access, dict):
        out["access"] = {k: access[k] for k in ("mode", "coverage") if k in access}
    return out


def _compact(variable: dict) -> dict:
    out = _pick(variable, COMPACT_FIELDS)
    since = (variable.get("support") or {}).get("since")
    if since is not None:
        out["since"] = since
    return out


def reduce(catalog: dict, variable_ids: list[str]) -> dict:
    """The catalog the planner reads (see the module docstring): every variable, compact; the shortlist in full."""
    keep = set(variable_ids)
    variables = [v for v in catalog["variables"] if v["id"] in keep]
    sources = {s for v in variables for s in v.get("sources") or []}
    return {
        "version": catalog.get("version"),
        "variables": [_variable(v) for v in variables],
        "other_variables": [_compact(v) for v in catalog["variables"] if v["id"] not in keep],
        "sources": [_source(s) for s in catalog.get("sources") or [] if s.get("id") in sources],
        "archetypes": [_pick(a, ARCHETYPE_FIELDS) for a in catalog.get("archetypes") or []],
    }


def prompt_catalog(catalog: dict, text: str, understanding: dict | None = None,
                   how: str | None = None, reading: dict | None = None) -> tuple[dict | None, dict]:
    """(catalog for the prompt, selection); (None, {"by": "off"}) when the prefilter is off."""
    how = how or mode()
    if how == "off":
        return None, {"variables": [v["id"] for v in catalog["variables"]], "by": "off"}
    selection = select(catalog, text, understanding, how, reading)
    return reduce(catalog, selection["variables"]), selection


def weights(catalog: dict) -> dict[str, int]:
    """Characters of each top-level part of the catalog, as JSON: what each part would cost in a prompt."""
    import json

    return {k: len(json.dumps(v, ensure_ascii=False, sort_keys=True)) for k, v in catalog.items()}
