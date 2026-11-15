"""Question → plan through the question cache, the understanding and the prefilter, with recorded model answers."""
import copy
import json

import pytest

from hup_api import catalog as catalog_module
from hup_api.assistant import service
from hup_api.library import plan_cache
from hup_api.llm.adapter import LLMUnavailable
from hup_data import paths, places

CATALOG = catalog_module.load()
ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [10.0, 40.0, 10.1, 40.1]}
PEOPLE_ARCHETYPE = next(a["id"] for a in CATALOG["archetypes"] if a.get("per") == "people")


def answer(**changes):
    base = {"kind": "plan", "missing": None, "design": "trend", "place": "Test zone", "variables": ["air_temperature"],
            "period": {"start": "2000", "end": "2024"}, "before": None, "after": None, "level": "complete",
            "archetypes": [], "quantity": None}
    return {**base, **changes}


class Recorded:
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, task, data):
        self.sent.append((task, copy.deepcopy(data)))
        if not self.answers:
            raise LLMUnavailable("no more recorded answers")
        call = {"task": task, "provider": "recorded", "model": "recorded", "input_tokens": 100, "output_tokens": 20,
                "cost_usd": None, "seconds": 0.0}
        return {"output": copy.deepcopy(self.answers.pop(0)), "call": call, "calls": [call]}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    for name in ("HUP_UNDERSTAND", "HUP_PREFILTER", "HUP_QUESTION_CACHE", "TYPESAFE_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(places, "resolve", lambda text, language="en": {"status": "found", "zone": copy.deepcopy(ZONE)}
                        if text.lower() in ("test zone", "testville") else {"status": "not_found"})


def use(monkeypatch, *answers):
    recorded = Recorded(*answers)
    monkeypatch.setattr(service, "llm_complete", recorded)
    return recorded


def ask(text="Is air temperature rising in the test zone since 2000?", language="en", place=None):
    return service.from_text(text, language, None, CATALOG, place=place)


def last_log_line():
    return json.loads(paths.requests_log().read_text(encoding="utf-8").splitlines()[-1])


def decided(out, field):
    return [d for d in out["plan"]["answer"].get("decided", []) if d["field"] == field]


def test_the_same_question_twice_makes_one_model_call(monkeypatch):
    recorded = use(monkeypatch, answer(), answer())
    first = ask()
    second = ask("is AIR temperature rising in the test zone, since 2000")  # the same once normalised
    assert first["kind"] == second["kind"] == "plan" and len(recorded.sent) == 1
    assert first["plan_fingerprint"] == second["plan_fingerprint"]
    assert last_log_line()["llm_calls"] == []
    other_language = ask(language="es")
    assert other_language["kind"] == "plan" and len(recorded.sent) == 2


def test_the_cache_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("HUP_QUESTION_CACHE", "off")
    recorded = use(monkeypatch, answer(), answer())
    ask(), ask()
    assert len(recorded.sent) == 2


def test_a_clarification_is_never_cached(monkeypatch):
    recorded = use(monkeypatch, answer(kind="clarification", missing="place", place=None, design=None, variables=[],
                                       period=None),
                   answer(kind="clarification", missing="place", place=None, design=None, variables=[],
                          period=None))
    assert ask("Is it getting hotter?")["kind"] == "clarification"
    assert ask("Is it getting hotter?")["kind"] == "clarification"
    assert len(recorded.sent) == 2


def test_a_place_not_found_is_not_cached(monkeypatch):
    recorded = use(monkeypatch, answer(place="Nowhere at all"), answer(place="Nowhere at all"))
    assert ask("Is it hotter in Nowhere at all?")["kind"] == "clarification"
    assert ask("Is it hotter in Nowhere at all?")["kind"] == "clarification"
    assert len(recorded.sent) == 2


def test_a_chosen_place_is_part_of_the_key(monkeypatch):
    recorded = use(monkeypatch, answer(), answer())
    ask("Is it hotter since 2000?", place=ZONE)
    ask("Is it hotter since 2000?", place={**ZONE, "id": "another-zone"})
    assert len(recorded.sent) == 2


def test_two_wordings_that_give_the_same_plan_find_the_same_analysis(monkeypatch):
    use(monkeypatch, answer(), answer())
    first = ask()
    monkeypatch.setattr(plan_cache, "existing", lambda fp: "20261114-000000-abcdef"
                        if fp == first["plan_fingerprint"] else None)
    second = ask("¿Sube la temperatura del aire en la zona de prueba desde 2000?", "es")
    assert second["plan_fingerprint"] == first["plan_fingerprint"]
    assert second["existing_analysis"] == "20261114-000000-abcdef"
    third = ask("¿Sube la temperatura del aire en la zona de prueba desde 2000?", "es")  # from the cache
    assert third["existing_analysis"] == "20261114-000000-abcdef"


def test_the_model_reads_a_shorter_catalog_that_still_names_every_variable(monkeypatch):
    recorded = use(monkeypatch, answer())
    ask("¿Cuánta gente vive en zona inundable en la zona de prueba?", "es")
    (task, data), = recorded.sent
    assert task == "plan" and "other_variables" in data["context"]
    for word in ("legislation", "\"norms\"", "\"blocks\"", "citation", "eurovoc"):
        assert word not in data["context"]
    for variable in CATALOG["variables"]:
        assert f'"{variable["id"]}"' in data["context"]
    assert data["schema"] == service.answer_schema(CATALOG)  # the model may still name any variable


def test_with_the_prefilter_off_the_catalog_is_sent_as_before(monkeypatch):
    monkeypatch.setenv("HUP_PREFILTER", "off")
    recorded = use(monkeypatch, answer())
    ask()
    full = json.dumps(service.prompt_catalog(CATALOG), ensure_ascii=False, sort_keys=True)
    assert full in recorded.sent[0][1]["context"]


def test_a_since_year_the_model_left_out_becomes_the_period(monkeypatch):
    use(monkeypatch, answer(period=None))
    out = ask("Is air temperature rising in the test zone since 2013?")
    assert out["plan"]["period"]["start"].startswith("2013")
    assert decided(out, "period") and "2013" in decided(out, "period")[-1]["text"]["es"]


def test_a_size_in_the_archetype_unit_becomes_the_quantity(monkeypatch):
    use(monkeypatch, answer(design="whatif", variables=[], period=None, archetypes=[PEOPLE_ARCHETYPE]))
    out = ask("¿Qué pasa si viven 5.000 habitantes más en la zona de prueba?", "es")
    assert out["kind"] == "plan", out
    assert "5.000 habitantes" in json.dumps(decided(out, "quantity"), ensure_ascii=False)


HOMES = "¿Qué pasa si se construyen 5.000 viviendas en la zona de prueba?"
CONVERSION = {"possible": True, "factor": 2.5, "low": 2.0, "high": 3.0,
              "basis_en": "typical number of residents per home",
              "basis_es": "número típico de residentes por vivienda",
              "scale_en": "a medium-sized town", "scale_es": "una ciudad mediana"}


def quantity_param(out):
    steps = [s for s in out["plan"]["steps"] if s["operation"] == "resource_estimate"]
    return steps[0]["params"].get("quantity") if steps else None


def test_a_size_in_another_unit_is_converted_as_a_visible_assumption(monkeypatch):
    recorded = use(monkeypatch, answer(design="whatif", variables=[], period=None, archetypes=[PEOPLE_ARCHETYPE]),
                   CONVERSION)
    out = ask(HOMES, "es")
    assert [task for task, _ in recorded.sent] == ["plan", "understand"]
    assert "5.000 viviendas" in recorded.sent[1][1]["request"] and "TO: people" in recorded.sent[1][1]["context"]
    assert quantity_param(out) == {"value": 12500.0, "unit": "people"}
    (note,) = decided(out, "quantity")
    for text in (note["text"]["es"], note["text"]["en"]):
        assert "5.000 viviendas" in text
    assert "factor de unos 2,5" in note["text"]["es"] and "12.500" in note["text"]["es"]
    assert "10.000 a 15.000" in note["text"]["es"] and "sin fuente" in note["text"]["es"]
    assert "12,500" in note["text"]["en"] and "10,000 to 15,000" in note["text"]["en"]
    assert note["text"] in out["plan"]["answer"]["limits"]  # also a limit of the plan, in both languages
    assert [c["task"] for c in last_log_line()["llm_calls"]] == ["plan", "understand"]
    again = ask(HOMES, "es")  # the question cache keeps the assumption and makes no call
    assert len(recorded.sent) == 2 and note["text"] in again["plan"]["answer"]["limits"]


@pytest.mark.parametrize("conversion", [None, {**CONVERSION, "possible": False},
                                        {**CONVERSION, "factor": 9.0}, {"kind": "plan"}])
def test_without_a_usable_conversion_the_size_is_named_and_not_converted(monkeypatch, conversion):
    answers = [answer(design="whatif", variables=[], period=None, archetypes=[PEOPLE_ARCHETYPE])]
    use(monkeypatch, *(answers + ([conversion] if conversion is not None else [])))
    out = ask(HOMES, "es")
    assert out["kind"] == "plan" and quantity_param(out) is None
    (note,) = decided(out, "quantity")
    assert "5.000 viviendas" in note["text"]["es"] and "no se pudo estimar" in note["text"]["es"]


def test_a_size_the_model_copied_in_the_wrong_unit_is_not_used(monkeypatch):
    use(monkeypatch, answer(design="whatif", variables=[], period=None, archetypes=[PEOPLE_ARCHETYPE],
                            quantity=5000), CONVERSION)
    assert quantity_param(ask(HOMES, "es")) == {"value": 12500.0, "unit": "people"}


# bench-style cases: what the time reference says about the kind of answer ---------------------------------------

LAST = __import__("time").gmtime().tm_year - 1


def test_a_future_year_is_a_scenario_with_the_catalog_horizons(monkeypatch):
    """Real failure (container ba9f9f8): «¿Cuánto calor hará en Valencia en 2050?» with the place chosen gave a
    trend over 2050 alone, which can only end in absences."""
    use(monkeypatch, answer(design="trend", variables=["air_temperature"], period={"start": "2050", "end": "2050"}))
    out = ask("¿Cuánto calor hará en Valencia en 2050?", "es", place=ZONE)
    plan = out["plan"]
    assert plan["design"] == "scenario" and plan["answer"]["level"] != "not_possible"
    assert "tas_mean" in plan["variables"] and "air_temperature" not in plan["variables"]
    assert plan["period"]["start"] <= "1995-12-31" and plan["period"]["end"] >= "2050-01-01"
    assert any("2050" in d["text"]["es"] and "escenarios" in d["text"]["es"] for d in decided(out, "design"))
    # already a scenario but over the future year alone: the period is set again; a good scenario is left alone
    use(monkeypatch, answer(design="scenario", variables=["tas_mean"], period={"start": "2050", "end": "2050"}))
    assert ask("How hot will it get here in 2050?", place=ZONE)["plan"]["period"]["start"].startswith("1995")
    use(monkeypatch, answer(design="scenario", variables=["tas_mean"], period={"start": "1995", "end": "2050"}))
    good = ask("Project the heat here to 2050.", place=ZONE)
    assert good["plan"]["period"]["end"].startswith("2050") and not decided(good, "design")


def test_since_a_named_moment_compares_time_without_a_spatial_reference(monkeypatch):
    use(monkeypatch, answer(design="trend", variables=["ndvi", "lst"], period={"start": "2024", "end": str(LAST)}))
    out = ask("¿Qué ha cambiado en la zona de prueba desde la DANA de 2022?", "es")
    plan = out["plan"]
    assert plan["design"] == "scan" and plan["answer"]["level"] == "with_limits"
    assert any("2022" in d["text"]["es"] for d in decided(out, "milestone"))
    assert any(s["operation"] == "anomaly" for s in plan["steps"])
    assert all(s["params"].get("area") != "reference" for s in plan["steps"])
    # With an unverified date, the recent observations still inform a scan.
    use(monkeypatch, answer(design="trend", variables=["ndvi", "lst"], period=None))
    out = ask("¿Qué ha cambiado en la zona de prueba desde la DANA?", "es")
    assert out["plan"]["design"] == "scan"
    assert any("could not date the event" in d["text"]["en"] for d in decided(out, "milestone"))
    # «since 1990» is a date, not a change: a trend stays a trend (bench E4)
    use(monkeypatch, answer(design="trend", variables=["air_temperature"], period={"start": "1990", "end": "2025"}))
    assert ask("Is air temperature rising in the test zone since 1990?")["plan"]["design"] == "trend"


def test_one_past_month_is_an_episode(monkeypatch):
    use(monkeypatch, answer(design="trend", variables=["precipitation"], period={"start": "2024", "end": "2024"}))
    out = ask("¿Cómo fue la lluvia de octubre de 2024 en la zona de prueba?", "es")
    plan = out["plan"]
    # the plan reads the record up to the episode, which closes it
    assert plan["design"] == "event" and plan["period"]["end"].startswith("2024-10"), plan["period"]
    assert decided(out, "design")


def test_other_kinds_of_answer_asked_for_are_named(monkeypatch):
    monkeypatch.setenv("HUP_UNDERSTAND", "llm")
    understood = {"language": "en", "place": None, "times": [], "intents": ["trend", "whatif"],
                  "subjects": ["heat"], "magnitudes": []}
    recorded = use(monkeypatch, understood, answer())
    out = ask("Is it getting hotter in the test zone, and what if 50 MW were built there?")
    assert [task for task, _ in recorded.sent] == ["understand", "plan"]
    assert any("what an archetype would ask for" in d["text"]["en"] for d in decided(out, "design"))
    assert [c["task"] for c in last_log_line()["llm_calls"]] == ["understand", "plan"]
