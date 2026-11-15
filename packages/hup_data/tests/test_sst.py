"""MUR SST: the sea next to a zone, its absences and its cache. Synthetic Zarr chunks, no network."""
import json

import numpy as np
import pytest
import requests
from numcodecs import Blosc

from hup_data import readers
from hup_data.readers import sst

DAYS = 62  # June and July 2002 whole, 1 August alone: that month is left out
# Chunk (129, 179): cell (r, c) has its centre at latitude 39.01 + 0.01 r and longitude -0.99 + 0.01 c.
COAST = 50  # columns from 50 eastwards are open sea; the rest is land, and every other chunk is land
LAKE = (slice(70, 80), slice(55, 60))  # a lake inside the strip, with a temperature (40 °C) that must not count
NEAR = {"id": "near", "name": "Coastal", "bbox": [-0.59, 39.41, -0.495, 39.61]}
INLAND = {"id": "inland", "name": "Inland", "bbox": [-0.99, 39.41, -0.89, 39.61]}  # about 34 km from the sea
BEHIND = {"id": "behind", "name": "Ten km inland", "bbox": [-0.70, 39.41, -0.62, 39.61]}  # sea in the strip only
PERIOD = {"start": "2002-01-01", "end": "2002-12-31"}


def chunk(name: str, row: int, col: int) -> bytes:
    if name == "mask":
        flags = np.full((DAYS, 100, 100), 2, "i1")
        if (row, col) == (129, 179):
            flags[:, :, COAST:] = 1
            flags[:, LAKE[0], LAKE[1]] = 5
        return Blosc().encode(flags.tobytes())
    values = np.full((DAYS, 100, 100), sst.FILL, "<i2")
    day = np.arange(DAYS)[:, None, None]
    sea = np.round((20 + 0.01 * day - sst.OFFSET) / sst.SCALE).astype("<i2")  # 20 °C plus 0.01 °C per day
    values[:, :, COAST:] = np.broadcast_to(sea, (DAYS, 100, 100 - COAST))
    values[:, LAKE[0], LAKE[1]] = np.round((40 - sst.OFFSET) / sst.SCALE)
    values[:, 0, COAST] = sst.FILL  # one sea cell always missing: it is left out, not taken as zero
    values[5] = sst.FILL  # a day with no value anywhere: June keeps 29 days
    return Blosc().encode(values.tobytes())


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    monkeypatch.setattr(sst, "N_DAYS", DAYS)
    calls = []

    def download(url):
        calls.append(url)
        name, key = url.removeprefix(sst.STORE + "/").split("/")
        _, row, col = key.split(".")
        return chunk(name, int(row), int(col))

    monkeypatch.setattr(sst, "_download", download)
    return tmp_path, calls


def manifest(folder):
    file = folder / "manifest.jsonl"
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()] if file.is_file() else []


def test_the_sea_next_to_a_coastal_zone_is_read_once_and_cached(store):
    folder, calls = store
    got = readers.read_series("sea_surface_temperature", NEAR, PERIOD)
    assert got["absence"] is None and got["origin"] == "remote" and got["unit"] == "°C"
    table = got["data"]
    assert table.t.tolist() == ["2002-06-01", "2002-07-01"]  # August has one day in the record: left out
    assert table.v.tolist() == pytest.approx([20 + 0.01 * np.mean([d for d in range(30) if d != 5]), 20.45], abs=1e-3)
    assert table.n_valid.tolist() == [29, 31]
    assert got["support"]["resolution_m"] == 1100 and got["support"]["cells"] > 100
    assert got["coverage"] == {"valid_pct": round(100 * 2 / 12, 1), "n_dates": 2}  # 12 months asked, 2 in the record
    assert got["sources"][0]["source"] == "mur-sst" and got["sources"][0]["n_items"] == 1

    sst_calls = [url for url in calls if "/analysed_sst/" in url]
    assert sst_calls == [f"{sst.STORE}/analysed_sst/0.129.179"]  # only the chunk with strip cells is fetched
    lines = manifest(folder)
    assert len(lines) == len(calls) and all(line["source"] == "mur-sst" for line in lines)
    assert {line["format"] for line in lines} == {"zarr"}
    assert [line["variables"] for line in lines if "variables" in line] == [["sea_surface_temperature"]]

    again = readers.read_series("sea_surface_temperature", NEAR, PERIOD)
    assert again["origin"] == "derived" and again["data"][table.columns].equals(table)
    summer = readers.read_series("sea_surface_temperature", NEAR, {**PERIOD, "months": [7, 8]})
    assert summer["origin"] == "cache" and summer["data"].t.tolist() == ["2002-07-01"]
    assert summer["coverage"]["valid_pct"] == 50.0
    assert len(calls) == len(lines) and len(manifest(folder)) == len(lines)  # nothing fetched or recorded twice


def test_a_zone_that_does_not_reach_the_sea_has_no_coverage(store):
    _, calls = store
    for zone in (INLAND, BEHIND):  # far from the sea, and close enough for the strip but not on the coast
        got = readers.read_series("sea_surface_temperature", zone, PERIOD)
        assert got["data"] is None and got["absence"]["reason"] == "no_coverage"
        assert got["sources"][0]["source"] == "mur-sst"
    assert not [url for url in calls if "/analysed_sst/" in url]


def test_a_period_outside_the_record_has_no_coverage_and_asks_nothing(store):
    _, calls = store
    got = readers.read_series("sea_surface_temperature", NEAR, {"start": "2021-01-01", "end": "2021-12-31"})
    assert got["absence"]["reason"] == "no_coverage" and "2002-08-01" in got["absence"]["detail"]["es"]
    assert calls == []


def test_offline_without_cache_and_a_silent_store_are_absences(store, monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    got = readers.read_series("sea_surface_temperature", NEAR, PERIOD)
    assert got["absence"]["reason"] == "source_unreachable" and got["origin"] == "cache"
    monkeypatch.delenv("HUP_OFFLINE")

    def broken(url):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(sst, "_download", broken)
    got = readers.read_series("sea_surface_temperature", NEAR, PERIOD)
    assert got["absence"]["reason"] == "source_unreachable" and got["origin"] == "remote"


@pytest.mark.data
def test_real_summer_sea_temperature_off_a_known_coast():
    """With the real store, or the chunks already cached: plausible Mediterranean summer means."""
    zone = {"id": "test-sagunt-coast", "name": "Test coast", "bbox": [-0.35, 39.60, -0.15, 39.75]}
    got = sst.read("sea_surface_temperature", zone, {"start": "2003-06-01", "end": "2019-08-31",
                                                       "months": [6, 7, 8]})
    if got["absence"] is not None:
        pytest.skip(f"MUR SST not readable here: {got['absence']['reason']}")
    assert len(got["data"]) == 51 and 18 < got["data"].v.min() and got["data"].v.max() < 30
