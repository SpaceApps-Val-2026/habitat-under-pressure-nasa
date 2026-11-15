"""GHSL built-up surface: a synthetic Mollweide grid with known values, so every sum is known in advance."""
import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely import box, transform
from shapely.geometry import mapping

from hup_data import readers
from hup_data.prepare import unpack
from hup_data.prepare.common import Index
from hup_data.readers import built_up

LEFT, TOP, CELL = -30_000.0, 4_760_000.0, 100.0  # inside the real tile R5_C19, near Valencia
VALUES = np.arange(100, dtype="uint16").reshape(10, 10) * 100  # cell (row, col) holds 1000 * row + 100 * col m²
TO_DEGREES = Transformer.from_crs("ESRI:54009", 4326, always_xy=True)


def _degrees(geometry):
    return transform(geometry, lambda xy: np.column_stack(TO_DEGREES.transform(*xy.T)))


def zone(west, south, east, north, zone_id="z"):
    """A zone drawn in Mollweide metres and handed over in degrees, as a place would arrive."""
    geometry = _degrees(box(west, south, east, north).segmentize(10))
    return {"id": zone_id, "geometry": mapping(geometry), "bbox": list(geometry.bounds)}


def cells(row0, col0, row1, col1, zone_id="z"):
    """The zone made of whole cells [row0, row1) × [col0, col1)."""
    return zone(LEFT + col0 * CELL, TOP - row1 * CELL, LEFT + col1 * CELL, TOP - row0 * CELL, zone_id)


def write_grid(data, epoch, values=VALUES):
    name = f"GHS_BUILT_S_E{epoch}_GLOBE_R2023A_54009_100_V1_0_R5_C19"
    path = data / "ready" / "grids" / "ghsl-built-s" / name / f"{name}.tif"
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0], count=1,
                       dtype="uint16", crs="ESRI:54009", transform=from_origin(LEFT, TOP, CELL, CELL),
                       nodata=65535) as raster:
        raster.write(values, 1)
    Index("grids", unpack.LAYOUT).put(f"ghsl-built-s/{name}/{name}.tif", {
        "source": "ghsl-built-s", "input_sha256": "0" * 64, "path": path.relative_to(data).as_posix()})


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    write_grid(tmp_path, 2015)
    write_grid(tmp_path, 2020, VALUES + 50)
    return tmp_path


PERIOD = {"start": "2015-01-01", "end": "2020-12-31"}


def test_whole_cells_add_up_exactly(data):
    result = built_up.read("built_up_surface", cells(2, 3, 4, 6), PERIOD)
    assert result["absence"] is None and result["unit"] == "m²"
    expected = sum(1000 * row + 100 * col for row in (2, 3) for col in (3, 4, 5))  # 6 cells
    table = result["data"]
    assert table.t.tolist() == ["2015-01-01", "2020-01-01"]
    assert table.v.tolist() == pytest.approx([expected, expected + 6 * 50], rel=1e-9)
    assert table.fraction.tolist() == pytest.approx([expected / 60_000, (expected + 300) / 60_000], rel=1e-9)
    assert table.n_valid.tolist() == [6, 6] and table.after_last_observation.tolist() == [False, True]
    assert result["support"] == {"resolution_m": 100, "since": 1975, "cells": pytest.approx(6, rel=1e-6)}
    assert result["coverage"] == {"valid_pct": 100.0, "n_dates": 2}
    assert result["sources"][0]["source"] == "ghsl-built-s" and result["sources"][0]["n_items"] == 2
    assert any("2018" in limit["en"] for limit in result["limits"])

    fraction = built_up.read("built_up_fraction", cells(2, 3, 4, 6), PERIOD)
    assert fraction["unit"] == "1" and fraction["data"].v.tolist() == table.fraction.tolist()


def test_a_cell_counts_by_the_share_of_its_area(data):
    # Half of cell (5, 5), 5500 m², and a quarter of cell (5, 6), 5600 m².
    half = zone(LEFT + 500, TOP - 600, LEFT + 550, TOP - 500)
    quarter = zone(LEFT + 600, TOP - 550, LEFT + 650, TOP - 500)
    period = {"start": "2015-01-01", "end": "2015-12-31"}
    assert built_up.read("built_up_surface", half, period)["data"].v.item() == pytest.approx(2750, rel=1e-6)
    assert built_up.read("built_up_surface", quarter, period)["data"].v.item() == pytest.approx(1400, rel=1e-6)
    assert built_up.read("built_up_fraction", half, period)["data"].v.item() == pytest.approx(0.55, rel=1e-6)


def test_cells_without_data_are_left_out_and_said(data):
    values = VALUES.copy()
    values[0, 0] = 65535
    write_grid(data, 2015, values)
    result = built_up.read("built_up_surface", cells(0, 0, 1, 2), {"start": "2015-01-01", "end": "2015-12-31"})
    assert result["data"].v.item() == pytest.approx(100, rel=1e-9)  # only cell (0, 1)
    assert result["data"].fraction.item() == pytest.approx(0.01, rel=1e-9)
    assert result["coverage"]["valid_pct"] == pytest.approx(50, abs=0.01)
    assert any("50" in limit["en"] for limit in result["limits"])


def test_values_outside_the_product_range_are_refused(data):
    write_grid(data, 2015, VALUES + 9000)
    result = built_up.read("built_up_surface", cells(9, 9, 10, 10), {"start": "2015-01-01", "end": "2015-12-31"})
    assert result["data"] is None and result["absence"]["reason"] == "insufficient_qa"


def test_absences_carry_their_reason(data):
    beyond = zone(-42_000, TOP - 500, LEFT + 500, TOP)  # reaches the tile R5_C18, not downloaded
    outside = built_up.read("built_up_surface", beyond, PERIOD)
    assert outside["data"] is None and outside["absence"]["reason"] == "needs_package"
    assert "R5_C18" in outside["absence"]["detail"]["en"] and outside["sources"][0]["source"] == "ghsl-built-s"

    no_epoch = built_up.read("built_up_surface", cells(0, 0, 1, 1), {"start": "2016-01-01", "end": "2019-12-31"})
    assert no_epoch["absence"]["reason"] == "no_coverage"
    not_prepared = built_up.read("built_up_surface", cells(0, 0, 1, 1), {"start": "2000-01-01", "end": "2010-12-31"})
    assert not_prepared["absence"]["reason"] == "needs_package"

    partial = built_up.read("built_up_surface", cells(0, 0, 1, 1), {"start": "2015-01-01", "end": "2025-12-31"})
    assert len(partial["data"]) == 2 and any("2025" in limit["en"] for limit in partial["limits"])


def test_tile_ids_follow_the_ghsl_grid():
    assert built_up.tiles_for(box(-31_000, 4_700_000, -30_000, 4_701_000)) == ["R5_C19"]
    assert built_up.tiles_for(box(-42_000, 4_700_000, -40_000, 4_701_000)) == ["R5_C18", "R5_C19"]


def test_the_common_door_reads_and_keeps_the_series(data):
    first = readers.read_series("built_up_surface", cells(2, 3, 4, 6, "door"), PERIOD)
    assert first["absence"] is None and first["origin"] == "package" and first["input_fingerprint"]
    again = readers.read_series("built_up_surface", cells(2, 3, 4, 6, "door"), PERIOD)
    assert again["origin"] == "derived" and again["data"].v.tolist() == first["data"].v.tolist()
