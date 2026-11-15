"""POST /api/places/resolve and POST /api/plans on the form path (no LLM). The gazetteer is a stand-in."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import contracts, executor, main, requests_log
from hup_data import paths, places

CATALOG = catalog_module.load()
ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [10.0, 40.0, 10.1, 40.1]}
OTHER = {"id": "other-zone", "name": "Other zone", "bbox": [11.0, 41.0, 11.1, 41.1]}
ANSWERS = {"test zone": {"status": "found", "zone": ZONE},
           "twin": {"status": "ambiguous", "candidates": [ZONE, OTHER]},
           "nowhere": {"status": "not_found"}}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    asked = []

    def resolve(text, language="en"):
        asked.append((text, language))
        return copy.deepcopy(ANSWERS.get(text.lower(), {"status": "not_found"}))

    monkeypatch.setattr(places, "resolve", resolve)
    return asked


@pytest.fixture
def client():
    return TestClient(main.app)


def trend_variable():
    return next(v["id"] for v in CATALOG["variables"] if "trend" in v["designs"])


def form(**changes):
    body = {"text": "Trend in the test zone", "language": "en", "design": "trend", "place": "Test zone",
            "variables": [trend_variable()], "period": {"start": "2000", "end": "2024"}}
    body.update(changes)
    return {k: v for k, v in body.items() if v is not None}


def test_places_resolve_returns_what_the_gazetteer_returns(client, setup):
    for text, expected in ANSWERS.items():
        response = client.post("/api/places/resolve", json={"text": text, "language": "es"})
        assert response.status_code == 200
        assert response.json() == expected
    assert setup[0] == ("test zone", "es")


@pytest.mark.parametrize("body", [{}, {"text": 3}, {"text": "x" * 1000}, {"text": "a", "language": "spanish"}])
def test_places_resolve_rejects_malformed_bodies(client, body):
    response = client.post("/api/places/resolve", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_the_form_gives_a_valid_plan(client):
    response = client.post("/api/plans", json=form())
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["kind"] == "plan"
    plan = answer["plan"]
    assert contracts.errors("plan", plan) == []
    assert executor.validate_plan(plan, CATALOG) == []
    assert plan["zone"] == ZONE and plan["design"] == "trend"
    assert plan["request"] == {"text": "Trend in the test zone", "language": "en", "origin": "form"}
    assert answer["plan_fingerprint"] and answer["existing_analysis"] is None
    line = json.loads(paths.requests_log().read_text(encoding="utf-8").splitlines()[-1])
    assert line["kind"] == "plan" and line["plan_fingerprint"] == answer["plan_fingerprint"]
    assert line["place"]["id"] == ZONE["id"] and line["llm_calls"] == []


def test_two_wordings_of_the_same_form_give_the_same_fingerprint(client):
    first = client.post("/api/plans", json=form()).json()
    second = client.post("/api/plans", json=form(text="Otra redacción", language="es", level="general")).json()
    assert first["plan_fingerprint"] == second["plan_fingerprint"]


@pytest.mark.parametrize(("place", "candidates"), [("twin", 2), ("nowhere", 0), (None, 0)])
def test_an_ambiguous_or_missing_place_asks(client, place, candidates):
    response = client.post("/api/plans", json=form(place=place))
    assert response.status_code == 200
    answer = response.json()
    assert answer["kind"] == "clarification"
    assert answer["question"]["en"] and answer["question"]["es"]
    assert len(answer["candidates"]) == candidates
    assert answer["missing"] == "place"


def test_a_candidate_zone_can_be_sent_back_as_the_place(client):
    response = client.post("/api/plans", json=form(place=OTHER))
    assert response.json()["kind"] == "plan"
    assert response.json()["plan"]["zone"] == OTHER


def test_an_impact_without_its_windows_falls_back_and_says_so(client):
    # Only the place is asked (AGENTS.md, 6 and 7): without the date of the change, a design that needs none.
    variable = next(v["id"] for v in CATALOG["variables"] if "impact" in v["designs"])
    answer = client.post("/api/plans", json=form(design="impact", variables=[variable])).json()
    assert answer["kind"] == "plan" and answer["plan"]["design"] != "impact"
    assert answer["plan"]["answer"]["level"] == "with_limits" and answer["plan"]["answer"]["alternative"]["es"]
    assert any("date of the change" in limit["en"] for limit in answer["plan"]["answer"]["limits"])
    answer = client.post("/api/plans", json=form(design="impact", variables=[variable], before=[2014, 2019],
                                                 after=[2021, 2024])).json()
    assert answer["kind"] == "plan"


def test_free_text_without_an_llm_is_read_from_the_words(client):
    response = client.post("/api/plans", json={"text": "Is it getting hotter in my town?", "language": "en"})
    assert response.status_code == 200
    assert response.json()["kind"] in {"plan", "clarification"}


@pytest.mark.parametrize("body", [
    {"text": 1, "language": "en"},
    {"text": "a", "language": "en", "design": "made_up", "place": "Test zone", "variables": ["x"]},
    {"text": "a", "language": "en", "design": "trend", "place": "Test zone", "variables": "ndvi"},
    {"text": "a", "language": "en", "design": "trend", "place": "Test zone", "variables": ["x"], "period": "2020"},
    {"text": "a" * 5000, "language": "en"},
    [],
])
def test_malformed_plan_requests_are_rejected(client, body):
    response = client.post("/api/plans", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def log_lines():
    return [json.loads(line) for line in paths.requests_log().read_text(encoding="utf-8").splitlines()]


def test_every_plan_request_leaves_one_log_line(client):
    client.post("/api/plans", json=form())
    client.post("/api/plans", json=form(place="twin"))
    client.post("/api/plans", json=form(period=None))
    client.post("/api/plans", json={"text": "Free text", "language": "en"})
    lines = log_lines()
    assert len(lines) == 4 and all(line["kind"] == "plan" for line in lines)
    plan, ambiguous, no_period, free = lines
    assert plan["plan_fingerprint"] and plan["answer_level"] and plan["origin"] == "form"
    assert no_period["plan_fingerprint"]  # a missing period is decided, not asked (AGENTS.md, 6)
    for question in (ambiguous,):
        assert question["plan_fingerprint"] is None and question["answer_level"] is None
        assert question["origin"] == "form" and question["text"] == "Trend in the test zone"
    assert free["origin"] == "assistant" and free["plan_fingerprint"] is None and free["llm_calls"] == []
    for line in lines:
        assert set(line) == set(requests_log.FIELDS)
