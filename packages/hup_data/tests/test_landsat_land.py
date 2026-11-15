"""Landsat land mask: permanent water from JRC Global Surface Water leaves the median, only when asked for.

Synthetic scenes: west of 0.315 W is water that the quality band does not flag (clear, not water), east is land.
The zone spans 0.34 W to 0.30 W, so water is 62.5% of it and wins the median unless it is masked.
"""
import json

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from hup_data.readers import landsat

ZONE = {"id": "coast-zone", "name": "Coast", "bbox": [-0.34, 39.72, -0.30, 39.75]}
PERIOD = {"start": "2020-06-01", "end": "2020-09-30"}
SHORE = -0.315
CLEAR = 1 << 6 | 1
LAND, WATER = (0.10, 0.30), (0.10, 0.02)  # (red, near infrared) reflectance: NDVI 0.5 and about -0.667


def digital(reflectance):
    return int(round((reflectance + 0.2) / 2.75e-5))


class Scene:
    """One scene in UTM 30N whose pixels west of SHORE look like water, with local files instead of signed URLs."""

    def __init__(self, folder, name, date):
        transform = from_origin(726000, 4406000, 30, 30)
        cols, rows = np.meshgrid(np.arange(1200), np.arange(1200))
        x, y = transform @ (cols + 0.5, rows + 0.5)
        lon, _ = Transformer.from_crs(32630, 4326, always_xy=True).transform(x, y)
        wet = lon < SHORE
        bands = {"qa_pixel": np.full(wet.shape, CLEAR),
                 "red": np.where(wet, digital(WATER[0]), digital(LAND[0])),
                 "nir08": np.where(wet, digital(WATER[1]), digital(LAND[1])),
                 "lwir11": np.full(wet.shape, int(round((30 + 273.15 - 149.0) / 0.00341802)))}
        # A Collection 2 Tier 1 id: since decision 1 of A10 the reader keeps only T1 scenes by default.
        self.id, self.properties = f"{name}_02_T1", {"platform": "landsat-8", "eo:cloud_cover": 5.0}
        self.datetime = __import__("datetime").datetime.fromisoformat(date)
        self.assets = {}
        for band, values in bands.items():
            path = folder / f"{name}_{band}.tif"
            with rasterio.open(path, "w", driver="GTiff", height=1200, width=1200, count=1, dtype="uint16",
                               crs="EPSG:32630", transform=transform) as out:
                out.write(values.astype("uint16"), 1)
            self.assets[band] = type("Asset", (), {"href": str(path)})()


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    folder = tmp_path / "scenes"
    folder.mkdir()
    scenes = [Scene(folder, "a", "2020-07-01"), Scene(folder, "b", "2020-08-02")]
    monkeypatch.setattr(landsat, "_search", lambda zone, year, months: scenes)
    return tmp_path


def jrc_tile(data, bounds=(-1.0, 39.0, 0.0, 40.0)):
    """A JRC-like occurrence tile: 100 west of SHORE, 0 east of it, 255 (no data) in the northern tenth."""
    west, south, east, north = bounds
    size = 0.00025
    width, height = round((east - west) / size), round((north - south) / size)
    lon = west + (np.arange(width) + 0.5) * size
    occurrence = np.repeat(np.where(lon < SHORE, 100, 0)[None, :], height, axis=0).astype("uint8")
    occurrence[: height // 10] = 255
    path = data / "packages" / "jrc-gsw" / "occurrence_test.tif"
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(west, north, size, size)) as out:
        out.write(occurrence, 1)
    line = {"source": "jrc-gsw", "product": "occurrence", "version": "1.4", "item": "occurrence_test",
            "scope": "global", "path": path.relative_to(data).as_posix(), "mb": 1, "sha256": "0" * 64,
            "format": "cog", "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload",
                                        "url": "https://example.org/occurrence_test.tif"},
            "license": "No restrictions; cite Pekel et al. (2016)"}
    (data / "manifest.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")


def test_without_the_parameter_water_enters_the_median_as_before(data):
    jrc_tile(data)
    got = landsat.read("ndvi", ZONE, PERIOD)
    assert got["data"].v.tolist() == pytest.approx([-2 / 3, -2 / 3], abs=1e-3)
    assert "land_mask" not in got and [s["source"] for s in got["sources"]] == ["landsat-c2-l2"]


def test_with_the_mask_the_median_is_the_land_one(data):
    jrc_tile(data)
    got = landsat.read("ndvi", ZONE, PERIOD, land_only=True)
    assert got["data"].v.tolist() == pytest.approx([0.5, 0.5], abs=1e-3)
    assert got["land_mask"]["applied"] is True
    assert [s["source"] for s in got["sources"]] == ["landsat-c2-l2", "jrc-gsw"]
    assert got["sources"][1]["items"] == ["occurrence_test"]
    # Masked and unmasked figures are kept apart in the cache.
    assert landsat.read("ndvi", ZONE, PERIOD)["data"].v.iloc[0] == pytest.approx(-2 / 3, abs=1e-3)


def test_without_a_tile_for_the_zone_it_reads_as_before_and_says_so(data):
    jrc_tile(data, bounds=(-10.0, 30.0, -5.0, 35.0))  # far from the zone
    got = landsat.read("ndvi", ZONE, PERIOD, land_only=True)
    assert got["data"].v.tolist() == pytest.approx([-2 / 3, -2 / 3], abs=1e-3)
    assert got["land_mask"]["applied"] is False and "No JRC" in got["land_mask"]["detail"]["en"]
    assert [s["source"] for s in got["sources"]] == ["landsat-c2-l2"]


def test_the_tile_must_hold_the_whole_zone(data):
    jrc_tile(data, bounds=(-1.0, 39.0, -0.32, 40.0))  # ends inside the zone
    assert landsat.water_tile(ZONE) is None


def test_the_map_pixels_use_the_same_land_mask(data):
    jrc_tile(data)
    scene = Scene(data / "scenes", "c", "2020-07-03")
    grid = landsat.pixel_grid(ZONE["bbox"], 0)
    west, _, east, _ = grid["bounds"]
    lon = west + (np.arange(grid["width"]) + 0.5) * (east - west) / grid["width"]
    wet, dry = lon < SHORE - 0.001, lon > SHORE + 0.001
    plain = landsat.scene_pixels(scene, grid)["ndvi"]
    assert np.nanmedian(plain[:, wet]) == pytest.approx(-2 / 3, abs=1e-3)
    water = landsat.water_path(landsat.water_tile(ZONE))
    masked = landsat.scene_pixels(scene, grid, water)["ndvi"]
    assert np.isnan(masked[:, wet]).all()
    assert np.nanmedian(masked[:, dry]) == pytest.approx(0.5, abs=1e-3)
    assert landsat.scene_table(ZONE, PERIOD, land_only=True)["data"].ndvi.tolist() == pytest.approx([0.5, 0.5],
                                                                                                    abs=1e-3)


# ---------------------------------------------------------------- with the real files
PORT_PERIOD = {"start": "2024-06-01", "end": "2024-09-30", "months": [6, 7, 8, 9]}


def ine_zone(zone_id, code, districts=None):
    import shapely
    from shapely.geometry import mapping

    from hup_data.readers import vectors

    found = vectors.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.6, 39.5, 0.2, 40.3])
    if found["absence"] is not None:
        pytest.skip("The INE census sections are not prepared on this computer")
    rows = found["data"][found["data"].CUMUN == code]
    rows = rows[rows.CDIS.isin(districts)] if districts else rows
    outline = shapely.union_all(rows.geometry.values)
    return {"id": zone_id, "name": zone_id, "bbox": [round(v, 6) for v in outline.bounds], "geometry": mapping(outline)}


@pytest.mark.data
def test_real_jrc_tile_on_the_coast_of_sagunt(monkeypatch):
    """Port de Sagunt (INE districts 06-08 of Sagunt) and its 1 km surroundings, which take in the sea and the port.

    Figures of .dev/notes/2026-11-14-pruebas-reales-agua-poblacion.md. The medians are only checked when their cache
    is on this computer (it is read offline, never searched here).
    """
    from rasterio.features import geometry_mask

    from hup_data import places

    coast = ine_zone("prueba-ine-46220-d06-d07-d08", "46220", ["06", "07", "08"])
    ring = {**places.reference(coast, 1.0), "id": "prueba-ine-46220-d06-d07-d08-entorno-1km"}
    tile = landsat.water_tile(ring)
    if tile is None or tile["item"] != "occurrence_10W_40Nv1_4_2021":
        pytest.skip("The JRC tile 10W_40N is not downloaded on this computer")
    assert landsat.water_tile(ine_zone("prueba-ine-12040", "12040")) is None  # Castelló crosses 40 N: no tile
    for zone, low, high in ((coast, 0.0, 0.01), (ring, 0.25, 0.35)):
        grid = landsat.pixel_grid(zone["bbox"], 0)
        inside = geometry_mask([zone["geometry"]], (grid["height"], grid["width"]), grid["transform"], invert=True,
                               all_touched=True)
        water = landsat._water(landsat.water_path(tile), "EPSG:4326", grid["transform"], inside.shape)
        assert low <= (water & inside).sum() / inside.sum() <= high
    monkeypatch.setenv("HUP_OFFLINE", "1")
    plain = landsat.read("ndvi", ring, PORT_PERIOD)
    land = landsat.read("ndvi", ring, PORT_PERIOD, land_only=True)
    if plain["absence"] is not None or land["absence"] is not None:
        pytest.skip("The Landsat medians of these zones are not cached on this computer")
    assert land["land_mask"]["applied"] is True and land["unit"] == "1"
    assert [s["source"] for s in land["sources"]] == ["landsat-c2-l2", "jrc-gsw"]
    # Without the mask, the sea that the quality band flags as water makes most scenes less than 80% clear.
    assert land["coverage"]["n_dates"] > plain["coverage"]["n_dates"]


def test_years_cached_without_the_mask_are_not_reused_by_a_masked_read(data):
    """Sagunt's ring was cached with water in before its tile existed: the masked read must not take those years."""
    unmasked = landsat.read("ndvi", ZONE, PERIOD)
    jrc_tile(data)
    masked = landsat.read("ndvi", ZONE, PERIOD, land_only=True)
    assert unmasked["data"].v.iloc[0] == pytest.approx(-2 / 3, abs=1e-3)
    assert masked["data"].v.tolist() == pytest.approx([0.5, 0.5], abs=1e-3) and masked["origin"] == "remote"


@pytest.mark.parametrize("bounds, applied", [((-10.0, 30.0, -5.0, 35.0), False), ((-1.0, 39.0, 0.0, 40.0), True)])
def test_an_absence_of_a_masked_read_still_says_whether_water_was_left_out(data, bounds, applied):
    jrc_tile(data, bounds=bounds)
    got = landsat.read("ndvi", ZONE, {"start": "2021-01-01", "end": "2021-03-31"}, land_only=True)
    assert got["absence"]["reason"] == "insufficient_qa"
    assert got["land_mask"]["applied"] is applied
    assert "land_mask" not in landsat.read("ndvi", ZONE, {"start": "2021-01-01", "end": "2021-03-31"})
