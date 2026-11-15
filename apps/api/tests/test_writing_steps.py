"""Write and review steps: the executor calls the writer and the reviewer, never the operation registry.

The writer and reviewer here are stand-in modules put in sys.modules by the tests, so these tests never depend on
the real ones (hup_api.writer and hup_api.reviewer, which another agent writes). The last test runs the real ones
when they exist.
"""
import copy
import importlib.util
import json
import sys
import threading
import time
import types

import pytest

from hup_api import contracts, executor
from hup_api.executor import runner
from hup_api.library import folder
from hup_data import paths

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
PLAN = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))
WRITE, REVIEW = "s6", "s7"


class Op:
    def __init__(self, op_id, version, fn):
        self.id, self.version, self.fn = op_id, version, fn


def registry(calls):
    """Stand-in reads and contrasts (tables without figures). No write or review operation: they must not be used."""
    def make(op_id):
        def fn(params, inputs, ctx):
            calls.append(op_id)
            variable = params.get("variable")
            source = next(v for v in CATALOG["variables"] if v["id"] == variable)["sources"][0]
            url = next(s for s in CATALOG["sources"] if s["id"] == source)["access"]["url"]
            body = {"type": "table", "variable": variable, "data_class": "measured",
                    "table": {"columns": ["operation"], "rows": [[op_id]]}, "sources": [{"source": source, "url": url}],
                    "method": {"operation": op_id, "version": "1.0", "params": params}}
            if op_id == "baci_contrast":
                body["evidence_level"] = "E2"
            return {"main": body}
        return fn
    return {o: Op(o, "1.0", make(o)) for o in ("read_series", "baci_contrast")}


class Stand:
    """A stand-in writer module and a stand-in reviewer module, with knobs to make each piece fail."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.writer = types.ModuleType("standin_writer")
        self.reviewer = types.ModuleType("standin_reviewer")
        self.writer.write_findings = self.write_findings
        self.writer.compose = self.compose
        self.reviewer.review = self.review

    def maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} failed with a secret path C:/secret")

    def write_findings(self, plan, results, catalog):
        self.maybe_fail("write_findings")
        contrast = next(r for r in results if r["step"] == "s5")
        return [{"id": "f1", "order": 1, "text": {"en": "Placeholder sentence.", "es": "Frase de prueba."},
                 "results": [contrast["id"]], "data_class": contrast["data_class"], "evidence_level": "E2",
                 "review": {"status": "pending"}}]

    def review(self, results, findings, catalog):
        self.maybe_fail("review")
        results, findings = copy.deepcopy(results), copy.deepcopy(findings)
        for result in results:
            if result["step"] == "s5":
                result["evidence_level"] = "E1"
        for finding in findings:
            finding.update(evidence_level="E1", review={"status": "approved", "by": "rules"})
        warning = {"code": "pre_trend_control", "text": {"en": "Lowered.", "es": "Rebajado."},
                   "result": "r-s5-main", "finding": None}
        return {"results": results, "findings": findings, "warnings": [warning]}

    def compose(self, plan, results, findings, catalog):
        self.maybe_fail("compose")
        return {style: [{"block": "MethodBox", "finding": findings[0]["id"]} if findings else {"block": "MethodBox"}]
                for style in ("journalistic", "institutional", "scientific")}


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def stand(monkeypatch):
    stand = Stand()
    monkeypatch.setitem(sys.modules, "standin_writer", stand.writer)
    monkeypatch.setitem(sys.modules, "standin_reviewer", stand.reviewer)
    monkeypatch.setattr(runner, "WRITER", "standin_writer")
    monkeypatch.setattr(runner, "REVIEWER", "standin_reviewer")
    return stand


def run(plan=PLAN):
    calls = []
    analysis_id = executor.run(copy.deepcopy(plan), catalog=CATALOG, registry=registry(calls))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        analysis = folder.read_analysis(analysis_id)
        if analysis["status"] in ("finished", "failed"):
            events = folder.read_events(analysis_id)
            for event in events:
                assert contracts.errors("event", event) == [], event
            assert contracts.errors("analysis", analysis) == []
            return analysis, events, calls
        time.sleep(0.02)
    raise AssertionError("the analysis did not finish")


def of(events, kind, step=None):
    return [e for e in events if e["type"] == kind and (step is None or e.get("step") == step)]


def test_write_review_and_compose_are_called_and_stored(stand):
    analysis, events, calls = run()
    assert analysis["status"] == "finished"
    assert stand.calls == ["write_findings", "review", "compose"]
    assert "write_findings" not in calls and "review_findings" not in calls
    assert [f["id"] for f in analysis["findings"]] == ["f1"]
    assert analysis["findings"][0]["review"]["status"] == "approved"
    assert set(analysis["story"]) == {"journalistic", "institutional", "scientific"}
    for step in (WRITE, REVIEW):
        assert len(of(events, "step_started", step)) == 1 and len(of(events, "step_finished", step)) == 1
    (warning,) = of(events, "warning", REVIEW)
    assert warning["data"] == {"code": "pre_trend_control", "text": {"en": "Lowered.", "es": "Rebajado."}}
    reviewed = {r["id"]: r for r in folder.read_results(analysis["id"])}
    assert reviewed["r-s5-main"]["evidence_level"] == "E1"  # the reviewed results are the ones kept
    assert reviewed["r-s4-main"]["evidence_level"] == "E2"


def test_the_write_step_waits_for_the_steps_it_depends_on(stand):
    _, events, _ = run()
    seq = {(e["type"], e.get("step")): e["seq"] for e in events}
    assert seq[("step_started", WRITE)] > max(seq[("step_finished", "s4")], seq[("step_finished", "s5")])
    assert seq[("step_started", REVIEW)] > seq[("step_finished", WRITE)]


@pytest.mark.parametrize("failing", ["write_findings", "review", "compose"])
def test_a_failing_piece_is_a_warning_and_the_analysis_still_finishes(stand, failing):
    stand.fail.add(failing)
    analysis, events, _ = run()
    assert analysis["status"] == "finished"
    assert analysis["findings"] == []
    story = analysis["story"]
    piece = story["editorial"]["journalistic"]
    assert all(piece[field]["en"] and piece[field]["es"] for field in ("headline", "lead", "cannot_say"))
    sources = [block for block in story["journalistic"] if block["block"] == "SourcesTable"]
    assert sources and set(sources[0]["results"]) == set(analysis["results"])
    assert not any("finding" in block for block in story["journalistic"])
    assert not of(events, "error")
    warning = next(e for e in of(events, "warning") if e["data"]["code"] in ("writing_failed", "review_failed"))
    for language in ("en", "es"):
        assert piece["cannot_say"][language].startswith(warning["data"]["text"][language] + " ")
    assert "secret" not in json.dumps([analysis, events])
    assert events[-1]["type"] == "analysis_finished"


def test_a_missing_module_or_function_is_a_warning(stand, monkeypatch):
    monkeypatch.setattr(runner, "WRITER", "no_such_writer_module")
    analysis, events, _ = run()
    assert analysis["status"] == "finished" and analysis["findings"] == []
    assert of(events, "warning", WRITE)

    monkeypatch.setattr(runner, "WRITER", "standin_writer")
    del stand.reviewer.review
    analysis, events, _ = run()
    assert analysis["status"] == "finished" and analysis["findings"] == []
    assert of(events, "warning", REVIEW)


def test_findings_that_break_the_contract_are_dropped(stand):
    stand.writer.write_findings = lambda plan, results, catalog: [{"id": "BAD ID", "text": "no"}]
    analysis, events, _ = run()
    assert analysis["status"] == "finished" and analysis["findings"] == []
    assert of(events, "warning")
    assert analysis["story"]["editorial"]["journalistic"]["headline"]["es"]


def test_the_report_survives_when_review_discards_all_findings(stand):
    stand.reviewer.review = lambda results, *_: {"results": results, "findings": [], "warnings": []}
    analysis, _, _ = run()
    assert analysis["status"] == "finished" and analysis["findings"] == []
    assert analysis["story"]["editorial"]["journalistic"]["lead"]["es"]
    assert all("finding" not in block for block in analysis["story"]["journalistic"])


def test_a_reviewer_that_changes_a_figure_is_not_trusted(stand):
    original = stand.review

    def changes_a_figure(results, findings, catalog):
        out = original(results, findings, catalog)
        for result in out["results"]:
            if result["step"] == "s4":
                result["table"]["rows"] = [["changed"]]
        return out

    stand.reviewer.review = changes_a_figure
    analysis, events, _ = run()
    kept = {r["id"]: r for r in folder.read_results(analysis["id"])}
    assert kept["r-s4-main"]["table"]["rows"] == [["baci_contrast"]]
    assert kept["r-s5-main"]["evidence_level"] == "E1"
    assert any(e["data"]["code"] == "review_changed_result" for e in of(events, "warning"))


def test_a_reviewer_cannot_raise_a_level(stand):
    def raises(results, findings, catalog):
        results = copy.deepcopy(results)
        for result in results:
            if result["step"] == "s5":
                result["evidence_level"] = "E4"
        return {"results": results, "findings": findings, "warnings": []}

    stand.reviewer.review = raises
    analysis, _, _ = run()
    kept = {r["id"]: r for r in folder.read_results(analysis["id"])}
    assert kept["r-s5-main"]["evidence_level"] == "E2"


def test_a_writer_that_hangs_does_not_hang_the_analysis(stand, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(runner, "WRITING_SECONDS", 0.3)
    stand.writer.write_findings = lambda plan, results, catalog: release.wait(10) and []
    try:
        analysis, events, _ = run()
    finally:
        release.set()
    assert analysis["status"] == "finished" and analysis["findings"] == []
    assert analysis["story"]["editorial"]["journalistic"]["headline"]["es"]
    assert of(events, "warning", WRITE)


def test_a_plan_with_write_but_no_review_still_composes(stand):
    plan = copy.deepcopy(PLAN)
    plan["steps"] = [s for s in plan["steps"] if s["type"] != "review"]
    analysis, _, _ = run(plan)
    assert stand.calls == ["write_findings", "compose"]
    assert analysis["findings"][0]["review"]["status"] == "pending"
    assert "story" in analysis


def example_registry():
    """Operations that give back the contract example results (the same step, variable and body), so the real
    writer and reviewer get results with values, an evidence level and an absence."""
    examples = json.loads((EXAMPLES / "results.json").read_text(encoding="utf-8"))
    by_op = {(r["method"]["operation"], r["variable"]): r for r in examples}
    fallback = registry([])

    def make(op_id):
        def fn(params, inputs, ctx):
            example = by_op.get((op_id, params.get("variable")))
            if example is None:
                return fallback[op_id].fn(params, inputs, ctx)
            key = example["id"].split("-", 2)[2]
            return {key: {k: v for k, v in example.items() if k not in ("id", "analysis", "step", "fingerprint")}}
        return fn
    return {o: Op(o, "1.0", make(o)) for o in ("read_series", "baci_contrast")}


@pytest.mark.skipif(importlib.util.find_spec("hup_api.writer") is None
                    or importlib.util.find_spec("hup_api.reviewer") is None,
                    reason="the real writer and reviewer are not there yet")
def test_the_real_writer_and_reviewer_run_through_the_executor():
    analysis_id = executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=example_registry())
    deadline = time.monotonic() + 20
    while folder.read_analysis(analysis_id)["status"] not in ("finished", "failed"):
        assert time.monotonic() < deadline, "the analysis did not finish"
        time.sleep(0.02)
    analysis, events = folder.read_analysis(analysis_id), folder.read_events(analysis_id)
    for event in events:
        assert contracts.errors("event", event) == [], event
    assert contracts.errors("analysis", analysis) == []
    assert analysis["status"] == "finished"
    codes = [e["data"]["code"] for e in of(events, "warning")]
    assert not {"writing_failed", "review_failed", "invalid_findings", "review_changed_result"} & set(codes), codes
    assert analysis["findings"]
    assert set(analysis["story"]) == {"journalistic", "institutional", "scientific", "editorial"}
    assert analysis["story"]["editorial"]["journalistic"]["headline"]["es"] == (
        "La zona cambió de forma distinta a su entorno"
    )


def test_an_analysis_with_only_absences_still_gets_a_story(stand, monkeypatch):
    """Nothing could be computed: there are no findings, but the report still says what was missing and why."""
    absent = {"reason": "insufficient_qa", "detail": {"en": "Too few years.", "es": "Pocos años."}}

    original = registry

    def nothing(calls):
        ops = original(calls)

        def fn(params, inputs, ctx):
            variable = params.get("variable")
            source = next(v for v in CATALOG["variables"] if v["id"] == variable)["sources"][0]
            url = next(s for s in CATALOG["sources"] if s["id"] == source)["access"]["url"]
            return {"main": {"type": "value", "variable": variable, "data_class": "measured", "absence": absent,
                             "sources": [{"source": source, "url": url}],
                             "method": {"operation": "baci_contrast", "version": "1.0", "params": params}}}
        ops["baci_contrast"] = Op("baci_contrast", "1.0", fn)
        return ops

    monkeypatch.setattr(sys.modules[__name__], "registry", nothing)
    stand.writer.write_findings = lambda plan, results, catalog: stand.calls.append("write_findings") or []
    analysis, events, _ = run()
    assert analysis["status"] == "finished" and analysis["findings"] == []
    assert stand.calls == ["write_findings", "compose"]  # nothing to review, but the story is composed
    assert set(analysis["story"]) == {"journalistic", "institutional", "scientific"}
    assert [w["data"]["code"] for w in of(events, "warning", REVIEW)] == ["review_skipped"]


def test_results_without_a_finding_still_get_a_story(stand):
    """A series or a table with no sentence about it is still shown, with its sources and its limits."""
    stand.writer.write_findings = lambda plan, results, catalog: []
    analysis, events, _ = run()
    assert analysis["findings"] == []
    assert set(analysis["story"]) == {"journalistic", "institutional", "scientific"}
    assert "compose" in stand.calls and "review" not in stand.calls
    assert [w["data"]["code"] for w in of(events, "warning", REVIEW)] == ["review_skipped"]
