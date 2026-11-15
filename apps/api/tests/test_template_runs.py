"""Every design, end to end: template → executor → the real operations of hup_ops, over stand-in readers.

The readers return a synthetic monthly series (a counter, not a measurement) for any variable, so this checks that
a template plan runs, that every event and result follows the contract and that nothing errors; not any figure.
Results may well be absences (short records, no reference): that is a valid outcome, an error is not.
"""
import copy
import time

import pandas as pd
import pytest

from hup_api import catalog as catalog_module
from hup_api import contracts, executor
from hup_api.assistant import templates
from hup_api.executor import runner
from hup_api.library import folder
from hup_api.writer import STYLES, compose
from hup_ops.registry import REGISTRY, Context

CATALOG = catalog_module.load()
ZONE = {"id": "synthetic-zone", "name": "Synthetic zone", "bbox": [0.0, 0.0, 0.1, 0.1]}


class SyntheticReaders:
    missing_summers = ()

    def read_series(self, variable, zone, period, *, grid_resolution_m=None, grid_min_years=1, land_only=False,
                    percentile=None, base_period=None):
        dates = pd.date_range(period["start"], period["end"], freq="MS")
        if grid_resolution_m:
            assert grid_min_years == 10
            dates = dates[dates.year >= 2012]
        if period.get("months"):
            dates = [d for d in dates if d.month in period["months"]]
        source = next(v for v in CATALOG["variables"] if v["id"] == variable)["sources"][0]
        url = next(s for s in CATALOG["sources"] if s["id"] == source)["access"]["url"]
        unit = next(v for v in CATALOG["variables"] if v["id"] == variable)["unit"]
        values = [float(i) for i in range(len(dates))]
        if grid_resolution_m:
            if zone["id"].endswith("-ring-1"):
                dates = [date for date in dates if date.year not in self.missing_summers or date.month != 8]
                # Keep four scenes in each incomplete summer: an extra June scene cannot replace August.
                dates += [date.replace(day=15) for date in dates
                          if date.year in self.missing_summers and date.month == 6]
            # Keep real variation after detrending. Plant an inverse association independent of the control.
            signal = [1, 7, 4, 2, 9, 5, 3, 8, 6, 0, 10, 12, 11]
            control = [8, 1, 4, 6, 10, 2, 3, 11, 9, 7, 5, 0, 12]
            values = [control[d.year - 2012] if zone["id"].endswith("-ring-1")
                      else signal[d.year - 2012] * (1 if variable == "ndvi" else -1) for d in dates]
        table = pd.DataFrame({"t": [d.strftime("%Y-%m-%d") for d in dates], "v": values})
        return {"data": table, "absence": None, "origin": "cache", "sources": [{"source": source, "url": url}],
                "support": {"resolution_m": grid_resolution_m} if grid_resolution_m else {},
                "coverage": {"valid_pct": 100.0, "n_dates": len(dates)}, "unit": unit,
                "input_fingerprint": None,
                **({"land_mask": {"applied": True, "detail": {"en": "Water out.", "es": "Sin agua."}}}
                   if land_only else {})}

    def read_exposure(self, params, zone, period):
        sources = [{"source": entry["id"], "url": entry["access"]["url"]} for entry in CATALOG["sources"]
                   if entry["id"] in {"ghsl", "copernicus-ems-emsr773"}]
        table = pd.DataFrame({"population": [25.0, 0.0, 50.0], "detected": [True, False, True]})
        return {"data": table, "absence": None, "origin": "cache", "sources": sources, "unit": "people",
                "support": {"resolution_m": 1000}, "coverage": {"valid_pct": 100.0}}

    def reference_zone(self, zone, km):
        return {**zone, "id": f"{zone['id']}-reference", "name": f"{zone['name']} reference"}

    def ring_zones(self, zone, km):
        return [{**zone, "id": f"{zone['id']}-ring-{i}"} for i in range(len(km))]


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    monkeypatch.setattr(runner, "_readers", lambda: SyntheticReaders())
    monkeypatch.setattr(runner, "WRITER", "hup_api_tests_no_writer")
    monkeypatch.setattr(runner, "REVIEWER", "hup_api_tests_no_reviewer")


@pytest.mark.parametrize(("design", "missing_summers"),
                         [(design, ()) for design in templates.DESIGNS] + [("scan", (2019, 2020))])
def test_a_template_plan_runs_to_the_end(design, missing_summers, monkeypatch):
    monkeypatch.setattr(SyntheticReaders, "missing_summers", missing_summers)
    variables = [v["id"] for v in CATALOG["variables"] if design in v["designs"]] or [CATALOG["variables"][0]["id"]]
    period = {"start": "2024-10-01", "end": "2024-10-31"} if design == "event" else \
        {"start": "1995-01-01", "end": "2024-12-31"}
    plan = templates.template(design, copy.deepcopy(ZONE), period, variables, CATALOG,
                              before=[1995, 2009], after=[2011, 2024])
    analysis_id = executor.run(plan, catalog=CATALOG)
    deadline = time.monotonic() + 60
    while folder.read_analysis(analysis_id)["status"] not in ("finished", "failed"):
        assert time.monotonic() < deadline, "the analysis did not finish"
        time.sleep(0.05)
    analysis, events = folder.read_analysis(analysis_id), folder.read_events(analysis_id)
    errors = [(e.get("step"), e["data"]["code"]) for e in events if e["type"] == "error"]
    assert errors == [] and analysis["status"] == "finished"
    assert contracts.errors("analysis", analysis) == []
    for event in events:
        assert contracts.errors("event", event) == [], event
    results = folder.read_results(analysis_id)
    for result in results:
        assert contracts.errors("result", result) == [], result["id"]
    if design == "exposure":
        exposure, = [result for result in results if result["method"]["operation"] == "exposure"]
        assert exposure["value"] == 75 and exposure["data_class"] == "approximated"
    reads = [e for e in events if e["type"] == "data_read"]
    assert {e["step"] for e in reads} == {
        s["id"] for s in plan["steps"] if s["type"] == "ingest" and s["operation"] != "zone_layer"
    }
    for step in (s for s in plan["steps"] if s["operation"] == "zone_layer"):
        layer, = [result for result in results if result["step"] == step["id"]]
        assert layer["type"] == "layer" and "absence" in layer


    if design == "scan":
        chain, = [result for result in results if result["method"]["operation"] == "evaluate_chain"]
        assert chain["method"]["params"]["min_years"] == 10
        assert chain["support"]["resolution_m"] == 100
        row, = chain["table"]["rows"]
        assert row[2] == "met"
        if missing_summers:
            assert any("11 of 13 complete summers" in limit["en"] and "11 de 13 veranos completos" in limit["es"]
                       and all(str(year) in limit["en"] and str(year) in limit["es"] for year in missing_summers)
                       for limit in chain["limits"])
        else:
            assert any("With 13 years and no lag" in limit["en"] and "Con 13 años y solo el mismo año" in limit["es"]
                       for limit in chain["limits"])
        story = compose(plan, results, [], CATALOG)
        assert story["editorial"]["journalistic"]["headline"]
        for style in STYLES:
            assert any(block["block"] == "LimitsBox" and chain["id"] in block["results"] for block in story[style])
        control, = [r for r in results if r["method"]["params"].get("ring_from_km") == 3]
        assert control["method"]["params"]["ring_to_km"] == 20
        assert len(control["series"]) == 13 - len(missing_summers)


def test_declared_flood_chain_with_one_observed_event_is_not_testable():
    declared = next(chain for chain in CATALOG["chains"] if chain["id"] == "flood_exposure")
    period = {"start": "2000-01-01", "end": "2024-12-31"}
    ctx = Context(ZONE, period, 42, None, lambda *_: None)
    rainfall = [{"t": str(year), "v": float(year - 1999)} for year in range(2000, 2025)]
    inputs = {
        "rain": [{"variable": "pr_max_1day", "series": rainfall}],
        "flood": [{"variable": "flood_extent", "series": [{"t": "2024-10-29", "v": 0.5}]}],
        "exposure": [{"variable": "population_in_flood_area", "value": 25.0}],
        "regional": [{"id": "regional-rain", "variable": "precipitation", "series": rainfall}],
    }
    params = {"chain": {"id": declared["id"], "links": declared["links"]}, "regional": "regional-rain"}
    chain = REGISTRY["evaluate_chain"].fn(params, inputs, ctx)["chain"]
    assert chain["evidence_level"] == "E0"
    rainfall_link, exposure_link = chain["table"]["rows"]
    for row, link, count in zip((rainfall_link, exposure_link), declared["links"], (1, 0), strict=True):
        assert row[:3] == [link["from"], link["to"], "not_testable"]
        assert row[4] == link["ref"] and row[4]
        assert f"{count} are available" in row[3] and f"Hay {count}" in row[5]
        assert {"en": row[3], "es": row[5]} in chain["limits"]
    assert contracts.errors("result", {**chain, "id": "r-s1-chain", "analysis": "a1", "step": "s1",
                                       "fingerprint": "0" * 64}) == []


def test_numpy_scalars_become_plain_values_and_nothing_else_changes():
    import numpy as np

    body = {"a": np.True_, "b": [np.float64(0.5), {"c": np.int64(3)}], "d": (1, 2), "e": "text"}
    out = runner.plain(body)
    assert out == {"a": True, "b": [0.5, {"c": 3}], "d": [1, 2], "e": "text"}
    assert type(out["a"]) is bool and type(out["b"][0]) is float and type(out["b"][1]["c"]) is int
    array = np.array([1, 2])
    copied = runner.plain({"x": array})["x"]
    assert copied is not array and isinstance(copied, np.ndarray)  # still an array: validation rejects it
