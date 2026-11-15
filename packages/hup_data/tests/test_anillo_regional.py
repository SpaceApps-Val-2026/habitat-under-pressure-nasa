"""Regional ring of the A10 chains: JRC tiles joined into one land mask, the clear share measured on land, and the
areas left out of the ring (urbanizable land of the official planning, the extent of a mapped flood). Synthetic tiles
and layers with known areas; the tests marked `data` read the real files of the cases."""
import hashlib
import json
from datetime import datetime

import numpy as np
import pytest
import rasterio
import shapely
from pyproj import Geod
from rasterio.transform import from_origin
from shapely.geometry import box, mapping, shape

from hup_data import paths, places
from hup_data.prepare import vectors
from hup_data.readers import flood, landsat

GEOD = Geod(ellps="WGS84")
PERIOD = {"start": "2013-01-01", "end": "2025-12-31"}
SHORE = -0.315  # west of it, water
SIZE = 0.00025  # JRC pixel


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


def jrc_tile(data, name, bounds, version="1.4"):
    """A JRC-like occurrence tile on the JRC grid: 100 west of SHORE, 0 east of it."""
    west, south, east, north = bounds
    width, height = round((east - west) / SIZE), round((north - south) / SIZE)
    lon = west + (np.arange(width) + 0.5) * SIZE
    occurrence = np.repeat(np.where(lon < SHORE, 100, 0)[None, :], height, axis=0).astype("uint8")
    path = data / "packages" / "jrc-gsw" / f"{name}.tif"
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(west, north, SIZE, SIZE)) as out:
        out.write(occurrence, 1)
    manifest_line(data, {"source": "jrc-gsw", "product": "occurrence", "version": version, "item": name,
                         "scope": "global", "path": path.relative_to(data).as_posix(), "mb": 1,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "format": "cog",
                         "ingest": {"url": f"https://example.org/{name}.tif"}, "license": "test"})


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


# ---------------------------------------------------------------- JRC tiles joined
def test_a_zone_across_two_tiles_gets_one_mosaic_of_both(data):
    jrc_tile(data, "west", (-1.0, 39.0, -0.32, 40.0))
    jrc_tile(data, "east", (-0.32, 39.0, 1.0, 40.0))
    across = {"bbox": [-0.34, 39.72, -0.30, 39.75]}
    tile = landsat.water_tile(across)
    assert [line["item"] for line in tile["tiles"]] == ["east", "west"] and tile["item"] == "east+west"
    assert landsat.water_source(tile)["items"] == ["east", "west"]
    with rasterio.open(landsat.water_path(tile)) as mosaic:
        assert tuple(mosaic.bounds) == pytest.approx((-1.0, 39.0, 1.0, 40.0))
        values = [value for value, in mosaic.sample([(-0.33, 39.5), (-0.318, 39.5), (-0.31, 39.5), (0.5, 39.5)])]
    assert values[0] == 100 and values[2] == 0 and values[3] == 0
    # A zone inside one tile keeps that tile alone, as before.
    assert landsat.water_tile({"bbox": [0.1, 39.5, 0.2, 39.6]})["item"] == "east"


def test_tiles_of_different_versions_or_with_a_gap_are_not_joined(data):
    jrc_tile(data, "west", (-1.0, 39.0, -0.32, 40.0))
    jrc_tile(data, "east", (-0.32, 39.0, 1.0, 40.0), version="1.3")
    assert landsat.water_tile({"bbox": [-0.34, 39.72, -0.30, 39.75]}) is None
    jrc_tile(data, "north", (-1.0, 39.9, 1.0, 40.5))
    assert landsat.water_tile({"bbox": [-0.5, 39.5, -0.4, 39.95]}) is not None  # west and north, same version
    assert landsat.water_tile({"bbox": [-0.5, 39.5, -0.4, 40.6]}) is None  # nothing north of 40.5


# ---------------------------------------------------------------- clear share measured on land
class Item:
    """One scene in EPSG:4326 whose pixels west of SHORE are water the quality band flags (bit 7)."""

    def __init__(self, folder):
        size, west, north = 0.0005, -0.36, 39.77
        cols = np.arange(160)
        lon = west + (cols + 0.5) * size
        wet = np.repeat((lon < SHORE)[None, :], 120, axis=0)
        bands = {"qa_pixel": np.where(wet, 1 << 7 | 1 << 6 | 1, 1 << 6 | 1), "red": np.full(wet.shape, 10000),
                 "nir08": np.full(wet.shape, 16000), "lwir11": np.full(wet.shape, 45000)}
        self.id, self.properties = "scene", {"platform": "landsat-8", "eo:cloud_cover": 5.0}
        self.datetime = datetime(2020, 7, 1)
        self.assets = {}
        for band, values in bands.items():
            path = folder / f"{band}.tif"
            with rasterio.open(path, "w", driver="GTiff", height=120, width=160, count=1, dtype="uint16",
                               crs="EPSG:4326", transform=from_origin(west, north, size, size)) as out:
                out.write(values.astype("uint16"), 1)
            self.assets[band] = type("Asset", (), {"href": str(path)})()


def test_with_the_mask_permanent_water_does_not_count_as_not_clear(data):
    """62.5 % of the zone is sea: without the mask no scene reaches MIN_CLEAR; with it the share is of the land."""
    jrc_tile(data, "west", (-1.0, 39.0, -0.32, 40.0))
    jrc_tile(data, "east", (-0.32, 39.0, 1.0, 40.0))
    the_zone = zone(box(-0.34, 39.72, -0.30, 39.75))
    item = Item(data)
    assert landsat._scene(item, the_zone) is None
    tile = landsat.water_tile(the_zone)
    scene = landsat._scene(item, the_zone, landsat.water_path(tile))
    assert scene is not None and scene["clear_pct"] >= 95


# ---------------------------------------------------------------- areas left out of the ring
@pytest.fixture
def ring_tiles(data):
    jrc_tile(data, "west", (-1.0, 39.0, -0.32, 40.0))
    jrc_tile(data, "east", (-0.32, 39.0, 1.0, 40.0))
    return zone(box(0.10, 39.60, 0.12, 39.62), "site")  # its ring runs from about -0.13 to 0.35: all land


def test_exclusions_leave_the_ring_and_bring_their_sources_and_limits(ring_tiles):
    works = places.exclusion(ring_tiles, "works", {"en": "works", "es": "obras"}, box(0.28, 39.60, 0.30, 39.62),
                             [{"source": "plan"}], [{"en": "Works out.", "es": "Obras fuera."}])
    unprepared = {"reason": "needs_package", "detail": {"en": "Not prepared.", "es": "Sin preparar."}}
    missing = places.exclusion(ring_tiles, "flood", {"en": "flooded area", "es": "área inundada"}, None,
                               [{"source": "ems"}], [], unprepared)
    assert missing["geometry"] is None and missing["absence"]["reason"] == "needs_package"
    got = places.regional(ring_tiles, exclude=[works, missing])
    assert got["absence"] is None
    assert got["removed"]["excluded_ha"] == pytest.approx(hectares(box(0.28, 39.60, 0.30, 39.62)), rel=0.01)
    assert shape(got["zone"]["geometry"]).intersection(box(0.28, 39.60, 0.30, 39.62)).area < 1e-12
    assert [ref["source"] for ref in got["sources"]] == ["jrc-gsw", "plan", "ems"]
    texts = [limit["es"] for limit in got["limits"]]
    assert "Obras fuera." in texts
    assert "No se ha podido quitar del control regional el área inundada: Sin preparar." in texts


def test_planned_development_takes_only_urbanizable_land(ring_tiles, data):
    outer = places.rings(ring_tiles, [3, 20])[-1]
    assert places.planned_development(outer)["absence"]["reason"] == "needs_package"
    layer(data, places.PLANNING, "classification", [
        (box(0.28, 39.60, 0.30, 39.62), {"clas_suelo": "SUZ", "zon_suelo": "ZND-IN"}),
        (box(0.28, 39.70, 0.30, 39.72), {"clas_suelo": "SUZ", "zon_suelo": "ZND-RE"}),
        (box(0.28, 39.50, 0.30, 39.52), {"clas_suelo": "SU", "zon_suelo": "ZUR-RE"})])
    layer(data, places.PLANNING, "no-classification", [(box(0.20, 39.50, 0.21, 39.51), {"name": "x"})])
    got = places.planned_development(outer)
    assert got["absence"] is None and [ref["product"] for ref in got["sources"]] == ["classification"]
    expected = shapely.union_all([box(0.28, 39.60, 0.30, 39.62), box(0.28, 39.70, 0.30, 39.72)])
    assert shape(got["geometry"]).symmetric_difference(expected).area < 1e-9
    ring = places.regional(ring_tiles, exclude=[got])
    assert ring["removed"]["excluded_ha"] == pytest.approx(hectares(expected), rel=0.01)
    assert any("SUZ" in limit["en"] for limit in ring["limits"])


PREFIX = "EMSR773_AOI01_DEL_PRODUCT_v1__EMSR773_AOI01_DEL_PRODUCT"


def test_flood_footprint_leaves_out_flooded_area_and_trace_from_the_episode_on(ring_tiles, data):
    layer(data, flood.EMS, f"{PREFIX}_areaOfInterestA_v1", [(box(0.0, 39.3, 0.4, 39.65), {"area_id": "AOI01"})])
    layer(data, flood.EMS, f"{PREFIX}_imageFootprintA_v1", [(box(-1.0, 39.0, 0.65, 40.0), {"obj_type": "x"})])
    layer(data, flood.EMS, f"{PREFIX}_observedEventA_v1", [
        (box(0.28, 39.54, 0.30, 39.56), {"notation": "Flooded area"}),
        (box(0.30, 39.54, 0.32, 39.56), {"notation": "Flood trace"})])
    outer = places.rings(ring_tiles, [3, 20])[-1]
    got = flood.footprint(outer, PERIOD)
    reached = box(0.28, 39.54, 0.32, 39.56)
    assert got["absence"] is None and shape(got["geometry"]).symmetric_difference(reached).area < 1e-9
    assert any("flood trace" in limit["en"] for limit in got["limits"])
    ring = places.regional(ring_tiles, exclude=[got])
    assert ring["removed"]["excluded_ha"] == pytest.approx(hectares(reached), rel=0.01)
    before = flood.footprint(outer, {"start": "2013-01-01", "end": "2020-12-31"})
    assert before["geometry"] is None and before["absence"] is None and before["limits"] == []
    elsewhere = flood.footprint(zone(box(0.6, 39.5, 0.7, 39.6)), PERIOD)
    assert elsewhere["geometry"] is None and "not mapped" in elsewhere["limits"][0]["en"]


# ---------------------------------------------------------------- real files of the cases
def cached_zone(zone_id):
    for file in sorted((paths.data_dir() / "places").glob("*.json")):
        text = file.read_text(encoding="utf-8")
        if f'"{zone_id}"' not in text:
            continue
        stack = [json.loads(text)]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("id") == zone_id and item.get("geometry"):
                    return item
                stack += list(item.values())
            elif isinstance(item, list):
                stack += item
    pytest.skip(f"{zone_id} is not in the place cache of this computer")


@pytest.mark.data
def test_real_ring_of_sagunt_has_a_land_mask_and_leaves_out_planned_works():
    sagunt = cached_zone("osm-relation-348833")
    outer = places.rings(sagunt, [3, 20])[-1]
    tile = landsat.water_tile(outer)
    if tile is None:
        pytest.skip("The JRC tiles 10W_40N and 0E_40N are not both on this computer")
    assert {"occurrence_10W_40Nv1_4_2021", "occurrence_0E_40Nv1_4_2021"} <= {line["item"] for line in tile["tiles"]}
    works = places.planned_development(outer)
    if works["absence"]:
        pytest.skip("The urbanizable land of the planning is not prepared on this computer")
    ring = places.regional(sagunt, exclude=[works, flood.footprint(outer, PERIOD)])
    assert ring["absence"] is None
    assert ring["removed"]["water_ha"] > 50_000  # the sea east of Sagunt, now on both sides of 0°
    assert ring["removed"]["excluded_ha"] > 0
    assert {"jrc-gsw", "icv-planeamiento"} <= {ref["source"] for ref in ring["sources"]}
