"""The plan read from the words, with no model: Delfos decides design, variables, period and windows."""

from hup_api.assistant import offline

CATALOG = {
    "version": "0.2.0",
    "variables": [
        {"id": "air_temperature", "designs": ["state", "trend", "scan"], "name": {"en": "Air temperature"}},
        {"id": "lst", "designs": ["state", "trend", "impact", "scan"], "name": {"en": "Surface temperature"}},
        {"id": "ndvi", "designs": ["state", "trend", "impact", "scan"], "name": {"en": "Greenness"}},
        {"id": "precipitation", "designs": ["state", "trend", "event", "scan"], "name": {"en": "Rain"}},
        {"id": "tas_mean", "designs": ["scenario"], "name": {"en": "Projected air temperature"}},
        {"id": "population_in_flood_area", "designs": ["exposure"], "name": {"en": "People in flood area"}},
    ],
}

ZONE = {"id": "osm-relation-345244", "name": "Alzira", "bbox": [-0.6, 39.0, -0.3, 39.2], "source": "test"}


def found(name, language):
    return {"status": "found", "zone": ZONE}


def missing(name, language):
    return {"status": "not_found"}


def test_the_words_decide_the_design_the_variable_and_the_period(monkeypatch):
    seen = {}
    monkeypatch.setattr(offline.service, "plan_answer", lambda **kwargs: seen.update(kwargs) or {"kind": "plan"})

    answer = offline.from_text("¿Cuánto ha subido la temperatura en Alzira desde 1981?", "es", CATALOG,
                               resolve=found, last_year=2025)

    assert answer == {"kind": "plan"}
    assert seen["design"] == "trend"
    assert seen["variables"] == ["air_temperature"]
    assert seen["period"] == {"start": "1981-01-01", "end": "2025-12-31"}
    assert seen["before"] is None and seen["after"] is None
    assert seen["request"]["origin"] == "assistant"


def test_a_change_around_a_place_is_a_before_and_after_contrast(monkeypatch):
    seen = {}
    monkeypatch.setattr(offline.service, "plan_answer", lambda **kwargs: seen.update(kwargs) or {"kind": "plan"})

    offline.from_text("¿Qué ha cambiado alrededor de la gigafactoría de Sagunt desde 2022?", "es", CATALOG,
                      resolve=found, last_year=2025)

    assert seen["design"] == "impact"
    assert seen["variables"] == ["lst"]
    assert seen["before"] == [2014, 2021]
    assert seen["after"] == [2022, 2024]


def test_people_in_a_flood_area_chooses_exposure(monkeypatch):
    seen = {}
    monkeypatch.setattr(offline.service, "plan_answer", lambda **kwargs: seen.update(kwargs) or {"kind": "plan"})

    offline.from_text("¿Cuántas personas viven en la zona inundable de Valencia?", "es", CATALOG,
                      resolve=found, last_year=2025)

    assert seen["design"] == "exposure"
    assert seen["variables"] == ["population_in_flood_area"]
    assert seen["period"] == {"start": "2025-01-01", "end": "2025-12-31"}


def test_a_future_year_chooses_the_scenario(monkeypatch):
    seen = {}
    monkeypatch.setattr(offline.service, "plan_answer", lambda **kwargs: seen.update(kwargs) or {"kind": "plan"})

    offline.from_text("¿Qué temperatura habrá en Alzira en 2050?", "es", CATALOG, resolve=found, last_year=2025)

    assert seen["design"] == "scenario"
    assert seen["variables"] == ["tas_mean"]
    assert seen["period"] == {"start": "1995-01-01", "end": "2054-12-31"}


def test_a_storm_in_a_named_year_is_an_episode(monkeypatch):
    seen = {}
    monkeypatch.setattr(offline.service, "plan_answer", lambda **kwargs: seen.update(kwargs) or {"kind": "plan"})

    offline.from_text("Lluvia extrema en Paiporta en 2024", "es", CATALOG, resolve=found, last_year=2025)

    assert seen["design"] == "event"
    assert seen["variables"] == ["precipitation"]
    assert seen["period"] == {"start": "2024-01-01", "end": "2024-12-31"}


def test_without_a_place_it_asks_for_it_and_never_for_a_form():
    answer = offline.from_text("¿Cuánto ha subido la temperatura?", "es", CATALOG, resolve=missing, last_year=2025)

    assert answer["kind"] == "clarification"
    assert answer["missing"] == "place"


def test_an_ambiguous_place_carries_its_candidates():
    other = {**ZONE, "id": "osm-relation-1", "name": "Arganda"}

    def ambiguous(name, language):
        return {"status": "ambiguous", "candidates": [ZONE, other]}

    answer = offline.from_text("¿Cuánto ha subido la temperatura en Alzira?", "es", CATALOG, resolve=ambiguous,
                               last_year=2025)

    assert answer["kind"] == "clarification"
    assert [candidate["id"] for candidate in answer["candidates"]] == ["osm-relation-345244", "osm-relation-1"]
