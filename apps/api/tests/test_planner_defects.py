"""Defects found by the real pass of 14 November (.dev/notes/2026-11-14-pasada-real-todos-los-disenos.md).

Each test failed before its fix. No LLM, no network: the model's answers are recorded, the data folder is empty or
holds a one-line manifest written here.
"""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import main, seed_example
from hup_api.assistant import service, templates
from hup_api.assistant.templates import template
from hup_api.llm.adapter import LLMUnavailable
from hup_data import coverage, paths, places

CATALOG = catalog_module.load()
ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [10.0, 40.0, 10.1, 40.1]}
EXPOSURE = "population_in_flood_area"


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    return tmp_path


def flood_layers(catalog=CATALOG):
    read = next(o for o in catalog["operations"] if o["id"] == "read_exposure")
    return read["params"]["properties"]["flood_layers"]["default"]


def decided(plan, field="period"):
    found = [d for d in plan["answer"].get("decided", []) if d["field"] == field]
    assert len(found) == 1, plan["answer"]
    return found[0]["text"]


# 1. exposure without a period ---------------------------------------------------------------------------------------

def test_exposure_without_a_period_takes_the_date_of_the_most_recent_catalogued_map():
    layers = [layer for layer in flood_layers()
              if int(layer["start"][:4]) <= templates._support_bounds(
                  next(v for v in CATALOG["variables"] if v["id"] == EXPOSURE))[1]]
    year = max(layer["start"] for layer in layers)[:4]
    plan = template("exposure", copy.deepcopy(ZONE), None, None, CATALOG)
    assert plan["design"] == "exposure"
    assert plan["period"] == {"start": f"{year}-01-01", "end": f"{year}-12-31"}
    text = decided(plan)
    assert year in text["en"] and year in text["es"] and "map" in text["en"]


def test_exposure_period_follows_the_catalogued_dates_not_a_fixed_year():
    catalog = copy.deepcopy(CATALOG)
    for item in flood_layers(catalog):
        item.update(start="2019-09-12", end="2019-09-14")
    plan = template("exposure", copy.deepcopy(ZONE), None, [EXPOSURE], catalog)
    assert plan["period"] == {"start": "2019-01-01", "end": "2019-12-31"}


def test_exposure_prefers_a_map_held_on_this_server(data_dir):
    catalog = copy.deepcopy(CATALOG)
    layers = flood_layers(catalog)
    held, other = layers[0]["source"], "made-up-flood-source"
    catalog["sources"].append({**copy.deepcopy(next(s for s in catalog["sources"] if s["id"] == held)), "id": other})
    layers.append({**copy.deepcopy(layers[0]), "source": other, "start": "2025-01-01", "end": "2025-01-02"})
    line = {"source": held, "item": "x", "scope": "global", "path": f"packages/{held}/x.zip", "mb": 1.0,
            "sha256": "0" * 64, "format": "zip",
            "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload", "url": "https://example.org/x"}}
    (data_dir / "manifest.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    coverage.rebuild({held: [EXPOSURE]})
    plan = templates.template("exposure", copy.deepcopy(ZONE), None, [EXPOSURE], catalog)
    year = layers[0]["start"][:4]
    assert plan["period"]["start"] == f"{year}-01-01"


def test_a_period_given_for_exposure_is_kept():
    period = {"start": "2024-01-01", "end": "2024-12-31"}
    plan = template("exposure", copy.deepcopy(ZONE), period, [EXPOSURE], CATALOG)
    assert plan["period"] == period
    assert not any(d["field"] == "period" for d in plan["answer"].get("decided", []))


# 2. "what has changed since..." ------------------------------------------------------------------------------------

def test_impact_without_windows_falls_back_to_a_trend_over_a_period_it_can_compute():
    impact = [v for v in CATALOG["variables"] if "impact" in v["designs"] and "trend" in v["designs"]
              and v["id"] in ("ndvi", "lst")] or [v for v in CATALOG["variables"]
                                                   if {"impact", "trend"} <= set(v["designs"])][:2]
    ids = [v["id"] for v in impact]
    plan = template("impact", copy.deepcopy(ZONE), {"start": "2024-01-01", "end": "2025-12-31"}, ids, CATALOG)
    assert plan["design"] == "trend"
    first, last = int(plan["period"]["start"][:4]), int(plan["period"]["end"][:4])
    minimum = templates._required_minimum("trend", {}, {o["id"]: o for o in CATALOG["operations"]}, plan["period"])
    assert last - first + 1 >= minimum
    assert plan["period"] == templates.decided_period(impact)
    text = decided(plan)
    assert "2024" in text["en"] and "2025" in text["en"] and str(first) in text["es"]
    limit = plan["answer"]["limits"][0]
    assert "comparable years before and after" in limit["en"] and "años comparables" in limit["es"]


def test_impact_fallback_keeps_a_long_enough_period():
    ids = [v["id"] for v in CATALOG["variables"] if {"impact", "trend"} <= set(v["designs"])]
    period = {"start": "2000-01-01", "end": "2024-12-31"}
    plan = template("impact", copy.deepcopy(ZONE), period, ids, CATALOG)
    assert plan["design"] == "trend" and plan["period"]["start"] <= "2013-01-01"
    assert not any(d["field"] == "period" for d in plan["answer"].get("decided", []))


# 3. the radar in an episode -----------------------------------------------------------------------------------------

def test_a_variable_read_only_as_an_episode_keeps_the_episode_period():
    only_event = [v["id"] for v in CATALOG["variables"] if v.get("designs") == ["event"]]
    if not only_event:
        pytest.skip("no episode-only variable in this catalog")
    episode = {"start": "2024-10-01", "end": "2024-11-30"}
    plan = template("event", copy.deepcopy(ZONE), episode, only_event[:1], CATALOG)
    assert plan["design"] == "event"
    assert plan["period"]["start"] == "2024-10-01" and plan["period"]["end"] == "2024-11-30"


# 4. scenario limits -------------------------------------------------------------------------------------------------

def test_scenario_limit_says_the_observed_record_ends_and_projections_go_on():
    scenario = next(v for v in CATALOG["variables"] if "scenario" in v["designs"] and v.get("baseline"))
    plan = template("scenario", copy.deepcopy(ZONE), {"start": "1995-01-01", "end": "2054-12-31"},
                    [scenario["id"]], CATALOG)
    texts = [limit["en"] for limit in plan["answer"].get("limits", [])]
    assert not any("the period outside the data is not covered" in t for t in texts), texts
    assert any("projections" in t and "2054" in t for t in texts), texts
    assert all(limit.get("es") for limit in plan["answer"]["limits"])


def test_scenario_limit_names_the_last_projected_year_held(data_dir):
    scenario = next(v for v in CATALOG["variables"] if "scenario" in v["designs"] and v.get("baseline"))
    source = scenario["sources"][0]
    lines = [{"source": source, "item": f"y{year}", "scope": "global", "path": f"packages/{source}/{year}.nc",
              "mb": 1.0, "sha256": "0" * 64, "format": "netcdf",
              "period": {"start": f"{year}-01-01", "end": f"{year}-12-31"},
              "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload", "url": "https://example.org/x"}}
             for year in (1995, 2054)]
    (data_dir / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    coverage.rebuild({source: [scenario["id"]]})
    plan = template("scenario", copy.deepcopy(ZONE), {"start": "1995-01-01", "end": "2060-12-31"},
                    [scenario["id"]], CATALOG)
    texts = [limit["en"] for limit in plan["answer"]["limits"]]
    assert any("reach 2054" in t for t in texts), texts


# 5. free text with a place sent back ------------------------------------------------------------------------------

def recorded(monkeypatch, output):
    def complete(task, data):
        call = {"task": task, "provider": "recorded", "model": "recorded", "input_tokens": 1, "output_tokens": 1,
                "cost_usd": None, "seconds": 0.0}
        return {"output": copy.deepcopy(output), "call": call, "calls": [call]}

    monkeypatch.setattr(service, "llm_complete", complete)


def model_answer(**changes):
    variable = next(v["id"] for v in CATALOG["variables"] if "trend" in v["designs"])
    return {"kind": "plan", "missing": None, "design": "trend", "place": "Valencia", "variables": [variable],
            "period": None, "before": None, "after": None, "level": "complete", "archetypes": [],
            "quantity": None, **changes}


def test_free_text_honours_a_zone_sent_back(monkeypatch):
    monkeypatch.setattr(places, "resolve", lambda text, language="en": {
        "status": "ambiguous", "candidates": [copy.deepcopy(ZONE)]})
    recorded(monkeypatch, model_answer())
    client = TestClient(main.app)
    asked = client.post("/api/plans", json={"text": "Is it getting hotter in Valencia?"}).json()
    assert asked["kind"] == "clarification" and asked["missing"] == "place"
    chosen = asked["candidates"][0]
    answer = client.post("/api/plans", json={"text": "Is it getting hotter in Valencia?", "place": chosen})
    assert answer.status_code == 200, answer.text
    out = answer.json()
    assert out["kind"] == "plan" and out["plan"]["zone"]["bbox"] == ZONE["bbox"]


def test_free_text_honours_a_place_when_the_model_asks_for_it(monkeypatch):
    recorded(monkeypatch, model_answer(kind="clarification", missing="place", place=None, design=None))
    point = {"id": "point-1", "name": "39.4699, -0.3763", "bbox": [-0.39, 39.45, -0.36, 39.49]}
    out = TestClient(main.app).post("/api/plans", json={"text": "Is it getting hotter here?", "place": point}).json()
    assert out["kind"] == "plan" and out["plan"]["zone"]["name"] == point["name"]


def test_free_text_with_a_zone_and_no_model_uses_the_zone(monkeypatch):
    def unavailable(task, data):
        raise LLMUnavailable("no model", [])

    monkeypatch.setattr(service, "llm_complete", unavailable)
    monkeypatch.setattr(places, "resolve", lambda text, language="en": {"status": "not_found"})
    out = TestClient(main.app).post("/api/plans", json={"text": "¿Está subiendo la temperatura en Valencia?",
                                                        "language": "es", "place": copy.deepcopy(ZONE)}).json()
    assert out["kind"] == "plan" and out["plan"]["zone"]["bbox"] == ZONE["bbox"]


def test_free_text_with_a_place_that_is_not_a_zone_is_refused(monkeypatch):
    recorded(monkeypatch, model_answer())
    answer = TestClient(main.app).post("/api/plans", json={"text": "hotter?", "place": {"name": "no box"}})
    assert answer.status_code == 422 and set(answer.json()["error"]["text"]) == {"en", "es"}


# 6. errors in both languages --------------------------------------------------------------------------------------

def test_every_error_of_these_routes_has_spanish_text(data_dir):
    client = TestClient(main.app)
    answers = [
        client.get("/api/analyses/nope"),
        client.get("/api/analyses?design=nope"),
        client.post("/api/analyses", json={"nope": 1}),
        client.post("/api/analyses", json={"plan": {"design": "trend"}}),
        client.post("/api/plans", json={"text": 3, "design": "nope"}),
        client.post("/api/places/resolve", json={"text": "x", "near": [500, 0]}),
        client.get("/api/legislation?variable=nope"),
        client.get("/api/nope"),
    ]
    for answer in answers:
        assert answer.status_code >= 400, answer.url
        text = answer.json()["error"]["text"]
        assert text["en"] and text["es"] and text["es"] != text["en"], (answer.url, text)


# 7. compression and caching -------------------------------------------------------------------------------------

def test_text_is_compressed_and_revalidated_and_hashed_assets_are_immutable(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>" + "x" * 4000 + "</html>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1);" * 400, encoding="utf-8")
    monkeypatch.setattr(main, "WEB_DIST", dist)
    client = TestClient(main.app)
    catalog = client.get("/api/catalog", headers={"Accept-Encoding": "gzip"})
    assert catalog.headers["content-encoding"] == "gzip" and catalog.headers["cache-control"] == "no-cache"
    assert catalog.json()["version"] == CATALOG["version"]
    script = client.get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert script.headers["cache-control"] == main.IMMUTABLE and script.headers["content-encoding"] == "gzip"
    shell = client.get("/library", headers={"Accept-Encoding": "gzip"})
    assert shell.headers["cache-control"] == "no-cache" and shell.text.startswith("<html>")


def test_ranges_and_the_event_stream_are_not_compressed(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    example = seed_example.seed()
    layer = paths.analysis(example) / "layers" / "big.tif"
    layer.parent.mkdir(parents=True, exist_ok=True)
    layer.write_bytes(bytes(range(256)) * 40)
    client = TestClient(main.app)
    part = client.get(f"/api/analyses/{example}/files/layers/big.tif",
                      headers={"Range": "bytes=10-19", "Accept-Encoding": "gzip"})
    assert part.status_code == 206 and part.content == bytes(range(10, 20))
    assert "content-encoding" not in part.headers
    events = client.get(f"/api/analyses/{example}/events?replay=1&speed=10000", headers={"Accept-Encoding": "gzip"})
    assert events.status_code == 200 and "content-encoding" not in events.headers
    assert events.headers["cache-control"] == "no-cache" and "event: analysis_finished" in events.text


# 8. what the assistant sends ----------------------------------------------------------------------------------------

def test_the_prompt_leaves_out_what_the_model_does_not_need_to_choose():
    sent = service.prompt_catalog(CATALOG)
    assert not {"legislation", "norms", "blocks"} & sent.keys()
    assert all("license" not in s and "citation" not in s for s in sent["sources"])
    assert all("params" not in o for o in sent["operations"])
    assert [v["id"] for v in sent["variables"]] == [v["id"] for v in CATALOG["variables"]]
    assert sent["variables"] == CATALOG["variables"] and sent.get("archetypes") == CATALOG.get("archetypes")
    whole = len(json.dumps(CATALOG, ensure_ascii=False, sort_keys=True))
    assert len(json.dumps(sent, ensure_ascii=False, sort_keys=True)) < 0.6 * whole
    assert "legislation" in CATALOG  # the catalog itself is not touched
