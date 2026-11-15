from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from hup_data.readers import landsat_grid


@pytest.mark.parametrize("with_water", [False, True])
def test_landsat_pixels_are_resampled_to_the_same_grid_before_zonal_medians(tmp_path, monkeypatch, with_water):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    bbox = transform_bounds(landsat_grid.CRS, "EPSG:4326", 10, 10, 190, 190)
    zone = {"name": "Synthetic area", "bbox": bbox}
    asset_values = {"qa_pixel": 64, "red": 10000, "nir08": 16000, "lwir11": 45000}
    assets = {}
    for band, value in asset_values.items():
        size = 100 if band == "lwir11" else 25
        path = tmp_path / f"{band}.tif"
        with rasterio.open(path, "w", driver="GTiff", width=200 // size, height=200 // size, count=1,
                           dtype="uint16", crs=landsat_grid.CRS, transform=from_origin(0, 200, size, size)) as image:
            image.write(np.full((200 // size, 200 // size), value, dtype=np.uint16), 1)
        assets[band] = SimpleNamespace(href=path)
    item = SimpleNamespace(assets=assets, id="LC08_L2SP_000000_20240701_02_T1",  # Tier 1: kept by default
                           datetime=datetime(2024, 7, 1, tzinfo=UTC),
                           properties={"platform": "landsat-8"})
    monkeypatch.setattr(landsat_grid.landsat, "_search", lambda *args: [item])
    if with_water:
        tile = {"sha256": "0" * 64, "path": "water.tif", "version": "1", "item": "water", "ingest": {},
                "license": "test"}
        with rasterio.open(tmp_path / tile["path"], "w", driver="GTiff", width=2, height=2, count=1, dtype="uint8",
                           crs=landsat_grid.CRS, transform=from_origin(0, 200, 100, 100)) as image:
            image.write(np.array([[0, 100], [0, 100]], dtype=np.uint8), 1)
        monkeypatch.setattr(landsat_grid.landsat, "water_tile", lambda zone: tile)
    period = {"start": "2024-06-01", "end": "2024-09-30"}
    ndvi, lst = [landsat_grid.read(variable, zone, period, 100, 1) for variable in ("ndvi", "lst")]
    assert ndvi["support"] == lst["support"] == {"resolution_m": 100, "since": 2013, "cells": 2 if with_water else 4}
    assert ("jrc-gsw" in {source["source"] for source in lst["sources"]}) == with_water
    assert ndvi["data"].iloc[0].v == pytest.approx(0.165 / 0.315)
    assert lst["data"].iloc[0].v == pytest.approx(45000 * 0.00341802 + 149.0 - 273.15)


def test_impossible_minimum_is_reported_before_reading_any_pixels(monkeypatch):
    def unexpected(*args):
        pytest.fail("a grid was opened for a period too short to test")

    monkeypatch.setattr(landsat_grid.landsat, "_search", unexpected)
    outcome = landsat_grid.read("lst", {"bbox": [0, 1, 1, 2]}, {"start": "2013-01-01", "end": "2025-12-31"}, 100, 15)
    assert outcome["absence"]["reason"] == "insufficient_qa"
    assert all("13" in line and "15" in line for line in outcome["absence"]["detail"].values())


def test_a_scene_too_cloudy_for_the_grid_is_left_before_its_value_bands_are_read(tmp_path, monkeypatch):
    """Half the cells clear (under MIN_CLEAR): the scene is out as before, and only its quality band is opened."""
    bbox = transform_bounds(landsat_grid.CRS, "EPSG:4326", 10, 10, 190, 190)
    zone = {"name": "Synthetic area", "bbox": bbox}
    grid, inside = landsat_grid._grid(zone, 100)
    quality = np.where(np.arange(inside.size).reshape(inside.shape) % 2 == 0, 64, 8 | 1).astype(np.uint16)
    opened = []

    def band(item, name, grid):
        opened.append(name)
        return quality if name == "qa_pixel" else np.full(quality.shape, 45000, dtype=np.uint16)

    monkeypatch.setattr(landsat_grid, "_band", band)
    item = SimpleNamespace(id="LC08_L2SP_000000_20240701_02_T1", datetime=datetime(2024, 7, 1, tzinfo=UTC),
                           properties={"platform": "landsat-8"})
    for variable in ("lst", "ndvi"):
        assert landsat_grid._scene(item, grid, inside, variable) is None
    assert opened == ["qa_pixel", "qa_pixel"]
    quality[:] = 64  # all clear: the value bands are read as before
    assert landsat_grid._scene(item, grid, inside, "lst") is not None
    assert opened[-2:] == ["qa_pixel", "lwir11"]



def cloudy_grid(tmp_path, monkeypatch, fail=False):
    """A past summer with one Tier 1 scene whose quality band is all cloud (or cannot be read), counting band reads."""
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    item = SimpleNamespace(id="LC08_L2SP_000000_20240701_02_T1", datetime=datetime(2024, 7, 1, tzinfo=UTC),
                           properties={"platform": "landsat-8"})
    opened = []

    def band(item, name, grid):
        opened.append(name)
        if fail:
            raise rasterio.errors.RasterioError("HTTP timeout")
        return np.full((grid["height"], grid["width"]), 8 | 1, dtype=np.uint16)  # cloud

    monkeypatch.setattr(landsat_grid.landsat, "_search", lambda *args: [item])
    monkeypatch.setattr(landsat_grid, "_band", band)
    zone = {"name": "Coastal ring", "bbox": transform_bounds(landsat_grid.CRS, "EPSG:4326", 10, 10, 190, 190)}
    period = {"start": "2024-06-01", "end": "2024-09-30", "months": [6, 7, 8, 9]}
    return opened, lambda: landsat_grid.read("lst", zone, period, 100, 1)


def test_a_grid_with_no_usable_scene_is_answered_again_without_reading_scenes(tmp_path, monkeypatch):
    opened, read = cloudy_grid(tmp_path, monkeypatch)
    first = read()
    assert first["absence"]["reason"] == "insufficient_qa" and first["origin"] == "remote" and opened == ["qa_pixel"]
    again = read()
    assert opened == ["qa_pixel"]  # no scene was opened the second time
    assert again["origin"] == "derived"
    assert {k: v for k, v in again.items() if k != "origin"} == {k: v for k, v in first.items() if k != "origin"}


def test_a_saved_absence_is_not_reused_once_a_water_tile_covers_the_zone(tmp_path, monkeypatch):
    opened, read = cloudy_grid(tmp_path, monkeypatch)
    read()
    tile = {"sha256": "1" * 64, "path": "water.tif", "version": "1", "item": "water", "ingest": {}, "license": "test"}
    monkeypatch.setattr(landsat_grid.landsat, "water_tile", lambda zone: tile)
    monkeypatch.setattr(landsat_grid.landsat, "water_path", lambda tile: "water.tif")
    monkeypatch.setattr(landsat_grid.landsat, "_water", lambda path, crs, transform, shape: np.zeros(shape, bool))
    assert read()["origin"] == "remote" and opened == ["qa_pixel", "qa_pixel"]  # a new key: read again


def test_scenes_that_could_not_be_read_leave_no_saved_absence(tmp_path, monkeypatch):
    opened, read = cloudy_grid(tmp_path, monkeypatch, fail=True)
    assert read()["absence"]["reason"] == "insufficient_qa"
    assert read()["origin"] == "remote" and opened == ["qa_pixel", "qa_pixel"]  # asked again next time
