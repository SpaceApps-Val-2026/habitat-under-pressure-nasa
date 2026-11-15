"""Masks from official layers: surface, share of the zone and people inside, with layers prepared by the real pipeline.

Synthetic layers have known areas. The fixture snczi_q100_valencia.geojson is a crop of the real SNCZI T100 layer
(Júcar, © Ministerio para la Transición Ecológica y el Reto Demográfico) around -0.43..-0.39, 39.41..39.45.
"""
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import rasterio
import shapely
from pyproj import Geod, Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from hup_data import masks
from hup_data.prepare import vectors

FIXTURES = Path(__file__).parent / "fixtures"
GEOD = Geod(ellps="WGS84")
MOLLWEIDE = "ESRI:54009"
GHSL_KEY = "ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.tif"
ORIGIN = (-30000.0, 4770000.0)  # Mollweide metres, about 0.35 W, 39.6 N


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


def prepare_layer(data, source, name, geometries):
    """A GeoJSON layer in packages/, prepared to GeoParquet by the real task and then removed."""
    folder = data / "packages" / source
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{name}.geojson"
    if isinstance(geometries, Path):
        shutil.copy(geometries, file)
    else:
        features = [{"type": "Feature", "properties": {"n": n}, "geometry": mapping(geometry)}
                    for n, geometry in enumerate(geometries)]
        file.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    path = file.relative_to(data).as_posix()
    line = {"source": source, "item": name, "scope": "global", "path": path, "mb": 1, "format": "geojson",
            "license": "test", "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload", "url": "https://example.org/layer"}}
    with (data / "manifest.jsonl").open("a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(line) + "\n")
    assert not vectors.build()["failed"]
    file.unlink()
    return f"{source}/{name}"


def zone(geometry):
    return {"id": "z", "name": "z", "bbox": list(geometry.bounds), "geometry": mapping(geometry)}


def hectares(geometry):
    """Geodesic area, computed apart from the code under test (edges densified to about 10 m)."""
    return abs(GEOD.geometry_area_perimeter(shapely.segmentize(geometry, 1e-4))[0]) / 1e4


@pytest.fixture
def halves(data):
    """A layer whose features are the western half of the test zone and a square far from it."""
    return prepare_layer(data, "test-layer", "halves", [box(0.0, 40.0, 0.01, 40.02), box(0.5, 40.5, 0.52, 40.52)])


def test_mask_surface_and_share_of_the_zone_are_exact(halves):
    square = box(0.0, 40.0, 0.02, 40.02)
    got = masks.zone_mask(halves, zone(square))
    assert got["absence"] is None and got["n_features"] == 1
    assert got["hectares"] == pytest.approx(hectares(box(0.0, 40.0, 0.01, 40.02)), rel=1e-5)
    assert got["percent"] == pytest.approx(50.0, abs=1e-6)  # a meridian halves a box of equal longitudes
    assert got["coverage"]["valid_pct"] == 100 and got["limits"] == []
    assert got["sources"][0] == {"source": "test-layer", "product": "halves", "url": "https://example.org/layer",
                                 "license": "test"}


def test_mask_follows_the_outline_of_the_zone_not_its_box(halves):
    triangle = shapely.segmentize(shapely.Polygon([(0.0, 40.0), (0.02, 40.0), (0.0, 40.02)]), 1e-4)
    got = masks.zone_mask(halves, zone(triangle))
    inside = box(0.0, 40.0, 0.01, 40.02).intersection(triangle)
    assert got["hectares"] == pytest.approx(hectares(inside), rel=1e-5)
    assert got["percent"] == pytest.approx(100 * hectares(inside) / hectares(triangle), rel=1e-5)
    assert triangle.buffer(1e-9).contains(got["geometry"])


def test_outside_the_layer_is_no_coverage_inside_without_features_is_zero(halves):
    far = masks.zone_mask(halves, zone(box(10.0, 50.0, 10.1, 50.1)))
    assert far["absence"]["reason"] == "no_coverage" and far["hectares"] is None
    empty = masks.zone_mask(halves, zone(box(0.2, 40.2, 0.22, 40.22)))
    assert empty["absence"] is None and empty["hectares"] == 0 and empty["percent"] == 0
    missing = masks.zone_mask("test-layer/not-there", zone(box(0.0, 40.0, 0.02, 40.02)))
    assert missing["absence"]["reason"] == "needs_package"


def test_a_zone_partly_outside_the_layer_says_how_much_is_unknown(halves):
    across = box(-0.02, 40.0, 0.02, 40.02)  # the layer begins at 0 E
    got = masks.zone_mask(halves, zone(across))
    assert got["coverage"]["valid_pct"] == pytest.approx(50, abs=0.01)
    assert "unknown, not zero" in got["limits"][0]["en"]


def test_real_snczi_crop_surface(data):
    key = prepare_layer(data, "snczi", "snczi_q100_valencia", FIXTURES / "snczi_q100_valencia.geojson")
    import geopandas as gpd

    square = box(-0.42, 39.42, -0.40, 39.44)
    expected = gpd.read_file(FIXTURES / "snczi_q100_valencia.geojson").union_all().intersection(square)
    got = masks.zone_mask(key, zone(square))
    assert got["absence"] is None and got["hectares"] > 0
    assert got["hectares"] == pytest.approx(hectares(expected), rel=1e-4)


@pytest.fixture
def ghsl(data):
    """A 20 x 20 GHSL-like grid of 1 km cells in Mollweide; cell (row, col) holds 100 * row + col people."""
    values = (100 * np.arange(20)[:, None] + np.arange(20)[None, :]).astype("float64")
    path = data / "ready" / "grids" / GHSL_KEY
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", height=20, width=20, count=1, dtype="float64", crs=MOLLWEIDE,
                       transform=from_origin(*ORIGIN, 1000, 1000), nodata=-200.0) as out:
        out.write(values, 1)
    entry = {"source": "ghsl", "path": path.relative_to(data).as_posix(), "version": "R2023A V1-0",
             "license": "CC BY 4.0", "from": "packages/ghsl/x.zip"}
    (data / "ready" / "grids" / "index.json").write_text(json.dumps({"entries": {GHSL_KEY: entry}}), "utf-8")
    return values


def cell_block(row0, row1, col0, col1):
    x, y = ORIGIN
    back = Transformer.from_crs(MOLLWEIDE, 4326, always_xy=True).transform
    block = shapely.segmentize(box(x + 1000 * col0, y - 1000 * row1, x + 1000 * col1, y - 1000 * row0), 50)
    return shapely.transform(block, lambda xy: np.column_stack(back(xy[:, 0], xy[:, 1])))


def test_population_in_mask_sums_the_cells_under_the_mask(data, ghsl):
    key = prepare_layer(data, "test-layer", "block", [cell_block(4, 7, 5, 9)])
    got = masks.population_in_mask(key, zone(cell_block(2, 10, 2, 12)))
    assert got["absence"] is None and got["year"] == 2025
    assert got["population"] == pytest.approx(ghsl[4:7, 5:9].sum(), rel=1e-5)
    assert got["support"]["cells"] == pytest.approx(12, rel=1e-4) and got["hectares"] == pytest.approx(1200, rel=1e-2)
    assert [s["source"] for s in got["sources"]] == ["test-layer", "ghsl"]


def test_population_in_an_empty_mask_is_zero_and_outside_is_an_absence(data, ghsl):
    key = prepare_layer(data, "test-layer", "block", [cell_block(4, 7, 5, 9), cell_block(16, 18, 16, 18)])
    empty = masks.population_in_mask(key, zone(cell_block(10, 14, 10, 14)))
    assert empty["absence"] is None and empty["population"] == 0
    far = masks.population_in_mask(key, zone(box(10.0, 50.0, 10.1, 50.1)))
    assert far["absence"]["reason"] == "no_coverage" and len(far["sources"]) == 2


TILE_KEY = "ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C19/GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C19.tif"


@pytest.fixture
def tile(data, ghsl):
    """A 100 m tile of the same epoch over km rows and cols 0..12: each km cell's people in its top-left 100 m cell."""
    fine = np.zeros((120, 120))
    fine[::10, ::10] = ghsl[:12, :12]
    path = data / "ready" / "grids" / TILE_KEY
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", height=120, width=120, count=1, dtype="float64", crs=MOLLWEIDE,
                       transform=from_origin(*ORIGIN, 100, 100), nodata=-200.0) as out:
        out.write(fine, 1)
    index = data / "ready" / "grids" / "index.json"
    entries = json.loads(index.read_text("utf-8"))["entries"]
    entries[TILE_KEY] = {"source": "ghsl", "path": path.relative_to(data).as_posix(), "version": "R2023A V1-0",
                         "license": "CC BY 4.0", "from": "packages/ghsl/tile.zip"}
    index.write_text(json.dumps({"entries": entries}), "utf-8")
    return fine


def corner_of(row, col):
    """The top-left 100 m cell of km cell (row, col), in EPSG:4326."""
    x, y = ORIGIN[0] + 1000 * col, ORIGIN[1] - 1000 * row
    back = Transformer.from_crs(MOLLWEIDE, 4326, always_xy=True).transform
    return shapely.transform(shapely.segmentize(box(x, y - 100, x + 100, y), 10),
                             lambda xy: np.column_stack(back(xy[:, 0], xy[:, 1])))


def test_population_in_mask_uses_the_100m_tile_when_it_covers_the_zone(data, ghsl, tile):
    key = prepare_layer(data, "test-layer", "corner", [corner_of(5, 6)])
    got = masks.population_in_mask(key, zone(cell_block(2, 10, 2, 11)))
    assert got["absence"] is None and got["year"] == 2025 and got["support"]["resolution_m"] == 100
    assert got["population"] == pytest.approx(ghsl[5, 6], rel=1e-3)  # 1 km would give a hundredth of it
    assert got["sources"][1]["product"] == "GHS-POP 100 m"


def test_population_in_mask_falls_back_to_1km_when_the_tile_misses_part_of_the_zone(data, ghsl, tile):
    """Regression: a 100 m tile and the 1 km grid of the same epoch; a zone beyond the tile uses the 1 km grid."""
    key = prepare_layer(data, "test-layer", "block", [cell_block(10, 14, 10, 14)])
    got = masks.population_in_mask(key, zone(cell_block(8, 16, 8, 16)))
    assert got["absence"] is None and got["support"]["resolution_m"] == 1000
    assert got["population"] == pytest.approx(ghsl[10:14, 10:14].sum(), rel=1e-5)
    assert any("does not cover the whole zone" in limit["en"] for limit in got["limits"])


@pytest.mark.data
def test_real_flood_zone_of_a_real_municipality():
    """SNCZI T100 (Júcar) inside Alzira, from the INE census sections: hectares, share, people and their sources."""
    from hup_data.readers import population
    from hup_data.readers import vectors as layers

    found = layers.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.6, 39.0, -0.3, 39.3])
    if found["absence"] is not None or not population.ghsl_grids() or "snczi/snczi_q100_jucar" not in layers.layers():
        pytest.skip("INE sections, GHSL or SNCZI are not prepared on this computer")
    alzira = shapely.union_all(found["data"][found["data"].CUMUN == "46017"].geometry.values)
    got = masks.population_in_mask("snczi/snczi_q100_jucar", zone(alzira))
    assert got["absence"] is None and 0 < got["percent"] < 100 and got["hectares"] > 0
    assert 0 < got["population"] < 48236  # fewer than the whole municipality (INE, 1 January 2025)
    expected = ["snczi", "ine-population", "ine", "ghsl-built-s"] if got["sources"][1]["source"] == "ine-population" \
        else ["snczi", "ghsl"]  # the INE where its tables are prepared, GHSL otherwise
    assert [s["source"] for s in got["sources"]] == expected
