"""B5: two wordings of one question give one analysis, and every request leaves one anonymous log line."""
import copy
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import contracts, executor, main, requests_log, seed_example
from hup_api.library import folder, plan_cache
from hup_data import paths

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
PLAN = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))
EXAMPLE_ANALYSIS = json.loads((EXAMPLES / "analysis.json").read_text(encoding="utf-8"))
EXAMPLE_ID = EXAMPLE_ANALYSIS["id"]


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(catalog_module, "load", lambda *args, **kwargs: copy.deepcopy(CATALOG))
    return tmp_path


@pytest.fixture
def client():
    return TestClient(main.app)


def validate(client, plan, **headers) -> dict:
    response = client.post("/api/plans/validate", json={"plan": plan}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"valid", "errors", "plan", "plan_fingerprint", "existing_analysis"}
    return body


def reworded(plan: dict) -> dict:
    """The same question asked another way: other text, language, ids, answer text and time estimates."""
    other = copy.deepcopy(plan)
    other["id"] = "plan-another-wording"
    other["request"] = {"text": "What changed near the Sagunt battery plant?", "language": "en", "origin": "form"}
    other["answer"]["limits"] = [{"en": "Soil moisture needs a package that is not here."}]
    for step in other["steps"]:
        if "data" in step:
            step["data"] = {"origin": step["data"]["origin"]}
    return other


def log_lines(data_dir) -> list[dict]:
    file = data_dir / "logs" / "requests.jsonl"
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()] if file.is_file() else []


# fingerprint and normalisation ----------------------------------------------------------------------------------


def test_example_plan_keeps_its_contract_fingerprint(client):
    body = validate(client, PLAN)
    assert body["valid"] is True and body["errors"] == []
    assert body["plan_fingerprint"] == EXAMPLE_ANALYSIS["plan_fingerprint"]
    assert body["existing_analysis"] is None
    assert contracts.errors("plan", body["plan"]) == []


def test_two_wordings_give_the_same_existing_analysis(client):
    seed_example.seed()
    first, second = validate(client, PLAN), validate(client, reworded(PLAN))
    assert first["plan_fingerprint"] == second["plan_fingerprint"]
    assert first["existing_analysis"] == second["existing_analysis"] == EXAMPLE_ID


def test_another_period_is_another_analysis(client):
    seed_example.seed()
    other = reworded(PLAN)
    other["period"]["end"] = "2024-09-30"
    body = validate(client, other)
    assert body["valid"] is True
    assert body["plan_fingerprint"] != EXAMPLE_ANALYSIS["plan_fingerprint"]
    assert body["existing_analysis"] is None


def test_periods_are_written_in_one_format(client):
    seed_example.seed()
    other = copy.deepcopy(PLAN)
    other["period"] = {"start": "2013-06", "end": "2025-09", "months": [9, 7, 6, 8, 6]}
    body = validate(client, other)
    assert body["plan"]["period"] == PLAN["period"]
    assert body["existing_analysis"] == EXAMPLE_ID

    years = copy.deepcopy(PLAN)
    years["period"] = {"start": "2013", "end": "2025"}
    assert plan_cache.normalize(years, CATALOG)["period"] == {"start": "2013-01-01", "end": "2025-12-31"}
    every_month = copy.deepcopy(PLAN)
    every_month["period"]["months"] = list(range(12, 0, -1))
    assert "months" not in plan_cache.normalize(every_month, CATALOG)["period"]


def test_default_params_are_made_explicit():
    catalog = copy.deepcopy(CATALOG)
    read_series = next(o for o in catalog["operations"] if o["id"] == "read_series")
    read_series["params"]["properties"]["max_cloud_pct"]["default"] = 20
    explicit = copy.deepcopy(PLAN)
    implicit = copy.deepcopy(PLAN)
    for step in implicit["steps"]:
        step["params"].pop("max_cloud_pct", None)
    for step in explicit["steps"]:
        if step["operation"] == "read_series":
            step["params"]["max_cloud_pct"] = 20
    normalized = plan_cache.normalize(implicit, catalog)
    assert [s["params"].get("max_cloud_pct") for s in normalized["steps"][:3]] == [20, 20, 20]
    assert plan_cache.fingerprint(implicit, catalog) == plan_cache.fingerprint(explicit, catalog)
    other = copy.deepcopy(explicit)
    other["steps"][0]["params"]["max_cloud_pct"] = 10
    assert plan_cache.fingerprint(other, catalog) != plan_cache.fingerprint(explicit, catalog)
    assert implicit["steps"][2]["params"] == {"variable": "soil_moisture"}  # the caller's plan is not changed


def test_only_finished_analyses_count(client):
    seed_example.seed()
    for status in ("failed", "running"):
        analysis = folder.read_analysis(EXAMPLE_ID)
        analysis["status"] = status
        folder.write_analysis(EXAMPLE_ID, analysis)
        assert validate(client, PLAN)["existing_analysis"] is None
    assert plan_cache.existing(EXAMPLE_ANALYSIS["plan_fingerprint"]) is None


def test_an_invalid_plan_says_why_without_a_fingerprint(client):
    broken = copy.deepcopy(PLAN)
    broken["variables"] = ["no_such_variable"]
    body = validate(client, broken)
    assert body["valid"] is False and any("no_such_variable" in e for e in body["errors"])
    assert body["plan_fingerprint"] is None and body["existing_analysis"] is None
    response = client.post("/api/plans/validate", json={"plan": "not a plan"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"


# the cache follows the analyses as they finish ---------------------------------------------------------------


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


def wait_finished(analysis_id, timeout=20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        analysis = folder.read_analysis(analysis_id)
        if analysis and analysis["status"] in ("finished", "failed"):
            return analysis
        time.sleep(0.02)
    raise AssertionError("the analysis did not finish")


def test_a_finished_run_is_found_without_a_restart(client):
    assert validate(client, PLAN)["existing_analysis"] is None  # the cache is built, and empty
    registry = {o["id"]: Operation(o["id"], o["version"], stand_in(o["id"])) for o in CATALOG["operations"]}
    analysis_id = executor.run(reworded(PLAN), catalog=CATALOG, registry=registry)
    assert wait_finished(analysis_id)["status"] == "finished"
    assert validate(client, PLAN)["existing_analysis"] == analysis_id


def test_the_cache_is_rebuilt_from_the_folders():
    seed_example.seed()
    assert plan_cache.rebuild() >= 1
    assert plan_cache.existing(EXAMPLE_ANALYSIS["plan_fingerprint"]) == EXAMPLE_ID
    assert plan_cache.lookup(reworded(PLAN), CATALOG) == (
        plan_cache.normalize(reworded(PLAN), CATALOG), EXAMPLE_ANALYSIS["plan_fingerprint"], EXAMPLE_ID)


def test_startup_rebuilds_the_cache(monkeypatch):
    seed_example.seed()
    calls = []
    real = plan_cache.rebuild
    monkeypatch.setattr(plan_cache, "rebuild", lambda: calls.append(1) or real())
    with TestClient(main.app):
        assert calls == [1]
        assert plan_cache._state["by_fp"][EXAMPLE_ANALYSIS["plan_fingerprint"]] == EXAMPLE_ID


def test_post_analyses_runs_the_normalised_plan(client, monkeypatch):
    started = []
    monkeypatch.setattr(executor, "run", lambda plan: started.append(plan) or "a-1")
    other = copy.deepcopy(PLAN)
    other["period"] = {"start": "2013-06", "end": "2025-09", "months": [6, 7, 8, 9]}
    assert client.post("/api/analyses", json={"plan": other}).json() == {"analysis_id": "a-1"}
    assert started[0]["period"] == PLAN["period"]


# request log --------------------------------------------------------------------------------------------------

IDENTIFYING = {
    "X-Forwarded-For": "203.0.113.77",
    "X-Real-IP": "198.51.100.23",
    "User-Agent": "IdentifyingBrowser/9.9 (person-laptop)",
    "Cookie": "session=cookie-secret-4242",
    "Authorization": "Bearer token-secret-8686",
    "Referer": "https://example.org/private-page",
    "X-User-Email": "someone@example.org",
}


def test_each_request_leaves_one_line_with_what_was_asked(client, data_dir):
    seed_example.seed()
    validate(client, reworded(PLAN))
    [line] = log_lines(data_dir)
    assert set(line) == set(requests_log.FIELDS)
    assert line["kind"] == "validate"
    assert line["language"] == "en" and line["text"] == "What changed near the Sagunt battery plant?"
    assert line["place"] == {"id": "parc-sagunt-ii", "name": "Parc Sagunt II", "bbox": PLAN["zone"]["bbox"]}
    assert line["plan_fingerprint"] == EXAMPLE_ANALYSIS["plan_fingerprint"]
    assert line["answer_level"] == "with_limits"
    assert line["cached"] is True and line["analysis_id"] == EXAMPLE_ID
    assert set(line["phases"]) == {"validate"} and line["phases"]["validate"] >= 0
    assert line["llm_calls"] == []
    assert line["absences"] is None  # not known before the run: absent, not an empty list
    assert line["ts"].endswith("Z")


def test_the_log_holds_nothing_that_identifies_a_person(client, data_dir):
    validate(client, PLAN, **IDENTIFYING)
    client.post("/api/analyses", json={"plan": {"broken": True}}, headers=IDENTIFYING)
    text = (data_dir / "logs" / "requests.jsonl").read_text(encoding="utf-8")
    assert text.strip()
    for name, value in IDENTIFYING.items():
        assert value not in text and name.lower() not in text.lower()
    for fragment in ("203.0.113", "198.51.100", "testclient", "127.0.0.1", "cookie", "bearer", "@example.org"):
        assert fragment not in text.lower()
    for line in log_lines(data_dir):
        assert set(line) == set(requests_log.FIELDS)


def test_record_keeps_only_the_listed_fields(data_dir):
    line = requests_log.record(kind="plan", language="es", text="¿Qué pasa?",
                               place={"name": "X", "bbox": [0, 0, 1, 1], "geometry": {"type": "Point"}},
                               llm_calls=[{"task": "plan", "model": "m", "input_tokens": 10, "output_tokens": 5,
                                           "cost_usd": 0.001, "prompt": "secret prompt", "api_key": "k"}],
                               phases={"assistant": 1.5})
    assert line["place"] == {"id": None, "name": "X", "bbox": [0, 0, 1, 1]}
    assert line["llm_calls"] == [{"task": "plan", "model": "m", "input_tokens": 10, "output_tokens": 5,
                                  "cost_usd": 0.001}]
    assert line["plan_fingerprint"] is None and line["analysis_id"] is None and line["cached"] is None
    assert log_lines(data_dir) == [line]
    with pytest.raises(TypeError):
        requests_log.record(kind="plan", ip="203.0.113.77")  # there is no field for it


def test_a_log_that_cannot_be_written_does_not_break_the_request(client, monkeypatch):
    def broken(line):
        raise OSError("disk full")
    monkeypatch.setattr(requests_log, "write_line", broken)
    assert validate(client, PLAN)["valid"] is True


def test_concurrent_lines_stay_whole(data_dir):
    threads = [threading.Thread(target=requests_log.record, kwargs={"kind": "validate", "text": f"q{i}" * 200})
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(line["text"] for line in log_lines(data_dir)) == sorted(f"q{i}" * 200 for i in range(20))
