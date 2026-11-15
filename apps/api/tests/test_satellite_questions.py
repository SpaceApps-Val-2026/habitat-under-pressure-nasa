"""Public question-to-report regression using the downloaded Black Marble and GRACE products."""
import copy
import time
from functools import partial

import pytest
from fastapi.testclient import TestClient

from hup_api import main
from hup_api.assistant import offline, understand
from hup_data import paths, places
from hup_data.readers import grace

XATIVA = {"id": "xativa", "name": "Xàtiva", "bbox": [-0.57, 38.96, -0.44, 39.03]}
SAGUNT = {"id": "sagunt", "name": "Sagunt", "bbox": [-0.29, 39.60, -0.18, 39.72]}
AUSTRALIA = {"id": "australia", "name": "Australia", "bbox": [112.90, -43.65, 153.65, -10.62]}
WORLD = {"id": "world", "name": "World", "bbox": [-180, -90, 180, 90]}
SERIES = "terrestrial_water_storage_anomaly"


@pytest.fixture
def client(monkeypatch):
    root = paths.data_dir()
    if not all((root / "packages" / source).is_dir() for source in ("black-marble", "grace-fo")):
        pytest.skip("This integration uses the two downloaded satellite products")
    monkeypatch.setenv("HUP_QUESTION_CACHE", "off")
    monkeypatch.setenv("HUP_OFFLINE", "1")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    locations = {understand.norm(zone["name"]): zone for zone in (XATIVA, SAGUNT, AUSTRALIA)}

    def resolve(text, _language):
        zone = locations.get(understand.norm(text))
        return {"status": "found", "zone": zone} if zone else {"status": "not_found"}

    monkeypatch.setattr(places, "resolve", resolve)
    monkeypatch.setattr(offline, "from_text", partial(offline.from_text, resolve=resolve))
    return TestClient(main.app)


def _request(client, text, language):
    response = client.post("/api/plans", json={"text": text, "language": language})
    assert response.status_code == 200, response.text
    return response.json()


def _report(client, plan):
    start = client.post("/api/analyses", json={"plan": plan})
    assert start.status_code == 202, start.text
    analysis_id = start.json()["analysis_id"]
    url = f"/api/analyses/{analysis_id}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = client.get(url).json()
        if state["status"] != "running":
            assert state["status"] == "finished", client.get(f"{url}/events").text
            assert state["story"]["journalistic"]
            results = client.get(f"{url}/results").json()
            for result in results:
                if result["variable"] == SERIES and "series" in result:
                    assert any(result["id"] in block["results"] for block in state["story"]["journalistic"])
            return results
        time.sleep(.05)
    pytest.fail(f"Analysis {analysis_id} did not finish")


@pytest.mark.data
@pytest.mark.parametrize(("text", "lang"), [
    ("Mostra la llum nocturna a Xàtiva de 2012 a 2025", "ca"),
    ("¿Cuánta luz nocturna se observa en Xàtiva entre 2012 y 2025?", "es"),
    ("How has nighttime radiance changed in Xàtiva since 2012?", "en"),
])
def test_nighttime_radiance_question_has_fourteen_annual_points(client, text, lang):
    reply = _request(client, text, lang)
    assert reply["kind"] == "plan" and reply["plan"]["zone"] == XATIVA
    assert reply["plan"]["variables"] == ["nighttime_radiance"]
    results = _report(client, reply["plan"])
    series = next(result for result in results if result["type"] == "series")
    assert series["sources"][0]["source"] == "black-marble"
    assert [p["t"] for p in series["series"]] == [str(year) for year in range(2012, 2026)]


@pytest.mark.data
@pytest.mark.parametrize(("zone", "text", "lang"), [
    (AUSTRALIA, "Show terrestrial water storage in Australia in 2024", "en"),
    (WORLD, "Muéstrame el agua almacenada a escala mundial durante 2024", "es"),
])
def test_water_storage_from_regional_to_global(client, zone, text, lang):
    reply = _request(client, text, lang)
    assert reply["kind"] == "plan" and reply["plan"]["zone"] == zone
    assert reply["plan"]["variables"] == [SERIES]
    results = _report(client, reply["plan"])
    regional = next(result for result in results if result["variable"] == SERIES)
    assert regional["unit"] == "cm" and regional["support"]["resolution_m"] >= 300_000
    assert regional["sources"][0]["source"] == "grace-fo"
    assert all(row["t"].startswith("2024-") for row in regional["series"])


@pytest.mark.data
def test_water_storage_change_uses_date_range_and_keeps_the_mission_gap(client):
    reply = _request(client, "How has terrestrial water storage changed in Australia since 2002?", "en")
    assert reply["kind"] == "plan" and reply["plan"]["design"] == "state"
    assert reply["plan"]["period"]["start"] == "2002-01-01"
    results = _report(client, reply["plan"])
    regional = next(result for result in results if result["variable"] == SERIES)
    months = {row["t"][:7] for row in regional["series"]}
    assert {"2017-06", "2018-06"} <= months and "2018-01" not in months


@pytest.mark.data
def test_water_use_keeps_the_grace_context_independent(client):
    reply = _request(client, "¿Cuánta agua gastaría un centro de datos de 90 MW en Sagunt?", "es")
    assert reply["kind"] == "plan" and reply["plan"]["design"] == "whatif"
    plan = reply["plan"]
    storage = next(s for s in plan["steps"] if s["params"].get("variable") == SERIES)
    estimates = [s for s in plan["steps"] if s["operation"] == "resource_estimate"]
    assert estimates and all(storage["id"] not in step["depends_on"] for step in estimates)
    results = _report(client, plan)
    regional = next(result for result in results if result["variable"] == SERIES)
    assert regional["series"] and "contexto regional" in regional["limits"][0]["es"]
    assert any(result["method"]["operation"] == "resource_estimate" and "absence" not in result for result in results)


@pytest.mark.data
def test_black_marble_outside_local_tiles_still_has_an_article(client):
    reply = _request(client, "Show nighttime lights in Australia in 2024", "en")
    assert reply["kind"] == "article"
    page = client.get(reply["article_url"])
    assert page.status_code == 200 and "Australia" in page.text and "Black Marble" in page.text


@pytest.mark.data
def test_annual_nighttime_radiance_is_not_used_as_an_october_observation(client):
    reply = _request(client, "¿Cuánta luz nocturna hubo en Xàtiva en octubre de 2024?", "es")
    if reply["kind"] == "article":
        assert client.get(reply["article_url"]).status_code == 200
    else:
        assert reply["plan"]["period"]["end"] == "2024-10-31"
        results = _report(client, reply["plan"])
        assert all("absence" in result for result in results if result["variable"] == "nighttime_radiance")


@pytest.mark.data
def test_original_grace_windows_include_the_two_january_2012_solutions(client):
    got = grace.read(SERIES, copy.deepcopy(SAGUNT), {"start": "2012-01-01", "end": "2012-01-31"})
    frame = got["data"]
    assert list(frame.t) == ["2012-01-01", "2012-01-16"]
    assert list(frame.window_start) == ["2011-12-17", "2012-01-01"]
