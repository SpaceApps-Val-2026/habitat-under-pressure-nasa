"""Jev in the question path, with a fake transport: on by default with a key, the fast path without the planning
model, and a silent fallback on any failure. No network."""
import copy
import logging

import pytest

from hup_api import catalog as catalog_module
from hup_api.assistant import prefilter, service, understand
from hup_api.llm import jev
from hup_api.llm.adapter import LLMUnavailable
from hup_data import places

CATALOG = catalog_module.load()
ZONE = {"id": "testville", "name": "Testville", "bbox": [10.0, 40.0, 10.1, 40.1]}
SECRET = "ts-test-not-a-real-key-0123456789"


def reading(design="trend", confidence=1.0, scope=0.95, place=0.99, likely=("air_temperature",)):
    def transport(url, headers, body, timeout):
        answers = {}
        for key, question in body["questions"].items():
            if question["type"] == "choice":
                options = list(question["criteria"])
                pick = design if key == "design" else "none"
                answers[key] = {"type": "choice", "choice": pick, "confidence": confidence if key == "design" else 1.0,
                                "probabilities": {o: (1.0 if o == pick else 0.0) for o in options}}
            elif key == "in_scope":
                answers[key] = {"type": "noul", "noul": scope}
            elif key == "place":
                answers[key] = {"type": "noul", "noul": place}
            else:
                answers[key] = {"type": "noul", "noul": 0.95 if key[2:] in likely or key[2:] == "heat" else 0.05}
        return 200, {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 2000, "output_tokens": 0}}
    return transport


class Recorded:
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, task, data):
        self.sent.append(task)
        if not self.answers:
            raise LLMUnavailable("no more recorded answers")
        call = {"task": task, "provider": "recorded", "model": "recorded", "input_tokens": 100, "output_tokens": 20,
                "cost_usd": None, "seconds": 0.0}
        return {"output": copy.deepcopy(self.answers.pop(0)), "call": call, "calls": [call]}


PLAN = {"kind": "plan", "missing": None, "design": "trend", "place": "Testville", "variables": ["air_temperature"],
        "period": None, "before": None, "after": None, "level": "complete", "archetypes": [], "quantity": None}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    for name in ("HUP_UNDERSTAND", "HUP_PREFILTER", "HUP_QUESTION_CACHE"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("TYPESAFE_API_KEY", SECRET)
    def resolve(text, language="en"):
        if text.lower() == "testville":
            return {"status": "found", "zone": copy.deepcopy(ZONE)}
        if text.lower() == "twinton":
            return {"status": "ambiguous", "candidates": [{**ZONE, "id": "a", "name": "Twinton A"},
                                                          {**ZONE, "id": "b", "name": "Twinton B"}]}
        return {"status": "not_found"}
    monkeypatch.setattr(places, "resolve", resolve)


def use(monkeypatch, *answers):
    recorded = Recorded(*answers)
    monkeypatch.setattr(service, "llm_complete", recorded)
    return recorded


def test_jev_is_on_by_default_only_with_its_key(monkeypatch):
    assert prefilter.mode() == "jev"
    monkeypatch.setenv("HUP_PREFILTER", "lexical")
    assert prefilter.mode() == "lexical"
    monkeypatch.setenv("HUP_PREFILTER", "")
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    assert prefilter.mode() == "lexical"


def test_confident_decisions_plan_without_the_planning_model(monkeypatch, caplog):
    monkeypatch.setattr(jev, "_transport", reading())
    recorded = use(monkeypatch)
    with caplog.at_level(logging.INFO, logger="hup_api.assistant"):
        out = service.from_text("¿Está subiendo la temperatura en Testville?", "es", None, CATALOG)
    assert recorded.sent == [] and out["kind"] == "plan"
    plan = out["plan"]
    assert plan["design"] == "trend" and plan["variables"] == ["air_temperature"] and plan["zone"] == ZONE
    assert any("sin el modelo de planificación" in d["text"]["es"] for d in plan["answer"]["decided"])
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Jev typed")]
    assert len(lines) == 1 and "fallback=no" in lines[0] and "Testville" not in lines[0] and SECRET not in lines[0]


def test_a_future_year_on_the_fast_path_is_a_scenario_with_its_horizons(monkeypatch):
    monkeypatch.setattr(jev, "_transport", reading(design="scenario", likely=("air_temperature",)))
    use(monkeypatch)
    plan = service.from_text("¿Cuánto calor hará en Testville en 2050?", "es", None, CATALOG)["plan"]
    assert plan["design"] == "scenario" and "tas_mean" in plan["variables"]
    assert plan["period"]["start"].startswith("1995") and plan["period"]["end"].startswith("2054")


@pytest.mark.parametrize("change", [{"confidence": 0.8}, {"scope": 0.5}, {"place": 0.2}, {"design": "impact"},
                                    {"design": "whatif"}, {"likely": ()}])
def test_anything_unsure_goes_to_the_planning_model(monkeypatch, change):
    monkeypatch.setattr(jev, "_transport", reading(**change))
    recorded = use(monkeypatch, PLAN)
    out = service.from_text("¿Está subiendo la temperatura en Testville?", "es", None, CATALOG)
    assert recorded.sent == ["plan"] and out["kind"] == "plan"


def test_inconsistent_time_or_unknown_place_goes_to_the_planning_model(monkeypatch):
    monkeypatch.setattr(jev, "_transport", reading(design="trend"))
    recorded = use(monkeypatch, PLAN, PLAN, PLAN)
    service.from_text("¿Subirá la temperatura en Testville en 2050?", "es", None, CATALOG)  # future, not scenario
    service.from_text("¿Qué ha cambiado en Testville desde la DANA de 2024?", "es", None, CATALOG)  # named moment
    service.from_text("¿Está subiendo la temperatura en Nowhereland?", "es", None, CATALOG)  # gazetteer says no
    assert recorded.sent == ["plan", "plan", "plan"]


@pytest.mark.parametrize("failure", ["429", "timeout", "malformed", "garbage"])
def test_a_jev_failure_falls_back_silently_to_the_words(monkeypatch, failure, caplog):
    import httpx

    def transport(url, headers, body, timeout):
        assert timeout == service.JEV_TIMEOUT
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out")
        if failure == "429":
            return 429, {"detail": "quota"}
        if failure == "garbage":
            return 200, None
        return 200, {"answers": {key: {"type": "noul"} for key in body["questions"]}}

    monkeypatch.setattr(jev, "_transport", transport)
    recorded = use(monkeypatch, PLAN)
    with caplog.at_level(logging.INFO, logger="hup_api.assistant"):
        out = service.from_text("¿Está subiendo la temperatura en Testville?", "es", None, CATALOG)
    assert out["kind"] == "plan" and recorded.sent == ["plan"]
    assert any("fallback=yes" in r.getMessage() for r in caplog.records)
    assert SECRET not in caplog.text


def test_without_a_key_nothing_calls_jev(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "")

    def never(*args):
        raise AssertionError("Jev must not be called without a key")
    monkeypatch.setattr(jev, "_transport", never)
    recorded = use(monkeypatch, PLAN)
    assert service.from_text("¿Está subiendo la temperatura en Testville?", "es", None, CATALOG)["kind"] == "plan"
    assert recorded.sent == ["plan"]


def test_a_place_with_several_matches_is_asked_without_the_planning_model(monkeypatch):
    monkeypatch.setattr(jev, "_transport", reading())
    recorded = use(monkeypatch)
    out = service.from_text("¿Está subiendo la temperatura en Twinton?", "es", None, CATALOG)
    assert recorded.sent == [] and out["kind"] == "clarification" and len(out["candidates"]) == 2
    use(monkeypatch)  # the person picks one: the same question, with the zone, plans on the fast path
    chosen = service.from_text("¿Está subiendo la temperatura en Twinton?", "es", None, CATALOG,
                               place=out["candidates"][0])
    assert chosen["kind"] == "plan" and chosen["plan"]["zone"]["id"] == "a"


def test_offline_nothing_calls_jev(monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")

    def never(*args):
        raise AssertionError("Jev must not be called offline")
    monkeypatch.setattr(jev, "_transport", never)
    recorded = use(monkeypatch, PLAN)
    assert service.from_text("¿Está subiendo la temperatura en Testville?", "es", None, CATALOG)["kind"] == "plan"
    assert recorded.sent == ["plan"]


def test_jev_subjects_and_design_join_the_understanding():
    words = understand.words("¿Qué pasará aquí?", "es")
    got = {"design": {"choice": "scenario", "confidence": 1.0, "probabilities": {}},
           "time": {"choice": "future"}, "in_scope": 0.9, "place": 0.1,
           "subjects": {"heat": 0.9, "rain": 0.1}, "variables": {}}
    merged = understand.with_typed(words, got)
    assert merged["intents"][0] == "scenario" and "heat" in merged["subjects"] and "rain" not in merged["subjects"]
    assert understand.with_typed(words, None) is words


def test_the_place_by_words():
    assert understand.place_words("¿Está subiendo la temperatura en Ontinyent?") == "Ontinyent"
    assert understand.place_words("¿Ha bajado el verde en Port de Sagunt desde 2013?") == "Port de Sagunt"
    assert understand.place_words("Is it getting hotter in Phoenix, Arizona since 2000?") == "Phoenix, Arizona"
    assert understand.place_words("¿Cómo fue octubre de 2024?") is None
    assert understand.place_words("Is it hot in October 2024?") is None
    assert understand.place_words("¿Está subiendo la temperatura?") is None


@pytest.mark.parametrize(("question", "place"), [
    ("¿Qué tal va todo por Vilafranca?", "Vilafranca"),
    ("Com va tot per Vilafranca?", "Vilafranca"),
    ("¿Qué daños hubo por la DANA?", None),
    ("¿Qué daños hubo por la DANA de Paiporta?", "Paiporta"),
    ("¿Llovió por Navidad?", None),
    ("Va ploure per Nadal?", None),
    ("¿Llovió en Vilafranca por Navidad?", "Vilafranca"),
])
def test_por_and_per_separate_places_from_events_and_dates(question, place):
    assert understand.place_words(question) == place


def test_the_jev_client_only_needs_what_the_image_installs():
    """Found in the container: the client imported a library that only the dev environment has."""
    import ast
    from pathlib import Path

    from hup_api.llm import jev

    imported = {name.name.split(".")[0] for node in ast.walk(ast.parse(Path(jev.__file__).read_text(encoding="utf-8")))
                if isinstance(node, ast.Import) for name in node.names}
    assert "httpx" not in imported and "requests" in imported


@pytest.mark.parametrize("change", [{"design": "whatif"}, {"design": "impact"}, {"confidence": 0.5}])
def test_several_places_are_asked_before_the_planning_model_on_the_slow_path_too(monkeypatch, change):
    """Measured live: an ambiguous name cost a planning call (about 5 s) only to end in the place question."""
    monkeypatch.setattr(jev, "_transport", reading(**change))
    recorded = use(monkeypatch)
    out = service.from_text("¿Qué pasa si llegan 15.000 habitantes a Twinton?", "es", None, CATALOG)
    assert recorded.sent == [] and out["kind"] == "clarification" and len(out["candidates"]) == 2


@pytest.mark.parametrize("question", [
    "¿Está subiendo la temperatura en Twinton?",
    "¿Qué tal va todo por Twinton?",
    "Com va tot per Twinton?",
])
def test_several_places_are_asked_first_without_jev_as_well(monkeypatch, question):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    recorded = use(monkeypatch)
    out = service.from_text(question, "es", None, CATALOG)
    assert recorded.sent == [] and out["kind"] == "clarification" and len(out["candidates"]) == 2


@pytest.mark.parametrize("text", [
    "Què pasa con las refinerías quemadas en el golfo con la guerra de Trump?",
    "What happened in the Gulf during the war of Trump?",
])
def test_a_generic_area_goes_to_the_planner_without_resolving_a_person(monkeypatch, text):
    monkeypatch.setattr(jev, "_transport", reading())
    resolved = []

    def resolve(name, language):
        resolved.append(name)
        return {"status": "found", "zone": {**ZONE, "name": name}}

    monkeypatch.setattr(places, "resolve", resolve)
    recorded = use(monkeypatch, {**PLAN, "place": "Persian Gulf"})
    out = service.from_text(text, "es", None, CATALOG)
    assert recorded.sent == ["plan"]
    assert resolved == ["Persian Gulf"]
    assert out["plan"]["zone"]["name"] == "Persian Gulf"


def test_place_words_keeps_a_named_area_inside_an_event_name():
    assert understand.place_words("Cómo afecta a la salud y el clima la Guerra del golfo Persico?") == "golfo Persico"
    assert understand.place_words("¿Qué ocurre en el Golfo de México tras el anuncio de Trump?") == "Golfo de México"
