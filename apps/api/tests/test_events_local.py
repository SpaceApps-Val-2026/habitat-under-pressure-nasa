"""Cloud Run (R1): with HUP_EVENTS_LOCAL_DIR a running analysis keeps its events on local disk.

The data folder (a mounted bucket) gets whole copies of the file, at most once every FLUSH_SECONDS and once more at
the end; readers in the process see the local file while the run is alive. Unset, events are appended to the data
folder as before. Stand-in operations, no science, no network.
"""
import asyncio
import copy
import json
import threading
import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from hup_api import executor, main
from hup_api.executor import recovery
from hup_api.library import folder
from hup_data import paths

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
PLAN = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Operation:
    id: str
    version: str
    fn: object


def registry(gate: threading.Event | None = None, chatter: int = 0) -> dict:
    """Stand-ins for every catalog operation. `gate` holds the contrasts; `chatter` progress events per step."""
    def make(op_id):
        def fn(params, inputs, ctx):
            variable = params.get("variable") or next(r["variable"] for rs in inputs.values() for r in rs)
            for i in range(chatter):
                ctx.progress(i / max(chatter, 1), "")
                time.sleep(0.01)
            if gate is not None and op_id == "baci_contrast":
                gate.wait(10)
            source = next(v for v in CATALOG["variables"] if v["id"] == variable)["sources"][0]
            url = next(s for s in CATALOG["sources"] if s["id"] == source)["access"]["url"]
            return {"main": {"type": "table", "variable": variable, "data_class": "measured",
                             "table": {"columns": ["operation"], "rows": [[op_id]]},
                             "sources": [{"source": source, "url": url}],
                             "method": {"operation": op_id, "version": "1.0", "params": params}}}
        return fn
    return {o["id"]: Operation(o["id"], o["version"], make(o["id"])) for o in CATALOG["operations"]}


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HUP_EVENTS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("HUP_MAX_RUNNING_ANALYSES", raising=False)
    monkeypatch.setattr(executor.runner, "WRITER", "hup_api_tests_no_writer")
    monkeypatch.setattr(executor.runner, "REVIEWER", "hup_api_tests_no_reviewer")
    return tmp_path / "data"


@pytest.fixture
def local_dir(monkeypatch, tmp_path):
    local = tmp_path / "local-events"
    monkeypatch.setenv("HUP_EVENTS_LOCAL_DIR", str(local))
    return local


def wait_finished(analysis_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        analysis = folder.read_analysis(analysis_id)
        if analysis and analysis["status"] in ("finished", "failed"):
            return analysis
        time.sleep(0.02)
    raise AssertionError(f"analysis {analysis_id} did not finish")


def saved_events(analysis_id) -> list[dict]:
    """events.jsonl in the data folder, parsed strictly: every line whole."""
    raw = (folder.directory(analysis_id) / "events.jsonl").read_text(encoding="utf-8")
    assert raw == "" or raw.endswith("\n")
    return [json.loads(line) for line in raw.splitlines()]


def shape(events):
    return sorted((e["type"], e.get("step", "")) for e in events)


def run_to_end(**kwargs):
    analysis_id = executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=registry(**kwargs))
    return analysis_id, wait_finished(analysis_id)


# unset: as before ------------------------------------------------------------------------------------------------

def test_unset_appends_to_the_data_folder_as_before(tmp_path):
    gate = threading.Event()
    analysis_id = executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=registry(gate=gate))
    time.sleep(0.3)
    assert folder._live == {}  # nothing local
    live = saved_events(analysis_id)  # the data folder has the events while the run is going
    assert live and live == folder.read_events(analysis_id)
    gate.set()
    assert wait_finished(analysis_id)["status"] == "finished"
    events = saved_events(analysis_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "analysis_finished"


def test_set_and_unset_give_the_same_events(monkeypatch, tmp_path):
    plain_id, _ = run_to_end()
    plain = saved_events(plain_id)
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data-2"))  # a fresh store: nothing reused from the first run
    monkeypatch.setenv("HUP_EVENTS_LOCAL_DIR", str(tmp_path / "local"))
    local_id, _ = run_to_end()
    assert shape(plain) == shape(saved_events(local_id))
    assert list((tmp_path / "local").iterdir()) == []  # the local file is gone once the data folder has it all


# set: live, flushed, throttled -----------------------------------------------------------------------------------

def test_events_are_read_live_from_local_disk_and_flushed_at_the_end(local_dir):
    gate = threading.Event()
    analysis_id = executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=registry(gate=gate))
    time.sleep(0.3)
    assert analysis_id in folder._live
    local = local_dir / f"{analysis_id}.events.jsonl"
    assert local.is_file()
    live = folder.read_events(analysis_id)
    assert live and live[0]["type"] == "step_started"
    # The copy in the data folder catches up within a flush interval, and is always whole lines.
    time.sleep(folder.FLUSH_SECONDS + 0.5)
    copy_now = saved_events(analysis_id)
    assert copy_now == folder.read_events(analysis_id)[:len(copy_now)] and copy_now
    gate.set()
    assert wait_finished(analysis_id)["status"] == "finished"
    events = saved_events(analysis_id)
    assert events[-1]["type"] == "analysis_finished"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert folder.read_events(analysis_id) == events
    assert analysis_id not in folder._live and not local.exists()


def test_the_stream_reads_the_whole_run(local_dir):
    analysis_id, _ = run_to_end(chatter=5)

    async def collect():
        return [event async for event in folder.follow_events(analysis_id, 0, poll=0.01)]

    events = asyncio.run(collect())
    assert events == saved_events(analysis_id)
    client = TestClient(main.app)
    with client.stream("GET", f"/api/analyses/{analysis_id}/events", headers={"Last-Event-ID": "3"}) as stream:
        ids = [int(line[3:].strip()) for line in stream.iter_lines() if line.startswith("id:")]
    assert ids == list(range(4, len(events) + 1))


def test_copies_to_the_data_folder_are_throttled(local_dir, monkeypatch):
    monkeypatch.setattr(folder, "FLUSH_SECONDS", 0.25)
    copies = []
    original = folder.write_atomic

    def recording(path, data):
        if path.name == "events.jsonl":
            copies.append(time.monotonic())
        original(path, data)

    monkeypatch.setattr(folder, "write_atomic", recording)
    analysis_id, _ = run_to_end(chatter=15)  # dozens of events a second
    events = saved_events(analysis_id)
    assert len(events) > 3 * len(copies)  # many events, few copies
    gaps = [b - a for a, b in zip(copies, copies[1:], strict=False)]
    assert all(gap >= 0.25 - 0.02 for gap in gaps), gaps
    assert events[-1]["type"] == "analysis_finished"


# a dead instance ------------------------------------------------------------------------------------------------

def test_recovery_after_the_local_file_is_lost(local_dir, monkeypatch):
    """The process dies: the local file and the last second of events are gone, the copy is a little behind.
    The next start closes it as failed, with whole lines and seq going on from the copy."""
    monkeypatch.setattr(folder, "FLUSH_SECONDS", 1000.0)  # only the copy made by hand below
    job = executor.runner.Run("20261114-000000-dead01", copy.deepcopy(PLAN), CATALOG, registry())
    job.prepare()
    for i in range(3):
        job.emit("progress", {"fraction": i / 3}, "s1")
    folder._live[job.id].flush()  # the last copy before dying
    for _ in range(4):
        job.emit("progress", {"fraction": 0.9}, "s1")  # never copied
    entry = folder._live.pop(job.id)  # the instance dies: no final copy, the local disk is gone
    entry.local.unlink()
    assert len(saved_events(job.id)) == 3

    assert recovery.recover_interrupted() == [job.id]
    events = saved_events(job.id)
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
    assert events[3]["type"] == "error" and events[3]["data"]["code"] == "interrupted"
    assert events[4] == {**events[4], "type": "analysis_finished", "data": {"status": "failed"}}
    assert folder.read_analysis(job.id)["status"] == "failed"
    assert folder.read_events(job.id) == events


def test_recovery_with_no_copy_at_all(local_dir):
    job = executor.runner.Run("20261114-000000-dead02", copy.deepcopy(PLAN), CATALOG, registry())
    job.prepare()
    job.emit("step_started", {"operation": "x", "visual_type": "table", "phrase": {"en": "x"}}, "s1")
    entry = folder._live.pop(job.id)
    entry.local.unlink()
    assert recovery.recover_interrupted() == [job.id]
    assert [(e["seq"], e["type"]) for e in saved_events(job.id)] == [(1, "error"), (2, "analysis_finished")]


def test_recovery_cuts_a_torn_line_in_one_write(tmp_path):
    job = executor.runner.Run("20261114-000000-torn01", copy.deepcopy(PLAN), CATALOG, registry())
    job.prepare()
    job.emit("progress", {"fraction": 0.1}, "s1")
    with open(folder.directory(job.id) / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"analysis": "half')
    assert recovery.recover_interrupted() == [job.id]
    assert [(e["seq"], e["type"]) for e in saved_events(job.id)] == [
        (1, "progress"), (2, "error"), (3, "analysis_finished")]


# ids ------------------------------------------------------------------------------------------------------------

def test_an_overlong_id_is_not_an_id():
    assert folder.valid_id("a" * folder.MAX_ID)
    assert not folder.valid_id("a" * (folder.MAX_ID + 1))
    assert not folder.valid_id(None)
    client = TestClient(main.app)
    for route in ("", "/results", "/events"):
        assert client.get(f"/api/analyses/{'a' * 300}{route}").status_code == 404
