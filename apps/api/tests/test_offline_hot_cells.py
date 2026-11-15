"""The floor with no model reads «hot cells» as the state of population_in_hot_cells, with the catalogue's threshold."""

import pytest

from hup_api import catalog as catalog_module
from hup_api.assistant import offline

ZONE = {"id": "osm-relation-345244", "name": "Alzira", "bbox": [-0.6, 39.0, -0.3, 39.2], "source": "test"}


@pytest.fixture(scope="module")
def catalog():
    return catalog_module.load()


def found(name, language):
    return {"status": "found", "zone": ZONE}


def plan_of(text, language, catalog):
    answer = offline.from_text(text, language, catalog, resolve=found, last_year=2025)
    assert answer["kind"] == "plan", answer
    return answer["plan"]


@pytest.mark.parametrize("text, language", [
    ("¿Cuánta gente vive en celdas calientes en Alzira?", "es"),
    ("How many people live in hot cells in Alzira?", "en"),
])
def test_hot_cells_is_the_state_of_population_in_hot_cells_with_the_catalogue_threshold(text, language, catalog):
    threshold = next(v for v in catalog["variables"] if v["id"] == "population_in_hot_cells")["threshold"]
    plan = plan_of(text, language, catalog)
    assert plan["design"] == "state"
    assert plan["variables"] == ["population_in_hot_cells"]
    read = [step for step in plan["steps"] if step["operation"] == "read_series"]
    assert len(read) == 1
    assert read[0]["params"]["variable"] == "population_in_hot_cells"
    assert read[0]["params"]["percentile"] == threshold["percentile"]
    assert read[0]["params"]["base_period"] == threshold["base_period"]


@pytest.mark.parametrize("text, language, variable", [
    ("¿Cuánta gente vive en Alzira?", "es", "population"),
    ("How many people live in the flood area in Alzira?", "en", "population_in_flood_area"),
])
def test_the_neighbouring_questions_keep_their_variable(text, language, variable, catalog):
    assert plan_of(text, language, catalog)["variables"] == [variable]


def test_people_before_and_after_a_works_does_not_turn_into_flood_exposure(catalog):
    answer = offline.from_text("¿Cuántas personas había antes y después de la obra en Alzira?", "es", catalog,
                               resolve=found, last_year=2025)
    assert answer["kind"] != "plan" or answer["plan"]["variables"] != ["population_in_flood_area"]
