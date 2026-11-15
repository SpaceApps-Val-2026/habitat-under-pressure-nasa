"""B2: a valid plan runs step by step, emits its events, reuses what was computed and leaves the analysis saved.

Operations here are stand-ins registered in a test-local registry with the shape of interfaces.md, section 5.
They return tables without figures: these tests check the executor, not any science.
"""
import copy
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import contracts, executor, main
from hup_api.assistant import templates
from hup_api.executor import recovery
from hup_api.library import folder, index, plan_cache
from hup_data import cancellation, fingerprint, paths, store

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
PLAN = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))
EXAMPLE_ANALYSIS = json.loads((EXAMPLES / "analysis.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Operation:
    id: str
    version: str
    fn: Callable


class StandIns:
    """A registry of stand-in operations that records every call, in order, from any thread."""

    def __init__(self, fail=None, barrier=None):
        self.fail = fail or (lambda op, params: False)
        self.barrier = barrier
        self.log = []
        self.lock = threading.Lock()
        self.registry = {o["id"]: Operation(o["id"], o["version"], self.make(o["id"])) for o in CATALOG["operations"]}

    def calls(self):
        return [entry for entry in self.log if entry[0] == "start"]

    def make(self, op_id):
        def fn(params, inputs, ctx):
            variable = params.get("variable") or next(r["variable"] for rs in inputs.values() for r in rs)
            with self.lock:
                self.log.append(("start", op_id, variable))
            ctx.progress(0.5, "halfway")
            if self.barrier and op_id == "read_series":
                self.barrier.wait()
            if self.fail(op_id, params):
                raise RuntimeError("stand-in failure with a secret path C:/secret")
            source = next(v for v in CATALOG["variables"] if v["id"] == variable)["sources"][0]
            url = next(s for s in CATALOG["sources"] if s["id"] == source)["access"]["url"]
            with self.lock:
                self.log.append(("end", op_id, variable))
            return {"main": {"type": "table", "variable": variable, "data_class": "measured",
                             "table": {"columns": ["operation"], "rows": [[op_id]]},
                             "sources": [{"source": source, "url": url}],
                             "method": {"operation": op_id, "version": "1.0", "params": params}}}
        return fn


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def no_writer(monkeypatch):
    """Write and review steps call the writer and reviewer modules, not the registry (test_writing_steps.py covers
    them). Here they point to a module that does not exist, so these tests never depend on the real ones."""
    monkeypatch.setattr(executor.runner, "WRITER", "hup_api_tests_no_writer")
    monkeypatch.setattr(executor.runner, "REVIEWER", "hup_api_tests_no_reviewer")


COMPUTED = [s for s in PLAN["steps"] if s["type"] in ("ingest", "compute")]


def wait_finished(analysis_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        analysis = folder.read_analysis(analysis_id)
        if analysis and analysis["status"] in ("finished", "failed"):
            return analysis
        time.sleep(0.02)
    raise AssertionError(f"analysis {analysis_id} did not finish")


def run(plan=PLAN, standins=None):
    standins = standins or StandIns()
    analysis_id = executor.run(copy.deepcopy(plan), catalog=CATALOG, registry=standins.registry)
    return analysis_id, wait_finished(analysis_id), standins


# validate_plan ---------------------------------------------------------------------------------------------------

def test_example_plan_is_valid():
    assert executor.validate_plan(PLAN, CATALOG) == []


def broken(change):
    plan = copy.deepcopy(PLAN)
    change(plan)
    return executor.validate_plan(plan, CATALOG)


@pytest.mark.parametrize(("change", "expected"), [
    (lambda p: p.pop("zone"), "zone"),
    (lambda p: p["steps"][0].update(operation="no_such_op"), "no_such_op"),
    (lambda p: p["steps"][0].update(type="compute"), "type"),
    (lambda p: p["steps"][0].update(version="9.9"), "version"),
    (lambda p: p["steps"][3]["params"].pop("reference_km"), "reference_km"),
    (lambda p: p["steps"][3].update(depends_on=["s99"]), "s99"),
    (lambda p: p["steps"][0].update(depends_on=["s7"]), "cycle"),
    (lambda p: p["steps"][1].update(id="s1"), "duplicate"),
    (lambda p: p["variables"].append("no_such_variable"), "no_such_variable"),
    (lambda p: p["steps"][0].update(sources=["no-such-source"]), "no-such-source"),
])
def test_invalid_plans_are_reported(change, expected):
    errors = broken(change)
    assert errors and any(expected in e for e in errors), errors


# 1. events -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("design", "months", "labels"), [
    ("trend", [], ("Annual mean air temperature", "Temperatura media anual del aire")),
    ("trend", [6, 7, 8], ("Mean air temperature by year", "Temperatura media del aire por año")),
    ("state", [], ("Air temperature, monthly mean", "Temperatura del aire, media mensual")),
])
def test_analysis_title_uses_the_aggregation_and_short_place_name(design, months, labels):
    catalog = catalog_module.load()
    place = {**PLAN["zone"], "name": "Alzira, Ribera Alta, València, España",
             "source": "OpenStreetMap (Nominatim), relation 345244"}
    period = {**PLAN["period"], "months": months}
    plan = templates.template(design, place, period, ["air_temperature"], catalog)
    run = executor.runner.Run("title-test", plan, catalog, {})
    assert run.title() == {lang: f"Alzira: {label}" for lang, label in zip(("en", "es"), labels, strict=True)}
    assert plan["zone"]["name"] == place["name"]


def test_example_plan_runs_and_its_events_validate():
    analysis_id, analysis, _ = run()
    events = folder.read_events(analysis_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "analysis_finished" and events[-1]["data"]["status"] == "finished"
    assert sum(e["type"] == "analysis_finished" for e in events) == 1
    for event in events:
        assert contracts.errors("event", event) == [], event
        assert event["analysis"] == analysis_id
    started = [e["step"] for e in events if e["type"] == "step_started"]
    assert sorted(started) == sorted(s["id"] for s in PLAN["steps"])
    assert {e["type"] for e in events} >= {"step_started", "progress", "partial_result", "step_finished"}

    assert contracts.errors("analysis", analysis) == []
    assert analysis["status"] == "finished"
    assert analysis["plan_fingerprint"] == fingerprint.plan_fingerprint(PLAN)
    assert analysis["plan_fingerprint"] == EXAMPLE_ANALYSIS["plan_fingerprint"]
    assert analysis["fingerprint"]["catalog_version"] == CATALOG["version"]
    assert folder.read_plan(analysis_id) == PLAN

    results = folder.read_results(analysis_id)
    assert [r["id"] for r in results] == analysis["results"]
    for result in results:
        assert contracts.errors("result", result) == []
        assert result["analysis"] == analysis_id and result["id"] == f"r-{result['step']}-main"
    partial = {e["data"]["result"] for e in events if e["type"] == "partial_result"}
    assert partial == set(analysis["results"])


def test_post_analyses_returns_202_and_validates(monkeypatch):
    standins = StandIns()
    monkeypatch.setattr(executor.runner, "default_registry", lambda: standins.registry)
    monkeypatch.setattr(catalog_module, "load", lambda *a, **k: CATALOG)
    client = TestClient(main.app)
    response = client.post("/api/analyses", json={"plan": PLAN})
    assert response.status_code == 202
    analysis_id = response.json()["analysis_id"]
    assert wait_finished(analysis_id)["status"] == "finished"

    bad = copy.deepcopy(PLAN)
    bad["steps"][0]["operation"] = "no_such_op"
    response = client.post("/api/analyses", json={"plan": bad})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_plan"
    assert "no_such_op" in response.json()["error"]["text"]["en"]
    assert client.post("/api/analyses", json={}).status_code == 422


# 2. same plan, same fingerprints, no second computation -------------------------------------------------------

def test_second_run_reuses_every_step():
    first_id, first, standins = run()
    calls = len(standins.calls())
    assert calls == len(COMPUTED)  # write and review steps are not registry operations
    second_id, second, _ = run(standins=standins)
    assert len(standins.calls()) == calls  # no operation was called again
    assert second_id != first_id
    assert second["plan_fingerprint"] == first["plan_fingerprint"]
    assert second["fingerprint"] == first["fingerprint"]
    first_fps = {r["step"]: r["fingerprint"] for r in folder.read_results(first_id)}
    second_results = folder.read_results(second_id)
    assert {r["step"]: r["fingerprint"] for r in second_results} == first_fps
    assert all(r["analysis"] == second_id for r in second_results)
    events = folder.read_events(second_id)
    reads = [e for e in events if e["type"] == "data_read"]
    assert {e["step"] for e in reads} == {s["id"] for s in COMPUTED}
    assert all(e["data"]["origin"] == "derived" for e in reads)
    assert all(e["data"]["n_items"] == 0 for e in reads)  # the stand-ins never called a reader
    for event in events:
        assert contracts.errors("event", event) == []


# 3. concurrency ------------------------------------------------------------------------------------------------

def test_independent_steps_run_at_the_same_time_and_dependents_wait(monkeypatch):
    # The three reading steps only get past the barrier if all three are running at once.
    standins = StandIns(barrier=threading.Barrier(3, timeout=10))
    put_derived = store.put_derived

    def write_to_prepared_cache(fp, result, files=None):
        assert paths.derived().is_dir()
        return put_derived(fp, result, files)

    monkeypatch.setattr(store, "put_derived", write_to_prepared_cache)
    analysis_id, analysis, _ = run(standins=standins)
    assert analysis["status"] == "finished"
    assert not [e for e in folder.read_events(analysis_id) if e["type"] == "error"]
    log = standins.log
    for variable in ("ndvi", "lst"):
        assert log.index(("end", "read_series", variable)) < log.index(("start", "baci_contrast", variable))
    seq = {(e["type"], e.get("step")): e["seq"] for e in folder.read_events(analysis_id)}
    assert seq[("step_started", "s6")] > max(seq[("step_finished", "s4")], seq[("step_finished", "s5")])
    assert seq[("step_started", "s7")] > seq[("step_finished", "s6")]


def test_cancel_releases_capacity_and_stops_publication_while_readers_are_busy(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    standins = StandIns()
    fake = SimpleNamespace()

    def blocked(variable, zone, period):
        entered.set()
        assert release.wait(20)
        cancellation.checkpoint()
        raise AssertionError("the cancelled reader should not continue")

    fake.read_series = blocked
    standins.registry["read_series"] = Operation("read_series", "1.0", lambda p, i, c: c.data.read_series(
        p["variable"], c.zone, c.period))
    monkeypatch.setenv("HUP_MAX_RUNNING_ANALYSES", "1")
    monkeypatch.setenv("HUP_SPLIT_APPENDS", "0")
    monkeypatch.setattr(executor.runner, "_readers", lambda: fake)
    monkeypatch.setattr(executor.runner, "default_registry", lambda: standins.registry)
    monkeypatch.setattr(catalog_module, "load", lambda *a, **k: CATALOG)
    client = TestClient(main.app)
    started = client.post("/api/analyses", json={"plan": PLAN})
    assert started.status_code == 202
    first = started.json()["analysis_id"]
    try:
        assert entered.wait(10)
        assert executor.runner.running() == 1
        assert first in {item["id"] for item in index.query()}
        assert client.post("/api/analyses", json={"plan": PLAN}).status_code == 429
        url = f"/api/analyses/{first}"
        cancelled = client.post(f"{url}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json() == {"analysis_id": first, "status": "cancelled"}
        repeat = client.post(f"{url}/cancel")
        assert repeat.status_code == 200 and repeat.json() == cancelled.json()
        assert client.get(url).json()["status"] == "cancelled"
        assert contracts.errors("analysis", client.get(url).json()) == []
        assert client.get(f"{url}/results").json() == []
        assert first not in {item["id"] for item in index.query()}
        assert plan_cache.existing(folder.read_analysis(first)["plan_fingerprint"]) is None
        assert executor.runner.running() == 0 and executor.runner.active(first)
        assert recovery.recover_interrupted() == []
        assert client.post("/api/analyses/unknown/cancel").status_code == 404

        events = folder.read_events(first)
        assert events[-1]["type"] == "analysis_cancelled"
        assert events[-1]["data"] == {"status": "cancelled"}
        assert contracts.errors("event", events[-1]) == []
        assert {event["type"] for event in events} <= {"analysis_cancelled", "step_started"}
        assert all(event.get("step") in {None, "s1", "s2", "s3"} for event in events)
        stream = client.get(f"{url}/events", headers={"Last-Event-ID": str(events[-1]["seq"] - 1)})
        assert stream.status_code == 200 and "event: analysis_cancelled" in stream.text
        assert not client.get(f"{url}/events", headers={"Last-Event-ID": str(events[-1]["seq"])}).text

        fast = StandIns()
        monkeypatch.setattr(executor.runner, "default_registry", lambda: fast.registry)
        started = client.post("/api/analyses", json={"plan": PLAN})
        assert started.status_code == 202
        second = started.json()["analysis_id"]
        assert wait_finished(second)["status"] == "finished"
        finished = client.post(f"/api/analyses/{second}/cancel")
        assert finished.status_code == 200 and finished.json()["status"] == "finished"
        assert executor.runner.active(first)
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while executor.runner.active(first) and time.monotonic() < deadline:
            time.sleep(0.01)
    assert not executor.runner.active(first)
    assert folder.read_events(first) == events and folder.read_results(first) == []
    lines = (paths.data_dir() / "logs" / "data-reads.jsonl").read_text(encoding="utf-8").splitlines()
    cancelled_reads = [line for line in map(json.loads, lines)
                       if line["analysis_id"] == first and line["kind"] == "read_series"]
    assert cancelled_reads
    for line in cancelled_reads:
        assert line["outcome"] == "cancelled" and 0 <= line["stop_seconds"] <= line["seconds"]
        assert line["sources"] in (["landsat-c2-l2"], ["smap-l3"])


def test_recovery_preserves_cancellation_recorded_before_the_state_write():
    job = executor.runner.Run("recovery-cancelled", PLAN, CATALOG, {})
    job.prepare()
    job.cancel()
    folder.write_analysis(job.id, job.analysis("running"))
    assert recovery.recover_interrupted() == [job.id]
    assert folder.read_analysis(job.id)["status"] == "cancelled"
    assert [e["type"] for e in folder.read_events(job.id)] == ["analysis_cancelled"]
    assert recovery.recover_interrupted() == []


def test_completed_step_cache_survives_cancellation():
    standins = StandIns()
    original = standins.registry["read_series"]

    def read(params, inputs, ctx):
        if params["variable"] != "soil_moisture":
            signal = cancellation.signal.get()
            assert signal.wait(10)
            cancellation.checkpoint()
        return original.fn(params, inputs, ctx)

    standins.registry["read_series"] = Operation("read_series", "1.0", read)
    analysis_id = executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=standins.registry)
    try:
        deadline = time.monotonic() + 5
        while not (completed := folder.read_results(analysis_id)) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [result["step"] for result in completed] == ["s3"]
    finally:
        assert executor.runner.cancel(analysis_id) == (True, "cancelled")
        deadline = time.monotonic() + 5
        while executor.runner.active(analysis_id) and time.monotonic() < deadline:
            time.sleep(0.01)
    assert not executor.runner.active(analysis_id)
    assert folder.read_results(analysis_id) == completed
    assert store.get_derived(completed[0]["fingerprint"])["results"]["main"]["variable"] == "soil_moisture"


# 4. a failing operation -----------------------------------------------------------------------------------------

def test_a_failing_step_gives_an_error_and_absences_downstream():
    standins = StandIns(fail=lambda op, params: op == "baci_contrast" and params.get("variable") == "ndvi")
    analysis_id, analysis, _ = run(standins=standins)
    events = folder.read_events(analysis_id)
    for event in events:
        assert contracts.errors("event", event) == []
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    errors = [e for e in events if e["type"] == "error"]
    assert [e["step"] for e in errors] == ["s4"]
    assert "secret" not in json.dumps(events)  # the exception text never reaches the browser
    assert events[-1]["type"] == "analysis_finished" and events[-1]["data"]["status"] == "failed"
    assert analysis["status"] == "failed"
    assert contracts.errors("analysis", analysis) == []

    results = {r["step"]: r for r in folder.read_results(analysis_id)}
    for result in results.values():
        assert contracts.errors("result", result) == []
    for step in ("s6", "s7"):  # write and review leave findings, not results, and run on what exists
        assert step not in results
        assert any(e["type"] == "step_finished" and e.get("step") == step for e in events)
    for step in ("s1", "s2", "s3", "s5"):
        assert "absence" not in results[step]
    assert "s4" not in results
    assert ("start", "write_findings", "ndvi") not in standins.log


def test_a_result_that_does_not_validate_is_a_step_error():
    standins = StandIns()
    original = standins.registry["baci_contrast"]

    def bad(params, inputs, ctx):
        out = original.fn(params, inputs, ctx)
        out["main"]["data_class"] = "made_up"
        return out

    standins.registry["baci_contrast"] = Operation("baci_contrast", "1.0", bad)
    analysis_id, analysis, _ = run(standins=standins)
    errors = [e for e in folder.read_events(analysis_id) if e["type"] == "error"]
    assert sorted(e["step"] for e in errors) == ["s4", "s5"]
    assert {e["data"]["code"] for e in errors} == {"invalid_result"}
    assert analysis["status"] == "failed"
    assert not any(r["step"] in ("s4", "s5") for r in folder.read_results(analysis_id))


def test_a_missing_operation_is_a_step_error_not_a_hang():
    standins = StandIns()
    del standins.registry["baci_contrast"]
    analysis_id, analysis, _ = run(standins=standins)
    errors = [e for e in folder.read_events(analysis_id) if e["type"] == "error"]
    assert sorted((e["step"], e["data"]["code"]) for e in errors) == [("s4", "operation_missing"),
                                                                      ("s5", "operation_missing")]
    assert analysis["status"] == "failed"


# 5. nobody listening -------------------------------------------------------------------------------------------

def test_the_analysis_runs_with_nobody_listening(monkeypatch):
    gate = threading.Event()
    standins = StandIns()
    original = standins.registry["baci_contrast"]
    standins.registry["baci_contrast"] = Operation(
        "baci_contrast", "1.0", lambda p, i, c: (gate.wait(10), original.fn(p, i, c))[1])
    monkeypatch.setattr(executor.runner, "default_registry", lambda: standins.registry)
    monkeypatch.setattr(catalog_module, "load", lambda *a, **k: CATALOG)
    client = TestClient(main.app)
    analysis_id = client.post("/api/analyses", json={"plan": PLAN}).json()["analysis_id"]
    # The request is over and nobody has opened the stream: the run is still going, held at its contrasts.
    time.sleep(0.3)
    assert folder.read_analysis(analysis_id)["status"] == "running"
    assert folder.read_events(analysis_id)
    gate.set()
    assert wait_finished(analysis_id)["status"] == "finished"
    assert folder.read_events(analysis_id)[-1]["type"] == "analysis_finished"


def test_a_result_is_never_more_direct_than_its_variable_or_its_inputs():
    """A model does not come out as measured: the class is the least direct of operation, variable and inputs."""
    from hup_api.executor.runner import Outcome, Run

    runner = Run.__new__(Run)
    runner.variables = {"modelled": {"data_class": "reanalysis"}, "observed": {}}
    measured = Outcome("s1", True, "0" * 64, results=[{"data_class": "measured"}])
    projected = Outcome("s2", True, "0" * 64, results=[{"data_class": "extrapolated"}])
    assert runner.data_class("measured", "observed", [measured]) == "measured"
    assert runner.data_class("measured", "modelled", []) == "reanalysis"  # the variable decides a reading
    assert runner.data_class("inherit", "modelled", [measured]) == "reanalysis"
    assert runner.data_class("measured", "observed", [measured, projected]) == "extrapolated"
    assert runner.data_class("approximated", "modelled", [measured]) == "approximated"
