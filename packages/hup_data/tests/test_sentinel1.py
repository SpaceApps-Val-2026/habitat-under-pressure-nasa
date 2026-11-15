"""Package A3b: Sentinel-1 RTC radar before and after, and the water it shows below a threshold.

The STAC search and the mosaic are replaced: nothing here uses the network. The real read of l'Horta Sud is in
.dev/telemetry/sentinel1_horta_sud.py.
"""
import json

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from hup_data import layers
from hup_data.readers import landsat, read_series, sentinel1

BBOX = [-0.43, 39.41, -0.39, 39.45]
ZONE = {"id": "fixture-zone", "name": "Fixture zone", "bbox": BBOX}
DATES = ("2024-10-20", "2024-11-01")


class Item:
    def __init__(self, id_):
        self.id = id_


def lines(root):
    file = root / "manifest.jsonl"
    return [json.loads(text) for text in file.read_text(encoding="utf-8").splitlines()] if file.exists() else []


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    return tmp_path


def bimodal(shape_, water_rows, level):
    """Land at -8 dB and water at -22 dB in the first `water_rows` rows, with a small ripple, shifted by `level`."""
    rng = np.random.default_rng(0)
    out = np.full(shape_, -8.0, dtype="float32") + rng.normal(0, 1, shape_).astype("float32")
    out[:water_rows] = -22.0 + rng.normal(0, 1, (water_rows, shape_[1]))
    return out + level


def jrc(monkeypatch, root, water_rows_share=0.25):
    """A JRC occurrence tile over BBOX: 100 % in its northern `water_rows_share`, 0 elsewhere, found by the reader."""
    path = root / "packages" / "jrc-gsw" / "occurrence.tif"
    path.parent.mkdir(parents=True, exist_ok=True)
    occurrence = np.zeros((400, 400), dtype="uint8")
    occurrence[: int(400 * water_rows_share)] = 100
    west, south, east, north = BBOX
    with rasterio.open(path, "w", driver="GTiff", height=400, width=400, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(west, north, (east - west) / 400, (north - south) / 400)) as out:
        out.write(occurrence, 1)
    tile = {"path": "packages/jrc-gsw/occurrence.tif", "version": "1.4", "item": "occurrence", "license": "test",
            "ingest": {"url": "https://global-surface-water.appspot.com/download"}}
    monkeypatch.setattr(landsat, "water_tile", lambda zone: tile)
    return tile


@pytest.fixture
def radar(monkeypatch, data):
    """Passes per orbit and day; before, a quarter of the rows is water, after, half of them."""
    passes = {110: {DATES[0]: [Item("a-110")], DATES[1]: [Item("b-110")]}, 8: {DATES[0]: [Item("a-8")]},
              37: {DATES[1]: [Item("b-37")]}}
    calls = []

    def fake_passes(zone, first, last):
        calls.append((first, last))
        return {orbit: {day: items for day, items in days.items() if first <= day <= last}
                for orbit, days in passes.items()}

    def fake_mosaic(items, crs, transform, shape_):
        return bimodal(shape_, shape_[0] // (4 if items[0].id.startswith("a") else 2), 0.0)

    monkeypatch.setattr(sentinel1, "_passes", fake_passes)
    monkeypatch.setattr(sentinel1, "_mosaic", fake_mosaic)
    jrc(monkeypatch, data)
    return calls


# ---------------------------------------------------------------- grid and threshold
def test_grid_is_metric_covers_the_box_and_is_the_same_for_the_same_zone():
    crs, transform, shape_ = sentinel1.grid_for(ZONE, 20)
    assert crs == "EPSG:32630" and transform.a == 20 and transform.e == -20
    to_grid = Transformer.from_crs(4326, crs, always_xy=True).transform
    west, south = to_grid(BBOX[0], BBOX[1])
    east, north = to_grid(BBOX[2], BBOX[3])
    assert transform.c <= west and transform.f >= north
    assert transform.c + shape_[1] * 20 >= east and transform.f - shape_[0] * 20 <= south
    assert sentinel1.grid_for({**ZONE, "bbox": [-70.0, -30.0, -69.9, -29.9]}, 20)[0] == "EPSG:32719"


def test_otsu_falls_between_two_classes_and_says_how_well_they_separate():
    values = bimodal((100, 100), 30, 0.0)
    threshold, eta = sentinel1.otsu(values)
    assert -19 < threshold < -11 and eta > 0.8
    flat_threshold, flat_eta = sentinel1.otsu(np.random.default_rng(1).normal(-10, 1, 10_000))
    assert flat_eta < eta  # one class: Otsu still cuts, and eta says it is a poor cut
    with pytest.raises(ValueError):
        sentinel1.otsu(np.full(10, -12.0))
    assert -20.0 < sentinel1.otsu(np.array([np.nan, -20.0, -5.0, np.nan]))[0] <= -5.0  # NaN left out


# ---------------------------------------------------------------- radar pair, cache, manifest, absences
def test_radar_pair_uses_the_orbit_both_dates_share_and_keeps_it_as_cogs_with_valid_manifest_lines(data, radar):
    first = sentinel1.read_radar_pair(ZONE, *DATES)
    assert first["absence"] is None and first["origin"] == "remote" and first["unit"] == "dB"
    array = first["data"]
    assert list(array.coords["phase"].values) == ["before", "after"]
    assert array.attrs["orbit"] == 110 and array.attrs["crs"] == "EPSG:32630" and array.attrs["dates"] == list(DATES)
    assert array.shape == (2, *sentinel1.grid_for(ZONE, 20)[2])
    assert first["sources"][0]["items"] == ["a-110", "b-110"] and first["coverage"]["valid_pct"] == 100.0

    written = lines(data)
    assert len(written) == 2 and {line["source"] for line in written} == {"sentinel-1-rtc"}
    assert all(line["format"] == "cog" and line["ingest"]["origin"] == "on_demand" for line in written)
    assert all(layers.check_cog(data / line["path"]) == [] for line in written)  # a real COG, as the line says

    radar.clear()
    again = sentinel1.read_radar_pair(ZONE, *DATES)
    assert again["origin"] == "cache" and radar == []  # not even a search
    assert np.array_equal(again["data"].values, array.values) and again["sources"] == first["sources"]
    assert len(lines(data)) == 2


def test_radar_pair_offline_reads_the_cache_and_otherwise_says_it_cannot(data, radar, monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    empty = sentinel1.read_radar_pair(ZONE, *DATES)
    assert empty["data"] is None and empty["absence"]["reason"] == "source_unreachable" and empty["sources"]
    assert radar == []
    monkeypatch.delenv("HUP_OFFLINE")
    sentinel1.read_radar_pair(ZONE, *DATES)
    monkeypatch.setenv("HUP_OFFLINE", "1")
    assert sentinel1.read_radar_pair(ZONE, *DATES)["origin"] == "cache"


def test_radar_pair_without_a_common_orbit_is_no_coverage_and_a_wrong_orbit_too(data, radar):
    got = sentinel1.read_radar_pair(ZONE, *DATES, orbit=8)
    assert got["absence"]["reason"] == "no_coverage" and "orbit 8" in got["absence"]["detail"]["en"]
    assert not lines(data)
    assert sentinel1.read_radar_pair(ZONE, *DATES, orbit=110)["absence"] is None


def test_the_window_takes_the_closest_passes_of_one_orbit(data, monkeypatch):
    passes = {110: {"2024-10-08": [Item("early")], "2024-10-20": [Item("a")], "2024-11-01": [Item("b")]},
              8: {"2024-10-19": [Item("c")], "2024-11-12": [Item("d")]}}
    monkeypatch.setattr(sentinel1, "_passes", lambda zone, first, last: passes)
    monkeypatch.setattr(sentinel1, "_mosaic", lambda items, crs, transform, shape_: bimodal(shape_, 5, 0.0))
    got = sentinel1.read_radar_pair(ZONE, "2024-10-25", "2024-10-30", slack_days=11)
    assert got["data"].attrs["orbit"] == 110 and got["data"].attrs["dates"] == list(DATES)  # 12 days, not 24
    assert sentinel1.read_radar_pair(ZONE, "2024-10-25", "2024-10-30")["absence"]["reason"] == "no_coverage"


def test_network_failure_empty_mosaic_and_bad_dates(data, monkeypatch, radar):
    assert sentinel1.read_radar_pair(ZONE, DATES[1], DATES[0])["absence"]["reason"] == "not_computed"
    assert sentinel1.read_radar_pair(ZONE, "2010-01-01", "2010-02-01")["absence"]["reason"] == "no_coverage"
    with pytest.raises(ValueError):
        sentinel1.read_radar_pair(ZONE, "yesterday", DATES[1])

    def down(zone, first, last):
        raise ConnectionError("no route")

    monkeypatch.setattr(sentinel1, "_passes", down)
    assert sentinel1.read_radar_pair(ZONE, *DATES)["absence"]["reason"] == "source_unreachable"

    monkeypatch.setattr(sentinel1, "_passes", lambda zone, first, last: {110: {day: [Item(day)] for day in DATES}})
    monkeypatch.setattr(sentinel1, "_mosaic", lambda items, crs, transform, shape_: np.full(shape_, np.nan, "float32"))
    got = sentinel1.read_radar_pair(ZONE, *DATES)
    assert got["data"] is None and got["absence"]["reason"] == "insufficient_qa"
    assert not lines(data)  # an empty read is not kept: it may be fine next time


# ---------------------------------------------------------------- water and the catalog variable
def test_calibration_weights_both_classes_the_same():
    rng = np.random.default_rng(2)
    values = np.concatenate([rng.normal(-22, 3, 300), rng.normal(-8, 3, 20_000)])[np.newaxis]
    occurrence = np.concatenate([np.full(300, 100), np.zeros(20_000)]).astype("uint8")
    weighted = sentinel1.calibrate(values[:, np.newaxis, :], occurrence[np.newaxis, :])
    assert weighted["threshold_db"] == pytest.approx(-15, abs=1) and weighted["water_pixels"] == 300
    assert sentinel1.otsu(values)[0] > -10  # unweighted, the threshold walks into the larger class
    with pytest.raises(ValueError):
        sentinel1.calibrate(values[:, np.newaxis, :20_000], occurrence[np.newaxis, :20_000] * 0)


def test_whole_zone_otsu_follows_the_larger_class():
    """Why the threshold is calibrated: with 1 % water and two kinds of ground, plain Otsu splits the ground."""
    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(-22, 1, 200), rng.normal(-11, 1.5, 12_000), rng.normal(-2, 2, 8_000)])
    assert sentinel1.otsu(values)[0] > -9


def test_water_below_otsu_is_a_quarter_before_and_half_after(data, radar):
    found = sentinel1.detect_water(ZONE, *DATES)
    detection = found["detection"]
    assert found["absence"] is None and found["unit"] == "1" and detection["eta"] > 0.8
    assert -19 < detection["threshold_db"] < -11 and "Otsu" in detection["method"]["en"]
    assert [source["source"] for source in found["sources"]] == ["sentinel-1-rtc", "jrc-gsw"]
    rows = sentinel1.grid_for(ZONE, 20)[2][0]
    inside = detection["inside_pixels"]
    shares = [water / valid for water, valid in zip(detection["water_pixels"], detection["valid_pixels"], strict=True)]
    assert shares[0] == pytest.approx((rows // 4) / rows, abs=0.03) and shares[1] == pytest.approx(0.5, abs=0.03)
    assert detection["new_water_pixels"] == detection["water_pixels"][1] - detection["water_pixels"][0]
    assert detection["valid_pixels"] == [inside, inside] and f"{detection['threshold_db']} dB" in \
        found["sources"][0]["product"]


def test_a_given_threshold_replaces_otsu_and_the_outline_limits_the_pixels(data, radar):
    fixed = sentinel1.detect_water(ZONE, *DATES, threshold_db=-30.0)["detection"]
    assert fixed["threshold_db"] == -30.0 and fixed["eta"] is None and fixed["water_pixels"] == [0, 0]
    west, south, east, north = BBOX
    half = {**ZONE, "geometry": {"type": "Polygon", "coordinates": [[[west, south], [(west + east) / 2, south],
                                                                      [(west + east) / 2, north], [west, north],
                                                                      [west, south]]]}}
    assert sentinel1.detect_water(half, *DATES)["detection"]["inside_pixels"] == pytest.approx(
        fixed["inside_pixels"] / 2, rel=0.05)


def test_radar_water_extent_through_read_series_is_two_rows_and_is_saved(data, radar, monkeypatch):
    got = read_series("radar_water_extent", ZONE, {"start": DATES[0], "end": DATES[1]})
    assert got["absence"] is None and list(got["data"].columns) == ["t", "v", "n_valid"]
    assert got["data"].t.tolist() == list(DATES) and got["data"].v.iloc[1] > got["data"].v.iloc[0]
    assert got["support"]["resolution_m"] == 20 and got["support"]["cells"] > 0
    assert radar == [("2024-10-09", "2024-11-12")]  # one search: 11 days before the first date, 11 after the last
    monkeypatch.setenv("HUP_OFFLINE", "1")
    again = read_series("radar_water_extent", ZONE, {"start": DATES[0], "end": DATES[1]})
    assert again["origin"] == "derived" and again["data"].v.tolist() == got["data"].v.tolist()
    assert "Otsu" in again["sources"][0]["product"] and again["sources"][1]["source"] == "jrc-gsw"
    other = read_series("radar_water_extent", {**ZONE, "id": "elsewhere"}, {"start": DATES[0], "end": DATES[1]})
    assert other["absence"]["reason"] == "source_unreachable"


def test_without_a_jrc_tile_or_known_water_the_threshold_must_be_given(data, radar, monkeypatch):
    monkeypatch.setattr(landsat, "water_tile", lambda zone: None)
    assert sentinel1.detect_water(ZONE, *DATES)["absence"]["reason"] == "needs_package"
    assert sentinel1.detect_water(ZONE, *DATES, threshold_db=-15.0)["absence"] is None
    jrc(monkeypatch, data, water_rows_share=0.0)
    assert sentinel1.detect_water(ZONE, *DATES)["absence"]["reason"] == "not_computed"
