"""A9 in the assistant front (authorised by Guillem; Lara's option C as the guide):

- D1-a: a plan carries the whole archetype, and the executor rejects one that is not the catalogued entry;
- option C: in whatif, an archetype counted in a unit that a catalog variable counts at a place (today population for
  household water) is sized by reading that variable for the place and period, not per one unit.

No network and no LLM: the population comes from a stand-in reader.
"""
import copy

import pandas as pd

from hup_api import catalog as catalog_module
from hup_api.assistant import offline, service, templates, understand
from hup_api.executor import runner
from hup_ops.registry import REGISTRY, Context

REAL = catalog_module.load()
WATER = next(a for a in REAL["archetypes"] if a["id"] == "household-water")
ZONE = {"id": "test-zone-valencia", "name": "Test zone", "bbox": [-0.30, 39.60, -0.20, 39.70]}
PERIOD = {"start": "2020-01-01", "end": "2025-12-31"}


def _plan(**kwargs):
    return templates.whatif(ZONE, PERIOD, None, REAL, archetypes=["household-water"], **kwargs)


def _estimate_step(plan):
    return next(s for s in plan["steps"] if s["operation"] == "resource_estimate")


def test_a_plan_with_the_catalogued_archetype_validates():
    plan = _plan()
    assert runner.validate_plan(plan, REAL) == []
    assert _estimate_step(plan)["params"]["archetype"] == WATER


def test_a_plan_with_coefficients_changed_by_hand_is_rejected():
    plan = _plan()
    step = _estimate_step(plan)
    step["params"]["archetype"]["low"] = 10
    assert runner.validate_plan(plan, REAL) == [
        f"step {step['id']}: archetype must match the catalogued archetype household-water"]


def test_a_plan_with_an_archetype_outside_the_catalog_is_rejected():
    plan = _plan()
    step = _estimate_step(plan)
    step["params"]["archetype"] = {**WATER, "id": "invented-water"}
    assert any("archetype must match the catalogued archetype invented-water" in e
               for e in runner.validate_plan(plan, REAL))


def test_household_water_without_a_stated_size_reads_the_population_of_the_place():
    plan = _plan()
    step = _estimate_step(plan)
    population, = [s for s in plan["steps"] if s["id"] in step["depends_on"]]
    assert population["operation"] == "read_series" and population["params"]["variable"] == "population"
    assert "quantity" not in step["params"]
    assert "population" in plan["variables"]
    assert any(d["field"] == "size" and "población" in d["text"]["es"].lower()
               for d in plan["answer"].get("decided", []))


def test_a_size_stated_in_the_question_still_wins_over_the_population():
    plan = _plan(quantity=1000)
    step = _estimate_step(plan)
    assert step["params"]["quantity"] == {"value": 1000.0, "unit": "people"}
    assert step["depends_on"] == []


def test_an_archetype_counted_in_a_unit_no_variable_counts_is_unchanged():
    archetype = next(a for a in REAL["archetypes"] if a["per"] != "people")
    plan = templates.whatif(ZONE, PERIOD, None, REAL, archetypes=[archetype["id"]])
    step = _estimate_step(plan)
    assert step["depends_on"] == []
    assert all(s["params"].get("variable") != "population" for s in plan["steps"] if s["operation"] == "read_series")


def test_the_estimate_multiplies_the_last_population_year_and_is_approximated():
    """Engine path with a stand-in population of two years: the last one counts, the class is approximated."""
    plan = _plan()
    step = _estimate_step(plan)
    population = next(s for s in plan["steps"] if s["id"] in step["depends_on"])
    limit = {"en": "Stand-in population for the test.", "es": "Población sustituta para la prueba."}

    class Readers:
        def read_series(self, variable, zone, period, **_):
            return {"data": pd.DataFrame({"t": ["2024-01-01", "2025-01-01"], "v": [900.0, 1000.0]}), "absence": None,
                    "origin": "package", "sources": [{"source": "ine-population", "url": "https://www.ine.es/"}],
                    "support": {"resolution_m": 100}, "coverage": {"valid_pct": 100.0, "n_dates": 2},
                    "unit": "people", "limits": [limit], "input_fingerprint": None}

    job = runner.Run("a-test-a9-asistente", plan, REAL, REGISTRY)
    ctx = Context(plan["zone"], plan["period"], 1, Readers(), lambda *_: None)
    read = job.complete(population, "0" * 64, REGISTRY["read_series"].fn(copy.deepcopy(population["params"]), {}, ctx))
    body = REGISTRY["resource_estimate"].fn(copy.deepcopy(step["params"]), {population["id"]: read}, ctx)
    estimate, = job.complete(step, "0" * 64, body)
    row = dict(zip(estimate["table"]["columns"], estimate["table"]["rows"][0], strict=True))
    assert (estimate["value"], row["low"], row["high"]) == (1000 * WATER["central"], 1000 * WATER["low"],
                                                            1000 * WATER["high"])
    assert row["quantity_year"] == "2025" and estimate["data_class"] == "approximated"
    assert limit in estimate["limits"] and all(item in estimate["limits"] for item in WATER["limits"])


# The question «¿Cuánta agua consumen los hogares de Sagunt?» (Guillem: «que encaixa mes en la definicio del projecte»)


SAGUNT = {"id": "test-sagunt", "name": "Sagunt", "bbox": [-0.30, 39.60, -0.20, 39.70]}
FOUND = {"status": "found", "zone": SAGUNT}


def _ids(text, catalog=REAL):
    return [a["id"] for a in understand.archetype_matches(text, catalog)]


def test_the_archetype_is_read_from_the_words_of_its_catalog_name():
    assert _ids("¿Cuánta agua consumen los hogares de Sagunt?") == ["household-water"]
    assert _ids("How much water do households in Sagunt use?") == ["household-water"]
    assert _ids("¿Cuánta agua necesitaría un centro de datos en Alzira?") == ["data-center-water"]


def test_a_resource_and_a_verb_alone_name_no_archetype():
    """«energía» and «consume» say what is asked, not who uses it: no archetype is chosen for it."""
    assert _ids("¿Cuánta energía consume Sagunt?") == []


def test_several_archetypes_that_fit_equally_are_all_returned():
    assert _ids("¿Qué pasa si se construye un centro de datos en Sagunt?") == [
        a["id"] for a in REAL["archetypes"] if a["id"].startswith("data-center-")]


def test_a_new_archetype_or_a_new_language_needs_no_code():
    hospital = {**WATER, "id": "test-hospital-water", "name": {"en": "Hospital water use",
                                                              "es": "Consumo de agua de un hospital"}}
    valencian = {**WATER, "name": {**WATER["name"], "ca": "Consum domèstic d'aigua"}}
    catalog = {**REAL, "archetypes": [valencian, hospital]}
    assert _ids("¿Cuánta agua consume un hospital en Sagunt?", catalog) == ["test-hospital-water"]
    assert _ids("Quanta aigua consumeixen les llars de Sagunt?", catalog) == ["household-water"]
    assert _ids("Quanta aigua consumeixen les llars de Sagunt?") == []  # the real catalog has no Valencian name


def test_a_consumption_question_is_not_a_change_over_time():
    assert understand.asks_consumption("¿Cuánta agua consumen los hogares de Sagunt?")
    assert understand.asks_consumption("How much water do households in Sagunt use?")
    assert not understand.asks_consumption("¿Ha aumentado el consumo de agua en Sagunt?")
    assert not understand.asks_consumption("How much rain fell in Valencia in 2024?")


def test_without_an_llm_the_household_question_is_sized_by_the_population_of_the_place():
    answer = offline.from_text("¿Cuánta agua consumen los hogares de Sagunt?", "es", REAL,
                               resolve=lambda *_, **__: FOUND, last_year=2025)
    plan = answer["plan"]
    assert plan["design"] == "whatif" and runner.validate_plan(plan, REAL) == []
    step = _estimate_step(plan)
    assert step["params"]["archetype"] == WATER and "quantity" not in step["params"]
    population, = [s for s in plan["steps"] if s["id"] in step["depends_on"]]
    assert population["params"]["variable"] == "population"
    assert any(d["field"] == "design" and "household" in d["text"]["en"].lower()
               for d in plan["answer"]["decided"])


def test_with_the_llm_route_the_question_is_planned_by_rule_without_a_model_call(monkeypatch):
    def no_model(*_, **__):
        raise AssertionError("the planning model must not be called")

    monkeypatch.setattr(service, "llm_complete", no_model)
    monkeypatch.setattr(understand, "understand", no_model)
    answer = service.from_text("How much water do households in Sagunt use?", "en", None, REAL, place=SAGUNT)
    assert answer["kind"] == "plan" and answer["plan"]["design"] == "whatif"
    assert _estimate_step(answer["plan"])["params"]["archetype"]["id"] == "household-water"


def test_a_consumption_question_without_an_archetype_reads_the_state_of_the_resource(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    answer = offline.from_text("¿Cuánta energía consume Sagunt?", "es", REAL, resolve=lambda *_, **__: FOUND,
                               last_year=2025)
    plan = answer["plan"]
    layers = {v["id"]: v.get("layer") for v in REAL["variables"]}
    assert plan["design"] == "state" and all(layers[v] == "energy" for v in plan["variables"])
    assert "coeficiente publicado" in plan["answer"]["limits"][0]["es"]


def test_several_archetypes_are_estimated_apart_and_said():
    answer = offline.from_text("¿Qué pasa si se construye un centro de datos de 30 MW en Sagunt?", "es", REAL,
                               resolve=lambda *_, **__: FOUND, last_year=2025)
    plan = answer["plan"]
    estimates = [s for s in plan["steps"] if s["operation"] == "resource_estimate"]
    assert len(estimates) == 3 and all(s["params"]["quantity"]["value"] == 30 for s in estimates)
    assert any(d["field"] == "archetypes" for d in plan["answer"]["decided"])


def test_a_what_if_that_names_no_archetype_keeps_its_old_reading():
    answer = offline.from_text("¿Qué pasa si llueve mucho en Sagunt?", "es", REAL, resolve=lambda *_, **__: FOUND,
                               last_year=2025)
    assert answer["plan"]["design"] != "whatif"
