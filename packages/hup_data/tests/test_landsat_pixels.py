"""A11b: Landsat pixels for the map. Synthetic scenes on disk, no network, no real data folder.

The zone figures of the reader must not move: `_scene` is checked against a copy of its code as it was before the
pixel path existed. The map must agree with them: same scenes, same mask, same scale.
"""
import datetime

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin, rowcol
from rasterio.warp import transform as reproject_points

from hup_data import layers, readers, store
from hup_data.readers import landsat

SMALL = {"id": "small-zone", "name": "Small zone", "bbox": [-0.34, 39.72, -0.30, 39.75]}
ORIGIN = (722400, 4408200)  # a 480 x 440 grid of 30 m pixels in UTM 30N around the zone and 3 km more
SHAPE = (440, 480)
CLEAR, CLOUD, WATER = 1 << 6 | 1, 1 << 3 | 1, 1 << 6 | 1 << 7 | 1


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    return tmp_path / "data"


def digital(reflectance):
    return np.round((np.asarray(reflectance) + 0.2) / 2.75e-5).astype("uint16")


def thermal(celsius):
    return np.round((np.asarray(celsius) + 273.15 - 149.0) / 0.00341802).astype("uint16")


def scene_index(lon, lat):
    """Row and column of the scene grid that holds a point."""
    (x,), (y,) = reproject_points("EPSG:4326", "EPSG:32630", [lon], [lat])
    return int((ORIGIN[1] - y) // 30), int((x - ORIGIN[0]) // 30)


class Scene:
    """What the catalogue returns for one scene, with local files instead of signed URLs."""

    def __init__(self, folder, name, date, platform, bands, cloud=5.0):
        # A Collection 2 Tier 1 id: since decision 1 of A10 the reader keeps only T1 scenes by default.
        self.id, self.properties = f"{name}_02_T1", {"platform": platform, "eo:cloud_cover": cloud}
        self.datetime = datetime.datetime.fromisoformat(date)
        self.assets = {}
        for band, grid in bands.items():
            path = folder / f"{name}_{band}.tif"
            grid = np.broadcast_to(np.asarray(grid, dtype="uint16"), SHAPE)
            with rasterio.open(path, "w", driver="GTiff", height=SHAPE[0], width=SHAPE[1], count=1, dtype="uint16",
                               crs="EPSG:32630", transform=from_origin(*ORIGIN, 30, 30), compress="deflate") as out:
                out.write(grid, 1)
            self.assets[band] = type("Asset", (), {"href": str(path)})()


def gradient_bands(nir_low, nir_high, quality=CLEAR):
    """NIR grows to the east and the ground warms to the north: a flipped or transposed map would show it."""
    columns = np.linspace(0, 1, SHAPE[1])[None, :]
    rows = np.linspace(0, 1, SHAPE[0])[:, None]
    return {"red": digital(0.10), "nir08": digital(nir_low + (nir_high - nir_low) * columns),
            "lwir11": thermal(40 - 20 * rows), "qa_pixel": quality}


def serve(monkeypatch, scenes_by_year):
    """The catalogue: searches by year (for the reader) and scenes by id (for the map). Returns the id calls."""
    by_id = {scene.id: scene for scenes in scenes_by_year.values() for scene in scenes}
    calls = []
    monkeypatch.setattr(landsat, "_search", lambda zone, year, months: list(scenes_by_year.get(year, [])))
    monkeypatch.setattr(landsat, "_items", lambda ids: calls.append(list(ids)) or [by_id[i] for i in ids])
    return calls


# ---------------------------------------------------------------- the zone figures do not move
def scene_as_it_was(item, zone):
    """`landsat._scene` copied from before the pixel path was added. The reference for the figures."""
    with rasterio.Env(**landsat.GDAL):
        quality, inside = landsat._window(item.assets["qa_pixel"].href, zone)
        if not inside.any():
            return None
        quality = quality.astype(np.uint16)
        clear = ((quality >> 6) & 1 == 1) & ((quality >> 7) & 1 == 0) & (quality > 1) & inside
        clear_share = float(clear.sum() / inside.sum())
        if clear_share < landsat.MIN_CLEAR:
            return None
        red, _ = landsat._window(item.assets["red"].href, zone)
        infrared, _ = landsat._window(item.assets["nir08"].href, zone)
        thermal_, _ = landsat._window(item.assets["lwir11"].href, zone)
    red_r, infrared_r = red * 2.75e-5 - 0.2, infrared * 2.75e-5 - 0.2
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(clear & (red > 0) & (infrared > 0), (infrared_r - red_r) / (infrared_r + red_r), np.nan)
    lst = np.where(clear & (thermal_ > 0), thermal_ * 0.00341802 + 149.0 - 273.15, np.nan)
    if not np.isfinite(ndvi).any() or not np.isfinite(lst).any():
        return None
    return {"t": item.datetime.strftime("%Y-%m-%d"), "platform": item.properties["platform"], "item": item.id,
            "ndvi": round(float(np.nanmedian(ndvi)), 5), "lst": round(float(np.nanmedian(lst)), 3),
            "clear_pct": round(100 * clear_share, 1), "cloud_pct": round(float(item.properties["eo:cloud_cover"]), 1),
            "pixels": int(inside.sum())}


def test_zone_medians_are_identical_to_the_code_before_the_pixel_path(tmp_path):
    rng = np.random.default_rng(7)
    triangle = {"type": "Polygon", "coordinates": [[[-0.34, 39.72], [-0.30, 39.72], [-0.32, 39.75], [-0.34, 39.72]]]}
    zones = [SMALL, {**SMALL, "id": "tri", "geometry": triangle}]
    for number in range(6):
        quality = np.where(rng.random(SHAPE) < 0.08 * number, CLOUD, CLEAR)
        quality[rng.random(SHAPE) < 0.03] = WATER
        quality[:, :3] = 1  # fill
        bands = {"red": rng.integers(0, 12000, SHAPE), "nir08": rng.integers(0, 20000, SHAPE),
                 "lwir11": rng.integers(0, 50000, SHAPE), "qa_pixel": quality}
        item = Scene(tmp_path, f"random-{number}", "2020-07-01", "landsat-8", bands)
        for zone in zones:
            assert landsat._scene(item, zone) == scene_as_it_was(item, zone)


def test_the_series_of_the_reader_does_not_change_when_the_map_is_drawn(data, monkeypatch, tmp_path):
    scenes = {2020: [Scene(tmp_path, "a", "2020-07-01", "landsat-8", gradient_bands(0.2, 0.4)),
                     Scene(tmp_path, "b", "2020-08-02", "landsat-9", gradient_bands(0.3, 0.5))]}
    serve(monkeypatch, scenes)
    period = {"start": "2020-01-01", "end": "2020-12-31"}
    before = readers.read_series("ndvi", SMALL, period)
    layers.zone_layers("ndvi", SMALL, period)
    after = landsat.read("ndvi", SMALL, period)
    assert after["data"].equals(before["data"]) and after["coverage"] == before["coverage"]
    table = landsat.scene_table(SMALL, period)["data"]
    assert table["item"].tolist() == ["a_02_T1", "b_02_T1"] and table["ndvi"].tolist() == before["data"].v.tolist()


# ---------------------------------------------------------------- the map
def before_and_after(tmp_path):
    """Greener before than after. The second scene before has a cloud in the north margin, outside the zone, and
    every scene has a water block in the south margin."""
    cloud_row, _ = scene_index(-0.32, 39.756)
    water_row, water_col = scene_index(-0.33, 39.714)
    cloud = np.full(SHAPE, CLEAR, dtype="uint16")
    cloud[: cloud_row + 1, :] = CLOUD
    water = np.full(SHAPE, CLEAR, dtype="uint16")
    water[water_row - 3: water_row + 4, water_col - 3: water_col + 4] = WATER

    def quality(*masks):
        out = np.full(SHAPE, CLEAR, dtype="uint16")
        for mask in masks:
            out = np.where(mask != CLEAR, mask, out)
        return out

    return {
        2019: [Scene(tmp_path, "b1", "2019-07-01", "landsat-8", gradient_bands(0.40, 0.60, quality(water))),
               Scene(tmp_path, "b2", "2019-07-17", "landsat-8", gradient_bands(0.50, 0.70, quality(water, cloud)))],
        2020: [],
        2023: [Scene(tmp_path, "a1", "2023-07-01", "landsat-9", gradient_bands(0.20, 0.40, quality(water)))],
    }


def read_frame(meta, frame):
    with rasterio.open(store.derived_path(meta["fingerprint"], frame["file"])) as cog:
        assert layers.check_cog(cog.name) == [] and cog.crs.to_epsg() == 4326 and cog.nodata == layers.NODATA
        assert tuple(cog.bounds) == pytest.approx(meta["bounds"], abs=1e-6)
        values = cog.read(1).astype("float64")
        transform = cog.transform
    values[values == layers.NODATA] = np.nan
    return values, lambda lon, lat: rowcol(transform, lon, lat)


def test_before_and_after_windows_share_one_range_and_both_variables_come_from_one_pass(data, monkeypatch, tmp_path):
    calls = serve(monkeypatch, before_and_after(tmp_path))
    period = {"start": "2019-01-01", "end": "2023-12-31"}
    windows = {"before": [2019, 2020], "after": [2023, 2023]}
    got = layers.zone_layers("ndvi", SMALL, period, windows=windows, margin_km=1)
    assert got["absence"] is None and got["origin"] == "remote" and got["unit"] == "1" and got["scale"] == "green"
    before, after = got["frames"]
    assert (before["window"], before["t"], before["years"]) == ("before", "2019-2020", [2019, 2020])
    assert (after["window"], after["file"]) == ("after", ".after.tif")
    assert before["coverage"] == {"valid_pct": 100.0, "n_dates": 2} and after["coverage"]["n_dates"] == 1
    west, south, east, north = got["bounds"]
    assert west < -0.35 < -0.29 < east and south < 39.712 < 39.758 < north  # the zone and 1 km around it
    assert got["support"]["resolution_m"] == 30 and got["support"]["cells"] > 1000
    assert got["sources"][0]["source"] == "landsat-c2-l2" and got["sources"][0]["n_items"] == 3
    assert any("1 km around" in limit["en"] for limit in got["limits"])

    old, point = read_frame(got, before)
    new, _ = read_frame(got, after)
    middle = point(-0.32, 39.735)
    assert old[middle] > new[middle]  # greener before
    assert old[middle[0], -3] > old[middle[0], 3]  # east is greener: the map is not flipped
    # The cloudy strip of b2 keeps only b1 (0.40 to 0.60 of NIR); below it, the median of b1 and b2.
    cloudy, clear = point(-0.32, 39.7585), point(-0.32, 39.735)
    nir = 0.40 + 0.20 * scene_index(-0.32, 39.7585)[1] / (SHAPE[1] - 1)
    assert old[cloudy] < old[clear] and old[cloudy] == pytest.approx((nir - 0.10) / (nir + 0.10), abs=2e-3)
    assert np.isnan(old[point(-0.33, 39.714)]) and np.isnan(new[point(-0.33, 39.714)])  # water is blank
    pooled = np.concatenate([old[np.isfinite(old)], new[np.isfinite(new)]])
    assert got["range"] == pytest.approx(list(np.percentile(pooled, layers.RANGE_PERCENTILES)), abs=1e-5)

    body = layers.layer_result(got, before, "layers/ndvi-before.tif")
    assert body["period"] == {"start": "2019-01-01", "end": "2020-12-31"} and body["layer"]["range"] == got["range"]
    assert body["method"]["params"] == {"window": "before"}

    searched = len(calls)
    heat = layers.zone_layers("lst", SMALL, period, windows=windows, margin_km=1)
    assert heat["origin"] == "derived" and len(calls) == searched  # saved in the same pass
    warm, point = read_frame(heat, heat["frames"][0])
    assert warm[point(-0.32, 39.756)] > warm[point(-0.32, 39.714)]  # the north is warmer: not flipped
    again = layers.zone_layers("ndvi", SMALL, period, windows=windows, margin_km=1)
    assert again["origin"] == "derived" and again["fingerprint"] == got["fingerprint"] and len(calls) == searched
    per_year = layers.zone_layers("ndvi", SMALL, period, margin_km=1)
    assert per_year["fingerprint"] != got["fingerprint"]
    assert [f["t"] for f in per_year["frames"]] == ["2019", "2020", "2021", "2022", "2023"]
    assert per_year["frames"][1]["absence"]["reason"] == "insufficient_qa"


def test_a_uniform_scene_draws_exactly_the_figure_of_the_zone(data, monkeypatch, tmp_path):
    uniform = {"red": digital(0.08), "nir08": digital(0.33), "lwir11": thermal(27.5), "qa_pixel": CLEAR}
    serve(monkeypatch, {2021: [Scene(tmp_path, "u", "2021-06-01", "landsat-8", uniform)]})
    period = {"start": "2021-01-01", "end": "2021-12-31"}
    figure = readers.read_series("ndvi", SMALL, period)["data"].v.item()
    drawn = layers.zone_layers("ndvi", SMALL, period)
    values, _ = read_frame(drawn, drawn["frames"][0])
    assert np.nanmax(np.abs(values - figure)) < 1e-5 and drawn["margin_km"] == layers.MARGIN_KM


def test_offline_or_unreachable_is_an_absence_and_writes_nothing(data, monkeypatch, tmp_path):
    period = {"start": "2019-01-01", "end": "2019-12-31"}
    monkeypatch.setenv("HUP_OFFLINE", "1")
    never = layers.zone_layers("ndvi", SMALL, period)  # the year was never read for this zone
    assert never["absence"]["reason"] == "source_unreachable" and never["frames"] == []
    monkeypatch.delenv("HUP_OFFLINE")
    serve(monkeypatch, before_and_after(tmp_path))
    readers.read_series("ndvi", SMALL, period)  # the figures are read: the scenes are known, not their pixels
    monkeypatch.setenv("HUP_OFFLINE", "1")
    offline = layers.zone_layers("ndvi", SMALL, period)
    assert offline["absence"]["reason"] == "source_unreachable" and "Offline" in offline["absence"]["detail"]["en"]
    monkeypatch.delenv("HUP_OFFLINE")

    def down(ids):
        raise OSError("no route to host")

    monkeypatch.setattr(landsat, "_items", down)
    assert layers.zone_layers("ndvi", SMALL, period)["absence"]["reason"] == "source_unreachable"
    assert not list((data / "derived").rglob("*.tif"))  # only the series of the figures was saved


def test_a_large_area_is_drawn_coarser_and_says_so():
    grid = landsat.pixel_grid([-1.0, 39.0, 0.0, 40.0], 3)
    assert grid["width"] * grid["height"] <= landsat.MAX_PIXELS and grid["pixel_m"] > 100
    west, south, east, north = grid["bounds"]
    assert west < -1.0 and east > 0.0 and south < 39.0 and north > 40.0
    small = landsat.pixel_grid(SMALL["bbox"], 1)
    assert small["pixel_m"] == 30 and small["transform"].e < 0
