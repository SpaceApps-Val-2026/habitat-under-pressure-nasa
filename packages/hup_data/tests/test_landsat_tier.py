"""Landsat tiers: by default (`tier1_only=True`, decision 1 of A10) only Collection 2 Tier 1 scenes count; with
`tier1_only=False` every tier enters, as before the decision.

Synthetic scenes copy what the Tier 2 scenes over Alzira look like (.dev/notes/2026-11-14-traspaso-landsat-tier2.md):
a cloud deck with reflectance near 1 that QA_PIXEL flags as high-confidence snow, which is also "clear" (30048),
at about -47 °C. A Tier 1 scene beside it is ordinary land at 30 °C.
"""
import inspect
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from hup_data import readers
from hup_data.readers import landsat, landsat_grid

ZONE = {"id": "tier-zone", "name": "Tiers", "bbox": [-0.34, 39.72, -0.30, 39.75]}
PERIOD = {"start": "2022-01-01", "end": "2022-12-31"}
LAND = {"qa_pixel": 1 << 6 | 1 << 8 | 1, "red": (0.10,), "nir08": (0.30,), "lwir11": 30.0}
DECK = {"qa_pixel": 30048, "red": (0.98,), "nir08": (0.91,), "lwir11": -47.0}  # snow + clear, as in QA_PIXEL


def digital(band, value):
    if band == "qa_pixel":
        return value
    if band == "lwir11":
        return int(round((value + 273.15 - 149.0) / 0.00341802))
    return int(round((value[0] + 0.2) / 2.75e-5))


def scene(folder, name, date, kind):
    assets = {}
    for band, value in kind.items():
        path = folder / f"{name}_{band}.tif"
        with rasterio.open(path, "w", driver="GTiff", height=400, width=400, count=1, dtype="uint16",
                           crs="EPSG:32630", transform=from_origin(726000, 4406000, 30, 30)) as out:
            out.write(np.full((400, 400), digital(band, value), dtype="uint16"), 1)
        assets[band] = SimpleNamespace(href=str(path))
    return SimpleNamespace(id=name, datetime=datetime.fromisoformat(date), assets=assets,
                           properties={"platform": "landsat-9", "eo:cloud_cover": 15.7})


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    (tmp_path / "scenes").mkdir()
    scenes = [scene(tmp_path / "scenes", "LC09_L2SP_198033_20221110_02_T1", "2022-11-10", LAND),
              scene(tmp_path / "scenes", "LC09_L2SP_198033_20221118_02_T2", "2022-11-18", DECK)]
    monkeypatch.setattr(landsat, "_search", lambda zone, year, months: scenes)
    return tmp_path


def test_the_tier_is_the_last_field_of_the_identifier():
    assert [landsat.tier(name) for name in ("LC08_L2SP_199033_20200316_02_T2", "LC09_L2SP_198033_20221110_02_T1",
                                            "LC08_L2SP_198033_20240101_02_RT")] == ["T2", "T1", "RT"]


def test_by_default_only_tier1_enters_and_says_so(data):
    """Decision 1 of A10 (Guillem, 2026-11-14): the default is Tier 1 only, in every Landsat entry point."""
    for function in (landsat.read, landsat.scene_table, landsat_grid.read, readers.read_series):
        assert inspect.signature(function).parameters["tier1_only"].default is True
    assert readers.READERS["lst"][0] == readers.READERS["ndvi"][0] == "2"  # what it serves changed: new derived keys
    got = landsat.read("lst", ZONE, PERIOD)
    assert got["data"].t.tolist() == ["2022-11-10"] and got["data"].v.iloc[0] == pytest.approx(30.0, abs=0.01)
    assert got["limits"] == [landsat.tier1_limit(1)]
    assert landsat.scene_table(ZONE, PERIOD)["data"]["item"].tolist() == ["LC09_L2SP_198033_20221110_02_T1"]


def test_tier1_only_false_lets_every_tier_in_without_the_limit(data):
    got = landsat.read("lst", ZONE, PERIOD, tier1_only=False)
    assert got["data"].v.tolist() == pytest.approx([30.0, -47.0], abs=0.01)
    assert "limits" not in got


def test_tier1_only_leaves_the_cloud_deck_out_and_says_so(data):
    got = landsat.read("lst", ZONE, PERIOD, tier1_only=True)
    assert got["data"].t.tolist() == ["2022-11-10"] and got["data"].v.iloc[0] == pytest.approx(30.0, abs=0.01)
    assert "1 Tier 2" in got["limits"][0]["en"] and "1 escena(s) T2" in got["limits"][0]["es"]
    # The cache keeps both tiers: the read with every tier afterwards still sees the T2 scene.
    assert len(landsat.read("lst", ZONE, PERIOD, tier1_only=False)["data"]) == 2
    table = landsat.scene_table(ZONE, PERIOD, tier1_only=True)["data"]
    assert table["item"].tolist() == ["LC09_L2SP_198033_20221110_02_T1"]


def test_tier1_only_with_nothing_left_is_an_absence(data, monkeypatch):
    got = landsat.read("lst", ZONE, {"start": "2022-11-15", "end": "2022-11-30"}, tier1_only=True)
    assert got["absence"]["reason"] == "insufficient_qa"


def test_read_series_keeps_the_two_readings_apart(data):
    plain = readers.read_series("lst", ZONE, PERIOD, tier1_only=False)
    tier1 = readers.read_series("lst", ZONE, PERIOD, tier1_only=True)
    assert plain["input_fingerprint"] != tier1["input_fingerprint"]
    assert len(plain["data"]) == 2 and len(tier1["data"]) == 1
    again = readers.read_series("lst", ZONE, PERIOD, tier1_only=True)
    assert again["origin"] == "derived" and len(again["data"]) == 1 and "T2" in again["limits"][0]["es"]
    assert len(readers.read_series("lst", ZONE, PERIOD, tier1_only=False)["data"]) == 2
    default = readers.read_series("lst", ZONE, PERIOD)
    assert default["input_fingerprint"] == tier1["input_fingerprint"] and len(default["data"]) == 1


def test_the_grid_reader_takes_the_same_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setattr(landsat_grid.landsat, "water_tile", lambda zone: None)
    items = []
    for name, celsius, day in (("LC09_X_198033_20220710_02_T1", 30.0, 10), ("LC09_X_198033_20220718_02_T2", -47.0, 18)):
        path = tmp_path / f"{name}.tif"
        with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint16",
                           crs=landsat_grid.CRS, transform=from_origin(0, 200, 100, 100)) as image:
            image.write(np.full((2, 2), digital("lwir11", celsius), dtype=np.uint16), 1)
        quality = tmp_path / f"{name}_qa.tif"
        with rasterio.open(quality, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint16",
                           crs=landsat_grid.CRS, transform=from_origin(0, 200, 100, 100)) as image:
            image.write(np.full((2, 2), 64, dtype=np.uint16), 1)
        items.append(SimpleNamespace(id=name, datetime=datetime(2022, 7, day),
                                     assets={"qa_pixel": SimpleNamespace(href=quality),
                                             "lwir11": SimpleNamespace(href=path)},
                                     properties={"platform": "landsat-9"}))
    monkeypatch.setattr(landsat_grid.landsat, "_search", lambda *args: items)
    zone = {"name": "Grid", "bbox": list(transform_bounds(landsat_grid.CRS, "EPSG:4326", 10, 10, 190, 190))}
    period = {"start": "2022-06-01", "end": "2022-09-30"}
    plain = landsat_grid.read("lst", zone, period, 100, 1, tier1_only=False)
    tier1 = landsat_grid.read("lst", zone, period, 100, 1, tier1_only=True)
    assert landsat_grid.read("lst", zone, period, 100, 1)["input_fingerprint"] == tier1["input_fingerprint"]
    assert plain["data"].v.tolist() == pytest.approx([30.0, -47.0], abs=0.01)
    assert tier1["data"].v.tolist() == pytest.approx([30.0], abs=0.01)
    assert plain["input_fingerprint"] != tier1["input_fingerprint"]
    assert not any("Tier 1" in limit["en"] for limit in plain["limits"])
    assert "1 Tier 2" in tier1["limits"][-1]["en"]


@pytest.mark.data
def test_real_alzira_tier2_scenes_are_the_cold_outliers(monkeypatch):
    """The cached Alzira medians (2013-2025): the three T2 scenes are the only ones below 0 °C or NDVI 0.05."""
    monkeypatch.setenv("HUP_OFFLINE", "1")
    alzira = {"id": "osm-relation-345244", "name": "Alzira", "bbox": [-0.6476, 39.068952, -0.303263, 39.186902]}
    period = {"start": "2013-01-01", "end": "2025-12-31"}
    table = landsat.scene_table(alzira, period, tier1_only=False)
    if table["absence"] is not None:
        pytest.skip("The Alzira Landsat cache is not on this computer")
    scenes: pd.DataFrame = table["data"]
    odd = scenes[(scenes.lst < 0) | (scenes.ndvi < 0.05)]
    assert sorted(odd["item"]) == ["LC08_L2SP_199033_20200316_02_T2", "LC09_L2SP_198033_20221118_02_T2",
                                   "LC09_L2SP_199033_20250306_02_T2"]
    assert set(scenes.loc[scenes["item"].map(landsat.tier) != "T1", "item"]) == set(odd["item"])
    tier1 = landsat.read("lst", alzira, period, tier1_only=True)
    assert tier1["data"].v.min() > 0 and len(tier1["data"]) == len(scenes) - 3
    assert "3 Tier 2" in tier1["limits"][0]["en"]
