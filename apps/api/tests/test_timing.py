"""Time forecast of a plan (executor/timing.py and POST /api/plans/estimate). Synthetic telemetry, no network."""
import json

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog, main, seed_example
from hup_api.executor import timing
from hup_api.library import folder
from hup_data import paths, store

CATALOG = {"sources": [{"id": "nasa-power", "name": "NASA POWER", "version": "2", "access": {"mode": "api"}},
                       {"id": "new-source", "name": "New", "version": "1", "access": {"mode": "api"}}]}


def read(step_id, source, depends_on=()):
    return {"id": step_id, "type": "ingest", "operation": "read_series", "version": "1.2",
            "params": {"variable": "air_temperature"}, "depends_on": list(depends_on), "sources": [source]}


def plan(*steps):
    return {"zone": {"id": "z", "name": "Z", "bbox": [0, 0, 1, 1]},
            "period": {"start": "2013-01-01", "end": "2025-12-31"}, "steps": list(steps)}


def line(seconds, source="nasa-power", origins=("remote",), kind="read_series"):
    return json.dumps({"analysis_id": "a", "step": "s1", "kind": kind, "variable": "air_temperature",
                       "sources": [source], "origins": list(origins), "outcome": "ok", "seconds": seconds})


@pytest.fixture
def telemetry(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    monkeypatch.setattr(timing, "TELEMETRY", timing.Telemetry())
    file = paths.requests_log().with_name("data-reads.jsonl")
    file.parent.mkdir(parents=True, exist_ok=True)

    def write(*lines):
        with open(file, "a", encoding="utf-8") as handle:
            handle.write("".join(text + "\n" for text in lines))
    return write


def test_a_measured_key_uses_the_median_and_quartiles_and_skips_broken_lines(telemetry):
    telemetry(line(1.0), "not json", "", '{"kind": "read_series"}', line("x"), line(2.0),
              json.dumps(["a", "list"]), line(10.0), line(99.0, kind="derived_result"))
    step = timing.estimate(plan(read("s1", "nasa-power")), CATALOG)["steps"][0]
    assert (step["basis"], step["n"], step["expected_origin"], step["source"]) == ("measured", 3, "cold", "nasa-power")
    assert (step["seconds"], step["low_seconds"], step["high_seconds"]) == (2.0, 1.5, 6.0)
    assert step["source_name"] == {"en": "NASA POWER", "es": "NASA POWER"} and step["kind"] == "read"


def test_the_summary_follows_the_file_as_it_grows(telemetry):
    telemetry(line(1.0), line(1.0))
    assert timing.estimate(plan(read("s1", "nasa-power")), CATALOG)["steps"][0]["basis"] == "prior"
    telemetry(line(4.0))
    step = timing.estimate(plan(read("s1", "nasa-power")), CATALOG)["steps"][0]
    assert step["basis"] == "measured" and step["n"] == 3 and step["seconds"] == 1.0


def test_an_unknown_source_falls_back_to_the_prior(telemetry):
    telemetry(line(1.0), line(1.0), line(1.0))
    got = timing.estimate(plan(read("s1", "new-source")), CATALOG)
    step = got["steps"][0]
    assert step["basis"] == "prior" and step["n"] == 0 and step["seconds"] == timing.PRIORS["*", "*", "cold"][0]
    assert got["basis"] == "prior"


def test_a_step_already_stored_is_near_zero(telemetry):
    telemetry(line(5.0), line(5.0), line(5.0))
    the_plan = plan(read("s1", "nasa-power"))
    fp = timing._fingerprints(the_plan, CATALOG)["s1"]
    store.put_derived(fp, {"results": {}})
    step = timing.estimate(the_plan, CATALOG)["steps"][0]
    assert step["expected_origin"] == "derived" and step["seconds"] < 0.5


def test_parallel_reads_add_up_as_the_critical_path_not_the_sum(telemetry):
    telemetry(line(2.0), line(2.0), line(2.0))
    compute = {"id": "s3", "type": "compute", "operation": "trend", "version": "1.0", "params": {},
               "depends_on": ["s1", "s2"]}
    write = {"id": "s4", "type": "write", "operation": "write_findings", "version": "1.0", "params": {},
             "depends_on": ["s3"]}
    got = timing.estimate(plan(read("s1", "nasa-power"), read("s2", "new-source"), compute, write), CATALOG)
    by_id = {step["id"]: step for step in got["steps"]}
    longest = by_id["s2"]["seconds"] + by_id["s3"]["seconds"] + by_id["s4"]["seconds"]
    assert got["total_seconds"] == pytest.approx(longest, abs=0.01)
    assert got["total_seconds"] < sum(step["seconds"] for step in got["steps"])
    assert got["low_seconds"] <= got["total_seconds"] <= got["high_seconds"] and got["basis"] == "mixed"
    assert by_id["s4"]["kind"] == "write" and by_id["s4"]["seconds"] == timing.WRITE_OFF[0]  # no provider


def test_the_forecast_line_compares_estimate_and_actual(telemetry):
    timing.record("a", {"total_seconds": 10.0, "low_seconds": 5.0, "high_seconds": 20.0, "basis": "prior"}, 12.3456,
                  "finished")
    timing.record("b", None, 1.0, "finished")  # no forecast, no line
    lines = paths.requests_log().with_name("forecasts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["actual_seconds"] == 12.346


def test_general_scan_budgets_less_time_for_the_writer_than_an_event(monkeypatch, telemetry):
    from hup_api.writer.llm import SCAN_TIME_LIMIT

    monkeypatch.setenv("HUP_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HUP_OFFLINE", "0")
    write = {"id": "s1", "type": "write", "operation": "write_findings", "version": "1.0", "params": {}}
    scan = {**plan(write), "design": "scan"}
    write_step = timing.estimate(scan, CATALOG)["steps"][0]
    assert write_step["high_seconds"] == SCAN_TIME_LIMIT
    assert timing.WRITE_OFF[0] < write_step["seconds"] < timing.WRITE[0]
    scan["answer"] = {"decided": [
        {"field": "milestone", "text": {"en": "Date unknown"}},
        {"field": "event", "text": {"en": "The flood"}},
    ]}
    assert timing.estimate(scan, CATALOG)["steps"][0]["seconds"] == timing.WRITE[0]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(timing, "TELEMETRY", timing.Telemetry())
    return TestClient(main.app), seed_example.seed()


def test_the_route_answers_the_contract_and_rejects_bad_bodies(client):
    client, example = client
    saved = json.loads((folder.directory(example) / "plan.json").read_text(encoding="utf-8"))
    versions = {operation["id"]: operation["version"] for operation in catalog.load()["operations"]}
    for step in saved["steps"]:  # the seeded example predates some operation versions
        step["version"] = versions.get(step["operation"], step["version"])
    response = client.post("/api/plans/estimate", json={"plan": saved})
    assert response.status_code == 200, response.text
    got = response.json()
    assert set(got) == {"total_seconds", "low_seconds", "high_seconds", "basis", "steps"}
    assert [step["id"] for step in got["steps"]] == [step["id"] for step in saved["steps"]]
    keys = {"id", "seconds", "low_seconds", "high_seconds", "basis", "n", "kind", "source", "source_name",
            "expected_origin"}
    assert all(set(step) == keys for step in got["steps"])
    assert got["low_seconds"] <= got["total_seconds"] <= got["high_seconds"]
    for body in ({"other": {}}, {"plan": "x"}):
        bad = client.post("/api/plans/estimate", json=body)
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
        assert {"en", "es"} <= set(bad.json()["error"]["text"])
    broken = client.post("/api/plans/estimate", json={"plan": {"steps": "x"}})
    assert broken.status_code == 422 and broken.json()["error"]["code"] == "invalid_plan"
