"""The executor attaches one annual map result per frame without changing the scientific analysis."""
import asyncio

import numpy as np
import pytest
from rasterio.transform import from_origin

from hup_api import catalog, contracts
from hup_api.assistant import templates
from hup_api.executor.runner import Outcome, Run, new_id
from hup_api.library import folder
from hup_api.writer import compose
from hup_data import layers, store
from hup_ops.registry import REGISTRY

ZONE = {"id": "zone-test", "name": "Zone test", "bbox": [0.0, 38.9, 0.2, 39.1]}
PERIOD = {"start": "2024-01-01", "end": "2025-12-31"}
SOURCE = {"source": "imerg-monthly", "url": "https://example.org/imerg"}
MISSING = {"reason": "no_coverage", "detail": {"en": "Outside the grid.", "es": "Fuera de la rejilla."}}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")


@pytest.mark.parametrize(
    ("design", "variables", "request_period"),
    [
        ("state", ["precipitation"], PERIOD),
        ("trend", ["ndvi", "precipitation"], PERIOD),
        ("event", ["precipitation"], {"start": "2024-10-29", "end": "2024-10-29"}),
        ("scenario", ["tas_mean"], PERIOD),
    ],
)
def test_supported_designs_include_one_independent_layer_step(design, variables, request_period):
    plan = templates.template(design, ZONE, request_period, variables, catalog.load())
    step, = [step for step in plan["steps"] if step["operation"] == "zone_layer"]
    writer, = [step_ for step_ in plan["steps"] if step_["type"] == "write"]
    assert step["depends_on"] == [] and step["id"] in writer["depends_on"]
    assert step["params"]["variable"] == variables[-1]
    assert all(step["id"] not in compute["depends_on"] for compute in plan["steps"] if compute["type"] == "compute")
    if design == "event":
        assert step["params"]["period"] == {"start": "2024-10-01", "end": "2024-10-31", "months": [10]}
    if design == "scenario":
        assert step["params"]["scenario"] == "ssp245"


@pytest.mark.parametrize("variable", ["ndvi", "lst"])
def test_impact_layers_use_only_the_windows_of_their_own_contrast(variable):
    current = catalog.load()
    plan = templates.template("impact", ZONE, PERIOD, [variable], current, before=[2024, 2024], after=[2025, 2025])
    assert contracts.errors("plan", plan) == []
    step, = [step for step in plan["steps"] if step["operation"] == "zone_layer"]
    contrast, = [step for step in plan["steps"] if step["operation"] == "baci_contrast"]
    writer, = [step for step in plan["steps"] if step["type"] == "write"]
    assert step["params"] == {"variable": variable, "source": "landsat-c2-l2"}
    assert step["depends_on"] == [contrast["id"]] and step["id"] in writer["depends_on"]


@pytest.mark.parametrize("design", ["state", "trend"])
def test_landsat_never_requests_annual_layers_in_a_series_plan(design):
    plan = templates.template(design, ZONE, {"start": "2013-01-01", "end": "2025-12-31"},
                              ["ndvi", "lst"], catalog.load())
    assert all(step["operation"] != "zone_layer" for step in plan["steps"])


@pytest.mark.parametrize(("before", "after", "maps_available", "missing_after"), [
    ([2024, 2024], [2025, 2025], True, False),
    (["2024-01-01", "2024-12-31"], ["2025-01-01", "2025-12-31"], True, True),
    (["2024-07-01", "2024-12-31"], [2025, 2025], False, True),
    (["2024-01-01", 2024], [2025, 2025], False, True),
    ([2024, 2024], [2024, 2025], False, True),
])
def test_impact_materializes_two_named_frames_or_explains_missing_windows(
    before, after, maps_available, missing_after, monkeypatch,
):
    current = catalog.load()
    plan = templates.template("impact", ZONE, PERIOD, ["ndvi"], current, before=before, after=after)
    job = Run(new_id(), plan, current, REGISTRY)
    job.prepare()
    layer, = [step for step in plan["steps"] if step["operation"] == "zone_layer"]
    contrast, = [step for step in plan["steps"] if step["operation"] == "baci_contrast"]
    contrast["params"]["reference_km"] = 5
    fingerprint = "a" * 64
    source = {"source": "landsat-c2-l2", "url": "https://example.org/landsat"}
    input_ = Outcome(contrast["id"], True, fingerprint, variable="ndvi", sources=[source], results=[{
        "variable": "ndvi", "method": {"operation": "baci_contrast", "params": contrast["params"]},
        "sources": [source], "data_class": "measured",
    }])

    def zone_layers(variable, zone, period, scenario, *, windows, margin_km):
        assert maps_available
        assert windows == {"before": [2024, 2024], "after": [2025, 2025]} and margin_km == 5
        return {
            "variable": variable, "period": period, "data_class": "measured",
            "unit": "1", "sources": [source], "origin": "derived", "fingerprint": fingerprint,
            "range": [0.1, 0.7], "scale": "green", "absence": None,
            "frames": [
                {"t": "2024-2024", "window": "before", "years": [2024, 2024], "file": ".before.tif",
                 "coverage": {"valid_pct": 100}},
                {"t": "2025-2025", "window": "after", "years": [2025, 2025],
                 **({"absence": MISSING} if missing_after else {"file": ".after.tif", "coverage": {"valid_pct": 90}})},
            ],
        }

    async def immediately(fn, *args):
        return fn(*args)

    if maps_available:
        for name in (["before"] if missing_after else ["before", "after"]):
            layers.write_cog(np.ones((2, 2), dtype="float32"), store.derived_path(fingerprint, f".{name}.tif"),
                             nodata=-9999, kind="values", transform=from_origin(0, 39.1, 0.1, 0.1))
    monkeypatch.setattr(layers, "zone_layers", zone_layers)
    monkeypatch.setattr(asyncio, "to_thread", immediately)
    with pytest.raises(StopIteration) as finished:
        job.step(layer, [input_]).send(None)
    assert finished.value.value.ok and not job.failed
    results = folder.read_results(job.id)
    if maps_available:
        first, second = results
        assert [first["id"], second["id"]] == [f"r-{layer['id']}-before", f"r-{layer['id']}-after"]
        assert first["method"]["params"]["window"] == "before"
        assert first["period"] == {"start": "2024-01-01", "end": "2024-12-31"}
        assert layers.check_cog(folder.directory(job.id) / first["layer"]["path"]) == []
        assert second["method"]["params"]["window"] == "after"
        if missing_after:
            assert second["absence"] == MISSING
        else:
            assert second["layer"]["range"] == first["layer"]["range"]
            assert layers.check_cog(folder.directory(job.id) / second["layer"]["path"]) == []
    else:
        missing, = results
        assert missing["absence"]["reason"] == "not_computed"
        assert "ventanas comparables" in missing["absence"]["detail"]["es"]


@pytest.mark.parametrize("condition", ["available", "missing", "error"])
def test_map_results_and_optional_failures_are_saved_and_linked(condition, monkeypatch):
    fp = "a" * 64
    source_file = store.derived_path(fp, ".2024.tif")
    if condition == "available":
        layers.write_cog(np.ones((2, 2), dtype="float32"), source_file, nodata=-9999, kind="values",
                         transform=from_origin(0, 39.1, 0.1, 0.1))

    def zone_layers(variable, zone, period, scenario):
        if condition == "error":
            raise OSError("test unreadable source")
        return {
            "variable": variable, "period": period, "scenario": scenario, "data_class": "measured",
            "unit": "mm/month", "sources": [SOURCE], "origin": "package", "fingerprint": fp,
            "range": [1.0, 2.0], "scale": "water",
            "absence": MISSING if condition == "missing" else None,
            "frames": [
                {"t": "2024", "file": ".2024.tif", "coverage": {"valid_pct": 100}},
                {"t": "2025", "absence": MISSING},
            ] if condition == "available" else [],
        }

    async def immediately(fn, *args):
        return fn(*args)

    monkeypatch.setattr(layers, "zone_layers", zone_layers)
    monkeypatch.setattr(asyncio, "to_thread", immediately)
    current = catalog.load()
    plan = templates.template("state", ZONE, PERIOD, ["precipitation"], current)
    job = Run(new_id(), plan, current, REGISTRY)
    job.prepare()
    step, = [step for step in plan["steps"] if step["operation"] == "zone_layer"]
    with pytest.raises(StopIteration) as finished:
        job.step(step, []).send(None)
    assert finished.value.value.ok and not job.failed
    results = folder.read_results(job.id)
    assert all(result["type"] == "layer" and contracts.errors("result", result) == [] for result in results)
    events = folder.read_events(job.id)
    assert not any(event["type"] == "error" for event in events)
    if condition == "available":
        first, missing = results
        served = folder.directory(job.id) / first["layer"]["path"]
        assert served.read_bytes() == source_file.read_bytes()
        assert layers.check_cog(served) == []
        assert first["layer"]["range"] == [1, 2]
    else:
        missing, = results
    assert missing["absence"]["reason"] == ("not_computed" if condition == "error" else "no_coverage")
    assert missing["absence"]["detail"]["es"]
    for style, blocks in compose(plan, results, [], current).items():
        if style == "editorial":
            continue
        assert any(block["block"] == "LimitsBox" and missing["id"] in block["results"] for block in blocks)
        if condition == "available":
            assert any(block["block"] == "SourcesTable" and first["id"] in block["results"] for block in blocks)
