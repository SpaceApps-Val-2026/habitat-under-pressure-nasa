"""Copernicus Imperviousness Density (sealed_surface): synthetic EPSG:3035 grids with known values, so every mean and
every sealed area is known in advance. The test marked `data` reads the real 2018 grid of the cases."""
import hashlib
import json
import zipfile

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely import box, transform
from shapely.geometry import mapping

from hup_data import readers
from hup_data.prepare import unpack
from hup_data.readers import imperviousness

LEFT, TOP, CELL = 3_420_000.0, 1_880_000.0, 10.0  # EPSG:3035 metres near Valencia
VALUES = np.arange(100, dtype="uint8").reshape(10, 10)  # pixel (row, col) is 10 * row + col % sealed
TO_DEGREES = Transformer.from_crs(3035, 4326, always_xy=True)
PERIOD = {"start": "2018-01-01", "end": "2018-12-31"}


def zone(west, south, east, north, zone_id="z"):
    """A zone drawn in EPSG:3035 metres and handed over in degrees, as a place would arrive."""
    geometry = transform(box(west, south, east, north).segmentize(1),
                         lambda xy: np.column_stack(TO_DEGREES.transform(*xy.T)))
    return {"id": zone_id, "geometry": mapping(geometry), "bbox": list(geometry.bounds)}


def pixels(row0, col0, row1, col1):
    """The zone made of whole pixels [row0, row1) × [col0, col1)."""
    return zone(LEFT + col0 * CELL, TOP - row1 * CELL, LEFT + col1 * CELL, TOP - row0 * CELL)


def tif(path, values=VALUES, nodata=255):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0], count=1,
                       dtype="uint8", crs="EPSG:3035", transform=from_origin(LEFT, TOP, CELL, CELL),
                       nodata=nodata) as raster:
        raster.write(values, 1)
    return path


def register(data, path, fmt, **extra):
    line = {"source": "copernicus-imperviousness", **extra, "item": path.stem, "scope": "cases",
            "path": path.relative_to(data).as_posix(), "mb": 0.01,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "format": fmt,
            "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload", "url": "https://example.org/imd"},
            "license": "CLMS data policy"}
    with (data / "manifest.jsonl").open("a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(line) + "\n")


def loose(data, year, values=VALUES, nodata=255):
    path = tif(data / "packages" / "copernicus-imperviousness" / f"IMD_{year}_010m_test_03035.tif", values, nodata)
    register(data, path, "cog", period={"start": f"{year}-01-01", "end": f"{year}-12-31"})


def zipped(data, year, values=None):
    """A ZIP of tiles as WEkEO would hand it over, registered with the year only in its version."""
    path = data / "packages" / "copernicus-imperviousness" / f"wekeo_IMD_{year}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        if values is not None:
            archive.write(tif(data / "tmp" / f"IMD_{year}_010m_E34N18_03035.tif", values),
                          f"IMD_{year}/IMD_{year}_010m_E34N18_03035.tif")
        archive.writestr("readme.txt", "test")
    register(data, path, "zip", version=f"Imperviousness Density {year} 10 m")
    unpack.build()


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    return tmp_path


def test_whole_pixels_give_the_exact_mean_and_sealed_area(data):
    loose(data, 2018)
    result = readers.read_series("sealed_surface", pixels(2, 3, 4, 6), PERIOD)
    assert result["absence"] is None and result["unit"] == "1"
    percents = [10 * row + col for row in (2, 3) for col in (3, 4, 5)]  # 6 pixels of 100 m²
    row = result["data"].iloc[0]
    assert row.t == "2018-01-01" and row.n_valid == 6
    assert row.v == pytest.approx(sum(percents) / 6 / 100, rel=1e-6)
    assert row.surface_m2 == pytest.approx(sum(percents), rel=1e-6)  # p % of 100 m² is p m²
    assert row.zone_area_m2 == pytest.approx(600, rel=1e-6) == row.valid_area_m2
    assert result["support"] == {"resolution_m": 10, "since": 2018, "cells": pytest.approx(6, rel=1e-6)}
    assert result["coverage"] == {"valid_pct": 100.0, "n_dates": 1}
    source = result["sources"][0]
    assert source["source"] == "copernicus-imperviousness" and "10.2909/3bf542bd" in source["version"]


def test_an_edge_pixel_counts_by_the_share_of_its_area(data):
    loose(data, 2018)
    # Half of pixel (5, 5), 55 %, and the whole pixel (5, 6), 56 %: 27.5 + 56 m² sealed over 150 m².
    result = imperviousness.read("sealed_surface", zone(LEFT + 55, TOP - 60, LEFT + 70, TOP - 50), PERIOD)
    row = result["data"].iloc[0]
    assert row.surface_m2 == pytest.approx(27.5 + 56, rel=1e-4)
    assert row.v == pytest.approx((27.5 + 56) / 150, rel=1e-4) and row.valid_area_m2 == pytest.approx(150, rel=1e-4)


def test_pixels_without_data_are_left_out(data):
    values = VALUES.copy()
    values[2, 3] = 255  # outside area
    values[2, 4] = 200  # the nodata the file declares
    loose(data, 2018, values, nodata=200)
    result = imperviousness.read("sealed_surface", pixels(2, 3, 3, 6), PERIOD)  # pixels 23, 24 and 25
    row = result["data"].iloc[0]
    assert row.n_valid == 1 and row.surface_m2 == pytest.approx(25, rel=1e-6) and row.v == pytest.approx(0.25)
    assert result["coverage"]["valid_pct"] == pytest.approx(33.33, abs=0.01)
    assert any("33.33%" in limit["en"] for limit in result["limits"])
    only_nodata = imperviousness.read("sealed_surface", pixels(2, 3, 3, 5), PERIOD)
    assert only_nodata["absence"]["reason"] == "insufficient_qa"


def test_a_value_above_100_is_not_read(data):
    values = VALUES.copy()
    values[0, 0] = 150
    loose(data, 2018, values)
    result = imperviousness.read("sealed_surface", pixels(0, 0, 2, 2), PERIOD)
    assert result["absence"]["reason"] == "insufficient_qa"


def test_absences(data):
    nothing = imperviousness.read("sealed_surface", pixels(0, 0, 2, 2), PERIOD)
    assert nothing["absence"]["reason"] == "needs_package"
    loose(data, 2018)
    before = imperviousness.read("sealed_surface", pixels(0, 0, 2, 2), {"start": "2010-01-01", "end": "2016-12-31"})
    assert before["absence"]["reason"] == "no_coverage"
    later = imperviousness.read("sealed_surface", pixels(0, 0, 2, 2), {"start": "2021-01-01", "end": "2024-12-31"})
    assert later["absence"]["reason"] == "needs_package" and "2021, 2024" in later["absence"]["detail"]["en"]
    far = imperviousness.read("sealed_surface", zone(LEFT + 5_000, TOP - 5_000, LEFT + 5_100, TOP - 4_900), PERIOD)
    assert far["absence"]["reason"] == "no_coverage"
    partly = imperviousness.read("sealed_surface", zone(LEFT + 50, TOP - 150, LEFT + 150, TOP - 50), PERIOD)
    assert partly["absence"]["reason"] == "no_coverage"  # half of it is beyond the grid


def test_a_zip_without_a_geotiff_is_a_clear_needs_package(data):
    loose(data, 2018)
    zipped(data, 2021)
    result = imperviousness.read("sealed_surface", pixels(0, 0, 2, 2), {"start": "2021-01-01", "end": "2021-12-31"})
    assert result["absence"]["reason"] == "needs_package"
    assert "wekeo_IMD_2021.zip" in result["absence"]["detail"]["en"] and ".tif" in result["absence"]["detail"]["en"]


def test_any_registered_year_is_read_from_a_loose_geotiff_or_a_zip_of_tiles(data):
    loose(data, 2018)
    zipped(data, 2021, VALUES + 1)
    result = imperviousness.read("sealed_surface", pixels(2, 3, 4, 6), {"start": "2018-01-01", "end": "2024-12-31"})
    table = result["data"]
    percents = sum(10 * row + col for row in (2, 3) for col in (3, 4, 5))
    assert table.t.tolist() == ["2018-01-01", "2021-01-01"]
    assert table.surface_m2.tolist() == pytest.approx([percents, percents + 6], rel=1e-6)
    assert "2021" in result["sources"][0]["version"] and result["sources"][0]["n_items"] == 2
    english = " ".join(limit["en"] for limit in result["limits"])
    assert "technical change" in english and "pending validation" in english and "2024" in english


def test_sealed_surface_is_served_by_imperviousness_not_by_ghsl():
    assert "sealed_surface" in readers.variables()
    assert readers.READERS["sealed_surface"][1] is imperviousness.read


@pytest.mark.data
def test_real_sealing_of_parc_sagunt_ii_and_paiporta_in_2018():
    import shapely

    from hup_data.readers import vectors as layers

    sections = layers.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.5, 39.3, -0.3, 39.5])
    plan = layers.read_layer("icv-planeamiento/parc-sagunt-ii-plan-especial", None)
    if sections["absence"] is not None or plan["absence"] is not None:
        pytest.skip("INE sections or the Parc Sagunt II plan are not prepared on this computer")
    rows = sections["data"]
    paiporta, sagunt = (shapely.union_all(rows[rows.CUMUN == "46186"].geometry.values),
                        shapely.union_all(plan["data"].geometry.values))
    paiporta, sagunt = ({"id": name, "geometry": mapping(geometry), "bbox": list(geometry.bounds)}
                        for name, geometry in (("paiporta", paiporta), ("parc-sagunt-ii", sagunt)))
    town = readers.read_series("sealed_surface", paiporta, PERIOD)
    site = readers.read_series("sealed_surface", sagunt, PERIOD)
    if town["absence"] or site["absence"]:
        pytest.skip("Copernicus Imperviousness 2018 is not prepared on this computer")
    assert town["support"]["resolution_m"] == 10 and site["coverage"]["valid_pct"] > 99
    # Before the works Parc Sagunt II was farmland, a town in l'Horta Sud is largely sealed.
    assert 0 <= site["data"].v.iloc[0] < 0.05 < 0.3 < town["data"].v.iloc[0] < 0.8
