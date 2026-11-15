"""The form, executor and real exposure operations agree on coverage and approximation."""

import asyncio
import copy
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box, mapping

from hup_api import api, catalog, executor
from hup_api.executor.runner import Run, new_id
from hup_ops.registry import REGISTRY

CRS = "EPSG:6933"
VARIABLE = "population_in_flood_area"
SOURCE = "example-flood"
URL = "https://example.org/flood-maps"


def _index(root, kind, entries):
    (root / kind / "index.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")


def _layer(tmp, name, shapes):
    frame = gpd.GeoDataFrame(geometry=shapes, crs=CRS).to_crs(4326)
    path = Path("ready/vectors") / f"{name}.parquet"
    frame.to_parquet(tmp / path, write_covering_bbox=True)
    return {"source": SOURCE, "path": path.as_posix(),
            "bbox": frame.total_bounds.tolist() if len(frame) else None}


def _zone(east, west=0):
    geometry = gpd.GeoSeries([box(west, 0, east, 1000)], crs=CRS).to_crs(4326).iloc[0]
    return {"name": "Example test area", "bbox": list(geometry.bounds), "geometry": mapping(geometry)}


@pytest.fixture
def flood_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    ready = tmp_path / "ready"
    for folder in ("vectors", "grids"):
        (ready / folder).mkdir(parents=True)
    path = ready / "grids" / "population.tif"
    with rasterio.open(path, "w", driver="GTiff", width=2, height=1, count=1, dtype="float32",
                       crs=CRS, transform=from_origin(0, 1000, 1000, 1000)) as image:
        image.write(np.array([[100, 200]], dtype=np.float32), 1)
    coarse = "ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.tif"
    _index(ready, "grids", {coarse: {"path": "ready/grids/population.tif"}})
    prefix = f"{SOURCE}/maps__"
    bow_tie = Polygon([(500, 0), (1500, 1000), (1500, 0), (500, 1000), (500, 0)])
    _index(ready, "vectors", {
        prefix + "test_observedEventA_v1": _layer(tmp_path, "observed", [bow_tie, box(0, 0, 500, 1000)]),
        prefix + "test_areaOfInterestA_v1": _layer(tmp_path, "scope", [box(0, 0, 2000, 1000)]),
        prefix + "test_imageFootprintA_v1": _layer(tmp_path, "image", [box(0, 0, 2000, 1000)]),
        f"{SOURCE}/hazard": _layer(tmp_path, "hazard", [box(1000, 0, 2000, 1000)]),
        f"{SOURCE}/coverage": _layer(tmp_path, "coverage", [box(0, 0, 2000, 1000)]),
    })
    data = catalog.load()
    data["sources"].append({**data["sources"][0], "id": SOURCE, "name": "Test map",
                            "access": {"mode": "package", "account": "none", "coverage": "cases", "url": URL}})
    variable = next(v for v in data["variables"] if v["id"] == VARIABLE)
    variable["sources"] = ["ghsl", SOURCE]
    op = next(o for o in data["operations"] if o["id"] == "read_exposure")
    props = op["params"]["properties"]
    props["flood_layers"]["default"] = [
        {"source": SOURCE, "url": URL, "kind": "observed", "start": "2024-01-01", "end": "2024-01-01",
         "masks": ["*observedEventA*"]},
        {"source": SOURCE, "url": URL, "kind": "hazard", "start": "2025-01-01", "return_years": 50,
         "coverage": f"{SOURCE}/coverage", "coverage_url": URL, "masks": ["hazard"]},
    ]
    monkeypatch.setattr(catalog, "load", lambda: data)
    return data


@pytest.mark.parametrize(
    ("time", "bounds", "expected", "map_limit"),
    [("2024-01-01", (2000,), 125, "Observed extent"),
     ("2025-02-01", (2000,), 200, "Flood hazard for a return period of 50 years"),
     ("2025-02-01", (1000,), 0, "Flood hazard"),
     ("2025-02-01", (4000, 3000), None, None),
     ("2023-01-01", (2000,), None, None)],
)
def test_form_exposure_with_available_and_unavailable_coverage(flood_catalog, monkeypatch,
                                                               time, bounds, expected, map_limit):
    out = api.post_plan(api.PlanRequest(design="exposure", place=_zone(*bounds), variables=[VARIABLE],
                                       period={"start": time, "end": time}))
    assert out["kind"] == "plan" and out["plan"]["design"] == "exposure"
    plan = out["plan"]
    assert executor.validate_plan(plan, flood_catalog) == []
    edited = copy.deepcopy(plan)
    edited["steps"][0]["params"]["flood_layers"][0]["url"] = "https://example.org/other-map"
    assert any("must match the catalogued exposure inputs" in error
               for error in executor.validate_plan(edited, flood_catalog))
    first, second = plan["steps"][:2]
    assert (first["operation"], second["operation"]) == ("read_exposure", "exposure")

    async def immediate(fn, *args):
        return fn(*args)

    monkeypatch.setattr(asyncio, "to_thread", immediate)
    run = Run(new_id(), plan, flood_catalog, REGISTRY)
    run.prepare()
    with pytest.raises(StopIteration) as done:
        run.step(first, []).send(None)
    source = done.value.value
    assert source.ok
    with pytest.raises(StopIteration) as done:
        run.step(second, [source]).send(None)
    outcome = done.value.value
    assert outcome.ok
    result, = outcome.results
    assert result["data_class"] == "approximated"
    if expected is None:
        assert result["absence"]["reason"] == "no_coverage"
        assert result["absence"]["detail"]["es"] and "value" not in result
        return
    assert result.get("value") == pytest.approx(expected, abs=1e-6), result.get("absence")
    assert result["coverage"]["valid_pct"] == pytest.approx(100)
    assert result["support"]["resolution_m"] == 1000
    assert any(map_limit in limit["en"] and limit["es"] for limit in result["limits"])
    if time == "2024-01-01":
        assert source.results[0]["diagnostics"]["repaired_geometries"] == 1


def test_exposure_uses_finest_covering_grid_and_separates_categories(flood_catalog, tmp_path):
    ready = tmp_path / "ready"
    grids = json.loads((ready / "grids/index.json").read_text(encoding="utf-8"))["entries"]
    fine = "ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0/GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C19.tif"
    grids[fine] = {"path": "ready/grids/population-fine.tif"}
    with rasterio.open(tmp_path / grids[fine]["path"], "w", driver="GTiff", width=20, height=10, count=1,
                       dtype="float32", crs=CRS, transform=from_origin(0, 1000, 100, 100)) as image:
        image.write(np.full((10, 20), 2, dtype=np.float32), 1)
    _index(ready, "grids", grids)
    parameters = next(op for op in flood_catalog["operations"] if op["id"] == "read_exposure")["params"]["properties"]
    base = parameters["flood_layers"]["default"][0]
    for name, area in (("flooded_area", box(0, 0, 500, 1000)), ("flood_trace", box(500, 0, 1500, 1000))):
        name_in_index = f"{SOURCE}/maps__{name}"
        layers = json.loads((ready / "vectors/index.json").read_text(encoding="utf-8"))["entries"]
        layers[name_in_index] = _layer(tmp_path, name, [area])
        _index(ready, "vectors", layers)
    from hup_data.readers.exposure import read_exposure

    parts = read_exposure({"grid": parameters["grid"]["default"], "flood_layers": [
        {**base, "masks": [f"maps__{name}"], "category": name} for name in ("flooded_area", "flood_trace")
    ]}, _zone(2000), {"start": "2024-01-01", "end": "2024-01-01"})["categories"]
    assert parts["flooded_area"]["data"]["population"].sum() == pytest.approx(100)
    assert parts["flood_trace"]["data"]["population"].sum() == pytest.approx(200)
    assert all(part["support"]["resolution_m"] == 100 for part in parts.values())


def test_exposure_prefers_the_register_when_its_sections_cover_the_zone(flood_catalog, monkeypatch):
    from hup_data.readers import exposure, ine_population, population

    source = {"source": "ine-population", "url": "https://www.ine.es/"}
    chosen = {"ok": True, "year": 2025, "outside": 0, "sources": [source], "built": [True]}
    monkeypatch.setattr(ine_population, "choose", lambda geometry: chosen)
    monkeypatch.setattr(ine_population, "count", lambda geometry, census: {
        "people": 48.5, "sections": 2, "by_area": 0, "pooled": 0,
    })

    def should_not_read_grid():
        pytest.fail("GHSL must not supply population here")

    monkeypatch.setattr(population, "ghsl_grids", should_not_read_grid)
    properties = next(op for op in flood_catalog["operations"] if op["id"] == "read_exposure")["params"]["properties"]
    params = {key: properties[key]["default"] for key in ("grid", "flood_layers")}
    got = exposure.read_exposure(params, _zone(2000), {"start": "2024-01-01", "end": "2024-01-01"})
    assert got["data"]["population"].sum() == pytest.approx(48.5)
    assert got["sources"][0] == source and got["support"]["resolution_m"] == 100
    assert got["diagnostics"]["census_sections"] == 2
    assert any("INE" in limit["en"] and "INE" in limit["es"] for limit in got["limits"])
