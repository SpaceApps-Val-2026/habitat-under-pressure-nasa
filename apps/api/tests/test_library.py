"""B8: saved analyses can be listed, searched and filtered; featured ones come first; failed ones only on request."""
import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from hup_api import catalog as catalog_module
from hup_api import contracts, executor, main, seed_example
from hup_api.library import folder, index
from hup_data import paths

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((EXAMPLES / "analysis.json").read_text(encoding="utf-8"))
COMMON = "https://habitat-under-pressure/contracts/common.schema.json"

LIST_SCHEMA = {
    "type": "object", "required": ["items"], "additionalProperties": False,
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "title", "summary", "design", "zone", "created", "status", "featured"],
        "properties": {
            "id": {"$ref": f"{COMMON}#/$defs/id"},
            "title": {"$ref": f"{COMMON}#/$defs/text"},
            "summary": {"anyOf": [{"$ref": f"{COMMON}#/$defs/text"}, {"type": "null"}]},
            "design": {"anyOf": [{"$ref": f"{COMMON}#/$defs/design"}, {"type": "null"}]},
            "zone": {"anyOf": [{"type": "null"}, {
                "type": "object", "required": ["id", "name", "bbox"], "additionalProperties": False,
                "properties": {"id": {"type": ["string", "null"]}, "name": {"type": ["string", "null"]},
                               "bbox": {"type": ["array", "null"]}}}]},
            "created": {"type": "string", "format": "date-time"},
            "status": {"enum": ["planned", "running", "finished", "failed"]},
            "featured": {"type": "boolean"},
            "versions": {"type": "integer", "minimum": 2}}}}},
}


def analysis(analysis_id, created, design, variables, title, finding, featured=None, status="finished",
             summary=None) -> dict:
    """A saved analysis built from the contract example, with its own id, date, design, text and status."""
    out = copy.deepcopy(EXAMPLE)
    out.update(id=analysis_id, created=created, status=status, title=title)
    out["plan"]["design"], out["plan"]["variables"] = design, variables
    out["findings"] = [{**out["findings"][0], "text": finding}]
    out.pop("featured", None)
    out.pop("summary", None)
    if featured is not None:
        out["featured"] = featured
    if summary is not None:
        out["summary"] = summary
    assert contracts.errors("analysis", out) == []
    return out


def save(a: dict) -> None:
    folder.directory(a["id"]).mkdir(parents=True, exist_ok=True)
    folder.write_analysis(a["id"], a)


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(catalog_module, "load", lambda *args, **kwargs: copy.deepcopy(CATALOG))
    return tmp_path


@pytest.fixture
def client():
    seed_example.seed()  # featured, created 2026-11-14
    save(analysis("valencia-reservoirs", "2026-11-16T09:00:00Z", "trend", ["water_demand"],
                  {"en": "Water demand around Valencia"},
                  {"en": "Reservoir storage went down over {r-s2-trend.value} years."},
                  summary={"en": "Thirty years of water demand.", "es": "Treinta años de demanda de agua."}))
    save(analysis("older-scan", "2026-11-15T09:00:00Z", "scan", ["lst"], {"en": "Heat scan"},
                  {"en": "Land surface temperature rose.", "es": "La temperatura de la superficie subió."},
                  featured=False))
    save(analysis("broken-run", "2026-11-17T09:00:00Z", "trend", ["lst"], {"en": "Failed heat trend"},
                  {"en": "Reservoir heat."}, status="failed"))
    return TestClient(main.app)


def listing(client, **params) -> list[dict]:
    response = client.get("/api/analyses", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    errors = list(Draft202012Validator(LIST_SCHEMA, registry=contracts._registry()).iter_errors(body))
    assert errors == [], errors
    return body["items"]


def ids(items) -> list[str]:
    return [i["id"] for i in items]


def test_featured_first_then_newest_and_failed_left_out(client):
    items = listing(client)
    assert ids(items) == [EXAMPLE["id"], "valencia-reservoirs", "older-scan"]
    first = items[0]
    assert first["featured"] is True and first["title"] == EXAMPLE["title"] and first["design"] == "impact"
    assert first["zone"] == {"id": "parc-sagunt-ii", "name": "Parc Sagunt II", "bbox": EXAMPLE["plan"]["zone"]["bbox"]}
    assert first["created"] == EXAMPLE["created"] and first["status"] == "finished"
    assert first["summary"] is None  # the example has no summary: null, not an empty text
    assert items[1]["summary"] == {"en": "Thirty years of water demand.", "es": "Treinta años de demanda de agua."}
    assert items[1]["featured"] is False


def test_failed_analyses_only_when_asked(client):
    assert ids(listing(client, status="failed")) == ["broken-run"]
    assert ids(listing(client, status="finished")) == [EXAMPLE["id"], "valencia-reservoirs", "older-scan"]
    assert "broken-run" not in ids(listing(client, q="reservoir"))
    assert ids(listing(client, q="reservoir", status="failed")) == ["broken-run"]


def test_search_finds_a_word_of_a_finding_in_any_language(client):
    assert ids(listing(client, q="greenness")) == [EXAMPLE["id"]]  # second finding of the example
    assert ids(listing(client, q="hotter")) == [EXAMPLE["id"]]      # English finding
    assert ids(listing(client, q="caliente")) == [EXAMPLE["id"]]    # Spanish finding
    assert ids(listing(client, q="reservoir")) == ["valencia-reservoirs"]
    assert ids(listing(client, q="superficie")) == ["older-scan"]


def test_search_covers_title_and_summary_ignores_accents_and_prefixes(client):
    assert ids(listing(client, q="gigafactoria")) == [EXAMPLE["id"]]   # title "gigafactoría", without the accent
    assert ids(listing(client, q="Treinta")) == ["valencia-reservoirs"]  # Spanish summary
    assert ids(listing(client, q="valen")) == ["valencia-reservoirs"]    # a prefix while typing
    assert ids(listing(client, q="heat scan")) == ["older-scan"]          # every word must be there


def test_odd_queries_never_break_the_search(client):
    for q in ['"', "*", "AND OR NOT", "a(b", "NEAR(", "'; DROP TABLE x; --", "   ", "ü", "{r-s5-change.value}"]:
        listing(client, q=q)
    assert ids(listing(client, q="   ")) == ids(listing(client))


def test_filters_by_design_variable_and_featured(client):
    assert ids(listing(client, design="trend")) == ["valencia-reservoirs"]
    assert ids(listing(client, variable="ndvi")) == [EXAMPLE["id"]]
    assert ids(listing(client, variable="lst")) == [EXAMPLE["id"], "older-scan"]
    assert ids(listing(client, featured="true")) == [EXAMPLE["id"]]
    assert ids(listing(client, featured="false")) == ["valencia-reservoirs", "older-scan"]
    assert ids(listing(client, design="trend", q="water")) == ["valencia-reservoirs"]
    assert ids(listing(client, limit=1)) == [EXAMPLE["id"]]


def test_bad_parameters_are_refused(client):
    for params in ({"limit": 0}, {"limit": 100000}, {"design": "nonsense"}, {"status": "lost"},
                   {"featured": "maybe"}):
        response = client.get("/api/analyses", params=params)
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "invalid_request"


def test_new_and_changed_analyses_are_seen(client):
    assert "late" not in ids(listing(client))
    running = analysis("late", "2026-11-18T09:00:00Z", "event", ["ndvi"], {"en": "Late flood"},
                       {"en": "Flooded fields."}, status="running")
    save(running)  # a new folder: the index notices the folder set changed
    [late] = listing(client, design="event")
    assert late["id"] == "late" and late["status"] == "running"
    finished = {**running, "status": "finished", "featured": True}
    folder.write_analysis("late", finished)
    index.record(finished)  # what the executor calls when a run ends
    assert ids(listing(client))[:2] == ["late", EXAMPLE["id"]]


def test_unreadable_folders_are_skipped(client, data_dir):
    (data_dir / "analyses" / "half-written").mkdir()
    (data_dir / "analyses" / "corrupt").mkdir()
    (data_dir / "analyses" / "corrupt" / "analysis.json").write_text("{not json", encoding="utf-8")
    assert ids(listing(client)) == [EXAMPLE["id"], "valencia-reservoirs", "older-scan"]


def test_nothing_of_the_index_is_written_to_disk(client, data_dir):
    before = sorted(p.relative_to(data_dir) for p in data_dir.rglob("*"))
    listing(client, q="reservoir")
    index.rebuild()
    after = sorted(p.relative_to(data_dir) for p in data_dir.rglob("*"))
    assert after == before


def test_startup_rebuilds_the_index(monkeypatch):
    seed_example.seed()
    calls = []
    real = index.rebuild
    monkeypatch.setattr(index, "rebuild", lambda: calls.append(1) or real())
    with TestClient(main.app) as client:
        assert calls == [1]
        assert ids(listing(client)) == [EXAMPLE["id"]]


@dataclass(frozen=True)
class Operation:
    id: str
    version: str
    fn: Callable


def stand_in(op_id: str) -> Callable:
    def fn(params, inputs, ctx):
        variable = params.get("variable") or next(r["variable"] for rs in inputs.values() for r in rs)
        source = next(v for v in CATALOG["variables"] if v["id"] == variable)["sources"][0]
        url = next(s for s in CATALOG["sources"] if s["id"] == source)["access"]["url"]
        return {"main": {"type": "table", "variable": variable, "data_class": "measured",
                         "table": {"columns": ["operation"], "rows": [[op_id]]},
                         "sources": [{"source": source, "url": url}],
                         "method": {"operation": op_id, "version": "1.0", "params": params}}}
    return fn


def test_a_run_appears_finished_when_it_ends(client):
    plan = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))
    registry = {o["id"]: Operation(o["id"], o["version"], stand_in(o["id"])) for o in CATALOG["operations"]}
    listing(client)
    analysis_id = executor.run(plan, catalog=CATALOG, registry=registry)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (folder.read_analysis(analysis_id) or {}).get("status") != "finished":
        time.sleep(0.02)
    [item] = [i for i in listing(client) if i["id"] == analysis_id]
    assert item["status"] == "finished" and item["design"] == "impact"
