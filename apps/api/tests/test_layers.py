"""A11 and A11b: map layers of an analysis over HTTP, from a synthetic IMERG package and synthetic Landsat scenes.

No network.
"""
import json

import h5py
import numpy as np
import pytest
from fastapi.testclient import TestClient

from hup_api import contracts, main
from hup_api.library import folder
from hup_data import coverage

ANALYSIS = "layers-test"
ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [-0.35, 39.60, -0.15, 39.75]}


def write_imerg(data, year, month, rate):
    folder_ = data / "packages" / "imerg-monthly"
    folder_.mkdir(parents=True, exist_ok=True)
    lon, lat = np.arange(-179.95, 180, 0.1).astype("float32"), np.arange(-89.95, 90, 0.1).astype("float32")
    path = folder_ / f"imerg_{year}{month:02d}.HDF5"
    with h5py.File(path, "w") as file:
        group = file.create_group("Grid")
        group["lon"], group["lat"] = lon, lat
        grid = np.full((1, lon.size, lat.size), rate, dtype="float32")
        group.create_dataset("precipitation", data=grid).attrs["_FillValue"] = np.array([-9999.9], dtype="float32")
    relative = path.relative_to(data).as_posix()
    return {"source": "imerg-monthly", "item": relative, "scope": "global", "path": relative, "mb": 1,
            "sha256": "0" * 64, "format": "hdf5", "license": "test",
            "period": {"start": f"{year}-{month:02d}-01", "end": f"{year}-{month:02d}-28"},
            "ingest": {"date": "2026-11-12T00:00:00Z", "origin": "predownload", "url": "https://example.org"}}


@pytest.fixture
def client(monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setenv("HUP_DATA_DIR", str(data))
    lines = [write_imerg(data, year, 1, rate) for year, rate in ((2000, 0.1), (2001, 0.3))]
    (data / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    coverage.rebuild({"imerg-monthly": ["precipitation"]})
    folder.directory(ANALYSIS).mkdir(parents=True)
    plan = {"zone": ZONE, "period": {"start": "2000-01-01", "end": "2002-12-31", "months": [1]},
            "variables": ["precipitation", "ndvi"]}
    folder.write_json(ANALYSIS, "plan.json", plan)
    return TestClient(main.app)


def test_layers_list_frames_with_one_range_and_serve_cogs_by_ranges(client):
    response = client.get(f"/api/analyses/{ANALYSIS}/layers")
    assert response.status_code == 200
    item, ndvi = response.json()["items"]
    assert ndvi["variable"] == "ndvi" and ndvi["absence"]["reason"] == "no_coverage"  # Landsat starts in 2013
    assert item["variable"] == "precipitation" and item["absence"] is None and item["format"] == "cog"
    assert item["unit"] == "mm/month" and item["scale"] == "water" and item["crs"] == "EPSG:4326"
    assert item["bounds"] == pytest.approx([-0.4, 39.6, -0.1, 39.8], abs=1e-4)
    assert item["range"] == pytest.approx([0.1 * 24 * 31, 0.3 * 24 * 31], rel=1e-5)
    first, second, third = item["frames"]
    assert [first["t"], second["t"], third["t"]] == ["2000", "2001", "2002"]
    assert third["url"] is None and third["absence"]["reason"] == "insufficient_qa"
    assert third["absence"] == third["result"]["absence"]
    for number, frame in enumerate(item["frames"]):
        body = {**frame["result"], "id": f"r-layer-{number}", "analysis": ANALYSIS, "step": "layer",
                "fingerprint": "0" * 64}
        assert contracts.errors("result", body) == []
    assert first["result"]["layer"]["range"] == second["result"]["layer"]["range"] == item["range"]

    whole = client.get(first["url"])
    assert whole.status_code == 200 and whole.content[:4] in (b"II*\x00", b"MM\x00*")
    part = client.get(first["url"], headers={"Range": "bytes=0-15"})
    assert part.status_code == 206 and part.content == whole.content[:16]

    again = client.get(f"/api/analyses/{ANALYSIS}/layers?variable=precipitation").json()["items"][0]
    assert again["origin"] == "derived" and again["frames"][0]["url"] == first["url"]


def test_a_variable_without_a_grid_is_an_absence_and_bad_ids_are_refused(client):
    ndvi = client.get(f"/api/analyses/{ANALYSIS}/layers?variable=ndvi").json()["items"][0]
    assert ndvi["absence"]["reason"] == "no_coverage" and ndvi["frames"] == []
    assert client.get(f"/api/analyses/{ANALYSIS}/layers?variable=lst").status_code == 404
    assert client.get("/api/analyses/nope/layers").status_code == 404
    for bad in ("../x", "A", "x/y", "%2e%2e"):
        assert client.get(f"/api/analyses/{ANALYSIS}/layers?variable={bad}").status_code in (404, 422)
    assert client.get(f"/api/analyses/{ANALYSIS}/layers?scenario=../../x").status_code == 422


# ---------------------------------------------------------------- Landsat pixels for an impact analysis (A11b)
class Scene:
    """One Landsat scene as the catalogue returns it, with local files: uniform bands in UTM 30N."""

    def __init__(self, folder, name, date, nir):
        import datetime

        import rasterio
        from rasterio.transform import from_origin

        # A Collection 2 Tier 1 id: since decision 1 of A10 the reader keeps only T1 scenes by default.
        self.id, self.properties = f"{name}_02_T1", {"platform": "landsat-8", "eo:cloud_cover": 1.0}
        self.datetime = datetime.datetime.fromisoformat(date)
        self.assets = {}
        values = {"red": 0.10, "nir08": nir}
        for band in ("red", "nir08", "lwir11", "qa_pixel"):
            if band in values:
                value = round((values[band] + 0.2) / 2.75e-5)
            else:
                value = round((25 + 273.15 - 149.0) / 0.00341802) if band == "lwir11" else 1 << 6 | 1
            path = folder / f"{name}_{band}.tif"
            with rasterio.open(path, "w", driver="GTiff", height=500, width=500, count=1, dtype="uint16",
                               crs="EPSG:32630", transform=from_origin(722400, 4408200, 30, 30)) as out:
                out.write(np.full((500, 500), value, dtype="uint16"), 1)
            self.assets[band] = type("Asset", (), {"href": str(path)})()


def test_an_impact_analysis_gets_before_and_after_landsat_frames(client, monkeypatch, tmp_path):
    from hup_data.readers import landsat

    monkeypatch.setenv("HUP_OFFLINE", "0")  # the search and the scenes are doubles: nothing here needs a network
    scenes = {2019: [Scene(tmp_path, "before-1", "2019-07-01", 0.50)], 2020: [],
              2021: [Scene(tmp_path, "after-1", "2021-07-01", 0.30)]}
    by_id = {scene.id: scene for found in scenes.values() for scene in found}
    monkeypatch.setattr(landsat, "_search", lambda zone, year, months: scenes[year])
    monkeypatch.setattr(landsat, "_items", lambda ids: [by_id[name] for name in ids])
    analysis = "impact-layers"
    folder.directory(analysis).mkdir(parents=True)
    small = {"id": "small-zone", "name": "Small zone", "bbox": [-0.34, 39.72, -0.30, 39.75]}
    steps = [{"id": "s1", "operation": "read_series", "params": {"variable": "ndvi", "reference_km": 1}},
             {"id": "s3", "operation": "baci_contrast", "params": {"before": [2019, 2019], "after": [2021, 2021]}}]
    folder.write_json(analysis, "plan.json", {"zone": small, "period": {"start": "2019-01-01", "end": "2021-12-31"},
                                              "variables": ["ndvi"], "steps": steps})

    (item,) = client.get(f"/api/analyses/{analysis}/layers").json()["items"]
    assert item["absence"] is None and item["scale"] == "green" and item["margin_km"] == 1.0
    assert item["windows"] == {"before": [2019, 2019], "after": [2021, 2021]}
    before, after = item["frames"]
    assert (before["window"], before["years"], after["window"]) == ("before", [2019, 2019], "after")
    assert before["url"].endswith(".before.tif") and after["url"].endswith(".after.tif")
    for number, frame in enumerate(item["frames"]):
        body = {**frame["result"], "id": f"r-layer-{number}", "analysis": analysis, "step": "layer",
                "fingerprint": "0" * 64}
        assert contracts.errors("result", body) == []
        assert frame["result"]["layer"]["range"] == item["range"]
        assert client.get(frame["url"], headers={"Range": "bytes=0-15"}).status_code == 206
    assert item["range"][0] == pytest.approx((0.30 - 0.10) / (0.30 + 0.10), abs=1e-4)  # after, less green
    assert item["range"][1] == pytest.approx((0.50 - 0.10) / (0.50 + 0.10), abs=1e-4)
    heat = client.get(f"/api/analyses/{analysis}/layers?variable=lst")
    assert heat.status_code == 404  # not a variable of this plan
