"""Variables and utilities for the A10 chains: built_up_fraction, flood_extent, flood_hazard_extent,
population_in_hot_cells, the common grid and the regional ring. Synthetic layers and grids with known areas; the
tests marked `data` read the real layers of the cases."""
import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest
import rasterio
import shapely
from pyproj import Geod, Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, mapping, shape

from hup_data import grid, masks, places, readers, store
from hup_data.prepare import unpack, vectors
from hup_data.prepare.common import Index
from hup_data.readers import built_up, flood, hot_cells

GEOD = Geod(ellps="WGS84")
MOLLWEIDE = "ESRI:54009"
TO_DEGREES = Transformer.from_crs(MOLLWEIDE, 4326, always_xy=True)
LEFT, TOP = -30_000.0, 4_760_000.0  # Mollweide metres near Valencia
PERIOD = {"start": "2013-01-01", "end": "2025-12-31"}


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    return tmp_path


def zone(geometry, zone_id="z"):
    return {"id": zone_id, "name": zone_id, "bbox": list(geometry.bounds), "geometry": mapping(geometry),
            "source": "test"}


def hectares(geometry):
    return abs(GEOD.geometry_area_perimeter(shapely.segmentize(geometry, 1e-4))[0]) / 1e4


def manifest_line(data, line):
    with (data / "manifest.jsonl").open("a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(line) + "\n")


def layer(data, source, name, features):
    """A GeoJSON layer with properties, prepared to GeoParquet by the real task. features: [(geometry, props)]."""
    folder = data / "packages" / source
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{name}.geojson"
    file.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props, "geometry": mapping(geometry)} for geometry, props in features]}),
        encoding="utf-8")
    manifest_line(data, {"source": source, "item": name, "scope": "global", "path": file.relative_to(data).as_posix(),
                         "mb": 1, "format": "geojson", "license": "test",
                         "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                         "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload",
                                    "url": "https://example.org/layer"}})
    assert not vectors.build()["failed"]
    return f"{source}/{name}"


# ------------------------------------------------------------ built_up_fraction (one name; sealed_surface is not GHSL)
def test_built_up_fraction_is_the_only_name_for_the_built_up_share(data):
    values = (np.arange(100, dtype="uint16").reshape(10, 10) * 100)
    name = "GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0_R5_C19"
    path = data / "ready" / "grids" / "ghsl-built-s" / name / f"{name}.tif"
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", width=10, height=10, count=1, dtype="uint16", crs=MOLLWEIDE,
                       transform=from_origin(LEFT, TOP, 100, 100), nodata=65535) as raster:
        raster.write(values, 1)
    Index("grids", unpack.LAYOUT).put(f"ghsl-built-s/{name}/{name}.tif", {
        "source": "ghsl-built-s", "input_sha256": "0" * 64, "path": path.relative_to(data).as_posix()})
    square = shapely.transform(box(LEFT + 200, TOP - 400, LEFT + 500, TOP - 200).segmentize(10),
                               lambda xy: np.column_stack(TO_DEGREES.transform(*xy.T)))
    period = {"start": "2020-01-01", "end": "2020-12-31"}
    served = readers.read_series("built_up_fraction", zone(square), period)
    fraction = built_up.read("built_up_fraction", zone(square), period)
    expected = sum(1000 * row + 100 * col for row in (2, 3) for col in (2, 3, 4)) / 60_000  # 6 whole cells
    assert served["absence"] is None and served["unit"] == "1" and served["support"]["resolution_m"] == 100
    assert served["data"].v.tolist() == pytest.approx([expected], rel=1e-9) == fraction["data"].v.tolist()
    assert any("2018" in limit["en"] for limit in served["limits"])  # last observation, epoch 2020 carried forward
    # sealed_surface is Copernicus imperviousness (readers/imperviousness.py), never the GHSL footprint.
    assert readers.READERS["sealed_surface"][1] is not built_up.read


# ---------------------------------------------------------------- zone_mask filter
def test_zone_mask_keeps_only_the_features_asked_for(data):
    key = layer(data, "t", "mixed", [(box(0.0, 40.0, 0.01, 40.02), {"notation": "Flooded area"}),
                                     (box(0.01, 40.0, 0.02, 40.02), {"notation": "Flood trace"})])
    square = box(0.0, 40.0, 0.02, 40.02)
    every = masks.zone_mask(key, zone(square))
    flooded = masks.zone_mask(key, zone(square), {"notation": "Flooded area"})
    assert every["percent"] == pytest.approx(100, abs=1e-6) and flooded["percent"] == pytest.approx(50, abs=1e-6)
    assert masks.zone_mask(key, zone(square), {"missing": 1})["absence"]["reason"] == "insufficient_qa"


# ---------------------------------------------------------------- flood_extent and flood_hazard_extent
PREFIX = "EMSR773_AOI01_DEL_PRODUCT_v1__EMSR773_AOI01_DEL_PRODUCT"


@pytest.fixture
def ems(data):
    """AOI and image footprint over the western three quarters of the zone; flooded area over its western quarter
    and a flood trace over the next quarter."""
    layer(data, flood.EMS, f"{PREFIX}_areaOfInterestA_v1", [(box(-0.1, 40.0, 0.015, 40.02), {"area_id": "AOI01"})])
    layer(data, flood.EMS, f"{PREFIX}_imageFootprintA_v1", [(box(-0.2, 39.9, 0.1, 40.1), {"obj_type": "x"})])
    layer(data, flood.EMS, f"{PREFIX}_observedEventA_v1", [
        (box(0.0, 40.0, 0.005, 40.02), {"notation": "Flooded area"}),
        (box(0.005, 40.0, 0.01, 40.02), {"notation": "Flood trace"})])


def test_flood_extent_counts_flooded_area_and_flood_trace_with_each_apart(ems):
    square = box(0.0, 40.0, 0.02, 40.02)
    got = readers.read_series("flood_extent", zone(square), PERIOD)
    assert got["absence"] is None and got["unit"] == "1"
    row = got["data"].iloc[0]
    assert row.t == "2024-10-29" and row.kind == "observed"
    quarter = hectares(box(0.0, 40.0, 0.005, 40.02))
    assert row.flooded_area_hectares == pytest.approx(quarter, rel=1e-4)
    assert row.flood_trace_hectares == pytest.approx(hectares(box(0.005, 40.0, 0.01, 40.02)), rel=1e-4)
    assert row.hectares == pytest.approx(row.flooded_area_hectares + row.flood_trace_hectares, rel=1e-9)
    # Half of the zone reached (a quarter of water, a quarter of trace), three quarters mapped.
    assert row.v == pytest.approx(2 / 3, rel=1e-4)
    assert row.flooded_area_share == pytest.approx(1 / 3, rel=1e-4) == row.flood_trace_share
    assert got["coverage"]["valid_pct"] == pytest.approx(75, abs=0.01)
    assert any("75.0% of the zone" in limit["en"] for limit in got["limits"])
    assert any("flood trace" in limit["en"] for limit in got["limits"])
    assert got["sources"][0]["source"] == flood.EMS


def test_flood_extent_outside_the_map_or_the_period_is_no_coverage(ems):
    far = readers.read_series("flood_extent", zone(box(2.0, 41.0, 2.01, 41.01)), PERIOD)
    assert far["absence"]["reason"] == "no_coverage" and far["sources"][0]["source"] == flood.EMS
    before = readers.read_series("flood_extent", zone(box(0.0, 40.0, 0.02, 40.02)),
                                 {"start": "2013-01-01", "end": "2020-12-31"})
    assert before["absence"]["reason"] == "no_coverage"


def test_flood_hazard_extent_one_row_per_map_and_return_period(data):
    square = box(0.0, 40.0, 0.02, 40.02)
    layer(data, "snczi", "laminasPB-q100__Q100_3Ciclo_PB_20260727", [(box(0.0, 40.0, 0.005, 40.02), {"a": 1}),
                                                                     (box(5.0, 45.0, 5.1, 45.1), {"a": 2})])
    layer(data, "snczi", "laminasPB-q500__Q500_3Ciclo_PB_20260727", [(box(0.0, 40.0, 0.01, 40.02), {"a": 1}),
                                                                     (box(5.0, 45.0, 5.1, 45.1), {"a": 2})])
    for level in range(1, 7):  # level n covers the n-th eighth of the zone, from the west
        west = 0.0025 * (level - 1)
        layer(data, "patricova", f"patricova_peligrosidad_{level}", [(box(west, 40.0, west + 0.0025, 40.02), {}),
                                                                     (box(5.0, 45.0, 5.1, 45.1), {})])
    got = readers.read_series("flood_hazard_extent", zone(square), PERIOD)
    assert got["absence"] is None
    table = got["data"].set_index(["source", "return_years"]).v
    assert table[("snczi", 100)] == pytest.approx(0.25, rel=1e-4)
    assert table[("snczi", 500)] == pytest.approx(0.5, rel=1e-4)
    assert table[("patricova", 25)] == pytest.approx(2 / 8, rel=1e-4)  # levels 1 and 3
    assert table[("patricova", 100)] == pytest.approx(4 / 8, rel=1e-4)  # levels 1 to 4
    assert table[("patricova", 500)] == pytest.approx(6 / 8, rel=1e-4)  # all six
    assert set(got["data"].kind) == {"hazard"}
    assert [s["source"] for s in got["sources"]] == ["snczi", "snczi", "patricova"]
    assert readers.read_series("flood_hazard_extent", zone(box(20.0, 60.0, 20.01, 60.01)),
                               PERIOD)["absence"]["reason"] == "no_coverage"


# ---------------------------------------------------------------- population_in_hot_cells
GHSL_KEY = "ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.tif"


def test_population_in_hot_cells_needs_a_threshold(data):
    square = box(0.0, 40.0, 0.02, 40.02)
    for got in (readers.read_series("population_in_hot_cells", zone(square), PERIOD),
                hot_cells.read_with_threshold(zone(square), PERIOD, None)):
        assert got["data"] is None and got["absence"]["reason"] == "not_computed"
        assert "threshold" in got["absence"]["detail"]["en"]


def hot_scene(data):
    """1 km GHSL population (cell (row, col) holds 100 * row + col people), a zone over km rows and cols 2..5, a hot
    square over rows and cols 3..4, and a writer of fine EPSG:4326 surface temperature frames around the zone."""
    people = (100 * np.arange(10)[:, None] + np.arange(10)[None, :]).astype("float64")
    path = data / "ready" / "grids" / GHSL_KEY
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float64", crs=MOLLWEIDE,
                       transform=from_origin(LEFT, TOP, 1000, 1000), nodata=-200.0) as out:
        out.write(people, 1)
    (data / "ready" / "grids" / "index.json").write_text(json.dumps({"entries": {GHSL_KEY: {
        "source": "ghsl", "path": path.relative_to(data).as_posix(), "version": "R2023A V1-0",
        "license": "CC BY 4.0", "from": "packages/ghsl/x.zip"}}}), "utf-8")

    def degrees(geometry):
        return shapely.transform(shapely.segmentize(geometry, 20), lambda xy: np.column_stack(
            TO_DEGREES.transform(*xy.T)))

    the_zone = degrees(box(LEFT + 2000, TOP - 6000, LEFT + 6000, TOP - 2000))
    hot = degrees(box(LEFT + 3000, TOP - 5000, LEFT + 5000, TOP - 3000))
    west, south, east, north = the_zone.buffer(0.01).bounds
    size = 0.0002
    width, height = math.ceil((east - west) / size), math.ceil((north - south) / size)
    transform = from_origin(west, north, size, size)
    xs, ys = np.meshgrid(west + (np.arange(width) + 0.5) * size, north - (np.arange(height) + 0.5) * size)
    centres = shapely.points(xs, ys)

    def layer(fp, frames):
        """frames: {year: values}; a zone_layers-like result whose COGs are in the derived store."""
        made = []
        for year, values in frames.items():
            file = f".{year}.tif"
            store.derived_path(fp, file).parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(store.derived_path(fp, file), "w", driver="GTiff", height=height, width=width,
                               count=1, dtype="float32", crs="EPSG:4326", transform=transform,
                               nodata=-9999.0) as out:
                out.write(values.astype("float32"), 1)
            made.append({"t": str(year), "file": file, "coverage": {"valid_pct": 100.0}})
        return {"fingerprint": fp, "absence": None, "support": {"resolution_m": 100}, "frames": made,
                "sources": [{"source": "landsat-c2-l2", "url": "https://example.org"}]}

    inside = shapely.contains(the_zone, centres)
    return people, the_zone, shapely.contains(hot, centres), inside, layer


def test_hot_cells_count_the_people_under_the_hot_pixels(data):
    people, the_zone, hot, _, layer = hot_scene(data)
    values = np.where(hot, 50.0, 30.0)
    values[0, 0] = -9999.0
    lst = layer("f" * 64, {2024: values})
    lst["frames"].append({"t": "2025", "absence": {"reason": "insufficient_qa"}})
    got = hot_cells.read_with_threshold(zone(the_zone), PERIOD, 45.0, layer=lst)
    assert got["absence"] is None and got["unit"] == "people" and got["support"]["resolution_m"] == 1000
    assert got["threshold"] == {"value_c": 45.0, "kind": "fixed_c"}
    row = got["data"].iloc[0]
    assert row.t == "2024-01-01" and row.population_year == 2025
    assert row.v == pytest.approx(people[3:5, 3:5].sum(), rel=0.01)  # pixel edges cut the cells by about 1 %
    assert row.hot_pct == pytest.approx(25, rel=0.02)
    assert [s["source"] for s in got["sources"]] == ["landsat-c2-l2", "ghsl"]
    assert "45.00 °C" in got["limits"][0]["en"]
    cold = hot_cells.read_with_threshold(zone(the_zone), PERIOD, 60.0, layer=lst)
    assert cold["data"].v.tolist() == [0.0] and cold["data"].hot_hectares.tolist() == [0.0]


def test_hot_cells_percentile_is_fixed_once_on_the_base_period_and_applied_to_every_year(data):
    people, the_zone, hot, inside, layer = hot_scene(data)
    rng = np.random.default_rng(7)
    # Base period: two summers, each pixel its own value; outside the zone 99 °C, which must not enter the sample.
    base_frames = {year: np.where(inside, rng.uniform(20, 40, inside.shape), 99.0) for year in (2013, 2014)}
    base_frames[2014][inside.nonzero()[0][0], inside.nonzero()[1][0]] = -9999.0  # no data inside: left out
    base = layer("b" * 64, base_frames)
    sample = np.concatenate([v[inside & (v != -9999.0)] for v in base_frames.values()])
    expected = float(np.percentile(sample, 95))
    # Analysis years: the hot square 1 °C above the base threshold, the rest 1 °C below; 2025 all cold.
    later = layer("c" * 64, {2024: np.where(hot, expected + 1, expected - 1),
                             2025: np.full(hot.shape, expected - 1)})
    period = {"start": "2024-06-01", "end": "2025-08-31", "months": [6, 7, 8]}
    got = hot_cells.read_with_threshold(zone(the_zone), period, percentile=95,
                                        base_period={"start": "2013-06-01", "end": "2014-08-31"},
                                        layer=later, base_layer=base)
    assert got["absence"] is None
    assert got["threshold"]["kind"] == "base_percentile" and got["threshold"]["value_c"] == pytest.approx(expected)
    assert got["threshold"]["n_values"] == sample.size and got["threshold"]["base_years"] == ["2013", "2014"]
    assert got["threshold"]["base_period"]["months"] == [6, 7, 8]  # the months of the period, by default
    assert got["data"].v.tolist() == pytest.approx([people[3:5, 3:5].sum(), 0.0], rel=0.01)
    assert any("umbral-celda-caliente" in limit["en"] and "2013-2014" in limit["en"] for limit in got["limits"])
    # The threshold does not move with the years analysed: the same base gives the same value on another period.
    again = hot_cells.read_with_threshold(zone(the_zone), period, percentile=95, base_period={
        "start": "2013-06-01", "end": "2014-08-31"}, layer=layer("d" * 64, {2024: np.full(hot.shape, 60.0)}),
        base_layer=base)
    assert again["threshold"]["value_c"] == got["threshold"]["value_c"]


@pytest.mark.parametrize("kwargs", [{"threshold_c": 40.0, "percentile": 95, "base_period": {"start": "2013-01-01",
                                                                                            "end": "2020-12-31"}},
                                    {"percentile": 95}, {"base_period": {"start": "2013-01-01", "end": "2020-12-31"}},
                                    {"percentile": 100, "base_period": {"start": "2013-01-01", "end": "2020-12-31"}}])
def test_hot_cells_threshold_forms_cannot_be_mixed_or_left_half(data, kwargs):
    got = hot_cells.read_with_threshold(zone(box(0.0, 40.0, 0.02, 40.02)), PERIOD, **kwargs)
    assert got["data"] is None and got["absence"]["reason"] == "not_computed"


# ---------------------------------------------------------------- common grid
def test_cells_zone_takes_the_whole_cells_that_touch_the_zone():
    to_6933 = Transformer.from_crs(4326, grid.CRS, always_xy=True)
    back = Transformer.from_crs(grid.CRS, 4326, always_xy=True)
    inner = box(10_100, 4_500_100, 12_900, 4_501_900)  # touches cells x 10..12, y 4500 (3 × 1 of 1 km)
    geometry = shapely.transform(inner.segmentize(50), lambda xy: np.column_stack(back.transform(*xy.T)))
    cells, n = grid.cells_zone(zone(geometry), 1000)
    assert n == 6  # y from 4 500 000 to 4 502 000: two rows
    made = shapely.transform(shape(cells["geometry"]), lambda xy: np.column_stack(to_6933.transform(*xy.T)))
    assert made.area == pytest.approx(6e6, rel=1e-6)


@pytest.fixture
def fake(monkeypatch):
    """A reader whose value is the area of the zone in hectares, at a declared resolution."""
    calls = []

    def read(variable, zone_, period):
        calls.append(zone_)
        return {"data": pd.DataFrame({"t": ["2020-01-01"], "v": [hectares(shape(zone_["geometry"]))]}),
                "absence": None, "origin": "package", "sources": [{"source": "t", "url": "https://example.org"}],
                "support": {"resolution_m": 100, "since": 2000}, "coverage": {}, "unit": "ha", "limits": [],
                "input_fingerprint": None}

    readers._load_all()
    monkeypatch.setitem(readers.READERS, "fake_variable", ("1", read))
    return calls


def test_to_resolution_regrids_to_the_coarser_cells_and_says_so(data, fake):
    the_zone = zone(box(-0.40, 39.40, -0.39, 39.41))
    same = grid.to_resolution("fake_variable", the_zone, PERIOD, 100)
    assert same["support"]["resolution_m"] == 100 and len(fake) == 1
    coarse = grid.to_resolution("fake_variable", the_zone, PERIOD, 1000)
    assert coarse["support"]["resolution_m"] == 1000 and coarse["support"]["since"] == 2000
    assert coarse["data"].v.iloc[0] == pytest.approx(coarse["support"]["cells"] * 100, rel=1e-3)  # whole km cells
    assert any("1000 m" in limit["en"] for limit in coarse["limits"])
    refine = grid.to_resolution("fake_variable", the_zone, PERIOD, 30)
    assert refine["absence"]["reason"] == "not_computed"
    assert grid.coarsest([same, coarse, refine]) == 1000


# ---------------------------------------------------------------- regional ring
def test_regional_ring_leaves_out_water_and_excluded_areas(data):
    the_zone = zone(box(-0.30, 39.60, -0.28, 39.62), "site")
    # A JRC-like occurrence tile, 0.001° pixels: everything east of -0.10 is water (100 %), with a no-data strip.
    west, north, size = -0.70, 40.00, 0.001
    occurrence = np.zeros((800, 800), dtype="uint8")
    occurrence[:, 600:] = 100
    occurrence[:, 590:600] = 255
    path = data / "packages" / "jrc-gsw" / "occurrence.tif"
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", height=800, width=800, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(west, north, size, size)) as out:
        out.write(occurrence, 1)
    manifest_line(data, {"source": "jrc-gsw", "product": "occurrence", "version": "1.4", "item": "occurrence_test",
                         "path": path.relative_to(data).as_posix(), "license": "test",
                         "ingest": {"url": "https://example.org/jrc"}})
    other_works = zone(box(-0.45, 39.60, -0.43, 39.62), "other")
    got = places.regional(the_zone, exclude=[other_works])
    assert got["absence"] is None and got["sources"][0]["source"] == "jrc-gsw"
    ring = shape(places.rings(the_zone, [3, 20])[1]["geometry"])
    kept = shape(got["zone"]["geometry"])
    assert kept.intersection(box(-0.10, 39.0, 0.0, 40.0)).area < 1e-9  # no water left
    assert kept.intersection(shape(other_works["geometry"])).area < 1e-12
    assert kept.intersection(shape(the_zone["geometry"]).buffer(0.02)).area < 1e-12  # nothing within ~2 km
    water = ring.intersection(box(-0.10, 39.0, 0.0, 40.0))
    assert got["removed"]["water_ha"] == pytest.approx(hectares(water), rel=0.01)
    assert got["removed"]["excluded_ha"] == pytest.approx(hectares(shape(other_works["geometry"])), rel=0.01)
    assert hectares(kept) == pytest.approx(hectares(ring) - hectares(water) - got["removed"]["excluded_ha"], rel=0.01)


def test_regional_ring_without_a_water_tile_is_needs_package(data):
    got = places.regional(zone(box(-0.30, 39.60, -0.28, 39.62), "site"))
    assert got["zone"] is None and got["absence"]["reason"] == "needs_package"


# ---------------------------------------------------------------- real layers of the cases
def real_zones():
    from hup_data.readers import vectors as layers

    sections = layers.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.5, 39.3, -0.3, 39.5])
    plan = layers.read_layer("icv-planeamiento/parc-sagunt-ii-plan-especial", None)
    if sections["absence"] is not None or plan["absence"] is not None:
        pytest.skip("INE sections or the Parc Sagunt II plan are not prepared on this computer")
    rows = sections["data"]
    paiporta = shapely.union_all(rows[rows.CUMUN == "46186"].geometry.values)
    return zone(paiporta, "paiporta"), zone(shapely.union_all(plan["data"].geometry.values), "parc-sagunt-ii")


@pytest.mark.data
def test_real_flood_maps_in_paiporta():
    paiporta, _ = real_zones()
    observed = readers.read_series("flood_extent", paiporta, PERIOD)
    hazard = readers.read_series("flood_hazard_extent", paiporta, PERIOD)
    if observed["absence"] or hazard["absence"]:
        pytest.skip("EMSR773, SNCZI or PATRICOVA are not prepared on this computer")
    row = observed["data"].iloc[0]
    assert row.t == "2024-10-29" and 0 < row.v < 1
    # In l'Horta Sud most of what the episode reached is mapped as flood trace (water gone by 30-31 October).
    assert row.flood_trace_hectares > row.flooded_area_hectares > 0
    # The union counts once what two AOIs map with different classes, so it lies between the larger class and the sum.
    assert max(row.flooded_area_share, row.flood_trace_share) <= row.v <= row.flooded_area_share + row.flood_trace_share
    table = hazard["data"].set_index(["source", "return_years"]).v
    assert table[("snczi", 10)] <= table[("snczi", 100)] <= table[("snczi", 500)] <= 1
    assert table[("patricova", 25)] <= table[("patricova", 100)] <= table[("patricova", 500)] <= 1


@pytest.mark.data
def test_real_built_up_fraction_and_regional_ring_of_parc_sagunt_ii():
    _, sagunt = real_zones()
    built = readers.read_series("built_up_fraction", sagunt, PERIOD)
    if built["absence"]:
        pytest.skip("GHSL built-up tiles are not prepared on this computer")
    assert built["support"]["resolution_m"] == 100 and ((built["data"].v >= 0) & (built["data"].v <= 1)).all()
    ring = places.regional(sagunt)
    if ring["absence"]:
        pytest.skip("No JRC tile holds the ring on this computer")
    assert ring["removed"]["water_ha"] > 0  # the sea east of Sagunt
    assert shape(ring["zone"]["geometry"]).intersection(shape(sagunt["geometry"])).is_empty
