"""B9: related EU legislation by EuroVoc concept, from a recorded Cellar answer. No network in these tests.

The recorded answer (tests/data/cellar_eurovoc_1440_spa.json) is what Cellar returned on 2026-11-14 for the query
of legislation.query_text(["1440"], "es") limited to five rows.
"""
import copy
import json
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import legislation, main

RECORDED = json.loads((Path(__file__).parent / "data" / "cellar_eurovoc_1440_spa.json").read_text(encoding="utf-8"))
REAL = catalog_module.load()


def catalog_with_flood():
    """The real catalog plus one test variable tagged with the flood concept, which no real variable has yet."""
    catalog = copy.deepcopy(REAL)
    flood = {**copy.deepcopy(catalog["variables"][0]), "id": "test_flood", "eurovoc": ["1440"]}
    bare = {k: v for k, v in copy.deepcopy(catalog["variables"][0]).items() if k != "eurovoc"}
    catalog["variables"] += [flood, {**bare, "id": "test_untagged"}]
    return catalog


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setattr(catalog_module, "load", lambda *a, **k: catalog_with_flood())
    asked = []

    def ask(query):
        asked.append(query)
        return copy.deepcopy(RECORDED)

    monkeypatch.setattr(legislation, "_ask", ask)
    return asked


@pytest.fixture
def client():
    return TestClient(main.app)


def test_a_flood_variable_lists_the_floods_directive(client, setup):
    response = client.get("/api/legislation", params={"variable": "test_flood", "language": "es"})
    assert response.status_code == 200
    answer = response.json()
    assert "absence" not in answer
    first = answer["items"][0]
    assert first["celex"] == "32007L0060"
    assert first["title"].startswith("Directiva 2007/60/CE")
    assert first["date"] == "2007-10-23"
    assert first["url"] == "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:32007L0060"
    assert answer["language"] == "es" and answer["retrieved"]
    (query,) = setup
    assert "<http://eurovoc.europa.eu/1440>" in query and "/language/SPA>" in query
    assert "resource-type/DIR>" in query and "resource-type/REG>" in query and "in-force" in query


def test_the_answer_is_cached_with_its_date(client, setup, tmp_path):
    first = client.get("/api/legislation", params={"variable": "test_flood", "language": "es"}).json()
    second = client.get("/api/legislation", params={"variable": "test_flood", "language": "es"}).json()
    assert second == first and len(setup) == 1
    (cached,) = list(tmp_path.rglob("*.json"))
    assert json.loads(cached.read_text(encoding="utf-8"))["retrieved"] == first["retrieved"]


def test_a_variable_without_eurovoc_is_not_computed(client, setup):
    answer = client.get("/api/legislation", params={"variable": "test_untagged", "language": "en"}).json()
    assert answer["items"] == [] and answer["absence"]["reason"] == "not_computed"
    assert answer["absence"]["detail"]["en"] and answer["absence"]["detail"]["es"]
    assert setup == []


def test_offline_without_cache_is_source_unreachable(client, setup, monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    answer = client.get("/api/legislation", params={"variable": "test_flood", "language": "es"}).json()
    # No live list, and it says so; the reviewed cards held for the variable's concepts may still come.
    assert answer["absence"]["reason"] == "source_unreachable"
    assert all(item["visibility"] == "card" and item["date"] is None for item in answer["items"])
    assert setup == []


def test_a_cellar_that_does_not_answer_is_source_unreachable_and_not_cached(client, monkeypatch, tmp_path):
    def timeout(query):
        raise requests.Timeout("no answer")

    monkeypatch.setattr(legislation, "_ask", timeout)
    answer = client.get("/api/legislation", params={"variable": "test_flood", "language": "es"}).json()
    assert answer["absence"]["reason"] == "source_unreachable"
    assert not list(tmp_path.rglob("*.json"))


def test_an_answer_that_is_not_sparql_json_is_source_unreachable(client, monkeypatch):
    monkeypatch.setattr(legislation, "_ask", lambda query: {"unexpected": True})
    answer = client.get("/api/legislation", params={"variable": "test_flood", "language": "es"}).json()
    assert answer["absence"]["reason"] == "source_unreachable"


def test_a_language_cellar_does_not_have_falls_back_to_english(client, setup):
    answer = client.get("/api/legislation", params={"variable": "test_flood", "language": "ca"}).json()
    assert answer["language"] == "en" and "/language/ENG>" in setup[0]
    assert answer["items"][0]["url"].startswith("https://eur-lex.europa.eu/legal-content/EN/")


@pytest.mark.parametrize("params", [{}, {"variable": "test_flood", "language": "spanish"},
                                    {"variable": "x" * 500, "language": "en"}])
def test_malformed_requests_are_rejected(client, params):
    response = client.get("/api/legislation", params=params)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"


def test_an_unknown_variable_is_not_found(client):
    response = client.get("/api/legislation", params={"variable": "no_such_variable", "language": "en"})
    assert response.status_code == 404


def test_the_note_lists_and_never_judges(client):
    answer = client.get("/api/legislation", params={"variable": "test_flood", "language": "es"}).json()
    note = json.dumps(answer["note"], ensure_ascii=False).lower()
    assert answer["note"]["en"] and answer["note"]["es"]
    assert not any(word in note for word in ("complies", "compliance", "breach", "violat", "cumple", "incumpl"))
