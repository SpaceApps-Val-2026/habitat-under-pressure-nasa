"""The executor says where each datum came from, and a restart never leaves an analysis hanging.

Ingest steps run the real read_series operation of hup_ops against stand-in readers: what the readers return is
made up here (dates and placeholders, no measured figure), and the tests check only what the executor reports.
"""
import copy
import json
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from hup_api import contracts, executor, main
from hup_api.executor import reads, recovery
from hup_api.executor.runner import Run, new_id
from hup_api.library import folder
from hup_data import paths
from hup_ops.ingest import READ_SERIES_VERSION

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
PLAN = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))
next(op for op in CATALOG["operations"] if op["id"] == "read_series")["version"] = READ_SERIES_VERSION
LANDSAT = next(s for s in CATALOG["sources"] if s["id"] == "landsat-c2-l2")["access"]["url"]
SMAP = next(s for s in CATALOG["sources"] if s["id"] == "smap-l3")["access"]["url"]


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


def series(dates, n_items=None, origin="remote", source="landsat-c2-l2", url=LANDSAT):
    ref = {"source": source, "url": url}
    if n_items is not None:
        ref["n_items"] = n_items
    table = pd.DataFrame({"t": dates, "v": [float(i) for i in range(len(dates))]})
    return {"data": table, "absence": None, "origin": origin, "sources": [ref], "support": {},
            "coverage": {"valid_pct": 100.0, "n_dates": len(dates)}, "unit": "1", "input_fingerprint": None}


def absent(reason="needs_package"):
    return {"data": None, "absence": {"reason": reason, "detail": {"en": "Not here.", "es": "No está."}},
            "origin": "cache", "sources": [{"source": "smap-l3", "url": SMAP}], "support": {}, "coverage": {},
            "unit": None, "input_fingerprint": None}


class StandInReaders:
    """Looks like hup_data.readers: read_series answers by variable; zones for reference and rings."""

    marker = "forwarded"

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def read_series(self, variable, zone, period):
        self.calls.append((variable, zone["name"]))
        answer = self.answers[variable]
        return answer(zone) if callable(answer) else copy.deepcopy(answer)

    def reference_zone(self, zone, km):
        return {**zone, "id": f"{zone['id']}-ref", "name": f"{zone['name']} reference"}

    def ring_zones(self, zone, km):
        return [{**zone, "id": f"{zone['id']}-ring{i}", "name": f"{zone['name']} ring {i}"} for i in range(len(km))]


def ingest_plan(*steps):
    plan = copy.deepcopy(PLAN)
    plan["steps"] = [{"id": f"s{i}", "type": "ingest", "operation": "read_series", "version": READ_SERIES_VERSION,
                      "params": p,
                      "depends_on": [], "sources": [src]} for i, (p, src) in enumerate(steps, start=1)]
    return plan


def registry_with_real_ingest():
    from hup_ops.registry import REGISTRY

    return {"read_series": REGISTRY["read_series"]}


def catalog_with_area():
    """The example catalog's read_series, with the area params of the real one, so a ring plan validates."""
    catalog = copy.deepcopy(CATALOG)
    op = next(o for o in catalog["operations"] if o["id"] == "read_series")
    op["params"]["properties"].update(area={"enum": ["zone", "reference", "ring"]},
                                      ring_km={"type": "array", "items": {"type": "number"}})
    return catalog


def run(plan, readers, monkeypatch, catalog=CATALOG):
    monkeypatch.setattr(executor.runner, "_readers", lambda: readers)
    analysis_id = executor.run(copy.deepcopy(plan), catalog=catalog, registry=registry_with_real_ingest())
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        analysis = folder.read_analysis(analysis_id)
        if analysis["status"] in ("finished", "failed"):
            return analysis_id, analysis
        time.sleep(0.02)
    raise AssertionError("the analysis did not finish")


def events_of(analysis_id, kind=None):
    events = folder.read_events(analysis_id)
    for event in events:
        assert contracts.errors("event", event) == [], event
    return [e for e in events if kind is None or e["type"] == kind]


# the recording proxy ---------------------------------------------------------------------------------------------

def test_the_proxy_forwards_every_attribute_and_records_each_read():
    inner = StandInReaders({"ndvi": series(["2020-06-01", "2021-06-01"], n_items=3)})
    proxy = reads.Recording(inner)
    assert proxy.marker == "forwarded"
    assert proxy.reference_zone({"id": "z", "name": "Z"}, 3)["id"] == "z-ref"
    got = proxy.read_series("ndvi", {"id": "z", "name": "Z", "bbox": [0, 0, 1, 1]}, PLAN["period"])
    assert got["origin"] == "remote"  # what the operation receives is untouched
    assert len(proxy.reads) == 1 and proxy.reads[0]["variable"] == "ndvi"


@pytest.mark.parametrize("split", ["0", "1"])
def test_read_timings_include_source_origin_and_absence(monkeypatch, split):
    from hup_data import store

    monkeypatch.setenv("HUP_SPLIT_APPENDS", split)
    inner = StandInReaders({
        "ndvi": series(["2021-06-01"], origin="remote", url="https://example.test/secret"),
        "soil_moisture": absent(),
    })
    proxy = reads.Recording(inner, "a", "s1", ["planned-source"], area="reference")
    for variable in ("ndvi", "soil_moisture"):
        proxy.read_series(variable, PLAN["zone"], PLAN["period"])
    fp = "0" * 64
    store.put_derived(fp, {"results": {}})
    proxy.derived("ndvi", fp)

    logs = paths.data_dir() / "logs"
    files = sorted((logs / "data-reads").glob("*.jsonl")) if split == "1" else [logs / "data-reads.jsonl"]
    text = "".join(path.read_text(encoding="utf-8") for path in files)
    assert "secret" not in text
    lines = [json.loads(line) for line in text.splitlines()]
    assert len(lines) == 3
    actual = {(row["kind"], row["variable"]): row for row in lines}
    assert actual["read_series", "ndvi"]["sources"] == ["landsat-c2-l2"]
    assert actual["read_series", "ndvi"]["origins"] == ["remote"]
    assert actual["read_series", "ndvi"]["n_items"] == 1
    assert "n_items" not in actual["read_series", "soil_moisture"] and "n_items" not in actual["derived_result", "ndvi"]
    assert actual["read_series", "soil_moisture"]["outcome"] == "absent"
    assert actual["read_series", "soil_moisture"]["absence"] == "needs_package"
    assert actual["derived_result", "ndvi"]["origins"] == ["derived"]
    assert all(row["seconds"] >= 0 and row["analysis_id"] == "a" and row["step"] == "s1"
               and row["area"] == "reference" for row in lines)


def test_missing_reader_uses_the_catalog_label_in_the_result_and_event(monkeypatch):
    plan = ingest_plan(({"variable": "ndvi"}, "landsat-c2-l2"))
    got = absent("not_computed")
    got["absence"]["detail"] = {
        "en": "No reader is available for the variable ndvi.",
        "es": "No hay lector disponible para la variable ndvi.",
    }
    aid, analysis = run(plan, StandInReaders({"ndvi": got}), monkeypatch)
    assert analysis["status"] == "finished"
    result, = folder.read_results(aid)
    labels = next(v["name"] for v in CATALOG["variables"] if v["id"] == "ndvi")
    bilingual = {"en": f"No reader is available for “{labels['en']}”.",
                 "es": f"No hay lector disponible para «{labels['es']}»."}
    assert result["absence"]["detail"] == bilingual
    assert bilingual in [e["data"]["text"] for e in events_of(aid, "warning")]


def test_a_read_becomes_a_data_read_event_with_its_origin_and_counts(monkeypatch):
    readers = StandInReaders({"ndvi": series(["2014-06-01", "2015-07-01", "2016-08-01"], n_items=7)})
    plan = ingest_plan(({"variable": "ndvi"}, "landsat-c2-l2"))
    analysis_id, analysis = run(plan, readers, monkeypatch)
    assert analysis["status"] == "finished"
    read = events_of(analysis_id, "data_read")
    assert len(read) == 1
    assert read[0]["step"] == "s1"
    assert read[0]["data"] == {"source": "landsat-c2-l2", "origin": "remote", "n_items": 7,
                               "period": {"start": "2014-06-01", "end": "2016-08-01"}}
    kinds = [e["type"] for e in events_of(analysis_id) if e.get("step") == "s1"]
    assert kinds.index("data_read") < kinds.index("partial_result") < kinds.index("step_finished")


def test_without_n_items_the_count_is_the_number_of_dates_read(monkeypatch):
    readers = StandInReaders({"ndvi": series(["2014", "2015"], origin="package")})
    analysis_id, _ = run(ingest_plan(({"variable": "ndvi"}, "landsat-c2-l2")), readers, monkeypatch)
    (read,) = events_of(analysis_id, "data_read")
    assert read["data"]["origin"] == "package"
    assert read["data"]["n_items"] == 2
    assert read["data"]["period"] == {"start": "2014-01-01", "end": "2015-12-31"}


def test_an_absence_becomes_a_warning_with_its_reason_and_both_languages(monkeypatch):
    readers = StandInReaders({"soil_moisture": absent("needs_package")})
    analysis_id, analysis = run(ingest_plan(({"variable": "soil_moisture"}, "smap-l3")), readers, monkeypatch)
    assert analysis["status"] == "finished"
    assert events_of(analysis_id, "data_read") == []
    warnings = events_of(analysis_id, "warning")
    assert [(w["step"], w["data"]["code"]) for w in warnings] == [("s1", "needs_package")]
    assert warnings[0]["data"]["text"] == {"en": "Not here.", "es": "No está."}


def test_every_read_of_a_step_is_reported_once(monkeypatch):
    def by_zone(zone):
        return series(["2014-06-01", "2015-06-01"], n_items=int(zone["id"][-1]) + 1)  # tells the rings apart

    readers = StandInReaders({"ndvi": by_zone})
    plan = ingest_plan(({"variable": "ndvi", "area": "ring", "ring_km": [1, 2, 3]}, "landsat-c2-l2"))
    analysis_id, analysis = run(plan, readers, monkeypatch, catalog=catalog_with_area())
    assert analysis["status"] == "finished", events_of(analysis_id, "error")
    read = events_of(analysis_id, "data_read")
    assert len(read) == len(readers.calls) == 3
    assert sorted(e["data"]["n_items"] for e in read) == [1, 2, 3]


def test_two_ingest_steps_keep_their_reads_apart(monkeypatch):
    readers = StandInReaders({"ndvi": series(["2014-06-01"], n_items=1), "soil_moisture": absent()})
    plan = ingest_plan(({"variable": "ndvi"}, "landsat-c2-l2"), ({"variable": "soil_moisture"}, "smap-l3"))
    analysis_id, _ = run(plan, readers, monkeypatch)
    assert [e["step"] for e in events_of(analysis_id, "data_read")] == ["s1"]
    assert [e["step"] for e in events_of(analysis_id, "warning")] == ["s2"]


def test_a_reused_step_reports_derived_and_does_not_read_again(monkeypatch):
    def ring_series(zone):
        ring = int(zone["id"][-1])
        dates = ["2014-06-01", f"201{5 + ring}-06-01"]
        return series(dates, n_items=None if ring == 0 else 10 + ring)

    async def immediately(fn, *args):
        return fn(*args)

    readers = StandInReaders({"ndvi": ring_series})
    plan = ingest_plan(({"variable": "ndvi", "area": "ring", "ring_km": [1, 2, 3]}, "landsat-c2-l2"))
    monkeypatch.setattr(executor.runner, "_readers", lambda: readers)
    monkeypatch.setattr(executor.runner.asyncio, "to_thread", immediately)
    first, second = (Run(new_id(), plan, catalog_with_area(), registry_with_real_ingest()) for _ in range(2))
    for job in (first, second):
        job.prepare()
        with pytest.raises(StopIteration) as finished:
            job.step(plan["steps"][0], []).send(None)
        assert finished.value.value.ok
    assert len(readers.calls) == 3
    fresh = [e["data"] for e in events_of(first.id, "data_read")]
    derived = [e["data"] for e in events_of(second.id, "data_read")]
    assert [event["n_items"] for event in fresh] == [2, 11, 12]
    assert [event["period"]["end"] for event in fresh] == ["2015-06-01", "2016-06-01", "2017-06-01"]
    assert derived == [{**event, "origin": "derived"} for event in fresh]


def test_old_derived_metadata_does_not_treat_aggregates_as_read_counts():
    ref = {"source": "landsat-c2-l2"}
    result = {"sources": [ref], "coverage": {"n_dates": 2}}
    assert reads.legacy_events([result]) == [{"source": "landsat-c2-l2", "n_items": 2}]
    result["method"] = {"params": {"aggregate": "year"}}
    assert reads.legacy_events([result]) == []
    result["sources"] = [{**ref, "n_items": 30}]
    assert reads.legacy_events([result]) == [{"source": "landsat-c2-l2", "n_items": 30}]


# restart recovery ------------------------------------------------------------------------------------------------

def interrupted(torn_line=True):
    """An analysis left half way by a process that died: status running, some events, maybe a torn last line."""
    job = Run(new_id(), copy.deepcopy(PLAN), CATALOG, {})
    job.prepare()
    job.emit("step_started", {"operation": "read_series", "visual_type": "satellite",
                              "phrase": {"en": "Reading"}}, "s1")
    if torn_line:
        with open(folder.directory(job.id) / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write('{"analysis": "half a li')
    return job.id


def test_a_running_analysis_from_a_dead_process_is_marked_failed():
    analysis_id = interrupted()
    assert recovery.recover_interrupted() == [analysis_id]
    analysis = folder.read_analysis(analysis_id)
    assert analysis["status"] == "failed"
    assert contracts.errors("analysis", analysis) == []
    events = events_of(analysis_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "analysis_finished" and events[-1]["data"]["status"] == "failed"
    assert events[-2]["type"] == "error" and events[-2]["data"]["code"] == "interrupted"
    assert (folder.directory(analysis_id) / "events.jsonl").read_text(encoding="utf-8").endswith("\n")
    assert recovery.recover_interrupted() == []  # a second start finds nothing to do


def test_an_analysis_another_process_is_running_now_is_left_alone():
    """Found with real data: a script that started on the same data folder closed what the server was running."""
    analysis_id = interrupted(torn_line=False)
    assert recovery.recover_interrupted(quiet_seconds=300) == []
    assert folder.read_analysis(analysis_id)["status"] == "running"
    assert recovery.recover_interrupted(quiet_seconds=0) == [analysis_id]


def test_finished_analyses_are_left_alone_and_startup_runs_the_recovery():
    from hup_api import seed_example

    seeded = seed_example.seed()
    before = (folder.directory(seeded) / "events.jsonl").read_bytes()
    analysis_id = interrupted(torn_line=False)
    with TestClient(main.app):  # the lifespan runs at startup
        pass
    assert folder.read_analysis(analysis_id)["status"] == "failed"
    assert (folder.directory(seeded) / "events.jsonl").read_bytes() == before


def test_a_step_saved_by_read_series_1_1_is_not_reused(monkeypatch):
    """read_series 1.2 (decision 1 of A10): steps saved by 1.1 may hold Landsat Tier 2 scenes. The operation version
    is part of the step fingerprint, so the same step saved under 1.1 is read again, not reused."""
    from hup_data import store
    from hup_data.fingerprint import step_fingerprint

    async def immediately(fn, *args):
        return fn(*args)

    assert READ_SERIES_VERSION == "1.2"
    readers = StandInReaders({"ndvi": series(["2014-06-01", "2015-06-01"], n_items=2)})
    plan = ingest_plan(({"variable": "ndvi"}, "landsat-c2-l2"))
    step = plan["steps"][0]
    monkeypatch.setattr(executor.runner, "_readers", lambda: readers)
    monkeypatch.setattr(executor.runner.asyncio, "to_thread", immediately)

    def once():
        job = Run(new_id(), plan, CATALOG, registry_with_real_ingest())
        job.prepare()
        with pytest.raises(StopIteration) as finished:
            job.step(step, []).send(None)
        assert finished.value.value.ok
        return job, finished.value.value.fingerprint

    first, current = once()
    previous = step_fingerprint("read_series", "1.1", step["params"], first.zone, first.inputs(step, []),
                                plan["period"])
    assert current != previous and store.get_derived(current) is not None
    store.put_derived(previous, store.get_derived(current))  # what 1.1 left in the store
    store.derived_path(current, ".json").unlink()
    second, _ = once()
    assert len(readers.calls) == 2
    assert [e["data"]["origin"] for e in events_of(second.id, "data_read")] == ["remote"]
